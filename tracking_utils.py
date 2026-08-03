"""
tracking_utils.py
-----------------
Utilidades para estabilizar el seguimiento de objetos:

1. TrackClassVoter: votación temporal por track_id para evitar parpadeo
   de etiquetas entre clases similares (auto↔camión, persona↔moto).

2. disambiguate_class: reglas heurísticas basadas en el tamaño/aspecto del
   bounding box para resolver confusiones frecuentes entre clases cercanas.
"""

from collections import defaultdict, Counter
import math
import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Clases de COCO referenciadas por índice
# ---------------------------------------------------------------------------
CLS_PERSON    = 0
CLS_BICYCLE   = 1
CLS_CAR       = 2
CLS_MOTORCYCLE= 3
CLS_BUS       = 5
CLS_TRAIN     = 6
CLS_TRUCK     = 7
CLS_BOAT      = 8


# ---------------------------------------------------------------------------
# Votación temporal por track_id
# ---------------------------------------------------------------------------
class TrackClassVoter:
    """
    Mantiene un historial de las últimas `window` predicciones de clase
    para cada track_id y devuelve la clase mayoritaria.

    Esto estabiliza etiquetas que oscilan entre clases en frames consecutivos
    (ej. auto→camión→auto) mostrando siempre la clase más frecuente reciente.
    """

    def __init__(self, window: int = 10):
        """
        Args:
            window: número de frames recientes a considerar en el voto.
                    Mayor window → más estabilidad, más latencia al cambiar.
        """
        self.window = window
        # {tracker_id: deque de class_id}
        self._history: dict = defaultdict(list)

    def update_and_vote(self, tracker_id: int, class_id: int) -> int:
        """
        Registra la nueva predicción y devuelve la clase ganadora del voto.
        """
        hist = self._history[tracker_id]
        hist.append(class_id)
        # Mantener solo los últimos `window` valores
        if len(hist) > self.window:
            hist.pop(0)
        winner, _ = Counter(hist).most_common(1)[0]
        return int(winner)

    def purge(self, active_ids: set):
        """Elimina tracks que ya no están activos para liberar memoria."""
        for tid in list(self._history.keys()):
            if tid not in active_ids:
                del self._history[tid]


