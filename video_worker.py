import time
import math
import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from ultralytics import YOLO
import supervision as sv
from alpr_engine import process_plate_image
from fr_engine import process_person_image

class VideoWorker(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)
    error_signal = pyqtSignal(str)

    def __init__(self, stream_source, model_path="yolov8n.pt"):
        super().__init__()
        self._run_flag = True
        self.stream_source = stream_source
        self.model = YOLO(model_path)
        self.tracker = sv.ByteTrack()
        self.box_annotator = sv.BoxAnnotator(thickness=1)
        self.label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)
        self.fps_monitor = sv.FPSMonitor()
        self.track_history = {}
        self.alpr_scanned_ids = {} # To avoid scanning the same vehicle multiple times
        self.fr_scanned_ids = {} # To avoid scanning the same person multiple times
        self.PIXELS_PER_METER = 20.0
        self.frame_count = 0

    def run(self):
        # Convert digit strings to integers for local cameras
        if isinstance(self.stream_source, str) and self.stream_source.isdigit():
            self.stream_source = int(self.stream_source)

        cap = None
        if isinstance(self.stream_source, str) and not self.stream_source.startswith(('rtsp://', 'http://', 'https://')):
            # Try to auto-detect protocol if it's just an IP or IP:PORT
            test_streams = [f"http://{self.stream_source}", f"http://{self.stream_source}/video", f"rtsp://{self.stream_source}", f"rtsp://{self.stream_source}/video"]
            for ts in test_streams:
                cap = cv2.VideoCapture(ts)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if cap.isOpened():
                    self.stream_source = ts
                    break
                cap.release()
                cap = None
        else:
            cap = cv2.VideoCapture(self.stream_source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if cap is None or not cap.isOpened():
            self.error_signal.emit(f"Failed to open stream: {self.stream_source}")
            return

        while self._run_flag:
            ret, frame = cap.read()
            if not ret:
                # Mjpeg or RTSP streams can drop frames or disconnect momentarily. 
                # Reconnect instead of stopping the thread.
                cap.release()
                cap = cv2.VideoCapture(self.stream_source)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue

            self.fps_monitor.tick()
            self.frame_count += 1

            # Inference (imgsz=480 reduces the resolution specifically for the neural network, making it much faster on CPU)
            results = self.model(frame, verbose=False, imgsz=480)[0]
            
            # Convert to Supervision Detections
            detections = sv.Detections.from_ultralytics(results)
            
            # Track
            detections = self.tracker.update_with_detections(detections)

            # Annotate
            labels = [
                f"#{tracker_id} {self.model.model.names[class_id]} {confidence:.2f}"
                for class_id, confidence, tracker_id
                in zip(detections.class_id, detections.confidence, detections.tracker_id)
            ]
            
            annotated_frame = self.box_annotator.annotate(scene=frame.copy(), detections=detections)
            annotated_frame = self.label_annotator.annotate(
                scene=annotated_frame, detections=detections, labels=labels
            )

            try:
                current_time = time.time()
                if detections.tracker_id is not None:
                    for xyxy, tracker_id, class_id in zip(detections.xyxy, detections.tracker_id, detections.class_id):
                        if tracker_id is None:
                            continue
                        
                        x_center = (xyxy[0] + xyxy[2]) / 2
                        y_center = (xyxy[1] + xyxy[3]) / 2

                        if tracker_id in self.track_history:
                            prev_x, prev_y, prev_time = self.track_history[tracker_id]
                            distance_px = math.hypot(x_center - prev_x, y_center - prev_y)
                            time_diff = current_time - prev_time
                            
                            if time_diff > 0:
                                speed_kmh = (distance_px / time_diff / self.PIXELS_PER_METER) * 3.6
                                
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
                        
                        self.track_history[tracker_id] = (x_center, y_center, current_time)

                        # ALPR Logic: classes 2=car, 5=bus, 7=truck
                        if class_id in [2, 5, 7]:
                            last_scan_time = self.alpr_scanned_ids.get(tracker_id, 0)
                            if current_time - last_scan_time > 2.0:
                                x1, y1, x2, y2 = map(int, xyxy)
                                pad = 10
                                y1 = max(0, y1 - pad)
                                y2 = min(frame.shape[0], y2 + pad)
                                x1 = max(0, x1 - pad)
                                x2 = min(frame.shape[1], x2 + pad)
                                
                                if (y2 - y1) > 20 and (x2 - x1) > 20:
                                    crop = frame[y1:y2, x1:x2]
                                    plates = process_plate_image(crop, str(self.stream_source))
                                    if plates:
                                        self.alpr_scanned_ids[tracker_id] = current_time + 3.0
                                    else:
                                        self.alpr_scanned_ids[tracker_id] = current_time
                                        
                        # FR Logic: class 0=person
                        if class_id == 0:
                            last_scan_time = self.fr_scanned_ids.get(tracker_id, 0)
                            if current_time - last_scan_time > 3.0:
                                x1, y1, x2, y2 = map(int, xyxy)
                                pad = 10
                                y1 = max(0, y1 - pad)
                                y2 = min(frame.shape[0], y2 + pad)
                                x1 = max(0, x1 - pad)
                                x2 = min(frame.shape[1], x2 + pad)
                                
                                if (y2 - y1) > 20 and (x2 - x1) > 20:
                                    crop = frame[y1:y2, x1:x2]
                                    import cv2
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    faces = process_person_image(crop_rgb, str(self.stream_source))
                                    if faces:
                                        self.fr_scanned_ids[tracker_id] = current_time + 8.0
                                    else:
                                        self.fr_scanned_ids[tracker_id] = current_time


                if self.frame_count % 100 == 0:
                    active_ids = set(detections.tracker_id) if detections.tracker_id is not None else set()
                    for tid in list(self.track_history.keys()):
                        if tid not in active_ids:
                            del self.track_history[tid]
                    for tid in list(self.alpr_scanned_ids.keys()):
                        if tid not in active_ids:
                            del self.alpr_scanned_ids[tid]
                    for tid in list(self.fr_scanned_ids.keys()):
                        if tid not in active_ids:
                            del self.fr_scanned_ids[tid]
            except Exception as e:
                print(f"Speed annotation error: {e}")

            # Add FPS Label
            fps = self.fps_monitor.fps
            h, w = annotated_frame.shape[:2]
            font_scale = max(0.5, h / 1000.0)
            thickness = max(1, int(font_scale * 2))
            cv2.putText(
                annotated_frame, 
                f"FPS: {fps:.1f}", 
                (20, int(40 * max(1, font_scale))), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                font_scale, 
                (0, 255, 255), 
                thickness, 
                cv2.LINE_AA
            )

            # Emit the annotated frame
            self.change_pixmap_signal.emit(annotated_frame)

        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()
