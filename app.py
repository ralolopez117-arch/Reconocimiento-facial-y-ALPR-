import uuid
from flask import Flask, render_template, Response, request, jsonify
from config_manager import (load_config, save_config, get_display_settings,
                            save_display_settings, get_detection_mode,
                            save_detection_mode, get_alpr_settings,
                            save_alpr_settings)
from plate_format import PLATE_PATTERNS, list_formats
from streamer import generate_frames
import database
from background_processor import background_manager
from ptz_control import get_ptz_controller

app = Flask(__name__)

# Start background processor manager
background_manager.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    config = load_config()
    return jsonify(config.get("cameras", []))

@app.route('/api/cameras', methods=['POST'])
def add_camera():
    data = request.json
    config = load_config()
    new_cam = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Unnamed"),
        "type": data.get("type", "IP"),
        "source": data.get("source", ""),
        "ip": data.get("ip", ""),
        "onvif_port": int(data.get("onvif_port", 80)) if data.get("onvif_port") else 80,
        "user": data.get("user", ""),
        "password": data.get("password", ""),
        "is_ptz": bool(data.get("is_ptz", False))
    }
    config.setdefault("cameras", []).append(new_cam)
    save_config(config)
    background_manager.sync_with_config()
    return jsonify(new_cam)

@app.route('/api/cameras/<cam_id>', methods=['PUT'])
def edit_camera(cam_id):
    data = request.json
    config = load_config()
    for cam in config.get("cameras", []):
        if cam["id"] == cam_id:
            cam["name"] = data.get("name", cam["name"])
            cam["type"] = data.get("type", cam["type"])
            cam["source"] = data.get("source", cam["source"])
            cam["ip"] = data.get("ip", cam.get("ip", ""))
            cam["onvif_port"] = int(data.get("onvif_port", cam.get("onvif_port", 80))) if data.get("onvif_port") is not None else 80
            cam["user"] = data.get("user", cam.get("user", ""))
            cam["password"] = data.get("password", cam.get("password", ""))
            cam["is_ptz"] = bool(data.get("is_ptz", cam.get("is_ptz", False)))
            break
    save_config(config)
    background_manager.sync_with_config()
    return jsonify({"status": "success"})

@app.route('/api/cameras/<cam_id>', methods=['DELETE'])
def delete_camera(cam_id):
    config = load_config()
    config["cameras"] = [c for c in config.get("cameras", []) if c["id"] != cam_id]
    save_config(config)
    background_manager.sync_with_config()
    return jsonify({"status": "success"})

@app.route('/api/display_settings', methods=['GET'])
def get_settings():
    return jsonify(get_display_settings())

@app.route('/api/display_settings', methods=['PUT'])
def update_settings():
    data = request.json
    current = get_display_settings()
    current.update({
        "show_fps": bool(data.get("show_fps", current["show_fps"])),
        "show_labels": bool(data.get("show_labels", current["show_labels"])),
        "show_speed": bool(data.get("show_speed", current["show_speed"])),
    })
    save_display_settings(current)
    return jsonify({"status": "success"})

@app.route('/api/detection_settings', methods=['GET'])
def get_detection_settings():
    return jsonify({"detection_mode": get_detection_mode()})

@app.route('/api/detection_settings', methods=['PUT'])
def update_detection_settings():
    data = request.json
    mode = data.get("detection_mode", "monitored")
    if mode in ["monitored", "all"]:
        save_detection_mode(mode)
        background_manager.set_mode(mode)
        return jsonify({"status": "success", "detection_mode": mode})
    return jsonify({"status": "error", "message": "Invalid detection mode"}), 400

@app.route('/api/alpr_settings', methods=['GET'])
def get_alpr_config():
    """Ajustes actuales del lector de matrículas y formatos disponibles."""
    return jsonify({
        **get_alpr_settings(),
        "available_formats": list_formats(),
    })