# ---------------------------------------------------------------------------
# Recuperación del identificador tras perder un track
# ---------------------------------------------------------------------------
class TrackIdStabilizer:
    """
    Reasigna a un track recién creado el identificador de otro que acababa de
    perderse en el mismo sitio.

    ByteTrack asocia por solapamiento de cajas. Cuando un vehículo se ocluye y
    reaparece desplazado, o cuando avanza mucho entre dos fotogramas, el
    solapamiento no basta y el seguidor lo da por objeto nuevo: es lo que hace
    que un auto pase de #99 a #111 mientras cruza la imagen.

    Aquí se guarda dónde estaba cada track al desaparecer y con qué velocidad
    iba. Si poco después surge un identificador nuevo cerca de donde ese track
    debería estar, con tamaño y clase parecidos, se entiende que es el mismo
    objeto y se le devuelve su identificador original.

    Es deliberadamente conservador: ante la duda deja el identificador nuevo.
    Confundir dos vehículos distintos es peor que mostrar un número cambiado,
    porque arrastraría el error a las lecturas de matrícula asociadas al track.
    """

    def __init__(self, max_gap_frames: int = 45, max_distance_ratio: float = 1.2,
                 max_size_ratio: float = 1.6, margen_ambiguedad: float = 1.8):
        """
        Args:
            max_gap_frames:     fotogramas que se recuerda un track desaparecido
            max_distance_ratio: distancia máxima admitida entre la posición
                                predicha y la nueva, en múltiplos del tamaño
                                del objeto
            max_size_ratio:     cambio de tamaño máximo admitido entre ambos
            margen_ambiguedad:  cuántas veces mejor debe ser el mejor candidato
                                frente al segundo para aceptarlo. Si dos tracks
                                perdidos explican igual de bien la aparición, no
                                se remapea nada.
        """
        self.max_gap_frames = max_gap_frames
        self.max_distance_ratio = max_distance_ratio
        self.max_size_ratio = max_size_ratio
        self.margen_ambiguedad = margen_ambiguedad
        self.descartes_por_ambiguedad = 0

        # {tid: {"centro", "tam", "vel", "frame", "clase"}}
        self._ultimo = {}
        # {tid_nuevo: tid_original}, para seguir traduciendo en los siguientes
        # fotogramas y no solo en el de la reaparición
        self._remapeo = {}
        self._conocidos = set()
        self.recuperados = 0

    @staticmethod
    def _centro_y_tam(caja):
        x1, y1, x2, y2 = float(caja[0]), float(caja[1]), float(caja[2]), float(caja[3])
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0), max(1.0, (x2 - x1)), max(1.0, (y2 - y1))

    def apply(self, tracker_ids, boxes, class_ids, frame_count):
        """
        Devuelve la lista de identificadores ya corregidos.

        Args:
            tracker_ids: identificadores que asignó el seguidor este fotograma
            boxes:       cajas correspondientes
            class_ids:   clases correspondientes
            frame_count: número de fotograma actual
        """
        if tracker_ids is None or len(tracker_ids) == 0:
            return tracker_ids

        activos = {int(t) for t in tracker_ids}
        salida = [None] * len(tracker_ids)

        # Identificadores ya ocupados en ESTE fotograma. Sin llevar esta cuenta,
        # dos apariciones que encajan con el mismo track perdido recibían ambas
        # su identificador y dos vehículos distintos acababan compartiéndolo.
        ocupados = set()
        pendientes = []

        # Primera pasada: resolver lo ya decidido y reservar sus identificadores
        for i, (tid, caja, cls) in enumerate(zip(tracker_ids, boxes, class_ids)):
            tid = int(tid)
            if tid in self._remapeo:
                salida[i] = self._remapeo[tid]
                ocupados.add(salida[i])
            elif tid in self._conocidos:
                salida[i] = tid
                ocupados.add(tid)
            else:
                pendientes.append((i, tid, caja, cls))

        # Segunda pasada: decidir las apariciones nuevas, de la más cercana a su
        # candidato a la más lejana, para que ante competencia gane la mejor
        propuestas = []
        for i, tid, caja, cls in pendientes:
            cand, dist = self._buscar_continuacion(caja, cls, frame_count, activos)
            propuestas.append((dist if cand is not None else float("inf"),
                               i, tid, cand))
        propuestas.sort(key=lambda p: p[0])

        for _, i, tid, cand in propuestas:
            # Un track perdido solo puede continuarse una vez por fotograma
            if cand is not None and cand not in ocupados:
                self._remapeo[tid] = cand
                self.recuperados += 1
                salida[i] = cand
                ocupados.add(cand)
            else:
                self._conocidos.add(tid)
                salida[i] = tid
                ocupados.add(tid)

        # Actualizar la última posición conocida de cada track ya traducido
        for tid_final, caja, cls in zip(salida, boxes, class_ids):
            self._registrar(int(tid_final), caja, int(cls), frame_count)

        self._purgar(frame_count)
        return salida

    def _buscar_continuacion(self, caja, cls, frame_count, activos):
        """
        Returns:
            (tid_candidato, distancia) o (None, inf) si no hay uno claro.
        """
        centro, ancho, alto = self._centro_y_tam(caja)
        tam = ancho * alto
        candidatos = []

        for tid, info in self._ultimo.items():
            # Un track que sigue vivo este fotograma no puede continuarse
            if tid in activos:
                continue
            hueco = frame_count - info["frame"]
            if hueco <= 0 or hueco > self.max_gap_frames:
                continue
            if info["clase"] != int(cls):
                continue

            # Tamaño parecido: un camión no continúa a una moto
            razon = max(tam, info["tam"]) / max(1.0, min(tam, info["tam"]))
            if razon > self.max_size_ratio:
                continue

            # Posición esperada según la velocidad que llevaba
            px = info["centro"][0] + info["vel"][0] * hueco
            py = info["centro"][1] + info["vel"][1] * hueco
            dist = math.hypot(centro[0] - px, centro[1] - py)
            if dist > max(ancho, alto) * self.max_distance_ratio:
                continue

            # Coherencia de sentido: el desplazamiento desde la última posición
            # conocida debe ir en la dirección en que circulaba. Sin esto, en un
            # cruce de carriles opuestos un vehículo podía heredar el
            # identificador del que venía de frente.
            vx, vy = info["vel"]
            if math.hypot(vx, vy) > 1.0:
                dx = centro[0] - info["centro"][0]
                dy = centro[1] - info["centro"][1]
                if dx * vx + dy * vy <= 0:
                    continue

            candidatos.append((dist, tid))

        if not candidatos:
            return None, float("inf")

        candidatos.sort()
        # Si el segundo candidato explica la aparición casi tan bien como el
        # primero, no hay forma de saber cuál es: se deja el identificador
        # nuevo. Equivocarse aquí intercambia vehículos, y ese error se
        # propagaría a las matrículas asociadas al track.
        if len(candidatos) > 1 and candidatos[1][0] < candidatos[0][0] * self.margen_ambiguedad:
            self.descartes_por_ambiguedad += 1
            return None, float("inf")

        return candidatos[0][1], candidatos[0][0]

    def _registrar(self, tid, caja, cls, frame_count):
        centro, ancho, alto = self._centro_y_tam(caja)
        previo = self._ultimo.get(tid)
        vel = (0.0, 0.0)
        if previo is not None:
            dt = frame_count - previo["frame"]
            if dt > 0:
                vel = ((centro[0] - previo["centro"][0]) / dt,
                       (centro[1] - previo["centro"][1]) / dt)
        self._ultimo[tid] = {"centro": centro, "tam": ancho * alto,
                             "vel": vel, "frame": frame_count, "clase": cls}
        self._conocidos.add(tid)

    def _purgar(self, frame_count):
        for tid in [t for t, i in self._ultimo.items()
                    if frame_count - i["frame"] > self.max_gap_frames]:
            del self._ultimo[tid]
            self._conocidos.discard(tid)
            for nuevo, viejo in [(n, v) for n, v in self._remapeo.items() if v == tid]:
                del self._remapeo[nuevo]


