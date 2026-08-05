"""
auth.py
-------
Usuarios, roles, permisos y control de sesión.

Roles
─────
    admin      Acceso total: cámaras, configuración, usuarios y borrados.
    operador   Solo visualización y alta de registros. No puede editar ni
               eliminar nada, ni cambiar el país ni el modo de detección.

Permisos
────────
El rol decide qué se puede tocar de la configuración; los permisos afinan, para
cada operador por separado, el acceso al material grabado: ver grabaciones y
exportar vídeo se conceden por cuenta. El administrador los tiene todos.

Caducidad de sesión
───────────────────
La sesión NO caduca por inactividad general, sino por tiempo **sin visualizar
ningún stream**. Mientras una cámara esté abierta en la cuadrícula, la sesión se
mantiene viva indefinidamente; en cuanto se cierran todas, empieza la cuenta
atrás. El administrador define ese plazo.

Esto se implementa marcando la actividad desde el propio generador de vídeo: por
cada fotograma servido se actualiza la marca de tiempo de la sesión.
"""

import functools
import hashlib
import hmac
import os
import secrets
import time

from flask import session, jsonify, redirect, request, url_for

from database import get_connection

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operador"
VALID_ROLES = (ROLE_ADMIN, ROLE_OPERATOR)

ROLE_LABELS = {
    ROLE_ADMIN: "Administrador",
    ROLE_OPERATOR: "Operador",
}

# ---------------------------------------------------------------------------
# Permisos por usuario
#
# El rol define lo que se puede hacer con la configuración del sistema; estos
# permisos afinan, operador por operador, el acceso al material grabado. Son
# dos cosas distintas: alguien puede necesitar vigilar cámaras en directo sin
# tener por qué rebobinar lo de ayer, y ver una grabación no es lo mismo que
# poder llevarse una copia en un archivo.
#
# El administrador los tiene todos por definición y no se le pueden quitar:
# quien gestiona el sistema puede devolvérselos en cualquier momento, así que
# restringirle solo daría una falsa sensación de control.
# ---------------------------------------------------------------------------
PERM_VIEW_RECORDINGS = "recordings.view"
PERM_EXPORT_RECORDINGS = "recordings.export"

PERMISSION_LABELS = {
    PERM_VIEW_RECORDINGS: "Ver grabaciones",
    PERM_EXPORT_RECORDINGS: "Exportar vídeo",
}
VALID_PERMISSIONS = tuple(PERMISSION_LABELS)

# Un operador nuevo puede consultar grabaciones, pero no sacarlas del sistema.
# Exportar produce un archivo que ya vive fuera de la aplicación, donde ni la
# retención ni la auditoría alcanzan, así que se concede a mano.
DEFAULT_OPERATOR_PERMISSIONS = (PERM_VIEW_RECORDINGS,)


def parse_permissions(valor) -> set:
    """
    Convierte la columna `permissions` en un conjunto.

    Un valor nulo corresponde a una fila anterior a la migración y se
    interpreta como "todos": esas cuentas venían de una versión sin control de
    permisos, donde nadie tenía restringido nada.
    """
    if valor is None:
        return set(VALID_PERMISSIONS)
    return {p for p in str(valor).split(",") if p in VALID_PERMISSIONS}


def format_permissions(permisos) -> str:
    """Serializa un conjunto de permisos para guardarlo, descartando los desconocidos."""
    return ",".join(p for p in VALID_PERMISSIONS if p in set(permisos or ()))


def user_permissions(user) -> set:
    """Permisos efectivos de un usuario, contando que el administrador los tiene todos."""
    if not user:
        return set()
    if user.get("role") == ROLE_ADMIN:
        return set(VALID_PERMISSIONS)
    return parse_permissions(user.get("permissions"))

