"""
camera_health.py
----------------
Vigila la disponibilidad de cada cámara para mostrar su estado en la lista.

El sondeo se hace en un hilo aparte y en paralelo, nunca durante la petición
web: abrir un stream puede tardar varios segundos, y hacerlo dentro de la
petición congelaría la interfaz cada vez que se refresca la lista.

Para no cargar las cámaras con conexiones de más, una que ya esté emitiendo en
la cuadrícula no se sondea: el propio generador de vídeo avisa de que está viva.
Muchas cámaras baratas admiten muy pocas conexiones simultáneas, así que abrir
una segunda solo para comprobar el estado puede llegar a cortar la que se está
viendo.
"""

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import cv2
import requests

# Puerto por defecto de cada esquema, cuando la URL no lo indica
PUERTOS_POR_ESQUEMA = {
    "rtsp": 554,
    "rtsps": 322,
    "rtmp": 1935,
    "http": 80,
    "https": 443,
}

# Estados posibles
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_UNKNOWN = "unknown"      # Aún no se ha comprobado desde el arranque

# Segundos entre rondas de comprobación
CHECK_INTERVAL = 30.0

# Tiempo máximo que se espera a una cámara antes de darla por caída.
# Las cámaras IP lentas pueden tardar un par de segundos en el primer fotograma.
PROBE_TIMEOUT = 6.0

# Si el generador de vídeo ha servido fotogramas de esta cámara hace menos de
# este tiempo, se da por viva sin sondearla.
ALIVE_GRACE = 20.0

# Sondeos simultáneos. Con muchas cámaras caídas, hacerlo en serie tardaría
# PROBE_TIMEOUT por cada una.
MAX_PARALLEL_PROBES = 6


def _probe_http(source: str) -> bool:
    """
    Comprueba una fuente HTTP/HTTPS pidiendo los primeros bytes.

    Es mucho más rápido que abrirla con OpenCV y evita decodificar vídeo solo
    para saber si responde. Se usa stream=True para no descargar un MJPEG
    entero, que es una respuesta infinita.
    """
    respuesta = None
    try:
        respuesta = requests.get(source, stream=True, timeout=(3, PROBE_TIMEOUT))
        if respuesta.status_code >= 400:
            return False
        # Que responda con cabeceras no basta: algunos equipos aceptan la
        # conexión y no envían nada. Se exige al menos un fragmento de datos.
        for fragmento in respuesta.iter_content(chunk_size=1024):
            return bool(fragmento)
        return False
    except requests.RequestException:
        return False
    finally:
        if respuesta is not None:
            respuesta.close()


def _probe_usb(indice: int) -> bool:
    """Comprueba una cámara USB local abriéndola y leyendo un fotograma."""
    cap = None
    try:
        cap = cv2.VideoCapture(indice)
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None)
    except Exception:
        return False
    finally:
        if cap is not None:
            cap.release()


def _probe_tcp(source: str) -> bool:
    """
    Comprueba una fuente de red abriendo una conexión TCP a su host y puerto.

    Para RTSP no se usa OpenCV: cv2.VideoCapture(url) conecta dentro del propio
    constructor, así que CAP_PROP_OPEN_TIMEOUT_MSEC se aplica cuando ya es tarde
    y un host inalcanzable bloquea el hilo unos 30 segundos hasta que salta el
    tiempo límite interno de FFmpeg.

    La conexión TCP sí respeta el tiempo límite y basta para el indicador: si el
    equipo acepta conexiones en el puerto de vídeo, está en servicio. No se
    negocia la sesión RTSP completa, que sería mucho más lenta y no aporta a
    efectos de "responde o no responde".
    """
    try:
        partes = urlsplit(source)
        host = partes.hostname
        if not host:
            return False
        puerto = partes.port or PUERTOS_POR_ESQUEMA.get(partes.scheme, 554)
        with socket.create_connection((host, puerto), timeout=PROBE_TIMEOUT):
            return True
    except (OSError, ValueError):
        return False


def probe_source(source) -> bool:
    """Determina si una fuente de vídeo responde ahora mismo."""
    if source is None or source == "":
        return False

    texto = str(source).strip()
    if texto.isdigit():                       # Cámara USB por índice
        return _probe_usb(int(texto))
    if texto.startswith(("http://", "https://")):
        return _probe_http(texto)
    if "://" in texto:                        # rtsp://, rtmp// y demás
        return _probe_tcp(texto)
    return os.path.exists(texto)              # Archivo de vídeo local


