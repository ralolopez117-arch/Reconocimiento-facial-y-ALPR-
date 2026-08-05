"""
live_views.py
-------------
Disposición de la cuadrícula de vídeo en directo, por usuario.

Dos cosas distintas que conviene no confundir:

    Vista actual   Lo que el usuario tiene delante ahora mismo. Se guarda sola
                   cada vez que cambia, para devolvérsela tal cual al volver a
                   entrar. No tiene nombre y solo hay una.

    Vistas guardadas  Composiciones con nombre que el usuario crea a mano
                   ("Entradas", "Perímetro"…) para saltar entre ellas sin
                   volver a arrastrar cámara por cámara.

En ambos casos se guarda la misma información: cuántas celdas tiene la
cuadrícula y qué cámara va en cada una, incluidas las vacías. La posición
importa: recolocar las cámaras al restaurar destruiría precisamente lo que el
usuario se molestó en ordenar.

Las cámaras se guardan por identificador. Si una se borra del sistema, su hueco
queda vacío al restaurar en vez de invalidar la vista entera.
"""

import json

from database import get_connection

# Distribuciones que ofrece la interfaz. Guardar cualquier otra cosa dejaría
# una vista que no se puede reproducir con los botones disponibles.
LAYOUTS_VALIDOS = (1, 4, 6, 8)

# Tope de vistas por usuario. Sin límite, un fallo del navegador que reenviara
# la petición en bucle llenaría la tabla.
MAX_VISTAS = 20

MAX_NOMBRE = 40


def init_live_views():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_layout (
            user_id INTEGER PRIMARY KEY,
            layout INTEGER NOT NULL,
            cameras TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            layout INTEGER NOT NULL,
            cameras TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_vistas_usuario '
                   'ON live_views(user_id, id)')
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------
def normalizar(layout, camaras, ids_validos=None):
    """
    Comprueba y ajusta una disposición.

    Args:
        layout:      número de celdas
        camaras:     lista de identificadores; None en las celdas vacías
        ids_validos: si se indica, las cámaras que no estén ahí se descartan.
                     Sirve para no guardar referencias a cámaras ya borradas.

    Returns:
        (layout, camaras, error) — error es None si todo fue bien.
    """
    try:
        layout = int(layout)
    except (TypeError, ValueError):
        return None, None, "La distribución debe ser un número"
    if layout not in LAYOUTS_VALIDOS:
        return None, None, (f"Distribución no válida: {layout}. "
                            f"Se admiten {', '.join(map(str, LAYOUTS_VALIDOS))}")

    if not isinstance(camaras, list):
        return None, None, "Se esperaba una lista de cámaras"

    # Se recorta o se rellena hasta cuadrar con el número de celdas: así la
    # lista y la cuadrícula no pueden desincronizarse al restaurar.
    limpias = []
    for i in range(layout):
        cid = camaras[i] if i < len(camaras) else None
        cid = str(cid).strip() if cid else None
        if cid and ids_validos is not None and cid not in ids_validos:
            cid = None
        limpias.append(cid or None)

    return layout, limpias, None


# ---------------------------------------------------------------------------
# Vista actual
# ---------------------------------------------------------------------------
def get_layout(user_id: int):
    """La última disposición del usuario, o None si nunca guardó ninguna."""
    conn = get_connection()
    fila = conn.execute(
        "SELECT layout, cameras FROM live_layout WHERE user_id = ?",
        (user_id,)).fetchone()
    conn.close()
    if fila is None:
        return None
    try:
        camaras = json.loads(fila["cameras"])
    except (ValueError, TypeError):
        return None
    return {"layout": fila["layout"], "cameras": camaras}


def save_layout(user_id: int, layout, camaras, ids_validos=None):
    """Guarda la disposición actual. Devuelve un error o None."""
    layout, camaras, error = normalizar(layout, camaras, ids_validos)
    if error:
        return error

    conn = get_connection()
    conn.execute('''
        INSERT INTO live_layout (user_id, layout, cameras, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            layout = excluded.layout,
            cameras = excluded.cameras,
            updated_at = CURRENT_TIMESTAMP
    ''', (user_id, layout, json.dumps(camaras)))
    conn.commit()
    conn.close()
    return None


