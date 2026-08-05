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
from auth import login_required, admin_required, permission_required
import audit
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
import nvr_client

app = Flask(__name__)
app.secret_key = auth.get_secret_key()

# La cookie de sesión sobrevive al cierre del navegador; quien decide cuándo
# caduca es la comprobación de actividad de streams, no el navegador.
app.permanent_session_lifetime = datetime.timedelta(days=7)

auth.init_users()
auth.init_preferences()
audit.init_audit()

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
        audit.log(audit.SESSION_LOGIN_FAILED,
                  target=(data.get('username') or '')[:60],
                  username=(data.get('username') or '?'), role='')
        return jsonify({'status': 'error',
                        'message': 'Usuario o contraseña incorrectos'}), 401

    auth.start_session(user)
    audit.log(audit.SESSION_LOGIN, target=user['username'])
    return jsonify({'status': 'success', 'role': user['role'],
                    'username': user['username']})


@app.route('/logout')
def logout():
    audit.log(audit.SESSION_LOGOUT)
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
                           permissions=sorted(auth.current_permissions()),
                           theme=prefs['theme'])

# ---------------------------------------------------------------------------
# Servidor de grabaciones (NVR)
#
# El navegador nunca habla directamente con el NVR: todo pasa por aquí, para
# que la clave de acceso no salga del servidor y para reutilizar la sesión.
# ---------------------------------------------------------------------------
@app.route('/api/nvr/settings', methods=['GET'])
@login_required
def get_nvr_config():
    ajustes = nvr_client.get_nvr_settings()
    # La clave no se envía al navegador: solo si está puesta o no
    return jsonify({
        'enabled': ajustes['enabled'],
        'url': ajustes['url'],
        'has_token': bool(ajustes['token']),
    })

@app.route('/api/nvr/settings', methods=['PUT'])
@admin_required
def update_nvr_config():
    datos = request.json or {}
    actual = nvr_client.get_nvr_settings()

    nuevos = {
        'enabled': bool(datos.get('enabled', actual['enabled'])),
        'url': (datos.get('url', actual['url']) or '').strip(),
        # Una clave vacía significa "no la cambies", para que el usuario pueda
        # editar la dirección sin volver a teclearla.
        'token': (datos.get('token') or '').strip() or actual['token'],
    }
    nvr_client.save_nvr_settings(nuevos)
    audit.log(audit.NVR_SETTINGS, target=nuevos['url'],
              details={'activo': nuevos['enabled']})
    return jsonify({'status': 'success'})

@app.route('/api/nvr/status', methods=['GET'])
@login_required
def get_nvr_status():
    """Estado del servidor de grabaciones, o el motivo de que no responda."""
    try:
        salud = nvr_client.health()
    except nvr_client.NvrError as e:
        return jsonify({'connected': False, 'message': e.mensaje})

    try:
        estado = nvr_client.status()
    except nvr_client.NvrError as e:
        # Responde pero rechaza la clave: es un problema distinto de "apagado"
        return jsonify({'connected': True, 'authenticated': False,
                        'message': e.mensaje, 'health': salud})

    return jsonify({'connected': True, 'authenticated': True,
                    'health': salud, **estado})

@app.route('/api/nvr/cameras', methods=['GET'])
@login_required
def get_nvr_cameras():
    """
    Cámaras de la aplicación cruzadas con lo que el NVR está grabando.

    Se combinan aquí para que la interfaz muestre todas las cámaras
    registradas, graben o no, con su estado de grabación al lado.
    """
    camaras = load_config().get('cameras', [])
    try:
        grabando = {c['camera_id']: c for c in nvr_client.get_cameras()['cameras']}
        disponible = True
        mensaje = ''
    except nvr_client.NvrError as e:
        grabando, disponible, mensaje = {}, False, e.mensaje

    resultado = []
    for cam in camaras:
        info = grabando.get(cam['id'], {})
        resultado.append({
            'camera_id': cam['id'],
            'name': cam['name'],
            'recording': bool(info.get('enabled')),
            'retention_days': int(info.get('retention_days', 3)),
            'days_recorded': info.get('days_recorded', 0),
            'oldest_day': info.get('oldest_day'),
            'newest_day': info.get('newest_day'),
            'bytes': info.get('bytes', 0),
        })

    return jsonify({'cameras': resultado, 'nvr_available': disponible,
                    'message': mensaje})

