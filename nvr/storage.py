"""
nvr/storage.py
--------------
Índice de segmentos grabados y política de retención.

Los archivos los escribe ffmpeg directamente; aquí se recorren, se anotan en un
índice y se aplica la caducidad. Indexar escaneando el disco en lugar de
apuntar cada archivo al crearlo tiene una ventaja concreta: si el servicio se
cae o se reinicia a media grabación, al volver reconstruye el estado real de lo
que hay en disco en vez de arrastrar un índice desincronizado.
"""

import datetime
import os
import re
import sqlite3
import threading

from .config import load_config, get_storage_path

DB_LOCK = threading.Lock()

# Los nombres los genera ffmpeg con strftime: 2026-08-03_16-05-00.mp4
PATRON_NOMBRE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.mp4$")


def _db_path():
    return os.path.join(get_storage_path(), "nvr_index.db")


def get_connection():
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            day TEXT NOT NULL,
            filename TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration REAL,
            size_bytes INTEGER,
            UNIQUE(camera_id, filename)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_seg_cam_day ON segments(camera_id, day)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_seg_started ON segments(camera_id, started_at)')
    conn.commit()
    conn.close()


def camera_dir(camera_id: str) -> str:
    ruta = os.path.join(get_storage_path(), camera_id)
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _parse_nombre(nombre: str):
    """Fecha y hora de inicio a partir del nombre del archivo."""
    m = PATRON_NOMBRE.match(nombre)
    if not m:
        return None
    a, me, d, h, mi, s = (int(x) for x in m.groups())
    try:
        return datetime.datetime(a, me, d, h, mi, s)
    except ValueError:
        return None