# ---------------------------------------------------------------------------
# Hash de contraseñas
#
# PBKDF2-HMAC-SHA256 de la biblioteca estándar: evita añadir una dependencia
# como bcrypt manteniendo un coste de cálculo razonable frente a fuerza bruta.
# ---------------------------------------------------------------------------
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Devuelve el hash almacenable de una contraseña, con sal aleatoria."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Comprueba una contraseña contra su hash almacenado."""
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iterations))
        # compare_digest evita filtrar información por el tiempo de comparación
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Clave de firma de la cookie de sesión
# ---------------------------------------------------------------------------
_SECRET_FILE = os.path.join(os.path.dirname(__file__), ".flask_secret")


def get_secret_key() -> bytes:
    """
    Devuelve la clave con la que Flask firma la cookie de sesión.

    Se guarda en disco en lugar de generarse en cada arranque para que las
    sesiones abiertas sobrevivan a un reinicio del servidor. El archivo queda
    fuera del control de versiones.
    """
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "rb") as fh:
            key = fh.read().strip()
            if key:
                return key

    key = secrets.token_hex(32).encode("ascii")
    with open(_SECRET_FILE, "wb") as fh:
        fh.write(key)
    return key


# ---------------------------------------------------------------------------
# Tabla de usuarios
# ---------------------------------------------------------------------------
DEFAULT_ADMIN_USER = "Admin"
DEFAULT_ADMIN_PASSWORD = "1234"


def init_users():
    """
    Crea la tabla de usuarios y siembra el administrador inicial.

    El administrador por defecto solo se crea si NO existe ningún usuario, de
    modo que no reaparece después de que se le cambie la contraseña o se le
    renombre.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operador',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

    # Los permisos se añadieron después, así que la columna puede faltar en
    # bases de datos ya existentes.
    cursor.execute("PRAGMA table_info(users)")
    columnas = {c["name"] for c in cursor.fetchall()}
    if "permissions" not in columnas:
        cursor.execute("ALTER TABLE users ADD COLUMN permissions TEXT")
        # A los operadores que ya existían se les conceden todos los permisos.
        # Hasta ahora podían ver y exportar grabaciones porque no había nada
        # que se lo impidiese; estrenar el control quitándoles acceso de golpe
        # rompería su trabajo sin que nadie lo hubiera decidido.
        cursor.execute("UPDATE users SET permissions = ?",
                       (format_permissions(VALID_PERMISSIONS),))
        conn.commit()

    cursor.execute("SELECT COUNT(*) AS n FROM users")
    if cursor.fetchone()["n"] == 0:
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (DEFAULT_ADMIN_USER, hash_password(DEFAULT_ADMIN_PASSWORD), ROLE_ADMIN)
        )
        conn.commit()
        print(f"[Auth] Usuario administrador inicial creado: "
              f"{DEFAULT_ADMIN_USER} / {DEFAULT_ADMIN_PASSWORD}")

    conn.close()


# ---------------------------------------------------------------------------
# Preferencias por usuario
#
# A diferencia de los ajustes de config.json, que son globales y afectan a todo
# el sistema, estas preferencias son de cada cuenta: dos personas conectadas a
# la vez pueden ver la interfaz con temas distintos.
# ---------------------------------------------------------------------------
THEME_DARK = "dark"
THEME_LIGHT = "light"
VALID_THEMES = (THEME_DARK, THEME_LIGHT)

DEFAULT_PREFERENCES = {"theme": THEME_DARK}


def init_preferences():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            theme TEXT NOT NULL DEFAULT 'dark'
        )
    ''')
    conn.commit()
    conn.close()


def get_preferences(user_id: int) -> dict:
    """
    Preferencias del usuario, completadas con los valores por defecto.

    Un usuario que nunca haya tocado la personalización no tiene fila propia;
    en ese caso se devuelven los valores por defecto sin crearla.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT theme FROM user_preferences WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return dict(DEFAULT_PREFERENCES)
    return {**DEFAULT_PREFERENCES, "theme": row["theme"]}