# ---------------------------------------------------------------------------
# Histéresis de confianza por track
# ---------------------------------------------------------------------------
class TrackConfidenceGate:
    """
    Decide si la detección de un track debe mostrarse, aplicando histéresis
    para evitar el parpadeo del recuadro durante oclusiones parciales.

    Máquina de estados por track_id:

        PENDIENTE ──(confianza ≥ activación, `confirm_frames` veces)──> CONFIRMADO
        CONFIRMADO ──(confianza < mantenimiento, `grace_frames` seguidos)──> PENDIENTE

    Un track CONFIRMADO conserva su recuadro mientras su confianza no baje del
    umbral de mantenimiento. Y aunque lo baje, aguanta `grace_frames` más antes
    de ocultarse: así una caída de uno o dos frames (un vehículo pasando bajo un
    semáforo colgante) no interrumpe la superposición.
    """

    def __init__(self, confirm_frames: int = 2, grace_frames: int = 5):
        """
        Args:
            confirm_frames: frames consecutivos sobre el umbral de activación
                            necesarios para confirmar un track nuevo. Mayor →
                            menos falsos positivos, más latencia al aparecer.
            grace_frames:   frames que un track confirmado sigue mostrándose
                            tras caer bajo el umbral de mantenimiento. Mayor →
                            tolera oclusiones más largas, más riesgo de dejar
                            un recuadro "pegado" a un objeto que ya se fue.
        """
        self.confirm_frames = confirm_frames
        self.grace_frames = grace_frames
        # {tid: nº de frames consecutivos sobre el umbral de activación}
        self._hits: dict = defaultdict(int)
        # {tid: True si el track ya está confirmado}
        self._confirmed: dict = {}
        # {tid: nº de frames consecutivos bajo el umbral de mantenimiento}
        self._misses: dict = defaultdict(int)

    def accept(self, tracker_id: int, class_id: int, confidence: float) -> bool:
        """
        Registra la detección de este frame y devuelve si debe dibujarse.
        """
        # Import local para no crear dependencia circular a nivel de módulo
        from label_mapper import get_activation_threshold, get_keep_threshold

        tid = int(tracker_id)
        activation = get_activation_threshold(class_id)
        keep = get_keep_threshold(class_id)

        if self._confirmed.get(tid, False):
            # Track ya visible: se aplica el umbral permisivo + margen de gracia
            if confidence >= keep:
                self._misses[tid] = 0
                return True
            self._misses[tid] += 1
            if self._misses[tid] <= self.grace_frames:
                return True          # Caída transitoria: se sostiene el recuadro
            # Caída sostenida: el track vuelve a estado pendiente
            self._confirmed[tid] = False
            self._hits[tid] = 0
            self._misses[tid] = 0
            return False

        # Track aún no confirmado: se exige el umbral estricto varias veces
        if confidence >= activation:
            self._hits[tid] += 1
            if self._hits[tid] >= self.confirm_frames:
                self._confirmed[tid] = True
                self._misses[tid] = 0
                return True
        else:
            self._hits[tid] = 0
        return False

    def is_confirmed(self, tracker_id: int) -> bool:
        """True si el track está actualmente en estado visible."""
        return self._confirmed.get(int(tracker_id), False)

    def purge(self, keep_ids: set):
        """Elimina el estado de tracks que ya no están activos ni perdidos."""
        for store in (self._hits, self._confirmed, self._misses):
            for tid in list(store.keys()):
                if tid not in keep_ids:
                    del store[tid]


