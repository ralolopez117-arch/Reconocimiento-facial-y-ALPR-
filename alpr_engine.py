import easyocr
import re
from database import insert_plate, check_plate_against_watchlist, insert_plate_alert
from ultralytics import YOLO
import os

# Initialize models
print("Loading ALPR Engine (Dual-Model)...")
plate_model_path = os.path.join(os.path.dirname(__file__), "license_plate_detector.pt")
plate_model = YOLO(plate_model_path) if os.path.exists(plate_model_path) else None

reader = easyocr.Reader(['en'], gpu=True) # It will fallback to CPU if no GPU
print("ALPR Engine loaded.")

def clean_plate_text(text):
    """
    Cleans the detected text. Keeps alphanumeric and uppercase.
    """
    cleaned = re.sub(r'[^A-Za-z0-9]', '', text)
    return cleaned.upper()

def process_plate_image(vehicle_crop, camera_id, confidence_threshold=0.5):
    """
    Process a cropped image of a vehicle.
    1. Uses YOLO to find the plate inside the vehicle.
    2. Crops the plate.
    3. Uses EasyOCR on the tiny plate crop.
    """
    try:
        detected_plates = []
        
        # Step 1: Detect Plate using YOLO
        if plate_model:
            results = plate_model(vehicle_crop, verbose=False)[0]
            if len(results.boxes) == 0:
                return [] # No plate detected by YOLO
            
            # Find the best plate bounding box
            best_box = max(results.boxes, key=lambda x: x.conf[0])
            x1, y1, x2, y2 = map(int, best_box.xyxy[0])
            
            # Add small padding to the plate crop
            pad = 5
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(vehicle_crop.shape[1], x2 + pad)
            y2 = min(vehicle_crop.shape[0], y2 + pad)
            
            plate_crop = vehicle_crop[y1:y2, x1:x2]
        else:
            # Fallback if no model (run OCR directly on vehicle_crop)
            plate_crop = vehicle_crop
            
        # Ensure crop is valid
        if plate_crop.size == 0 or plate_crop.shape[0] < 5 or plate_crop.shape[1] < 5:
            return []

        # Step 2: Read text with EasyOCR
        ocr_results = reader.readtext(plate_crop)
        
        for (bbox, text, prob) in ocr_results:
            cleaned_text = clean_plate_text(text)
            
            if len(cleaned_text) >= 4 and prob >= confidence_threshold:
                insert_plate(camera_id, cleaned_text, prob)
                detected_plates.append((cleaned_text, prob))
                
                # Check watchlist — trigger alert if matched
                match = check_plate_against_watchlist(cleaned_text)
                if match:
                    insert_plate_alert(camera_id, cleaned_text, match['plate_pattern'], prob)
                    print(f"[PLATE ALERT] Placa vigilada detectada: {cleaned_text} | Cámara: {camera_id}")
                
        return detected_plates
    except Exception as e:
        print(f"ALPR Error: {e}")
        return []