# ---------------------------------------------------------------------------
# Vistas guardadas
# ---------------------------------------------------------------------------
def list_views(user_id: int):
    conn = get_connection()
    filas = conn.execute(
        "SELECT id, name, layout, cameras FROM live_views "
        "WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    conn.close()

    vistas = []
    for f in filas:
        try:
            camaras = json.loads(f["cameras"])
        except (ValueError, TypeError):
            camaras = []
        vistas.append({"id": f["id"], "name": f["name"],
                       "layout": f["layout"], "cameras": camaras,
                       # Para la interfaz: cuántas celdas van ocupadas
                       "used": sum(1 for c in camaras if c)})
    return vistas


def _validar_nombre(user_id, nombre, excluir_id=None):
    nombre = (nombre or "").strip()
    if not nombre:
        return None, "La vista necesita un nombre"
    if len(nombre) > MAX_NOMBRE:
        return None, f"El nombre no puede pasar de {MAX_NOMBRE} caracteres"

    conn = get_connection()
    consulta = ("SELECT id FROM live_views WHERE user_id = ? "
                "AND name = ? COLLATE NOCASE")
    parametros = [user_id, nombre]
    if excluir_id is not None:
        consulta += " AND id != ?"
        parametros.append(excluir_id)
    existe = conn.execute(consulta, parametros).fetchone()
    conn.close()

    if existe:
        return None, f'Ya tienes una vista llamada "{nombre}"'
    return nombre, None


def create_view(user_id: int, nombre, layout, camaras, ids_validos=None):
    """
    Crea una vista guardada.

    Returns:
        (id, error) — uno de los dos siempre es None.
    """
    nombre, error = _validar_nombre(user_id, nombre)
    if error:
        return None, error

    layout, camaras, error = normalizar(layout, camaras, ids_validos)
    if error:
        return None, error

    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS n FROM live_views WHERE user_id = ?",
                         (user_id,)).fetchone()["n"]
    if total >= MAX_VISTAS:
        conn.close()
        return None, f"No puedes guardar más de {MAX_VISTAS} vistas"

    cursor = conn.execute(
        "INSERT INTO live_views (user_id, name, layout, cameras) VALUES (?, ?, ?, ?)",
        (user_id, nombre, layout, json.dumps(camaras)))
    vista_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return vista_id, None


def update_view(user_id: int, view_id: int, nombre=None, layout=None,
                camaras=None, ids_validos=None):
    """
    Renombra una vista, sustituye su contenido, o ambas cosas.

    Se filtra por user_id además de por id: sin eso, cualquiera podría editar
    las vistas de otro sin más que acertar el número.
    """
    conn = get_connection()
    actual = conn.execute(
        "SELECT id FROM live_views WHERE id = ? AND user_id = ?",
        (view_id, user_id)).fetchone()
    conn.close()
    if actual is None:
        return "Esa vista no existe"

    if nombre is not None:
        nombre, error = _validar_nombre(user_id, nombre, excluir_id=view_id)
        if error:
            return error

    if layout is not None or camaras is not None:
        if layout is None or camaras is None:
            return "Para cambiar el contenido hacen falta la distribución y las cámaras"
        layout, camaras, error = normalizar(layout, camaras, ids_validos)
        if error:
            return error

    conn = get_connection()
    if nombre is not None:
        conn.execute("UPDATE live_views SET name = ? WHERE id = ? AND user_id = ?",
                     (nombre, view_id, user_id))
    if layout is not None:
        conn.execute(
            "UPDATE live_views SET layout = ?, cameras = ? WHERE id = ? AND user_id = ?",
            (layout, json.dumps(camaras), view_id, user_id))
    conn.commit()
    conn.close()
    return None


def delete_view(user_id: int, view_id: int):
    conn = get_connection()
    cursor = conn.execute("DELETE FROM live_views WHERE id = ? AND user_id = ?",
                          (view_id, user_id))
    borradas = cursor.rowcount
    conn.commit()
    conn.close()
    return None if borradas else "Esa vista no existe"


def delete_user_data(user_id: int):
    """Limpia lo guardado por un usuario que se elimina del sistema."""
    conn = get_connection()
    conn.execute("DELETE FROM live_views WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM live_layout WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
