import datetime
import uuid
from flask import (Flask, render_template, Response, request, jsonify,
                   redirect, session, url_for)
from config_manager import (load_config, save_config, get_display_settings,
                            save_display_settings, get_detection_mode,
                            save_detection_mode, get_alpr_settings,
                            save_alpr_settings, get_security_settings,
                            save_security_settings)
import auth
from auth import login_required, admin_required
from plate_format import PLATE_PATTERNS, list_formats
from plate_types import (ANY_TYPE, list_types, is_known_type, describe_mismatch,
                         country_has_specific_types)
from streamer import generate_frames
import database
from background_processor import background_manager
import camera_health
from camera_health import health_monitor
from analysis_queue import analysis_queue
from ptz_control import get_ptz_controller

app = Flask(__name__)
app.secret_key = auth.get_secret_key()

# La cookie de sesión sobrevive al cierre del navegador; quien decide cuándo
# caduca es la comprobación de actividad de streams, no el navegador.
app.permanent_session_lifetime = datetime.timedelta(days=7)

auth.init_users()
auth.init_preferences()

# Start background processor manager
background_manager.start()

# Vigilancia del estado de las cámaras para el indicador de la lista
health_monitor.start()

# Hilos que ejecutan matrículas y rostros fuera del bucle de vídeo
analysis_queue.start()


@app.before_request
def enforce_session_timeout():
    """
    Cierra la sesión tras el plazo configurado sin visualizar ningún stream.

    Se comprueba antes de cada petición. Las rutas de inicio de sesión y los
    archivos estáticos quedan fuera para no crear un bucle de redirecciones.
    """
    if request.endpoint in ('login', 'logout', 'static') or request.path.startswith('/static/'):
        return None
    if auth.current_user() is None:
        return None

    timeout_min = get_security_settings().get("session_timeout_minutes", 15)
    # 0 o negativo desactiva la caducidad, para instalaciones de sala de control
    # donde la pantalla debe quedarse siempre abierta.
    if timeout_min <= 0:
        return None

    if auth.seconds_since_stream(session.get("sid")) > timeout_min * 60:
        auth.end_session()
        if request.path.startswith('/api/'):
            return jsonify({"status": "error", "code": "session_expired",
                            "message": "Sesión caducada por inactividad"}), 401
        return redirect(url_for('login'))
    return None


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if auth.current_user() is not None:
            return redirect(url_for('index'))
        return render_template('login.html')

    data = request.json or {}
    user = auth.get_user_by_name(data.get('username', ''))
    if user is None or not auth.verify_password(data.get('password', ''),
                                                user['password_hash']):
        # El mismo mensaje para usuario inexistente y contraseña incorrecta:
        # distinguirlos permitiría averiguar qué usuarios existen.
        return jsonify({'status': 'error',
                        'message': 'Usuario o contraseña incorrectos'}), 401

    auth.start_session(user)
    return jsonify({'status': 'success', 'role': user['role'],
                    'username': user['username']})


@app.route('/logout')
def logout():
    auth.end_session()
    return redirect(url_for('login'))


@app.route('/api/session', methods=['GET'])
@login_required
def get_session_info():
    """
    Estado de la sesión. El frontend lo consulta periódicamente para redirigir
    al inicio de sesión en cuanto caduca.
    """
    timeout_min = get_security_settings().get("session_timeout_minutes", 15)
    idle = auth.seconds_since_stream(session.get("sid"))
    return jsonify({
        **auth.current_user(),
        'role_label': auth.ROLE_LABELS.get(session.get('role'), ''),
        'session_timeout_minutes': timeout_min,
        'idle_seconds': int(idle),
        'seconds_left': None if timeout_min <= 0 else max(0, int(timeout_min * 60 - idle)),
    })


@app.route('/api/preferences', methods=['GET'])
@login_required
def get_preferences():
    """Personalización del usuario en sesión. Cada cuenta tiene la suya."""
    return jsonify(auth.get_preferences(session['user_id']))