def save_preferences(user_id: int, theme: str):
    """
    Guarda las preferencias del usuario.

    Returns:
        error (str) o None si fue bien.
    """
    if theme not in VALID_THEMES:
        return f"Tema no válido: {theme}"

    conn = get_connection()
    cursor = conn.cursor()
    # UPSERT: la fila puede no existir todavía si es la primera vez que el
    # usuario cambia algo de la personalización.
    cursor.execute('''
        INSERT INTO user_preferences (user_id, theme) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET theme = excluded.theme
    ''', (user_id, theme))
    conn.commit()
    conn.close()
    return None


def delete_preferences(user_id: int):
    """Elimina las preferencias de un usuario borrado, para no dejar huérfanas."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_by_name(username: str):
    """Busca un usuario por nombre, sin distinguir mayúsculas."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                   (username.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    """Todos los usuarios, sin exponer los hashes de contraseña."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, permissions, created_at FROM users "
                   "ORDER BY role, username")
    rows = cursor.fetchall()
    conn.close()

    usuarios = []
    for row in rows:
        u = dict(row)
        # Se devuelven los permisos ya resueltos: la interfaz marca las
        # casillas del administrador sin tener que replicar la regla de que
        # los tiene todos.
        u["permissions"] = sorted(user_permissions(u))
        usuarios.append(u)
    return usuarios


def create_user(username: str, password: str, role: str, permissions=None):
    """
    Crea un usuario.

    Args:
        permissions: iterable de permisos; si es None se usan los de partida
                     para operadores.

    Returns:
        (user_id, error) — uno de los dos siempre es None.
    """
    username = (username or "").strip()
    if not username:
        return None, "El nombre de usuario no puede estar vacío"
    if not password:
        return None, "La contraseña no puede estar vacía"
    if role not in VALID_ROLES:
        return None, f"Rol no válido: {role}"
    if get_user_by_name(username):
        return None, f'El usuario "{username}" ya existe'

    if permissions is None:
        permissions = DEFAULT_OPERATOR_PERMISSIONS

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, role, permissions) "
        "VALUES (?, ?, ?, ?)",
        (username, hash_password(password), role, format_permissions(permissions))
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id, None


def update_user(user_id: int, role: str = None, password: str = None,
                permissions=None):
    """
    Cambia el rol, la contraseña o los permisos de un usuario.

    Cada argumento que llegue como None se deja como estaba, de modo que
    cambiar solo la contraseña no toca los permisos y viceversa.

    Returns:
        error (str) o None si fue bien.
    """
    user = get_user(user_id)
    if not user:
        return "El usuario no existe"

    if role is not None:
        if role not in VALID_ROLES:
            return f"Rol no válido: {role}"
        # Impedir quedarse sin ningún administrador: sin él, nadie podría
        # volver a gestionar usuarios ni la configuración.
        if user["role"] == ROLE_ADMIN and role != ROLE_ADMIN and count_admins() <= 1:
            return "No se puede quitar el rol al último administrador"

    conn = get_connection()
    cursor = conn.cursor()
    if role is not None:
        cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    if password:
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (hash_password(password), user_id))
    if permissions is not None:
        cursor.execute("UPDATE users SET permissions = ? WHERE id = ?",
                       (format_permissions(permissions), user_id))
    conn.commit()
    conn.close()
    return None


def delete_user(user_id: int):
    """Elimina un usuario. Devuelve un error si dejaría el sistema sin admin."""
    user = get_user(user_id)
    if not user:
        return "El usuario no existe"
    if user["role"] == ROLE_ADMIN and count_admins() <= 1:
        return "No se puede eliminar al último administrador"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    # Sin esto, las preferencias quedarían huérfanas y un usuario nuevo podría
    # heredarlas al reutilizarse el identificador.
    delete_preferences(user_id)
    return None


def count_admins() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?", (ROLE_ADMIN,))
    n = cursor.fetchone()["n"]
    conn.close()
    return n


