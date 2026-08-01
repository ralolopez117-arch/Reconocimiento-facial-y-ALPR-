import time
import threading
import cv2
import supervision as sv
from ultralytics import YOLO

from config_manager import load_config, get_detection_mode
from alpr_engine import process_plate_image
from fr_engine import process_person_image

class CameraWorker(threading.Thread):
    def __init__(self, camera_info, model_path="yolov8n.pt"):
        super().__init__(daemon=True)
        self.camera_info = camera_info
        self.cam_id = camera_info.get("id")
        self.source = camera_info.get("source")
        self.model_path = model_path
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        print(f"[BackgroundProcessor] Worker started for camera: {self.camera_info.get('name', self.cam_id)}")
        
        try:
            model = YOLO(self.model_path)
            tracker = sv.ByteTrack()
        except Exception as e:
            print(f"[BackgroundProcessor] Failed to load YOLO/ByteTrack for camera {self.cam_id}: {e}")
            return

        stream_source = self.source
        if isinstance(stream_source, str) and stream_source.isdigit():
            stream_source = int(stream_source)

        cap = None
        if isinstance(stream_source, str) and not stream_source.startswith(('rtsp://', 'http://', 'https://')):
            test_streams = [
                f"http://{stream_source}",
                f"http://{stream_source}/video",
                f"rtsp://{stream_source}",
                f"rtsp://{stream_source}/video"
            ]
            for ts in test_streams:
                cap = cv2.VideoCapture(ts)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if cap.isOpened():
                    stream_source = ts
                    break
                cap.release()
                cap = None
        else:
            cap = cv2.VideoCapture(stream_source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if cap is None or not cap.isOpened():
            print(f"[BackgroundProcessor] Could not open camera stream: {self.source}")
            return

        alpr_scanned_ids = {}
        fr_scanned_ids = {}
        frame_count = 0

        while not self._stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                if self._stop_event.is_set():
                    break
                cap.release()
                cap = cv2.VideoCapture(stream_source)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue

            frame_count += 1
            current_time = time.time()

            # Process every Nth frame to reduce load if necessary, e.g. every frame or every 2nd frame
            try:
                results = model(frame, verbose=False, imgsz=480)[0]
                detections = sv.Detections.from_ultralytics(results)
                detections = tracker.update_with_detections(detections)

                if detections.tracker_id is not None:
                    for xyxy, tracker_id, class_id in zip(detections.xyxy, detections.tracker_id, detections.class_id):
                        if tracker_id is None:
                            continue

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
                                    crop = frame[y1:y2, x1:x2]
                                    plates = process_plate_image(crop, str(self.cam_id))
                                    if plates:
                                        alpr_scanned_ids[tracker_id] = current_time + 3.0
                                    else:
                                        alpr_scanned_ids[tracker_id] = current_time

                        # FR Logic: class 0=person
                        if class_id == 0:
                            last_scan_time = fr_scanned_ids.get(tracker_id, 0)
                            if current_time - last_scan_time > 3.0:
                                x1, y1, x2, y2 = map(int, xyxy)
                                pad = 10
                                y1 = max(0, y1 - pad)
                                y2 = min(frame.shape[0], y2 + pad)
                                x1 = max(0, x1 - pad)
                                x2 = min(frame.shape[1], x2 + pad)

                                if (y2 - y1) > 20 and (x2 - x1) > 20:
                                    crop = frame[y1:y2, x1:x2]
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    faces = process_person_image(crop_rgb, str(self.cam_id))
                                    if faces:
                                        fr_scanned_ids[tracker_id] = current_time + 8.0
                                    else:
                                        fr_scanned_ids[tracker_id] = current_time

                # Cleanup tracking memory
                if frame_count % 100 == 0:
                    active_ids = set(detections.tracker_id) if detections.tracker_id is not None else set()
                    for tid in list(alpr_scanned_ids.keys()):
                        if tid not in active_ids:
                            del alpr_scanned_ids[tid]
                    for tid in list(fr_scanned_ids.keys()):
                        if tid not in active_ids:
                            del fr_scanned_ids[tid]

            except Exception as e:
                print(f"[BackgroundProcessor] Inference error on {self.cam_id}: {e}")

            # Sleep slightly to avoid maxing out CPU loop
            time.sleep(0.01)

        cap.release()
        print(f"[BackgroundProcessor] Worker stopped for camera: {self.cam_id}")


class BackgroundProcessorManager:
    def __init__(self):
        self.workers = {} # cam_id -> CameraWorker
        self.lock = threading.Lock()
        self.active_mode = "monitored"

    def start(self):
        self.sync_with_config()

    def sync_with_config(self):
        with self.lock:
            mode = get_detection_mode()
            self.active_mode = mode
            config = load_config()
            cameras = config.get("cameras", [])

            if mode == "monitored":
                # Stop all background workers when mode is "monitored"
                self._stop_all_workers()
            elif mode == "all":
                # Ensure a worker is running for every camera in config
                current_ids = {c["id"]: c for c in cameras}
                
                # Stop workers for cameras that no longer exist
                for cam_id, worker in list(self.workers.items()):
                    if cam_id not in current_ids:
                        worker.stop()
                        del self.workers[cam_id]

                # Start workers for cameras that are not yet running
                for cam_id, cam_info in current_ids.items():
                    if cam_id not in self.workers or not self.workers[cam_id].is_alive():
                        worker = CameraWorker(cam_info)
                        self.workers[cam_id] = worker
                        worker.start()

    def set_mode(self, mode):
        self.sync_with_config()

    def is_camera_running_in_background(self, cam_id):
        with self.lock:
            return self.active_mode == "all" and cam_id in self.workers and self.workers[cam_id].is_alive()

    def _stop_all_workers(self):
        for cam_id, worker in list(self.workers.items()):
            worker.stop()
        self.workers.clear()


# Global instance
background_manager = BackgroundProcessorManager()