# ---------------------------------------------------------------------------
# Desambiguación de clase por geometría del bounding box
# ---------------------------------------------------------------------------
def disambiguate_class(class_id: int, xyxy: np.ndarray, frame_shape: tuple) -> int:
    """
    Aplica reglas heurísticas para corregir clasificaciones erróneas
    frecuentes en cámaras de tráfico/videovigilancia.

    Reglas implementadas:
    ─────────────────────
    Truck (7) → Car (2):
        Si el ancho del bbox es menor al 8% del ancho del frame Y el área
        es menor al 3% del frame, probablemente es un auto o camioneta
        pickup visto de lejos, no un camión.

    Truck (7) → Car (2) [aspecto]:
        Los camiones reales son más anchos que altos (aspect > 1.8).
        Si el bbox es casi cuadrado o vertical, es más probable un auto/pickup.

    Person (0) → Motorcycle (3):
        Las personas son más altas que anchas (aspect < 0.7 típicamente).
        Si el bbox detectado como "person" es más ancho que alto (aspect > 1.0)
        probablemente es una motocicleta con jinete.

    Train (6) → Truck (7):
        Los trenes son extremadamente anchos. Si la detección de tren no
        ocupa al menos el 30% del ancho del frame, puede ser un camión.

    Boat (8) → None (filtrar):
        Si el objeto no está en la parte inferior del frame (agua/calle),
        es más probable que sea otro objeto mal clasificado.
        Esta función devuelve CLS_BOAT con confianza reducida a nivel del
        caller (la función devuelve -1 para señalar "ignorar detección").

    Args:
        class_id:    clase predicha por YOLO (índice COCO)
        xyxy:        array [x1, y1, x2, y2] del bounding box en píxeles
        frame_shape: tupla (height, width, channels) del frame

    Returns:
        class_id posiblemente corregido. Devuelve -1 para señalar que
        la detección debe ser descartada.
    """
    x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
    frame_h, frame_w = frame_shape[0], frame_shape[1]

    bbox_w = x2 - x1
    bbox_h = y2 - y1
    bbox_area = bbox_w * bbox_h
    frame_area = frame_w * frame_h

    if bbox_w <= 0 or bbox_h <= 0:
        return class_id

    aspect = bbox_w / bbox_h          # >1 = más ancho que alto (horizontal)
    rel_w  = bbox_w / frame_w         # fracción del ancho del frame
    rel_area = bbox_area / frame_area  # fracción del área del frame

    # ------------------------------------------------------------------
    # Regla 1 — Truck pequeño o cuadrado → Car
    #   Los camiones/tráilers reales son anchos y grandes.
    #   Si el bbox es pequeño O no es horizontal → probablemente auto/pickup
    # ------------------------------------------------------------------
    if class_id == CLS_TRUCK:
        # Muy pequeño en relación al frame → auto o pickup lejano
        if rel_area < 0.025 and rel_w < 0.12:
            return CLS_CAR
        # Más alto que ancho → no es camión (podría ser una camioneta de frente/atrás)
        if aspect < 0.85:
            return CLS_CAR

    # ------------------------------------------------------------------
    # Regla 2 — Person con aspect ratio horizontal → Motorcycle
    #   Una persona de pie siempre es más alta que ancha (aspect < 0.8).
    #   Si aparece horizontal/cuadrada es jinete en moto.
    # ------------------------------------------------------------------
    if class_id == CLS_PERSON:
        if aspect > 0.95:
            return CLS_MOTORCYCLE

    # ------------------------------------------------------------------
    # Regla 3 — Train que no ocupa gran parte del frame → Truck
    #   Un tren real tapa la mayor parte del encuadre horizontalmente.
    # ------------------------------------------------------------------
    if class_id == CLS_TRAIN:
        # Un tren detectado por una cámara de carretera es siempre un
        # tractocamión mal clasificado: el remolque largo y liso se parece a un
        # vagón. Se convierte sin condición de tamaño.
        #
        # La clase "tren" NO se retira de la lista de inferencia aunque nunca
        # llegue a mostrarse. Comprobado que hacerlo pierde la detección
        # entera: ultralytics asigna la clase por máximo sobre las 80 y filtra
        # después, así que una caja cuya mejor clase es "tren" desaparece en
        # lugar de reetiquetarse. Manteniéndola, la caja sobrevive y aquí se
        # corrige. En 70 fotogramas de una cámara real eran 37 detecciones que
        # se habrían perdido.
        return CLS_TRUCK

    # ------------------------------------------------------------------
    # Regla 4 — Boat que no está en zona inferior del frame → descartar
    #   En cámaras de tráfico terrestre, un barco en la parte alta del
    #   frame casi siempre es un falso positivo (tanque, tubo, etc.).
    # ------------------------------------------------------------------
    if class_id == CLS_BOAT:
        y_center_rel = ((y1 + y2) / 2) / frame_h
        if y_center_rel < 0.65:   # No está en la mitad inferior
            return -1             # Señal de "descartar"

    return class_id