def scan_camera(camera_id: str):
    """
    Recorre los archivos de una cámara y actualiza el índice.

    Returns:
        Número de segmentos nuevos anotados.
    """
    carpeta = camera_dir(camera_id)
    try:
        archivos = sorted(f for f in os.listdir(carpeta) if f.endswith(".mp4"))
    except OSError:
        return 0

    # El último archivo suele ser el que ffmpeg está escribiendo ahora mismo.
    # Indexarlo daría una duración y un tamaño que cambian bajo los pies, así
    # que se deja para la siguiente pasada, cuando ya esté cerrado.
    if archivos:
        archivos = archivos[:-1]
    if not archivos:
        return 0

    with DB_LOCK:
        conn = get_connection()
        c = conn.cursor()
        existentes = {r["filename"] for r in c.execute(
            "SELECT filename FROM segments WHERE camera_id = ?", (camera_id,))}

        nuevos = 0
        for i, nombre in enumerate(archivos):
            if nombre in existentes:
                continue
            inicio = _parse_nombre(nombre)
            if inicio is None:
                continue

            ruta = os.path.join(carpeta, nombre)
            try:
                tam = os.path.getsize(ruta)
            except OSError:
                continue
            if tam == 0:
                continue

            # La duración se deduce del inicio del siguiente segmento, que es
            # exacto y no cuesta nada. Solo para el último se recurre a la
            # fecha de modificación del archivo.
            fin = None
            if i + 1 < len(archivos):
                fin = _parse_nombre(archivos[i + 1])
            if fin is None:
                try:
                    fin = datetime.datetime.fromtimestamp(os.path.getmtime(ruta))
                except OSError:
                    fin = None

            duracion = (fin - inicio).total_seconds() if fin else None
            # Un salto absurdo indica que la grabación se cortó: se descarta la
            # duración en vez de dibujar una franja falsa en la línea de tiempo.
            if duracion is not None and (duracion <= 0 or duracion > 3600):
                duracion = None
                fin = None

            c.execute(
                '''INSERT OR IGNORE INTO segments
                   (camera_id, day, filename, started_at, ended_at, duration, size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (camera_id, inicio.strftime("%Y-%m-%d"), nombre,
                 inicio.strftime("%Y-%m-%d %H:%M:%S"),
                 fin.strftime("%Y-%m-%d %H:%M:%S") if fin else None,
                 duracion, tam))
            nuevos += 1

        # Retirar del índice lo que ya no está en disco, por si se borró a mano
        if existentes:
            en_disco = set(archivos) | ({archivos[-1]} if archivos else set())
            try:
                en_disco = set(f for f in os.listdir(carpeta) if f.endswith(".mp4"))
            except OSError:
                pass
            huerfanos = existentes - en_disco
            for nombre in huerfanos:
                c.execute("DELETE FROM segments WHERE camera_id = ? AND filename = ?",
                          (camera_id, nombre))

        conn.commit()
        conn.close()
    return nuevos


def list_days(camera_id: str):
    """Días con grabación, del más reciente al más antiguo."""
    conn = get_connection()
    filas = conn.execute(
        '''SELECT day, COUNT(*) AS segmentos, SUM(size_bytes) AS bytes,
                  SUM(duration) AS segundos
           FROM segments WHERE camera_id = ?
           GROUP BY day ORDER BY day DESC''', (camera_id,)).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def list_segments(camera_id: str, day: str = None,
                  desde: str = None, hasta: str = None):
    """Segmentos de una cámara, opcionalmente acotados por día o intervalo."""
    consulta = "SELECT * FROM segments WHERE camera_id = ?"
    params = [camera_id]
    if day:
        consulta += " AND day = ?"
        params.append(day)
    if desde:
        consulta += " AND started_at >= ?"
        params.append(desde)
    if hasta:
        consulta += " AND started_at <= ?"
        params.append(hasta)
    consulta += " ORDER BY started_at"

    conn = get_connection()
    filas = conn.execute(consulta, params).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def get_segment(segment_id: int):
    conn = get_connection()
    fila = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
    conn.close()
    return dict(fila) if fila else None


def find_segment_at(camera_id: str, momento: str):
    """
    Segmento que contiene un instante dado, para saltar a una hora concreta.

    Si ninguno lo contiene —por un hueco sin grabación— se devuelve el
    siguiente disponible, que es lo que espera quien arrastra la línea de
    tiempo hasta un tramo vacío.
    """
    conn = get_connection()
    fila = conn.execute(
        '''SELECT * FROM segments
           WHERE camera_id = ? AND started_at <= ?
             AND (ended_at IS NULL OR ended_at >= ?)
           ORDER BY started_at DESC LIMIT 1''',
        (camera_id, momento, momento)).fetchone()
    if fila is None:
        fila = conn.execute(
            '''SELECT * FROM segments WHERE camera_id = ? AND started_at >= ?
               ORDER BY started_at LIMIT 1''', (camera_id, momento)).fetchone()
    conn.close()
    return dict(fila) if fila else None


def storage_stats():
    """Uso de disco por cámara y total."""
    conn = get_connection()
    filas = conn.execute(
        '''SELECT camera_id, COUNT(*) AS segmentos, SUM(size_bytes) AS bytes,
                  MIN(started_at) AS desde, MAX(started_at) AS hasta,
                  COUNT(DISTINCT day) AS dias
           FROM segments GROUP BY camera_id''').fetchall()
    conn.close()
    por_camara = [dict(f) for f in filas]
    total = sum((c["bytes"] or 0) for c in por_camara)

    ruta = get_storage_path()
    try:
        import shutil as _sh
        uso = _sh.disk_usage(ruta)
        libre, capacidad = uso.free, uso.total
    except Exception:
        libre = capacidad = None

    return {
        "cameras": por_camara,
        "total_bytes": total,
        "disk_free_bytes": libre,
        "disk_total_bytes": capacidad,
        "storage_path": ruta,
    }


# ---------------------------------------------------------------------------
# Retención
# ---------------------------------------------------------------------------
def apply_retention(camera_id: str, retention_days: int):
    """
    Borra los días que exceden la retención, del más antiguo hacia adelante.

    No se vacía todo al superar el plazo: se elimina únicamente el día más
    antiguo cada vez, de modo que la grabación nueva va sustituyendo a la
    vieja y siempre quedan disponibles los últimos N días completos.

    Returns:
        (días_borrados, bytes_liberados)
    """
    if retention_days <= 0:
        return 0, 0

    dias = list_days(camera_id)          # del más reciente al más antiguo
    if len(dias) <= retention_days:
        return 0, 0

    sobrantes = dias[retention_days:]    # los más antiguos
    borrados, liberados = 0, 0
    for info in sobrantes:
        liberados += _borrar_dia(camera_id, info["day"])
        borrados += 1
    return borrados, liberados


def enforce_global_limit(max_total_gb: float):
    """
    Respeta el tope global de disco borrando el día más antiguo de todos.

    Sin este límite, configurar una retención larga en varias cámaras llenaría
    la unidad y la grabación se detendría sin aviso. Se recorta la cámara que
    tenga el día más antiguo, para repartir el sacrificio de forma natural.

    Returns:
        (días_borrados, bytes_liberados)
    """
    if max_total_gb <= 0:
        return 0, 0

    limite = max_total_gb * 1024 ** 3
    borrados, liberados = 0, 0

    for _ in range(500):                 # tope de seguridad
        stats = storage_stats()
        if stats["total_bytes"] <= limite:
            break

        conn = get_connection()
        fila = conn.execute(
            '''SELECT camera_id, day FROM segments
               GROUP BY camera_id, day ORDER BY day ASC LIMIT 1''').fetchone()
        conn.close()
        if fila is None:
            break

        liberados += _borrar_dia(fila["camera_id"], fila["day"])
        borrados += 1

    return borrados, liberados


def _borrar_dia(camera_id: str, day: str) -> int:
    """Borra los archivos de un día y sus filas del índice."""
    carpeta = camera_dir(camera_id)
    liberados = 0

    with DB_LOCK:
        conn = get_connection()
        filas = conn.execute(
            "SELECT filename, size_bytes FROM segments WHERE camera_id = ? AND day = ?",
            (camera_id, day)).fetchall()

        for f in filas:
            try:
                os.remove(os.path.join(carpeta, f["filename"]))
                liberados += f["size_bytes"] or 0
            except FileNotFoundError:
                pass          # ya no estaba: se limpia el índice igualmente
            except OSError as e:
                # Un archivo en uso no debe abortar la limpieza del resto
                print(f"[NVR] No se pudo borrar {f['filename']}: {e}")

        conn.execute("DELETE FROM segments WHERE camera_id = ? AND day = ?",
                     (camera_id, day))
        conn.commit()
        conn.close()

    print(f"[NVR] Día {day} de {camera_id} eliminado "
          f"({liberados / 1024**2:.0f} MB liberados)")
    return liberados
