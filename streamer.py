import time
import math
import cv2
import numpy as np
import supervision as sv
from model_cache import get_model
from config_manager import get_display_settings
from analysis_queue import analysis_queue
from fr_engine import has_known_faces
from frame_source import FrameGrabber
from label_mapper import get_label_es, ALLOWED_CLASS_IDS
from tracking_utils import (TrackClassVoter, TrackConfidenceGate,
                            TrackIdStabilizer, disambiguate_class,
                            render_ghost_tracks, get_track_id)

def generate_frames(stream_source, model_path="yolov8n.pt", cam_id=None):
    from background_processor import background_manager
    # Instancia compartida: antes cada conexión HTTP creaba su propio YOLO,
    # así que la cuadrícula de 8 cámaras mantenía 8 copias del mismo modelo.
    model = get_model(model_path)

    # --- Parámetros de ByteTrack ---
    #
    # lost_track_buffer: fotogramas que se conserva un track perdido antes de
    #   descartarlo. 60 evita que los recuadros desaparezcan en movimientos
    #   rápidos u oclusiones breves.
    #
    # track_activation_threshold: confianza mínima para ACTIVAR un track nuevo.
    #
    # minimum_matching_threshold: OJO, no es un IoU mínimo pese a lo que
    #   sugiere el nombre. supervision construye la matriz de coste como
    #   1 - IoU y compara contra este valor, así que 0.85 significa exigir
    #   IoU >= 0.15. Cuanto mayor el número, más permisiva la asociación.
    #
    #   Medido sobre 90 fotogramas de una cámara real: el 25% de los objetos
    #   tiene un solapamiento inferior a 0.30 entre fotogramas consecutivos, de
    #   modo que con el 0.70 anterior una de cada cuatro veces el seguidor no
    #   lograba asociar el vehículo y le asignaba un identificador nuevo. Es la
    #   causa de que un auto pase de #99 a #111 al avanzar por la imagen.
    #
    #   Pasar a 0.85 reduce a la mitad los identificadores creados sobre el
    #   mismo metraje, sin aumentar las asociaciones erróneas: los saltos
    #   bruscos de posición se mantienen en 3 de unas 500 detecciones.
    #
    # frame_rate: se comprobó que ajustarlo al valor real no cambia nada
    #   apreciable (+2% de identificadores), así que se deja como estaba.
    tracker = sv.ByteTrack(
        track_activation_threshold=0.20,
        lost_track_buffer=60,
        minimum_matching_threshold=0.85,
        frame_rate=25,
    )

    # Votación temporal de clase por track para estabilizar etiquetas oscilantes
    class_voter = TrackClassVoter(window=12)

    # Histéresis de confianza: evita que el recuadro parpadee cuando la confianza
    # cae momentáneamente por una oclusión parcial (semáforo colgante, cable, poste)
    conf_gate = TrackConfidenceGate(confirm_frames=2, grace_frames=5)

    # Devuelve su identificador original a un vehículo que el seguidor dio
    # por nuevo tras una oclusión o un salto grande entre fotogramas.
    id_stabilizer = TrackIdStabilizer()

    # Historial de último frame visto y última clase por track_id
    # Usado por render_ghost_tracks para dibujar posiciones predichas en oclusión
    track_last_seen  = {}   # {tid: frame_count}
    track_last_class = {}   # {tid: class_id}

    box_annotator   = sv.BoxAnnotator(thickness=1)
    label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)
    fps_monitor     = sv.FPSMonitor()
    track_history   = {}
    alpr_scanned_ids = {}
    fr_scanned_ids   = {}
    PIXELS_PER_METER = 20.0
    frame_count = 0
    display = get_display_settings()

    # La lectura va en su propio hilo: cap.read() tarda entre 1 y 138 ms según
    # la cámara, y hacerla aquí dejaba el acelerador parado toda esa espera.
    # El try/finally garantiza que la fuente se libera al desconectarse el
    # cliente; antes quedaba abierta indefinidamente.
    grabber = FrameGrabber(stream_source).start()
    try:
        yield from _bucle_de_video(
            grabber, model, tracker, class_voter, conf_gate, id_stabilizer,
            track_last_seen, track_last_class, box_annotator, label_annotator,
            fps_monitor, track_history, alpr_scanned_ids, fr_scanned_ids,
            PIXELS_PER_METER, display, cam_id, stream_source,
        )
    finally:
        grabber.stop()