# ---------------------------------------------------------------------------
# Identificador de un track interno del tracker
# ---------------------------------------------------------------------------

# Valor con el que ByteTrack marca un track que aún no tiene identificador
# público, por no haber superado minimum_consecutive_frames.
_SIN_ID = -1


def get_track_id(track):
    """
    Devuelve el identificador público de un track interno de ByteTrack.

    El nombre del atributo cambió entre versiones de supervision: en 0.29 los
    objetos STrack exponen `external_track_id` (el que acaba en
    detections.tracker_id) e `internal_track_id`, mientras que versiones
    anteriores usaban `track_id`. Se prueban ambos para no depender de una
    versión concreta.

    Returns:
        int con el identificador, o None si el track todavía no tiene uno
        asignado o el objeto no expone ninguno reconocible.
    """
    for atributo in ("external_track_id", "track_id"):
        valor = getattr(track, atributo, None)
        if valor is None:
            continue
        try:
            valor = int(valor)
        except (TypeError, ValueError):
            continue
        if valor != _SIN_ID:
            return valor
    return None


# ---------------------------------------------------------------------------
# Ghost box rendering — muestra posición predicha de tracks perdidos
# ---------------------------------------------------------------------------

# Máximo de frames perdidos para seguir mostrando el ghost box.
# A 25fps, 25 frames = 1 segundo de predicción visual.
# lost_track_buffer del tracker puede ser mayor; el ghost deja de dibujarse
# antes para no generar ruido visual de tracks muy viejos.
GHOST_MAX_FRAMES = 25

# Color del ghost box: naranja ámbar para distinguirlo de detecciones reales
GHOST_COLOR = (0, 165, 255)   # BGR: naranja

