import os
import urllib.request

def download_file(url, filename):
    print(f"Downloading {filename} from {url}...")
    try:
        urllib.request.urlretrieve(url, filename)
        print(f"Successfully downloaded {filename}.")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")

if __name__ == "__main__":
    model_path = "license_plate_detector.pt"
    # Fallback to a known repository containing a pre-trained yolov8 license plate detector
    url = "https://github.com/Muhammad-Zeerak-Khan/Automatic-License-Plate-Recognition-using-YOLOv8/raw/main/license_plate_detector.pt"
    
    if not os.path.exists(model_path):
        download_file(url, model_path)
    else:
        print(f"{model_path} already exists.")
