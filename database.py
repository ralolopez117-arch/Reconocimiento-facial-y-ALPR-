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

def insert_watched_plate(plate_pattern, note=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO watched_plates (plate_pattern, note) VALUES (?, ?)',
        (plate_pattern.upper().strip(), note)
    )
    conn.commit()
    conn.close()

def update_watched_plate(plate_id, plate_pattern, note=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE watched_plates SET plate_pattern = ?, note = ? WHERE id = ?',
        (plate_pattern.upper().strip(), note, plate_id)
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
    """Returns the matched watched_plate row if the plate is in the watchlist, else None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM watched_plates')
    watched = cursor.fetchall()
    conn.close()
    for w in watched:
        pattern = w['plate_pattern'].upper()
        # Simple substring match (e.g. partial plate) or exact
        if pattern in plate_text.upper() or plate_text.upper() == pattern:
            return dict(w)
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