# Errores ya avisados, para no repetir el mismo mensaje en cada fotograma
_errores_ghost_avisados = set()


def _draw_dashed_rect(img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                      color: tuple, thickness: int = 1, dash_len: int = 10) -> None:
    """
    Dibuja un rectángulo con borde discontinuo (dashed) sobre 'img' in-place.
    Se usan 4 segmentos de línea (top, right, bottom, left) con huecos regulares.
    """
    # Los 4 lados: (ax,ay) → (bx,by)
    sides = [
        (x1, y1, x2, y1),   # top
        (x2, y1, x2, y2),   # right
        (x2, y2, x1, y2),   # bottom
        (x1, y2, x1, y1),   # left
    ]
    for ax, ay, bx, by in sides:
        dx, dy = bx - ax, by - ay
        length = max(1, int(math.hypot(dx, dy)))
        step = dash_len * 2
        for i in range(0, length, step):
            t0 = i / length
            t1 = min((i + dash_len) / length, 1.0)
            p0 = (int(ax + t0 * dx), int(ay + t0 * dy))
            p1 = (int(ax + t1 * dx), int(ay + t1 * dy))
            cv2.line(img, p0, p1, color, thickness, cv2.LINE_AA)


def draw_ghost_box(img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                   label: str = "", color: tuple = GHOST_COLOR,
                   alpha: float = 0.12, dash_len: int = 9) -> None:
    """
    Dibuja un "ghost box" semitransparente con borde discontinuo para indicar
    la posición predicha (Kalman) de un track perdido por oclusión.

    Args:
        img:      frame BGR sobre el que se dibuja (modificado in-place)
        x1,y1,   esquina superior-izquierda del bounding box predicho
        x2,y2:   esquina inferior-derecha del bounding box predicho
        label:   texto de etiqueta (ej. "#12 ~Auto")
        color:   color BGR del borde y etiqueta
        alpha:   opacidad del relleno semitransparente (0=invisible, 1=sólido)
        dash_len: longitud de cada trazo discontinuo en píxeles
    """
    h, w = img.shape[:2]

    # Clamp a los límites del frame
    x1 = max(0, min(int(x1), w - 1))
    y1 = max(0, min(int(y1), h - 1))
    x2 = max(x1 + 1, min(int(x2), w - 1))
    y2 = max(y1 + 1, min(int(y2), h - 1))

    if x2 <= x1 or y2 <= y1:
        return

    # Relleno semitransparente
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
    cv2.addWeighted(overlay, alpha, img, 1.0 - alpha, 0, img)

    # Borde discontinuo
    _draw_dashed_rect(img, x1, y1, x2, y2, color, thickness=1, dash_len=dash_len)

    # Etiqueta compacta
    if label:
        font        = cv2.FONT_HERSHEY_SIMPLEX
        font_scale  = 0.42
        thickness   = 1
        (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness)
        lx = x1 + 2
        ly = y1 + th + 3
        # Fondo de texto
        cv2.rectangle(img, (lx - 1, ly - th - 2), (lx + tw + 2, ly + 2), (20, 20, 20), -1)
        cv2.putText(img, label, (lx, ly), font, font_scale, color, thickness, cv2.LINE_AA)


def _iou(caja_a, caja_b) -> float:
    """Intersección sobre unión de dos cajas [x1, y1, x2, y2]."""
    ax1, ay1, ax2, ay2 = caja_a
    bx1, by1, bx2, by2 = caja_b

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    ancho, alto = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    interseccion = ancho * alto
    if interseccion <= 0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - interseccion
    return interseccion / union if union > 0 else 0.0


# Solapamiento a partir del cual se considera que un ghost box representa el
# mismo objeto que una detección activa, y por tanto sobra.
GHOST_MAX_OVERLAP = 0.30


