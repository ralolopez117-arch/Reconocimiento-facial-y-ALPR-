"""
audit.py
--------
Registro de acciones administrativas.

Deja constancia de quién cambió qué y cuándo: altas y bajas de cámaras,
cambios de configuración, gestión de usuarios y borrado de registros de
detección.

Como regla se anotan las acciones que MODIFICAN algo: registrar también las
consultas llenaría la tabla de ruido y enterraría lo que de verdad importa.

La excepción es la apertura de streams. En videovigilancia, quién miró qué
cámara y cuándo es en sí mismo un dato auditable, así que sí se registra, pero
con dos cautelas para que no ahogue al resto (ver más abajo): se anota una vez
por sesión y cámara, no una por petición, y caduca antes que las demás.

El registro no se puede vaciar desde la aplicación, a propósito: un historial
que el propio administrador puede borrar no sirve para auditar. Los plazos de
caducidad son constantes de este módulo y no se exponen en la interfaz,
precisamente para que nadie pueda acortarlos y tapar sus propias huellas.
"""

import datetime
import json
import re
import threading
import time

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

NVR_SETTINGS = "nvr.settings"
VIDEO_EXPORTED = "nvr.exported"
NVR_CAMERAS = "nvr.cameras"
NVR_RECORDINGS_DELETED = "nvr.recordings_deleted"

STREAM_STARTED = "camera.stream_started"

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
    NVR_SETTINGS: "Servidor de grabaciones",
    VIDEO_EXPORTED: "Vídeo exportado",
    NVR_CAMERAS: "Cámaras en grabación",
    NVR_RECORDINGS_DELETED: "Grabaciones borradas",
    STREAM_STARTED: "Stream iniciado",
    SESSION_LOGIN: "Inicio de sesión",
    SESSION_LOGIN_FAILED: "Intento de acceso fallido",
    SESSION_LOGOUT: "Cierre de sesión",
}


# ---------------------------------------------------------------------------
# Caducidad
#
# Sin ningún límite la tabla crece indefinidamente. Se distinguen dos plazos
# porque no todo vale lo mismo: un cambio de permisos o el borrado de unas
# grabaciones interesa conservarlo mucho tiempo, mientras que saber que alguien
# abrió una cámara hace ocho meses aporta poco y en cambio ocupa la mayor parte
# de las filas.
#
# Son constantes y no ajustes de la interfaz: si el administrador pudiera
# bajarlas a un día, borraría el rastro de sus propias acciones sin que quedara
# constancia.
# ---------------------------------------------------------------------------
RETENCION_DIAS = 365                      # acciones que modifican el sistema
RETENCION_DIAS_ALTA_FRECUENCIA = 30       # eventos de mero visionado

# Acciones que no modifican nada y se generan solas al usar la aplicación.
ACCIONES_ALTA_FRECUENCIA = (STREAM_STARTED,)

# Tope absoluto de filas, como red de seguridad por si algo registrara en
# bucle: evita que la base de datos crezca sin control dentro del plazo.
MAX_ENTRADAS = 200_000

# Cada cuántas anotaciones se comprueba la caducidad. Hacerlo en cada INSERT
# supondría un DELETE por cada acción registrada, para casi nunca borrar nada.
_CADA_CUANTAS_PURGAS = 200

_contador_inserciones = 0
_lock = threading.Lock()

# Última vez que se anotó la apertura de cada (sesión, cámara), para no repetir
_ultimo_stream = {}

# Ventana durante la cual no se vuelve a anotar la misma cámara en la misma
# sesión. Media hora: absorbe recargas de página, cambios de distribución y
# reconexiones del stream, pero deja constancia si alguien vuelve más tarde.
VENTANA_STREAM_SEGUNDOS = 30 * 60


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
    # La purga filtra por acción y fecha a la vez
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_accion ON audit_log(action, timestamp)')
    conn.commit()
    conn.close()

    # Un arranque es buen momento: si el proceso estuvo parado meses, la purga
    # periódica no habría llegado a ejecutarse.
    purgar_caducadas()


