"""
nvr/config.py
-------------
Configuración del servidor de grabaciones.

El NVR es un servicio independiente: puede correr en el mismo equipo que la
aplicación principal o en otro de la red, así que lleva su propia configuración
y no comparte config.json. Lo único que necesita saber de la aplicación es qué
cámaras grabar, y eso se lo indica ella por la API.
"""

import copy
import json
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "nvr_config.json")

# Retención por defecto. Es deliberadamente corta: con vídeo, quedarse sin
# disco es un fallo silencioso que solo se descubre cuando falta la grabación
# que se necesitaba.
DEFAULT_RETENTION_DAYS = 3

# Duración de cada archivo. Cinco minutos equilibra dos cosas: segmentos largos
# hacen lenta la búsqueda en la línea de tiempo, y muy cortos multiplican los
# archivos y el coste de indexarlos.
DEFAULT_SEGMENT_SECONDS = 300

DEFAULT_CONFIG = {
    # Carpeta donde se escriben las grabaciones. Conviene un disco con espacio;
    # se puede apuntar a otra unidad.
    "storage_path": os.path.join(BASE_DIR, "recordings"),

    "port": 8001,
    # 0.0.0.0 para que la aplicación pueda estar en otro equipo de la red
    "host": "0.0.0.0",

    # Clave compartida con la aplicación principal. Se genera al crear el
    # archivo y hay que copiarla en la configuración de la app.
    "api_token": "",

    "segment_seconds": DEFAULT_SEGMENT_SECONDS,

    # Límite total de disco para grabaciones, en gigabytes. Al superarlo se
    # borran los días más antiguos aunque no hayan cumplido su retención: sin
    # este tope, configurar muchos días llenaría la unidad.
    "max_total_gb": 100,

    # Fotogramas por segundo a los que grabar. Menos que el directo es
    # suficiente para revisar después y reduce mucho el tamaño.
    "record_fps": 10,

    # Calidad: a mayor número, menor tamaño y menos detalle.
    "quality": 26,

    # {camera_id: {name, source, enabled, retention_days}}
    "cameras": {},
}


def default_config():
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["api_token"] = secrets.token_hex(24)
    return config


def load_config():
    """Configuración del NVR, creando el archivo la primera vez."""
    if not os.path.exists(CONFIG_FILE):
        config = default_config()
        save_config(config)
        print(f"[NVR] Configuración creada: {CONFIG_FILE}")
        print(f"[NVR] Clave de acceso: {config['api_token']}")
        return config

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[NVR] No se pudo leer {CONFIG_FILE}: {e}")
        print("[NVR] Se usan los valores por defecto sin tocar el archivo.")
        return default_config()

    # Completar claves que falten sin pisar las existentes
    completa = {**DEFAULT_CONFIG, **data}
    if not completa.get("api_token"):
        completa["api_token"] = secrets.token_hex(24)
        save_config(completa)
    return completa


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[NVR] Error guardando la configuración: {e}")


def get_storage_path() -> str:
    ruta = load_config()["storage_path"]
    os.makedirs(ruta, exist_ok=True)
    return ruta