def render_ghost_tracks(
    img: np.ndarray,
    tracker,
    frame_count: int,
    track_last_seen: dict,
    track_last_class: dict,
    get_label_fn,
    ghost_max_frames: int = GHOST_MAX_FRAMES,
    active_boxes=None,
    max_overlap: float = GHOST_MAX_OVERLAP,
) -> None:
    """
    Recorre tracker.lost_tracks y dibuja ghost boxes para los tracks que
    fueron vistos recientemente y ahora están perdidos por oclusión.

    Args:
        img:               frame BGR (modificado in-place)
        tracker:           instancia de sv.ByteTrack
        frame_count:       frame actual del stream
        track_last_seen:   dict {track_id → frame_count cuando se vio por última vez}
        track_last_class:  dict {track_id → class_id votado más reciente}
        get_label_fn:      función get_label_es(class_id) → str
        ghost_max_frames:  máximo de frames perdidos para mostrar ghost
        active_boxes:      cajas de las detecciones que sí se están dibujando
                           este frame. Los ghosts que se solapen con alguna se
                           omiten: representan el mismo objeto físico.
        max_overlap:       IoU a partir del cual se considera duplicado
    """
    lost_tracks = getattr(tracker, 'lost_tracks', [])
    if not lost_tracks:
        return

    cajas_activas = [] if active_boxes is None else [
        (float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in active_boxes
    ]

    # Cuando el seguidor fragmenta un objeto, deja varios tracks perdidos casi
    # encima. Se recorren del más reciente al más antiguo y se van acumulando
    # los ya dibujados, de modo que ante un solapamiento sobrevive el fantasma
    # con información más fresca.
    def _visto_por_ultima_vez(t):
        tid = get_track_id(t)
        return track_last_seen.get(tid, -1) if tid is not None else -1

    lost_tracks = sorted(lost_tracks, key=_visto_por_ultima_vez, reverse=True)
    cajas_dibujadas = []

    for lost_track in lost_tracks:
        try:
            tid = get_track_id(lost_track)
            if tid is None:
                continue
            last_seen = track_last_seen.get(tid)
            if last_seen is None:
                continue
            frames_lost = frame_count - last_seen
            if frames_lost <= 0 or frames_lost > ghost_max_frames:
                continue

            # Posición predicha por el filtro de Kalman del tracker
            tlbr = lost_track.tlbr          # [x1, y1, x2, y2]

            # Si ya hay una detección activa sobre ese mismo sitio, este ghost
            # es un track viejo que el seguidor sustituyó por otro con id
            # distinto. Dibujarlo apilaría dos o tres recuadros sobre el mismo
            # vehículo, cada uno con su número.
            caja = (float(tlbr[0]), float(tlbr[1]), float(tlbr[2]), float(tlbr[3]))
            if any(_iou(caja, otra) > max_overlap
                   for otra in cajas_activas + cajas_dibujadas):
                continue

            x1, y1, x2, y2 = int(tlbr[0]), int(tlbr[1]), int(tlbr[2]), int(tlbr[3])

            cls_id  = track_last_class.get(tid, -1)
            cls_str = get_label_fn(cls_id) if cls_id >= 0 else "?"
            # "~" indica que es posición predicha (no detectada directamente)
            ghost_label = f"#{tid} ~{cls_str}"

            # Reducir alpha a medida que el track lleva más tiempo perdido
            # (fade-out gradual: de 0.15 a 0.04 conforme frames_lost → ghost_max_frames)
            fade = 1.0 - (frames_lost / ghost_max_frames)
            alpha = max(0.04, 0.15 * fade)

            draw_ghost_box(img, x1, y1, x2, y2, label=ghost_label, alpha=alpha)
            cajas_dibujadas.append(caja)

        except Exception as e:
            # Un ghost box fallido no debe romper el stream, pero tampoco puede
            # desaparecer sin dejar rastro: al tragarse la excepción en silencio,
            # un cambio de nombre de atributo en supervision dejó esta función
            # sin dibujar nada durante mucho tiempo sin que nadie lo notara.
            # Se informa una sola vez por tipo de error para no inundar la
            # consola, ya que esto corre en cada fotograma.
            firma = f"{type(e).__name__}: {e}"
            if firma not in _errores_ghost_avisados:
                _errores_ghost_avisados.add(firma)
                print(f"[GhostTracks] Ghost box omitido — {firma}")