def _bucle_de_video(grabber, model, tracker, class_voter, conf_gate,
                    id_stabilizer,
                    track_last_seen, track_last_class, box_annotator,
                    label_annotator, fps_monitor, track_history,
                    alpr_scanned_ids, fr_scanned_ids, PIXELS_PER_METER,
                    display, cam_id, stream_source):
    """Bucle de anotación y emisión. Separado para poder cerrar el lector."""
    from background_processor import background_manager
    frame_count = 0

    while True:
        frame = grabber.read(timeout=10.0)
        if frame is None:
            # Sin fotograma nuevo: puede ser un corte pasajero. El lector
            # reintenta por su cuenta, así que basta con volver a esperar.
            continue

        fps_monitor.tick()
        frame_count += 1

        # Re-read display settings every 30 frames to pick up changes
        if frame_count % 30 == 1:
            display = get_display_settings()

        # La resolución la decide model_cache según el dispositivo: 640 en
        # GPU, donde sale casi gratis, y 480 en CPU.
        # classes filtra dentro de la inferencia: descarta semáforos, tazas
        # y demás antes de generar cajas, en vez de tirarlas después.
        results = model.predict(frame, verbose=False, classes=ALLOWED_CLASS_IDS)
        
        detections = sv.Detections.from_ultralytics(results)
        detections = tracker.update_with_detections(detections)

        # Recuperar identificadores antes de cualquier paso que los use: la
        # votación de clase, la histéresis y el control de reescaneo de ALPR
        # y rostros dependen todos de que el identificador sea estable.
        if len(detections) > 0 and detections.tracker_id is not None:
            detections.tracker_id = np.array(
                id_stabilizer.apply(detections.tracker_id, detections.xyxy,
                                    detections.class_id, frame_count),
                dtype=np.int64)

        # --- Desambiguación geométrica + votación temporal de clase ---
        # Corrige confusiones frecuentes por tamaño y proporción (camión↔auto,
        # tren↔camión, persona↔moto) y estabiliza la clase mostrada votando los
        # últimos fotogramas.
        #
        # IMPRESCINDIBLE que esto vaya ANTES del filtro de confianza. Al revés,
        # el umbral se aplicaba sobre la clase cruda: un tractocamión que YOLO
        # etiqueta como "tren" con 0.79 de confianza se descartaba por no llegar
        # al 0.85 que se exige a los trenes, y la regla que lo habría convertido
        # en camión —umbral 0.35, que sí superaba— nunca llegaba a ejecutarse.
        if len(detections) > 0 and detections.tracker_id is not None:
            corrected_class_ids = []
            discard_mask = []
            for cls, xyxy, tid in zip(detections.class_id, detections.xyxy, detections.tracker_id):
                new_cls = disambiguate_class(int(cls), xyxy, frame.shape)
                if new_cls == -1:                   # Señal de "descartar"
                    discard_mask.append(False)
                    corrected_class_ids.append(int(cls))  # placeholder, se filtrará
                else:
                    voted_cls = class_voter.update_and_vote(int(tid), new_cls)
                    corrected_class_ids.append(voted_cls)
                    discard_mask.append(True)

            discard_mask = np.array(discard_mask, dtype=bool)
            detections = detections[discard_mask]
            # Reemplazar class_id con las clases estabilizadas (solo las que se mantienen)
            if len(detections) > 0:
                detections.class_id = np.array(
                    [cid for cid, keep in zip(corrected_class_ids, discard_mask) if keep],
                    dtype=np.int64
                )

        # --- Filtro de confianza por clase con histéresis ---
        # Un track nuevo debe superar el umbral estricto de su clase para
        # aparecer; una vez confirmado, se mantiene visible con un umbral mucho
        # más bajo. Esto impide que el recuadro desaparezca cuando un vehículo
        # pasa bajo un semáforo colgante y su confianza cae unas décimas.
        #
        # Se evalúa sobre la clase ya corregida, que es la que se va a mostrar.
        if detections.tracker_id is not None and len(detections) > 0:
            keep_mask = np.array([
                conf_gate.accept(int(tid), int(cls), float(conf))
                for tid, cls, conf in zip(detections.tracker_id,
                                          detections.class_id,
                                          detections.confidence)
            ], dtype=bool)
            detections = detections[keep_mask]

        if display.get("show_labels", True):
            labels = [
                f"#{tracker_id} {get_label_es(class_id, model.names)} {confidence:.2f}"
                for class_id, confidence, tracker_id
                in zip(detections.class_id, detections.confidence, detections.tracker_id)
            ]
            annotated_frame = box_annotator.annotate(scene=frame.copy(), detections=detections)
            annotated_frame = label_annotator.annotate(
                scene=annotated_frame, detections=detections, labels=labels
            )
        else:
            annotated_frame = frame.copy()

        # --- Actualizar historial de último frame visto y clase por track ---
        if detections.tracker_id is not None and len(detections) > 0:
            for tid, cls in zip(detections.tracker_id, detections.class_id):
                track_last_seen[int(tid)]  = frame_count
                track_last_class[int(tid)] = int(cls)

        # --- Ghost boxes: posiciones predichas (Kalman) de tracks ocluidos ---
        # Se renderizan con borde discontinuo naranja y relleno semitransparente.
        # Solo se muestran tracks perdidos recientemente (hasta GHOST_MAX_FRAMES).
        # Se pasan las cajas ya dibujadas para que no se superponga un fantasma
        # sobre un objeto que el seguidor ya está siguiendo con otro id.
        #
        # Desactivado por defecto: es una ayuda de diagnóstico. Ocultarlo no
        # afecta al seguimiento durante una oclusión, que depende del
        # lost_track_buffer de ByteTrack y de la histéresis de confianza, no de
        # que se dibuje nada.
        if display.get("show_ghost_boxes", False):
            render_ghost_tracks(
                annotated_frame, tracker, frame_count,
                track_last_seen, track_last_class,
                get_label_fn=get_label_es,
                active_boxes=detections.xyxy if len(detections) > 0 else None,
            )

        # Check if background processor is already executing ALPR/FR for this camera
        is_bg_active = cam_id and background_manager.is_camera_running_in_background(cam_id)

        try:
            current_time = time.time()
            if display.get("show_speed", True) and detections.tracker_id is not None:
                for xyxy, tracker_id, class_id in zip(detections.xyxy, detections.tracker_id, detections.class_id):
                    if tracker_id is None:
                        continue

                    x_center = (xyxy[0] + xyxy[2]) / 2
                    y_center = (xyxy[1] + xyxy[3]) / 2

                    if tracker_id in track_history:
                        prev_x, prev_y, prev_time = track_history[tracker_id]
                        distance_px = math.hypot(x_center - prev_x, y_center - prev_y)
                        time_diff = current_time - prev_time

                        if time_diff > 0:
                            speed_kmh = (distance_px / time_diff / PIXELS_PER_METER) * 3.6

                            h_frame = annotated_frame.shape[0]
                            f_scale = max(0.4, h_frame / 1500.0)
                            t_thick = max(1, int(f_scale * 2))

                            text = f"{speed_kmh:.1f} km/h"
                            (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, f_scale, t_thick)

                            box_bottom_y = int(xyxy[3])
                            text_x = int(x_center - text_w / 2)
                            text_y = box_bottom_y + text_h + 5

                            text_y = min(text_y, h_frame - 5)
                            text_x = max(5, min(text_x, annotated_frame.shape[1] - text_w - 5))

                            cv2.rectangle(annotated_frame, (text_x - 3, text_y - text_h - 3),
                                          (text_x + text_w + 3, text_y + 3), (0, 0, 0), -1)
                            cv2.putText(annotated_frame, text, (text_x, text_y),
                                        cv2.FONT_HERSHEY_SIMPLEX, f_scale, (0, 255, 0), t_thick, cv2.LINE_AA)

                    track_history[tracker_id] = (x_center, y_center, current_time)

                    # Only run local ALPR & FR if background processor is NOT actively running for this camera
                    if not is_bg_active:
                        # ALPR Logic: classes 2=car, 5=bus, 7=truck (COCO dataset)
                        if class_id in [2, 5, 7]:
                            last_scan_time = alpr_scanned_ids.get(tracker_id, 0)
                            if current_time - last_scan_time > 2.0:
                                x1, y1, x2, y2 = map(int, xyxy)
                                pad = 10
                                y1 = max(0, y1 - pad)
                                y2 = min(frame.shape[0], y2 + pad)
                                x1 = max(0, x1 - pad)
                                x2 = min(frame.shape[1], x2 + pad)
                                
                                if (y2 - y1) > 20 and (x2 - x1) > 20:
                                    # .copy() es imprescindible: el recorte se
                                    # analiza en otro hilo y el fotograma
                                    # original se reutiliza mientras tanto.
                                    crop = frame[y1:y2, x1:x2].copy()
                                    target_cam_tag = str(cam_id) if cam_id else str(stream_source)
                                    analysis_queue.submit_plate(crop, target_cam_tag)
                                    # Ya no se sabe aquí si se leyó algo, así que
                                    # se aplica siempre la misma espera antes de
                                    # reintentar sobre este mismo vehículo.
                                    alpr_scanned_ids[tracker_id] = current_time

                        # FR Logic: class 0=person.
                        # Sin rostros registrados no hay contra qué comparar,
                        # así que se omite el análisis por completo y la cámara
                        # sigue leyendo matrículas con normalidad.
                        if class_id == 0 and has_known_faces():
                            last_scan_time = fr_scanned_ids.get(tracker_id, 0)
                            if current_time - last_scan_time > 3.0:
                                x1, y1, x2, y2 = map(int, xyxy)
                                pad = 10
                                y1 = max(0, y1 - pad)
                                y2 = min(frame.shape[0], y2 + pad)
                                x1 = max(0, x1 - pad)
                                x2 = min(frame.shape[1], x2 + pad)
                                
                                if (y2 - y1) > 20 and (x2 - x1) > 20:
                                    # cvtColor ya devuelve un array propio, así
                                    # que no hace falta copiar de nuevo.
                                    crop_rgb = cv2.cvtColor(frame[y1:y2, x1:x2],
                                                            cv2.COLOR_BGR2RGB)
                                    target_cam_tag = str(cam_id) if cam_id else str(stream_source)
                                    analysis_queue.submit_face(crop_rgb, target_cam_tag)
                                    fr_scanned_ids[tracker_id] = current_time


            # Always update track history even when speed is hidden (for when it's re-enabled)
            if not display.get("show_speed", True) and detections.tracker_id is not None:
                current_time = time.time()
                for xyxy, tracker_id in zip(detections.xyxy, detections.tracker_id):
                    if tracker_id is not None:
                        x_center = (xyxy[0] + xyxy[2]) / 2
                        y_center = (xyxy[1] + xyxy[3]) / 2
                        track_history[tracker_id] = (x_center, y_center, current_time)

            # Cleanup old history every 100 frames
            if frame_count % 100 == 0:
                active_ids = set(detections.tracker_id) if detections.tracker_id is not None else set()
                for tid in list(track_history.keys()):
                    if tid not in active_ids:
                        del track_history[tid]
                for tid in list(alpr_scanned_ids.keys()):
                    if tid not in active_ids:
                        del alpr_scanned_ids[tid]
                for tid in list(fr_scanned_ids.keys()):
                    if tid not in active_ids:
                        del fr_scanned_ids[tid]
                # Purgar historial de ghost tracking (solo ids ya no activos NI perdidos)
                lost_ids = {tid for tid in
                            (get_track_id(t) for t in getattr(tracker, 'lost_tracks', []))
                            if tid is not None}
                ghost_keep = active_ids | lost_ids
                for tid in list(track_last_seen.keys()):
                    if tid not in ghost_keep:
                        track_last_seen.pop(tid, None)
                        track_last_class.pop(tid, None)
                # Purgar con ghost_keep (activos + perdidos), no solo con los
                # activos: un track ocluido sigue vivo en el tracker y debe
                # conservar su voto de clase y su estado de confirmación para
                # reaparecer sin parpadear ni cambiar de etiqueta.
                class_voter.purge(ghost_keep)
                conf_gate.purge(ghost_keep)
        except Exception as e:
            print(f"Speed annotation error: {e}")


        if display.get("show_fps", True):
            fps = fps_monitor.fps
            h, w = annotated_frame.shape[:2]
            font_scale = max(0.5, h / 1000.0)
            thickness = max(1, int(font_scale * 2))
            cv2.putText(annotated_frame, f"FPS: {fps:.1f}", (20, int(40 * max(1, font_scale))), 
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), thickness, cv2.LINE_AA)

        # Encode frame as JPEG
        ret, buffer = cv2.imencode('.jpg', annotated_frame)
        frame_bytes = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