class CameraHealthMonitor:
    """
    Mantiene el estado de cada cámara y lo refresca en segundo plano.

    El estado se guarda en memoria: al reiniciar el servidor todas las cámaras
    vuelven a "unknown" hasta la primera ronda, que es lo correcto — no hay nada
    que garantice que sigan igual que antes del reinicio.
    """

    def __init__(self):
        # {cam_id: {"status", "checked_at", "latency_ms", "name"}}
        self._estado = {}
        # {cam_id: marca de tiempo del último fotograma servido}
        self._vivas = {}
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self._despertar = threading.Event()

    # -- Interfaz pública ---------------------------------------------------

    def start(self):
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def stop(self):
        self._parar.set()
        self._despertar.set()

    def report_alive(self, cam_id):
        """
        Marca una cámara como viva porque acaba de servir un fotograma.

        Lo llama el generador de vídeo. Evita sondear cámaras que ya están
        emitiendo y hace que el indicador reaccione al instante al arrastrar
        una cámara a la cuadrícula.
        """
        if not cam_id:
            return
        ahora = time.time()
        with self._lock:
            self._vivas[cam_id] = ahora
            entrada = self._estado.setdefault(cam_id, {})
            entrada["status"] = STATUS_ONLINE
            entrada["checked_at"] = ahora

    def get_status(self, cameras):
        """
        Estado de todas las cámaras indicadas, listo para enviar al navegador.

        Args:
            cameras: lista de dicts de cámara tal como están en config.json
        """
        ahora = time.time()
        with self._lock:
            resultado = {}
            for cam in cameras:
                cam_id = cam.get("id")
                entrada = self._estado.get(cam_id, {})
                visto = self._vivas.get(cam_id, 0)

                # Emitiendo ahora mismo: viva con independencia del último sondeo
                if ahora - visto < ALIVE_GRACE:
                    estado = STATUS_ONLINE
                else:
                    estado = entrada.get("status", STATUS_UNKNOWN)

                comprobado = entrada.get("checked_at")
                resultado[cam_id] = {
                    "status": estado,
                    "latency_ms": entrada.get("latency_ms"),
                    "checked_seconds_ago": (int(ahora - comprobado)
                                            if comprobado else None),
                    "streaming": ahora - visto < ALIVE_GRACE,
                }
            return resultado

    def refresh_soon(self):
        """
        Fuerza una ronda inmediata.

        Se llama al añadir o editar una cámara, para no dejar el indicador en
        "sin comprobar" hasta la siguiente ronda periódica.
        """
        self._despertar.set()

    def forget(self, cam_id):
        with self._lock:
            self._estado.pop(cam_id, None)
            self._vivas.pop(cam_id, None)

    # -- Interior -----------------------------------------------------------

    def _bucle(self):
        while not self._parar.is_set():
            try:
                self._ronda()
            except Exception as e:
                # Nunca dejar morir el hilo por un fallo puntual: sin él, todos
                # los indicadores se quedarían congelados para siempre.
                print(f"[CameraHealth] Error en la ronda de comprobación: {e}")

            # Espera interrumpible: refresh_soon() la corta al instante
            self._despertar.wait(timeout=CHECK_INTERVAL)
            self._despertar.clear()

    def _ronda(self):
        from config_manager import load_config

        cameras = load_config().get("cameras", [])
        if not cameras:
            return

        ahora = time.time()
        with self._lock:
            pendientes = [c for c in cameras
                          if ahora - self._vivas.get(c.get("id"), 0) >= ALIVE_GRACE]
            # Limpiar cámaras que ya no están en la configuración
            ids_actuales = {c.get("id") for c in cameras}
            for cam_id in list(self._estado):
                if cam_id not in ids_actuales:
                    self._estado.pop(cam_id, None)
                    self._vivas.pop(cam_id, None)

        if not pendientes:
            return

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_PROBES) as pool:
            list(pool.map(self._comprobar_una, pendientes))

    def _comprobar_una(self, cam):
        cam_id = cam.get("id")
        inicio = time.time()
        try:
            viva = probe_source(cam.get("source"))
        except Exception:
            viva = False
        transcurrido = int((time.time() - inicio) * 1000)

        with self._lock:
            self._estado[cam_id] = {
                "status": STATUS_ONLINE if viva else STATUS_OFFLINE,
                "checked_at": time.time(),
                "latency_ms": transcurrido if viva else None,
            }


# Instancia global, igual que background_manager
health_monitor = CameraHealthMonitor()