@app.route('/api/alpr_settings', methods=['PUT'])
def update_alpr_config():
    """
    Cambia el formato de matrícula que se exige al validar una lectura.

    Solo se aceptan claves conocidas: una expresión regular arbitraria enviada
    desde el navegador podría desactivar la validación por completo o provocar
    un coste de evaluación desmedido.
    """
    data = request.json or {}
    current = get_alpr_settings()

    plate_format = data.get("plate_format", current["plate_format"])
    if plate_format not in PLATE_PATTERNS:
        return jsonify({
            "status": "error",
            "message": f"Formato de placa no válido: {plate_format}",
        }), 400

    min_confidence = data.get("min_confidence", current["min_confidence"])
    try:
        min_confidence = float(min_confidence)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Confianza no numérica"}), 400
    if not 0.0 <= min_confidence <= 1.0:
        return jsonify({
            "status": "error",
            "message": "La confianza debe estar entre 0 y 1",
        }), 400

    save_alpr_settings({
        "plate_format": plate_format,
        "min_confidence": min_confidence,
    })
    return jsonify({"status": "success", "plate_format": plate_format,
                    "min_confidence": min_confidence})

# --- ONVIF PTZ REST Endpoints ---
def _find_camera_by_id(cam_id):
    config = load_config()
    for cam in config.get("cameras", []):
        if cam["id"] == cam_id:
            return cam
    return None

@app.route('/api/camera/<cam_id>/ptz/move', methods=['POST'])
def ptz_move(cam_id):
    cam = _find_camera_by_id(cam_id)
    if not cam:
        return jsonify({"status": "error", "message": "Cámara no encontrada"}), 404
    
    data = request.json or {}
    pan = float(data.get("pan", 0.0))
    tilt = float(data.get("tilt", 0.0))
    speed = float(data.get("speed", 1.0))

    ctrl = get_ptz_controller(cam)
    success, msg = ctrl.move(pan=pan * speed, tilt=tilt * speed, zoom=0.0)
    if success:
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 500

@app.route('/api/camera/<cam_id>/ptz', methods=['POST'])
def ptz_action(cam_id):
    cam = _find_camera_by_id(cam_id)
    if not cam:
        return jsonify({"status": "error", "message": "Cámara no encontrada"}), 404
    
    data = request.json or {}
    action = data.get("action", "").lower()
    speed = float(data.get("speed", 0.5))
    ctrl = get_ptz_controller(cam)

    if action == "zoom_in":
        success, msg = ctrl.move(pan=0.0, tilt=0.0, zoom=abs(speed))
    elif action == "zoom_out":
        success, msg = ctrl.move(pan=0.0, tilt=0.0, zoom=-abs(speed))
    elif action == "stop":
        success, msg = ctrl.stop()
    else:
        return jsonify({"status": "error", "message": "Acción PTZ no válida"}), 400

    if success:
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 500

@app.route('/api/camera/<cam_id>/ptz/stop', methods=['POST'])
def ptz_stop(cam_id):
    cam = _find_camera_by_id(cam_id)
    if not cam:
        return jsonify({"status": "error", "message": "Cámara no encontrada"}), 404
    
    ctrl = get_ptz_controller(cam)
    success, msg = ctrl.stop()
    if success:
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 500


@app.route('/api/plates/latest', methods=['GET'])
def get_latest_plates():
    limit = request.args.get('limit', 10, type=int)
    plates = database.get_latest_plates(limit)
    return jsonify(plates)

@app.route('/api/plates/search', methods=['GET'])
def search_plates():
    query = request.args.get('q', '')
    limit = request.args.get('limit', 50, type=int)
    plates = database.search_plates(query, limit)
    return jsonify(plates)

