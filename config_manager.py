import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Migrate old "streams" format to "cameras"
                if "streams" in data:
                    data["cameras"] = []
                    for i, s in enumerate(data.pop("streams")):
                        data["cameras"].append({
                            "id": f"cam_migrated_{i}",
                            "name": f"Camera {i+1}",
                            "type": "IP" if "://" in str(s) or "." in str(s) else "USB",
                            "source": s
                        })
                    save_config(data)
                return data
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"cameras": []}
    return {"cameras": []}

def save_config(config):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_display_settings():
    config = load_config()
    defaults = {"show_fps": True, "show_labels": True, "show_speed": True}
    stored = config.get("display_settings", {})
    return {**defaults, **stored}

def save_display_settings(settings):
    config = load_config()
    config["display_settings"] = settings
    save_config(config)

def get_detection_mode():
    config = load_config()
    return config.get("detection_mode", "monitored")

def save_detection_mode(mode):
    if mode not in ["monitored", "all"]:
        mode = "monitored"
    config = load_config()
    config["detection_mode"] = mode
    save_config(config)

