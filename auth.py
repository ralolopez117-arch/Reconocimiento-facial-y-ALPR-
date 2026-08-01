"""
auth.py
-------
Usuarios, roles y control de sesión.

Roles
─────
    admin      Acceso total: cámaras, configuración, usuarios y borrados.
    operador   Solo visualización y alta de registros. No puede editar ni
               eliminar nada, ni cambiar el país ni el modo de detección.

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
    cursor.execute("SELECT id, username, role, created_at FROM users "
                   "ORDER BY role, username")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def create_user(username: str, password: str, role: str):
    """
    Crea un usuario.

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

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hash_password(password), role)
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id, None


def update_user(user_id: int, role: str = None, password: str = None):
    """
    Cambia el rol o la contraseña de un usuario.

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
