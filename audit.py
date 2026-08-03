"""
audit.py
--------
Registro de acciones administrativas.

Deja constancia de quién cambió qué y cuándo: altas y bajas de cámaras,
cambios de configuración, gestión de usuarios y borrado de registros de
detección.

Solo se anotan las acciones que MODIFICAN algo. Registrar también las consultas
llenaría la tabla de ruido y enterraría lo que de verdad importa auditar.

El registro no se puede borrar desde la aplicación, a propósito: un historial
que el propio administrador puede vaciar no sirve para auditar. Si hiciera falta
purgarlo, se hace sobre la base de datos de forma consciente.
"""

import datetime
import json

from database import get_connection

# ---------------------------------------------------------------------------
# Acciones registrables
#
# Se nombran en pasado y en un vocabulario cerrado para poder filtrar después.
# ---------------------------------------------------------------------------
CAMERA_ADDED = "camera.added"
CAMERA_EDITED = "camera.edited"
CAMERA_DELETED = "camera.deleted"

SETTINGS_DISPLAY = "settings.display"
SETTINGS_DETECTION = "settings.detection"
SETTINGS_ALPR = "settings.alpr"
SETTINGS_SECURITY = "settings.security"

USER_ADDED = "user.added"
USER_EDITED = "user.edited"
USER_DELETED = "user.deleted"

FACE_REGISTERED = "face.registered"
FACE_EDITED = "face.edited"
FACE_DELETED = "face.deleted"
FACES_CLEARED = "face.cleared_all"

PLATE_WATCH_ADDED = "watchlist.added"
PLATE_WATCH_EDITED = "watchlist.edited"
PLATE_WATCH_DELETED = "watchlist.deleted"
PLATE_WATCH_CLEARED = "watchlist.cleared_all"

DETECTIONS_PLATES_CLEARED = "detections.plates_cleared"
DETECTIONS_FACES_CLEARED = "detections.faces_cleared"
DETECTIONS_ALERTS_CLEARED = "detections.alerts_cleared"

SESSION_LOGIN = "session.login"
SESSION_LOGIN_FAILED = "session.login_failed"
SESSION_LOGOUT = "session.logout"

# Texto legible para la interfaz
ACTION_LABELS = {
    CAMERA_ADDED: "Cámara añadida",
    CAMERA_EDITED: "Cámara editada",
    CAMERA_DELETED: "Cámara eliminada",
    SETTINGS_DISPLAY: "Superposiciones de video",
    SETTINGS_DETECTION: "Modo de detección",
    SETTINGS_ALPR: "Formato de matrículas",
    SETTINGS_SECURITY: "Caducidad de sesión",
    USER_ADDED: "Usuario creado",
    USER_EDITED: "Usuario modificado",
    USER_DELETED: "Usuario eliminado",
    FACE_REGISTERED: "Rostro registrado",
    FACE_EDITED: "Rostro modificado",
    FACE_DELETED: "Rostro eliminado",
    FACES_CLEARED: "Todos los rostros eliminados",
    PLATE_WATCH_ADDED: "Placa vigilada añadida",
    PLATE_WATCH_EDITED: "Placa vigilada editada",
    PLATE_WATCH_DELETED: "Placa vigilada eliminada",
    PLATE_WATCH_CLEARED: "Lista de vigilancia vaciada",
    DETECTIONS_PLATES_CLEARED: "Detecciones de placas borradas",
    DETECTIONS_FACES_CLEARED: "Detecciones de rostros borradas",
    DETECTIONS_ALERTS_CLEARED: "Alertas de placas borradas",
    SESSION_LOGIN: "Inicio de sesión",
    SESSION_LOGIN_FAILED: "Intento de acceso fallido",
    SESSION_LOGOUT: "Cierre de sesión",
}


def init_audit():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            username TEXT NOT NULL,
            role TEXT DEFAULT '',
            action TEXT NOT NULL,
            target TEXT DEFAULT '',
            details TEXT DEFAULT '',
            ip TEXT DEFAULT ''
        )
    ''')
    # Se consulta casi siempre ordenado por fecha descendente
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp DESC)')
    conn.commit()
    conn.close()


def log(action: str, target: str = "", details=None,
        username: str = None, role: str = None, ip: str = None):
    """
    Anota una acción.

    Los datos del usuario se toman de la sesión activa si no se indican, para
    que quien llama no tenga que repetirlos en cada endpoint.

    Nunca lanza excepción: un fallo al auditar no debe impedir la operación que
    el usuario pidió, ni dejar el sistema a medias.
    """
    try:
        if username is None or role is None or ip is None:
            try:
                from flask import request, session
                username = username if username is not None else session.get("username", "?")
                role = role if role is not None else session.get("role", "")
                ip = ip if ip is not None else (request.remote_addr or "")
            except Exception:
                # Fuera de una petición: se registra igualmente sin esos datos
                username = username or "sistema"
                role = role or ""
                ip = ip or ""

        if details is not None and not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO audit_log (timestamp, username, role, action, target, details, ip)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             username, role, action, str(target), details or "", ip)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Audit] No se pudo registrar la acción {action}: {e}")


def get_entries(limit: int = 100, action: str = None, username: str = None,
                search: str = None):
    """
    Últimas entradas del registro, de la más reciente a la más antigua.

    Args:
        limit:    número máximo de entradas
        action:   filtrar por una acción concreta
        username: filtrar por usuario
        search:   texto libre sobre destino y detalles
    """
    consulta = "SELECT * FROM audit_log WHERE 1=1"
    parametros = []

    if action:
        consulta += " AND action = ?"
        parametros.append(action)
    if username:
        consulta += " AND username = ?"
        parametros.append(username)
    if search:
        # La búsqueda debe funcionar con lo que el usuario VE. En la tabla se
        # muestra "Cámara añadida", pero en la base de datos está "camera.added":
        # buscar el texto visible no encontraría nada. Se traducen primero las
        # etiquetas que coincidan y se incluyen sus claves en la consulta.
        termino = search.lower()
        acciones_coincidentes = [clave for clave, etiqueta in ACTION_LABELS.items()
                                 if termino in etiqueta.lower()]

        patron = f"%{search}%"
        condiciones = ["target LIKE ?", "details LIKE ?", "action LIKE ?",
                       "username LIKE ?"]
        parametros += [patron, patron, patron, patron]

        if acciones_coincidentes:
            marcadores = ",".join("?" * len(acciones_coincidentes))
            condiciones.append(f"action IN ({marcadores})")
            parametros += acciones_coincidentes

        consulta += " AND (" + " OR ".join(condiciones) + ")"

    consulta += " ORDER BY id DESC LIMIT ?"
    parametros.append(limit)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(consulta, parametros)
    filas = cursor.fetchall()
    conn.close()

    resultado = []
    for fila in filas:
        entrada = dict(fila)
        entrada["action_label"] = ACTION_LABELS.get(entrada["action"], entrada["action"])
        resultado.append(entrada)
    return resultado


def count_entries() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM audit_log")
    n = cursor.fetchone()["n"]
    conn.close()
    return n


def list_actions():
    """Acciones presentes en el registro, para el desplegable de filtrado."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
    acciones = [r["action"] for r in cursor.fetchall()]
    conn.close()
    return [{"key": a, "label": ACTION_LABELS.get(a, a)} for a in acciones]