@app.route('/api/nvr/cameras', methods=['PUT'])
@admin_required
def update_nvr_cameras():
    """Define qué cámaras se graban y durante cuántos días."""
    datos = request.json or {}
    seleccion = {str(c.get('camera_id')): c for c in datos.get('cameras', [])}

    # La fuente se toma de la configuración de la aplicación, no de lo que
    # llegue del navegador: el NVR debe grabar la cámara real, no una URL
    # arbitraria que alguien pudiera inyectar.
    payload = []
    for cam in load_config().get('cameras', []):
        elegida = seleccion.get(cam['id'])
        if elegida is None:
            continue
        try:
            dias = int(elegida.get('retention_days', 3))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': f"Retención no numérica en {cam['name']}"}), 400
        if not 1 <= dias <= 365:
            return jsonify({'status': 'error',
                            'message': 'La retención debe estar entre 1 y 365 días'}), 400
        payload.append({
            'camera_id': cam['id'], 'name': cam['name'], 'source': cam['source'],
            'enabled': bool(elegida.get('recording')), 'retention_days': dias,
        })

    try:
        nvr_client.set_cameras(payload)
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

    activas = [c['name'] for c in payload if c['enabled']]
    audit.log(audit.NVR_CAMERAS, target=f'{len(activas)} en grabación',
              details={'camaras': ', '.join(activas) or 'ninguna'})
    return jsonify({'status': 'success'})

@app.route('/api/nvr/cameras/<camera_id>/recordings', methods=['DELETE'])
@admin_required
def delete_nvr_recordings(camera_id):
    """Borra todo el material grabado de una cámara. No toca su configuración."""
    nombre = next((c['name'] for c in load_config().get('cameras', [])
                   if c['id'] == camera_id), camera_id)
    try:
        r = nvr_client.delete_camera_recordings(camera_id)
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

    audit.log(audit.NVR_RECORDINGS_DELETED, target=nombre,
              details={'dias': r.get('days_deleted', 0),
                       'bytes_liberados': r.get('bytes_freed', 0)})
    return jsonify(r)

@app.route('/api/nvr/recordings/days', methods=['GET'])
@permission_required(auth.PERM_VIEW_RECORDINGS)
def nvr_recording_days():
    try:
        return jsonify(nvr_client.recording_days(request.args.get('camera_id', '')))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

@app.route('/api/nvr/recordings/segments', methods=['GET'])
@permission_required(auth.PERM_VIEW_RECORDINGS)
def nvr_recording_segments():
    try:
        return jsonify(nvr_client.recording_segments(
            request.args.get('camera_id', ''),
            day=request.args.get('day'),
            desde=request.args.get('from'),
            hasta=request.args.get('to')))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

@app.route('/api/nvr/recordings/at', methods=['GET'])
@permission_required(auth.PERM_VIEW_RECORDINGS)
def nvr_recording_at():
    try:
        return jsonify(nvr_client.recording_at(
            request.args.get('camera_id', ''), request.args.get('at', '')))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

@app.route('/api/nvr/segment/<int:segment_id>', methods=['GET'])
@permission_required(auth.PERM_VIEW_RECORDINGS)
def nvr_segment(segment_id):
    """
    Reenvía un segmento de vídeo desde el NVR al navegador.

    Se retransmite por trozos y se conservan las cabeceras de rango: son las
    que permiten al reproductor saltar dentro del vídeo sin descargarlo entero.
    """
    try:
        remota = nvr_client.segment_stream(segment_id, request.headers.get('Range'))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

    def retransmitir():
        try:
            for trozo in remota.iter_content(chunk_size=256 * 1024):
                if trozo:
                    yield trozo
        finally:
            remota.close()

    cabeceras = {}
    for h in ('Content-Range', 'Accept-Ranges', 'Content-Length'):
        if h in remota.headers:
            cabeceras[h] = remota.headers[h]

    return Response(retransmitir(), status=remota.status_code,
                    mimetype=remota.headers.get('Content-Type', 'video/mp4'),
                    headers=cabeceras, direct_passthrough=True)

@app.route('/api/nvr/export', methods=['POST'])
@permission_required(auth.PERM_EXPORT_RECORDINGS)
def nvr_export_create():
    """Encola una exportación de vídeo y devuelve el trabajo."""
    datos = request.json or {}
    try:
        r = nvr_client.create_export(
            datos.get('camera_id', ''), datos.get('from', ''),
            datos.get('to', ''), datos.get('name', ''))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

    job = r.get('job', {})
    audit.log(audit.VIDEO_EXPORTED, target=job.get('filename', ''),
              details={'desde': datos.get('from'), 'hasta': datos.get('to')})
    return jsonify(r)

@app.route('/api/nvr/export/<job_id>', methods=['GET'])
@permission_required(auth.PERM_EXPORT_RECORDINGS)
def nvr_export_status(job_id):
    try:
        return jsonify(nvr_client.export_status(job_id))
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