@app.route('/api/faces/register', methods=['POST'])
def register_face():
    if 'image' not in request.files:
        return jsonify({"status": "error", "message": "No image provided"}), 400
    
    name = request.form.get('name')
    if not name:
        return jsonify({"status": "error", "message": "No name provided"}), 400
        
    image_file = request.files['image']
    image_bytes = image_file.read()
    
    import fr_engine
    success, msg, face_id = fr_engine.register_face(image_bytes, name)
    if success:
        import os
        os.makedirs("static/faces", exist_ok=True)
        with open(f"static/faces/{face_id}.jpg", "wb") as f:
            f.write(image_bytes)
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/faces/latest', methods=['GET'])
def get_latest_faces():
    limit = request.args.get('limit', 10, type=int)
    faces = database.get_latest_face_detections(limit)
    return jsonify(faces)

@app.route('/api/faces/known', methods=['GET'])
def get_known_faces():
    faces = database.get_all_known_faces()
    # Don't send embeddings to the frontend (too large)
    return jsonify([{'id': f['id'], 'name': f['name']} for f in faces])

@app.route('/api/faces/known/<int:face_id>', methods=['PUT'])
def rename_face(face_id):
    new_name = request.form.get('name', '').strip()
    image_file = request.files.get('image')
    
    if not new_name:
        return jsonify({'status': 'error', 'message': 'Nombre vacío'}), 400
        
    if image_file:
        image_bytes = image_file.read()
        import fr_engine
        success, msg = fr_engine.update_face(face_id, image_bytes, new_name)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        import os
        os.makedirs("static/faces", exist_ok=True)
        with open(f"static/faces/{face_id}.jpg", "wb") as f:
            f.write(image_bytes)
    else:
        database.rename_known_face(face_id, new_name)
        
    return jsonify({'status': 'success'})

@app.route('/api/faces/known/<int:face_id>', methods=['DELETE'])
def delete_face(face_id):
    database.delete_known_face(face_id)
    import os
    try:
        os.remove(f"static/faces/{face_id}.jpg")
    except OSError:
        pass
    return jsonify({'status': 'success'})

@app.route('/api/faces/known', methods=['DELETE'])
def delete_all_faces():
    database.delete_all_known_faces()
    import os
    import glob
    for f in glob.glob("static/faces/*.jpg"):
        try:
            os.remove(f)
        except OSError:
            pass
    return jsonify({'status': 'success'})

# --- Watchlist API Endpoints ---
@app.route('/api/watched_plates', methods=['GET'])
def get_watched_plates():
    plates = database.get_all_watched_plates()
    return jsonify(plates)

@app.route('/api/watched_plates', methods=['POST'])
def add_watched_plate():
    data = request.json
    pattern = data.get('plate_pattern', '').strip()
    note = data.get('note', '').strip()
    if not pattern:
        return jsonify({'status': 'error', 'message': 'Patrón de placa vacío'}), 400
    database.insert_watched_plate(pattern, note)
    return jsonify({'status': 'success'})

@app.route('/api/watched_plates/<int:plate_id>', methods=['PUT'])
def update_watched_plate(plate_id):
    data = request.json
    pattern = data.get('plate_pattern', '').strip()
    note = data.get('note', '').strip()
    if not pattern:
        return jsonify({'status': 'error', 'message': 'Patrón de placa vacío'}), 400
    database.update_watched_plate(plate_id, pattern, note)
    return jsonify({'status': 'success'})

@app.route('/api/watched_plates/<int:plate_id>', methods=['DELETE'])
def delete_watched_plate(plate_id):
    database.delete_watched_plate(plate_id)
    return jsonify({'status': 'success'})

@app.route('/api/watched_plates', methods=['DELETE'])
def delete_all_watched_plates():
    database.delete_all_watched_plates()
    return jsonify({'status': 'success'})

@app.route('/api/plate_alerts/latest', methods=['GET'])
def get_latest_plate_alerts():
    limit = request.args.get('limit', 10, type=int)
    alerts = database.get_latest_plate_alerts(limit)
    return jsonify(alerts)

@app.route('/video_feed/<cam_id>')
def video_feed(cam_id):
    config = load_config()
    source = None
    for cam in config.get("cameras", []):
        if cam["id"] == cam_id:
            source = cam["source"]
            break
    
    if source is None:
        return "Camera not found", 404

    return Response(generate_frames(source, cam_id=cam_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