# ---------------------------------------------------------------------------
# Actividad de streams y caducidad de sesión
# ---------------------------------------------------------------------------

# {sid: marca de tiempo del último fotograma servido a esa sesión}
#
# Vive en memoria a propósito: al reiniciar el servidor no queda actividad que
# recordar, y todas las sesiones arrancan su cuenta atrás de nuevo.
_STREAM_ACTIVITY = {}


def touch_stream_activity(sid: str):
    """Marca que esta sesión está recibiendo vídeo ahora mismo."""
    if sid:
        _STREAM_ACTIVITY[sid] = time.time()


def seconds_since_stream(sid: str) -> float:
    """
    Segundos transcurridos desde el último fotograma servido a esta sesión.

    Si nunca ha visto ninguno, se cuenta desde el inicio de sesión.
    """
    last = _STREAM_ACTIVITY.get(sid)
    if last is None:
        last = session.get("login_time", time.time())
    return time.time() - last


def forget_session(sid: str):
    _STREAM_ACTIVITY.pop(sid, None)


def start_session(user: dict):
    """Registra al usuario en la sesión tras un inicio correcto."""
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["sid"] = secrets.token_hex(16)
    session["login_time"] = time.time()
    session.permanent = True
    touch_stream_activity(session["sid"])


def end_session():
    forget_session(session.get("sid"))
    session.clear()


def current_user():
    """Datos del usuario de la sesión actual, o None si no hay sesión."""
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "username": session.get("username", ""),
        "role": session.get("role", ROLE_OPERATOR),
    }


def is_admin() -> bool:
    return session.get("role") == ROLE_ADMIN


def current_permissions() -> set:
    """
    Permisos del usuario conectado, consultados en la base de datos.

    No se guardan en la sesión a propósito. Si el administrador le retira un
    permiso a alguien que está conectado, el cambio debe surtir efecto en la
    siguiente petición y no cuando esa persona vuelva a entrar.
    """
    if is_admin():
        return set(VALID_PERMISSIONS)
    uid = session.get("user_id")
    if uid is None:
        return set()
    return user_permissions(get_user(uid))


def has_permission(permiso: str) -> bool:
    return permiso in current_permissions()


def _wants_json() -> bool:
    """
    True si la petición espera JSON en lugar de una redirección.

    Las llamadas de la API deben recibir 401 para que el frontend reaccione;
    la navegación normal debe ir a la pantalla de inicio de sesión.
    """
    return (request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json")


def login_required(view):
    """Exige sesión iniciada y no caducada."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if _wants_json():
                return jsonify({"status": "error", "code": "unauthenticated",
                                "message": "Sesión no iniciada"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Exige sesión iniciada y rol de administrador."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if _wants_json():
                return jsonify({"status": "error", "code": "unauthenticated",
                                "message": "Sesión no iniciada"}), 401
            return redirect(url_for("login"))
        if not is_admin():
            return jsonify({
                "status": "error", "code": "forbidden",
                "message": "Esta acción requiere permisos de administrador",
            }), 403
        return view(*args, **kwargs)
    return wrapped


def permission_required(permiso: str):
    """
    Exige sesión iniciada y un permiso concreto.

    Se comprueba en el servidor además de ocultar los botones en la interfaz:
    esconder un botón no impide llamar a la URL a mano.
    """
    def decorador(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                if _wants_json():
                    return jsonify({"status": "error", "code": "unauthenticated",
                                    "message": "Sesión no iniciada"}), 401
                return redirect(url_for("login"))
            if not has_permission(permiso):
                etiqueta = PERMISSION_LABELS.get(permiso, permiso)
                return jsonify({
                    "status": "error", "code": "forbidden",
                    "message": f'Su cuenta no tiene el permiso "{etiqueta}"',
                }), 403
            return view(*args, **kwargs)
        return wrapped
    return decorador