@app.route('/api/nvr/export/<job_id>/download', methods=['GET'])
@permission_required(auth.PERM_EXPORT_RECORDINGS)
def nvr_export_download(job_id):
    """Reenvía el archivo exportado al navegador, por trozos."""
    try:
        remota = nvr_client.export_download(job_id)
    except nvr_client.NvrError as e:
        return jsonify({'status': 'error', 'message': e.mensaje}), 502

    def retransmitir():
        try:
            for trozo in remota.iter_content(chunk_size=512 * 1024):
                if trozo:
                    yield trozo
        finally:
            remota.close()

    cabeceras = {}
    for h in ('Content-Length', 'Content-Disposition'):
        if h in remota.headers:
            cabeceras[h] = remota.headers[h]

    return Response(retransmitir(), status=remota.status_code,
                    mimetype='video/mp4', headers=cabeceras,
                    direct_passthrough=True)

@app.route('/api/detections/summary', methods=['GET'])
@login_required
def get_detections_summary():
    """Cuántos registros de detección hay de cada tipo."""
    return jsonify({
        'plates': database.count_plates(),
        'faces': database.count_face_detections(),
        'alerts': database.count_plate_alerts(),
    })

@app.route('/api/plates', methods=['DELETE'])
@admin_required
def clear_plate_detections():
    """
    Vacía el historial de matrículas leídas.

    No toca la lista de vigilancia: son las placas que se buscan, no el
    historial de lo detectado.
    """
    n = database.delete_all_plates()
    audit.log(audit.DETECTIONS_PLATES_CLEARED, details={'eliminados': n})
    return jsonify({'status': 'success', 'deleted': n})

@app.route('/api/faces/detections', methods=['DELETE'])
@admin_required
def clear_face_detections():
    """
    Vacía el historial de rostros detectados.

    No toca los rostros registrados: son las personas que se buscan.
    """
    n = database.delete_all_face_detections()
    audit.log(audit.DETECTIONS_FACES_CLEARED, details={'eliminados': n})
    return jsonify({'status': 'success', 'deleted': n})

@app.route('/api/plate_alerts', methods=['DELETE'])
@admin_required
def clear_plate_alerts():
    """Vacía el historial de alertas de placas vigiladas."""
    n = database.delete_all_plate_alerts()
    audit.log(audit.DETECTIONS_ALERTS_CLEARED, details={'eliminadas': n})
    return jsonify({'status': 'success', 'deleted': n})

@app.route('/api/audit_log', methods=['GET'])
@admin_required
def get_audit_log():
    """
    Registro de acciones administrativas.

    No existe endpoint para borrarlo a propósito: un historial que el propio
    administrador puede vaciar no sirve para auditar.
    """
    return jsonify({
        'entries': audit.get_entries(
            limit=min(request.args.get('limit', 100, type=int), 500),
            action=request.args.get('action') or None,
            username=request.args.get('username') or None,
            search=request.args.get('q') or None,
        ),
        'total': audit.count_entries(),
        'actions': audit.list_actions(),
    })

def _describir_permisos(permisos):
    """Permisos en texto legible para la auditoría, no con sus claves internas."""
    concedidos = [auth.PERMISSION_LABELS[p] for p in auth.VALID_PERMISSIONS
                  if p in set(permisos or ())]
    return ', '.join(concedidos) or 'ninguno'

@app.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    return jsonify({
        'users': auth.list_users(),
        'roles': [{'key': r, 'label': auth.ROLE_LABELS[r]} for r in auth.VALID_ROLES],
        'permissions': [{'key': p, 'label': auth.PERMISSION_LABELS[p]}
                        for p in auth.VALID_PERMISSIONS],
        'current_user_id': session.get('user_id'),
    })

