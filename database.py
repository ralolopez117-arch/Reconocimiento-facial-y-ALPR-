import sqlite3
import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "alpr_data.db")

def get_connection():
    # check_same_thread=False is needed if multiple threads access the connection,
    # though it's safer to open/close connection per thread.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            plate_text TEXT NOT NULL,
            confidence REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Facial Recognition Tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS known_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            embedding TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            name TEXT NOT NULL,
            confidence REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Plate Watchlist Tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watched_plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_pattern TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plate_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            plate_text TEXT NOT NULL,
            matched_pattern TEXT NOT NULL,
            confidence REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    _migrate_schema()

def _migrate_schema():
    """
    Aplica cambios de esquema sobre bases de datos ya existentes.

    CREATE TABLE IF NOT EXISTS no añade columnas nuevas a una tabla que ya
    existe, así que las incorporaciones posteriores se hacen aquí. Cada paso
    comprueba primero si la columna está para poder ejecutarse en cada arranque
    sin efectos.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(watched_plates)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "plate_type" not in columns:
        # Las entradas anteriores a esta columna no tienen tipo asignado, y
        # 'any' es justamente el valor que no impone restricción, así que
        # conservan su comportamiento original.
        cursor.execute(
            "ALTER TABLE watched_plates ADD COLUMN plate_type TEXT DEFAULT 'any'"
        )

    if "country" not in columns:
        # País vigente cuando se registró la placa. Permite validar el tipo
        # aunque después se cambie el país global en la configuración.
        cursor.execute(
            "ALTER TABLE watched_plates ADD COLUMN country TEXT DEFAULT ''"
        )

    conn.commit()
    conn.close()

def insert_plate(camera_id, plate_text, confidence):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO plates (camera_id, plate_text, confidence, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (camera_id, plate_text, confidence, now))
    conn.commit()
    conn.close()

def search_plates(query, limit=50):
    conn = get_connection()
    cursor = conn.cursor()
    
    if query:
        search_term = f"%{query}%"
        cursor.execute('''
            SELECT * FROM plates 
            WHERE plate_text LIKE ? OR camera_id LIKE ? OR timestamp LIKE ?
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (search_term, search_term, search_term, limit))
    else:
        cursor.execute('''
            SELECT * FROM plates 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (limit,))
        
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_latest_plates(limit=10):
    return search_plates("", limit)

# --- Facial Recognition Database Functions ---

def insert_known_face(name, embedding_json):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO known_faces (name, embedding)
        VALUES (?, ?)
    ''', (name, embedding_json))
    face_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return face_id

def update_known_face_full(face_id, name, embedding_json):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE known_faces SET name = ?, embedding = ? WHERE id = ?', (name, embedding_json, face_id))
    conn.commit()
    conn.close()

def get_all_known_faces():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM known_faces')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def rename_known_face(face_id, new_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE known_faces SET name = ? WHERE id = ?', (new_name, face_id))
    conn.commit()
    conn.close()

def delete_known_face(face_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM known_faces WHERE id = ?', (face_id,))
    conn.commit()
    conn.close()

def delete_all_known_faces():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM known_faces')
    conn.commit()
    conn.close()

def insert_face_detection(camera_id, name, confidence):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO face_detections (camera_id, name, confidence, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (camera_id, name, confidence, now))
    conn.commit()
    conn.close()

def get_latest_face_detections(limit=10):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM face_detections 
        ORDER BY timestamp DESC 
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# --- Plate Watchlist Functions ---

def get_all_watched_plates():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM watched_plates ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def insert_watched_plate(plate_pattern, note='', plate_type='any', country=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO watched_plates (plate_pattern, note, plate_type, country)
           VALUES (?, ?, ?, ?)''',
        (plate_pattern.upper().strip(), note, plate_type, country)
    )
    conn.commit()
    conn.close()

def update_watched_plate(plate_id, plate_pattern, note='', plate_type='any', country=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''UPDATE watched_plates
           SET plate_pattern = ?, note = ?, plate_type = ?, country = ?
           WHERE id = ?''',
        (plate_pattern.upper().strip(), note, plate_type, country, plate_id)
    )
    conn.commit()
    conn.close()

def delete_watched_plate(plate_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM watched_plates WHERE id = ?', (plate_id,))
    conn.commit()
    conn.close()

def delete_all_watched_plates():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM watched_plates')
    conn.commit()
    conn.close()

def check_plate_against_watchlist(plate_text):
    """
    Devuelve la entrada de la lista de vigilancia que coincide, o None.

    La coincidencia es EXACTA salvo que el patrón registrado incluya comodines:

        ABC123    coincide solo con ABC123
        ABC*      coincide con cualquier placa que empiece por ABC
        ABC1?3    ? sustituye a un único carácter

    Antes se comparaba por subcadena, de modo que un patrón corto disparaba
    alertas sin parar: "ABC" saltaba con ABC123, XABC99 y cualquier placa que lo
    contuviera. Los comodines dejan esa búsqueda parcial disponible, pero solo
    cuando se pide de forma explícita.

    Si la entrada tiene un tipo de placa asignado, la lectura además debe encajar
    con el subformato de ese tipo. Así una placa vigilada de remolque colombiano
    (R12345) no alerta ante un texto que no empiece por R.
    """
    import fnmatch
    from plate_types import matches_type, ANY_TYPE

    text = plate_text.upper().strip()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM watched_plates')
    watched = cursor.fetchall()
    conn.close()

    for w in watched:
        entry = dict(w)
        pattern = entry['plate_pattern'].upper().strip()
        if not pattern:
            continue

        if '*' in pattern or '?' in pattern:
            coincide = fnmatch.fnmatchcase(text, pattern)
        else:
            coincide = (text == pattern)

        if not coincide:
            continue

        # Filtro adicional por tipo de placa, si la entrada lo especifica
        plate_type = entry.get('plate_type') or ANY_TYPE
        country = entry.get('country') or ''
        if plate_type != ANY_TYPE and not matches_type(text, country, plate_type):
            continue

        return entry
    return None

def insert_plate_alert(camera_id, plate_text, matched_pattern, confidence):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        'INSERT INTO plate_alerts (camera_id, plate_text, matched_pattern, confidence, timestamp) VALUES (?, ?, ?, ?, ?)',
        (camera_id, plate_text, matched_pattern, confidence, now)
    )
    conn.commit()
    conn.close()

def get_latest_plate_alerts(limit=10):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM plate_alerts ORDER BY timestamp DESC LIMIT ?',
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Initialize on import
init_db()