def purgar_caducadas():
    """
    Borra las entradas que superan su plazo de conservación.

    Returns:
        Número de filas eliminadas.
    """
    borradas = 0
    try:
        ahora = datetime.datetime.now()
        limite = (ahora - datetime.timedelta(days=RETENCION_DIAS)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        limite_frecuentes = (ahora - datetime.timedelta(days=RETENCION_DIAS_ALTA_FRECUENCIA)
                             ).strftime("%Y-%m-%d %H:%M:%S")

        conn = get_connection()
        cursor = conn.cursor()

        marcadores = ",".join("?" * len(ACCIONES_ALTA_FRECUENCIA))
        cursor.execute(
            f"DELETE FROM audit_log WHERE action IN ({marcadores}) AND timestamp < ?",
            (*ACCIONES_ALTA_FRECUENCIA, limite_frecuentes))
        borradas += cursor.rowcount

        cursor.execute(
            f"DELETE FROM audit_log WHERE action NOT IN ({marcadores}) AND timestamp < ?",
            (*ACCIONES_ALTA_FRECUENCIA, limite))
        borradas += cursor.rowcount

        # Red de seguridad por número de filas: se conservan las más recientes
        cursor.execute("SELECT COUNT(*) AS n FROM audit_log")
        sobrantes = cursor.fetchone()["n"] - MAX_ENTRADAS
        if sobrantes > 0:
            cursor.execute(
                "DELETE FROM audit_log WHERE id IN "
                "(SELECT id FROM audit_log ORDER BY id ASC LIMIT ?)", (sobrantes,))
            borradas += cursor.rowcount

        conn.commit()
        conn.close()
        if borradas:
            print(f"[Audit] {borradas} entradas caducadas eliminadas")
    except Exception as e:
        # La purga es mantenimiento: que falle no debe tumbar el arranque ni
        # impedir que se sigan registrando acciones.
        print(f"[Audit] No se pudo purgar el registro: {e}")
    return borradas


def log_stream_start(camera_id: str, camera_name: str = ""):
    """
    Anota que alguien abrió el stream en vivo de una cámara.

    Se llama desde la vista de vídeo, que recibe una petición HTTP por cada
    apertura del flujo. Eso no equivale a "un usuario miró una cámara": recargar
    la página, cambiar la distribución de la cuadrícula, arrastrar la cámara a
    otra celda o una simple reconexión de red generan peticiones nuevas. Anotar
    todas llenaba el registro de entradas idénticas y enterraba las acciones
    administrativas, que son el motivo de que exista este historial.

    Por eso se agrupa: la misma cámara, en la misma sesión, se anota una vez
    cada VENTANA_STREAM_SEGUNDOS.
    """
    try:
        from flask import session
        sid = session.get("sid") or session.get("username") or "?"
    except Exception:
        sid = "?"

    clave = (sid, camera_id)
    ahora = time.monotonic()

    with _lock:
        anterior = _ultimo_stream.get(clave)
        if anterior is not None and ahora - anterior < VENTANA_STREAM_SEGUNDOS:
            return False
        _ultimo_stream[clave] = ahora

        # Las sesiones cerradas dejarían su entrada aquí para siempre. Se
        # aprovecha el paso para soltar las que ya no pueden bloquear nada.
        if len(_ultimo_stream) > 500:
            caducadas = [k for k, t in _ultimo_stream.items()
                         if ahora - t > VENTANA_STREAM_SEGUNDOS]
            for k in caducadas:
                del _ultimo_stream[k]

    log(STREAM_STARTED, target=camera_name or camera_id,
        details={"camera_id": camera_id, "camera_name": camera_name})
    return True


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
        return

    # Mantenimiento de vez en cuando, fuera del try anterior para que un fallo
    # purgando no se confunda con un fallo al registrar: la acción ya está
    # guardada, que es lo importante.
    global _contador_inserciones
    with _lock:
        _contador_inserciones += 1
        toca = _contador_inserciones % _CADA_CUANTAS_PURGAS == 0
    if toca:
        purgar_caducadas()


def _patrones_de_fecha(termino: str):
    """
    Traduce una fecha escrita como dd/mm/aaaa al formato en que se almacena.

    En la tabla la marca de tiempo es aaaa-mm-dd, porque así ordena bien al
    compararla como texto, pero en pantalla se muestra dd/mm/aaaa. Sin esta
    traducción, buscar la fecha que se está viendo no encontraría nada.

    Se aceptan también fechas parciales:
        03/08/2026  -> ese día concreto
        03/08       -> ese día y mes de cualquier año
        08/2026     -> ese mes completo

    Returns:
        Lista de fragmentos a buscar dentro de la marca de tiempo.
    """
    t = termino.strip()
    patrones = []

    # dd/mm/aaaa
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", t)
    if m:
        dia, mes, anio = m.groups()
        patrones.append(f"{anio}-{int(mes):02d}-{int(dia):02d}")
        return patrones

    # mm/aaaa — se comprueba antes que dd/mm porque el año de cuatro cifras
    # lo hace inequívoco
    m = re.match(r"^(\d{1,2})[/\-.](\d{4})$", t)
    if m:
        mes, anio = m.groups()
        patrones.append(f"{anio}-{int(mes):02d}-")
        return patrones

    # dd/mm de cualquier año
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})$", t)
    if m:
        dia, mes = m.groups()
        patrones.append(f"-{int(mes):02d}-{int(dia):02d}")
        return patrones

    return patrones


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
        # timestamp entra en la búsqueda directa para que funcionen tanto el
        # formato almacenado (2026-08-03) como una hora suelta (16:54)
        condiciones = ["target LIKE ?", "details LIKE ?", "action LIKE ?",
                       "username LIKE ?", "timestamp LIKE ?"]
        parametros += [patron] * 5

        if acciones_coincidentes:
            marcadores = ",".join("?" * len(acciones_coincidentes))
            condiciones.append(f"action IN ({marcadores})")
            parametros += acciones_coincidentes

        # Fechas escritas como se muestran en pantalla
        for fragmento in _patrones_de_fecha(search):
            condiciones.append("timestamp LIKE ?")
            parametros.append(f"%{fragmento}%")

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
    """Acciones disponibles para el desplegable de filtrado.

    Combina las acciones definidas en ACTION_LABELS (siempre visibles) con
    cualquier acción desconocida que ya exista en la base de datos, para que el
    desplegable esté completo desde el primer uso y no dependa de que haya al
    menos un registro de cada tipo.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
    en_bd = {r["action"] for r in cursor.fetchall()}
    conn.close()

    # Unir las definidas + las que estén en BD pero no en el diccionario
    todas = set(ACTION_LABELS.keys()) | en_bd
    return sorted(
        [{"key": a, "label": ACTION_LABELS.get(a, a)} for a in todas],
        key=lambda x: x["label"]
    )