@app.route('/api/users', methods=['POST'])
@admin_required
def add_user():
    data = request.json or {}
    permisos = data.get('permissions')
    user_id, error = auth.create_user(data.get('username', ''),
                                      data.get('password', ''),
                                      data.get('role', auth.ROLE_OPERATOR),
                                      permissions=permisos)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    concedidos = auth.parse_permissions(auth.format_permissions(
        permisos if permisos is not None else auth.DEFAULT_OPERATOR_PERMISSIONS))
    audit.log(audit.USER_ADDED, target=data.get('username', ''),
              details={'rol': data.get('role', auth.ROLE_OPERATOR),
                       'permisos': _describir_permisos(concedidos)})
    return jsonify({'status': 'success', 'id': user_id})

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@admin_required
def edit_user(user_id):
    data = request.json or {}
    error = auth.update_user(user_id, role=data.get('role'),
                             password=data.get('password'),
                             permissions=data.get('permissions'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    # Si el administrador se cambia el rol a sí mismo, la sesión debe reflejarlo
    # de inmediato o seguiría viendo la interfaz de administrador sin permisos.
    if user_id == session.get('user_id') and data.get('role'):
        session['role'] = data['role']
    objetivo = auth.get_user(user_id) or {}
    audit.log(audit.USER_EDITED, target=objetivo.get('username', user_id),
              details={'rol': data.get('role'),
                       'contrasena_cambiada': bool(data.get('password')),
                       'permisos': (_describir_permisos(data['permissions'])
                                    if data.get('permissions') is not None
                                    else None)})
    return jsonify({'status': 'success'})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def remove_user(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'status': 'error',
                        'message': 'No puedes eliminar tu propio usuario'}), 400
    objetivo = auth.get_user(user_id) or {}
    error = auth.delete_user(user_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    audit.log(audit.USER_DELETED, target=objetivo.get('username', user_id))
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
    audit.log(audit.SETTINGS_SECURITY, target=f'{minutes} min')
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
    audit.log(audit.CAMERA_ADDED, target=new_cam['name'],
              details={'id': new_cam['id'], 'tipo': new_cam['type'],
                       'ptz': new_cam['is_ptz']})
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
    audit.log(audit.CAMERA_EDITED, target=data.get('name', cam_id),
              details={'id': cam_id})
    return jsonify({"status": "success"})

@app.route('/api/cameras/<cam_id>', methods=['DELETE'])
@admin_required
def delete_camera(cam_id):
    config = load_config()
    # Se guarda antes de borrar para poder dejar constancia de qué se eliminó;
    # después ya no habría forma de saberlo.
    eliminada = next((c for c in config.get("cameras", []) if c["id"] == cam_id), None)
    config["cameras"] = [c for c in config.get("cameras", []) if c["id"] != cam_id]
    save_config(config)
    background_manager.sync_with_config()
    health_monitor.forget(cam_id)
    audit.log(audit.CAMERA_DELETED, target=(eliminada or {}).get('name', cam_id),
              details={'id': cam_id, 'fuente': (eliminada or {}).get('source', '')})
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
    audit.log(audit.SETTINGS_DISPLAY, details=current)
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
        audit.log(audit.SETTINGS_DETECTION, target=mode)
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
    audit.log(audit.SETTINGS_ALPR, target=plate_format,
              details={'confianza_minima': min_confidence})
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
        audit.log(audit.FACE_REGISTERED, target=name, details={'id': face_id})
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
    audit.log(audit.FACE_DELETED, target=face_id)
    import os
    try:
        os.remove(f"static/faces/{face_id}.jpg")
    except OSError:
        pass
    return jsonify({'status': 'success'})

@app.route('/api/faces/known', methods=['DELETE'])
@admin_required
def delete_all_faces():
    n = len(database.get_all_known_faces())
    database.delete_all_known_faces()
    import fr_engine
    fr_engine.invalidate_known_faces_cache()
    audit.log(audit.FACES_CLEARED, details={'eliminados': n})
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
    audit.log(audit.PLATE_WATCH_ADDED, target=payload['pattern'],
              details={'tipo': payload['plate_type'], 'pais': payload['country']})
    return jsonify({'status': 'success', 'warning': payload['warning']})

@app.route('/api/watched_plates/<int:plate_id>', methods=['PUT'])
@admin_required
def update_watched_plate(plate_id):
    payload, error = _validated_watch_payload(request.json or {})
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    database.update_watched_plate(plate_id, payload['pattern'], payload['note'],
                                  payload['plate_type'], payload['country'])
    audit.log(audit.PLATE_WATCH_EDITED, target=payload['pattern'],
              details={'id': plate_id, 'tipo': payload['plate_type']})
    return jsonify({'status': 'success', 'warning': payload['warning']})

@app.route('/api/watched_plates/<int:plate_id>', methods=['DELETE'])
@admin_required
def delete_watched_plate(plate_id):
    database.delete_watched_plate(plate_id)
    audit.log(audit.PLATE_WATCH_DELETED, target=plate_id)
    return jsonify({'status': 'success'})

@app.route('/api/watched_plates', methods=['DELETE'])
@admin_required
def delete_all_watched_plates():
    n = len(database.get_all_watched_plates())
    database.delete_all_watched_plates()
    audit.log(audit.PLATE_WATCH_CLEARED, details={'eliminadas': n})
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
    cam_name = cam_id
    for cam in config.get("cameras", []):
        if cam["id"] == cam_id:
            source = cam["source"]
            cam_name = cam.get("name", cam_id)
            break
    
    if source is None:
        return "Camera not found", 404

    # Deja constancia de quién abrió el stream. Agrupa por sesión y cámara:
    # el navegador pide esta URL de nuevo en cada recarga, cambio de
    # distribución o reconexión, y anotarlas todas llenaba el registro.
    audit.log_stream_start(cam_id, cam_name)

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