@app.route('/api/preferences', methods=['PUT'])
@login_required
def update_preferences():
    """
    Guarda la personalización del usuario en sesión.

    No lleva admin_required a propósito: es preferencia personal, no
    configuración del sistema, así que un operador también puede cambiarla.
    Siempre se aplica al usuario de la sesión, nunca a otro.
    """
    data = request.json or {}
    current = auth.get_preferences(session['user_id'])
    error = auth.save_preferences(session['user_id'],
                                  data.get('theme', current['theme']))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', **auth.get_preferences(session['user_id'])})


@app.route('/')
@login_required
def index():
    user = auth.current_user()
    # El tema se inyecta en el HTML para que la página nazca ya con él y no
    # haya un destello del tema anterior mientras carga el JavaScript.
    prefs = auth.get_preferences(session['user_id'])
    return render_template('index.html', user=user, is_admin=auth.is_admin(),
                           theme=prefs['theme'])

@app.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    return jsonify({
        'users': auth.list_users(),
        'roles': [{'key': r, 'label': auth.ROLE_LABELS[r]} for r in auth.VALID_ROLES],
        'current_user_id': session.get('user_id'),
    })

@app.route('/api/users', methods=['POST'])
@admin_required
def add_user():
    data = request.json or {}
    user_id, error = auth.create_user(data.get('username', ''),
                                      data.get('password', ''),
                                      data.get('role', auth.ROLE_OPERATOR))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'id': user_id})

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@admin_required
def edit_user(user_id):
    data = request.json or {}
    error = auth.update_user(user_id, role=data.get('role'),
                             password=data.get('password'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    # Si el administrador se cambia el rol a sí mismo, la sesión debe reflejarlo
    # de inmediato o seguiría viendo la interfaz de administrador sin permisos.
    if user_id == session.get('user_id') and data.get('role'):
        session['role'] = data['role']
    return jsonify({'status': 'success'})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def remove_user(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'status': 'error',
                        'message': 'No puedes eliminar tu propio usuario'}), 400
    error = auth.delete_user(user_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success'})

@app.route('/api/security_settings', methods=['GET'])
@login_required
def get_security_config():
    return jsonify(get_security_settings())

@app.route('/api/security_settings', methods=['PUT'])
@admin_required
def update_security_config():
    data = request.json or {}
    try:
        minutes = int(data.get('session_timeout_minutes', 15))
    except (TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'El tiempo debe ser un número de minutos'}), 400
    if minutes < 0 or minutes > 1440:
        return jsonify({'status': 'error',
                        'message': 'El tiempo debe estar entre 0 y 1440 minutos'}), 400

    save_security_settings({'session_timeout_minutes': minutes})
    return jsonify({'status': 'success', 'session_timeout_minutes': minutes})

@app.route('/api/cameras', methods=['GET'])
@login_required
def get_cameras():
    config = load_config()
    return jsonify(config.get("cameras", []))

@app.route('/api/cameras/status', methods=['GET'])
@login_required
def get_cameras_status():
    """
    Estado en línea de cada cámara, para el indicador de la lista.

    Devuelve lo que el monitor tiene cacheado: no sondea durante la petición,
    porque abrir un stream puede tardar segundos y bloquearía la respuesta.
    """
    cameras = load_config().get("cameras", [])
    return jsonify({
        'cameras': health_monitor.get_status(cameras),
        'check_interval_seconds': int(camera_health.CHECK_INTERVAL),
    })

@app.route('/api/cameras', methods=['POST'])
@admin_required
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
    # Comprobar ya, para no dejar el indicador en "sin comprobar" hasta la
    # siguiente ronda periódica
    health_monitor.refresh_soon()
    return jsonify(new_cam)

@app.route('/api/cameras/<cam_id>', methods=['PUT'])
@admin_required
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
    # La fuente puede haber cambiado: el estado cacheado ya no es válido
    health_monitor.forget(cam_id)
    health_monitor.refresh_soon()
    return jsonify({"status": "success"})

@app.route('/api/cameras/<cam_id>', methods=['DELETE'])
@admin_required
def delete_camera(cam_id):
    config = load_config()
    config["cameras"] = [c for c in config.get("cameras", []) if c["id"] != cam_id]
    save_config(config)
    background_manager.sync_with_config()
    health_monitor.forget(cam_id)
    return jsonify({"status": "success"})

@app.route('/api/display_settings', methods=['GET'])
@login_required
def get_settings():
    return jsonify(get_display_settings())

@app.route('/api/display_settings', methods=['PUT'])
@login_required
def update_settings():
    data = request.json
    current = get_display_settings()
    current.update({
        "show_fps": bool(data.get("show_fps", current["show_fps"])),
        "show_labels": bool(data.get("show_labels", current["show_labels"])),
        "show_speed": bool(data.get("show_speed", current["show_speed"])),
        "show_ghost_boxes": bool(data.get("show_ghost_boxes",
                                          current["show_ghost_boxes"])),
    })
    save_display_settings(current)
    return jsonify({"status": "success"})

@app.route('/api/detection_settings', methods=['GET'])
@login_required
def get_detection_settings():
    return jsonify({"detection_mode": get_detection_mode()})

@app.route('/api/detection_settings', methods=['PUT'])
@admin_required
def update_detection_settings():
    data = request.json
    mode = data.get("detection_mode", "monitored")
    if mode in ["monitored", "all"]:
        save_detection_mode(mode)
        background_manager.set_mode(mode)
        return jsonify({"status": "success", "detection_mode": mode})
    return jsonify({"status": "error", "message": "Invalid detection mode"}), 400

@app.route('/api/alpr_settings', methods=['GET'])
@login_required
def get_alpr_config():
    """Ajustes actuales del lector de matrículas y formatos disponibles."""
    return jsonify({
        **get_alpr_settings(),
        "available_formats": list_formats(),
    })

@app.route('/api/alpr_settings', methods=['PUT'])
@admin_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def get_latest_plates():
    limit = request.args.get('limit', 10, type=int)
    plates = database.get_latest_plates(limit)
    return jsonify(plates)

@app.route('/api/plates/search', methods=['GET'])
@login_required
def search_plates():
    query = request.args.get('q', '')
    limit = request.args.get('limit', 50, type=int)
    plates = database.search_plates(query, limit)
    return jsonify(plates)

@app.route('/api/faces/register', methods=['POST'])
@login_required
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
    # Si era el primero, el reconocimiento estaba omitiéndose por completo.
    # Se invalida la caché para que se active ya y no al vencer su plazo.
    fr_engine.invalidate_known_faces_cache()
    if success:
        import os
        os.makedirs("static/faces", exist_ok=True)
        with open(f"static/faces/{face_id}.jpg", "wb") as f:
            f.write(image_bytes)
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route('/api/faces/latest', methods=['GET'])
@login_required
def get_latest_faces():
    limit = request.args.get('limit', 10, type=int)
    faces = database.get_latest_face_detections(limit)
    return jsonify(faces)

@app.route('/api/faces/known', methods=['GET'])
@login_required
def get_known_faces():
    faces = database.get_all_known_faces()
    # Don't send embeddings to the frontend (too large)
    return jsonify([{'id': f['id'], 'name': f['name']} for f in faces])

@app.route('/api/faces/known/<int:face_id>', methods=['PUT'])
@admin_required
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
@admin_required
def delete_face(face_id):
    database.delete_known_face(face_id)
    import fr_engine
    fr_engine.invalidate_known_faces_cache()
    import os
    try:
        os.remove(f"static/faces/{face_id}.jpg")
    except OSError:
        pass
    return jsonify({'status': 'success'})

@app.route('/api/faces/known', methods=['DELETE'])
@admin_required
def delete_all_faces():
    database.delete_all_known_faces()
    import fr_engine
    fr_engine.invalidate_known_faces_cache()
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
@login_required
def get_watched_plates():
    plates = database.get_all_watched_plates()
    return jsonify(plates)

@app.route('/api/plate_types', methods=['GET'])
@login_required
def get_plate_types():
    """
    Tipos de placa disponibles para un país.

    Sin parámetro `country` se usa el país configurado en los ajustes de ALPR,
    que es el caso normal desde la interfaz.
    """
    country = request.args.get('country') or get_alpr_settings()['plate_format']
    return jsonify({
        'country': country,
        'specific': country_has_specific_types(country),
        'types': list_types(country),
    })

def _validated_watch_payload(data):
    """
    Extrae y valida los campos comunes al alta y la edición de placas vigiladas.

    Returns:
        (payload, error) — uno de los dos siempre es None. `payload` incluye la
        clave 'warning' cuando la matrícula no encaja con el tipo elegido: no
        impide guardar, solo se informa al usuario.
    """
    pattern = (data.get('plate_pattern') or '').strip()
    if not pattern:
        return None, 'Patrón de placa vacío'

    country = data.get('country') or get_alpr_settings()['plate_format']
    plate_type = data.get('plate_type') or ANY_TYPE

    if not is_known_type(country, plate_type):
        return None, f'Tipo de placa no válido para {country}: {plate_type}'

    # Los patrones con comodín no se validan contra el formato: representan
    # varias matrículas y no tienen por qué encajar como una sola.
    warning = None
    if '*' not in pattern and '?' not in pattern:
        warning = describe_mismatch(pattern, country, plate_type)

    return {
        'pattern': pattern,
        'note': (data.get('note') or '').strip(),
        'plate_type': plate_type,
        'country': country,
        'warning': warning,
    }, None

@app.route('/api/watched_plates', methods=['POST'])
@login_required
def add_watched_plate():
    payload, error = _validated_watch_payload(request.json or {})
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    database.insert_watched_plate(payload['pattern'], payload['note'],
                                  payload['plate_type'], payload['country'])
    return jsonify({'status': 'success', 'warning': payload['warning']})

@app.route('/api/watched_plates/<int:plate_id>', methods=['PUT'])
@admin_required
def update_watched_plate(plate_id):
    payload, error = _validated_watch_payload(request.json or {})
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    database.update_watched_plate(plate_id, payload['pattern'], payload['note'],
                                  payload['plate_type'], payload['country'])
    return jsonify({'status': 'success', 'warning': payload['warning']})

@app.route('/api/watched_plates/<int:plate_id>', methods=['DELETE'])
@admin_required
def delete_watched_plate(plate_id):
    database.delete_watched_plate(plate_id)
    return jsonify({'status': 'success'})

@app.route('/api/watched_plates', methods=['DELETE'])
@admin_required
def delete_all_watched_plates():
    database.delete_all_watched_plates()
    return jsonify({'status': 'success'})

@app.route('/api/plate_alerts/latest', methods=['GET'])
@login_required
def get_latest_plate_alerts():
    limit = request.args.get('limit', 10, type=int)
    alerts = database.get_latest_plate_alerts(limit)
    return jsonify(alerts)

@app.route('/video_feed/<cam_id>')
@login_required
def video_feed(cam_id):
    config = load_config()
    source = None
    for cam in config.get("cameras", []):
        if cam["id"] == cam_id:
            source = cam["source"]
            break
    
    if source is None:
        return "Camera not found", 404

    # El identificador se lee aquí, dentro del contexto de la petición: el
    # generador se consume después, cuando ya no hay acceso a `session`.
    sid = session.get('sid')

    def tracked_frames():
        """
        Envuelve el generador de vídeo marcando actividad en cada fotograma.

        Es lo que mantiene viva la sesión mientras haya una cámara en pantalla:
        la conexión MJPEG es una única petición de larga duración, así que no
        basta con registrar la actividad al inicio.
        """
        for chunk in generate_frames(source, cam_id=cam_id):
            auth.touch_stream_activity(sid)
            health_monitor.report_alive(cam_id)
            yield chunk

    return Response(tracked_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

