"""
config_manager.py
-----------------
Configuración local de la instalación: cámaras registradas y preferencias
globales.

config.json NO se versiona, porque contiene las URLs de las cámaras y sus
credenciales ONVIF. Por eso una instalación recién clonada no lo tiene, y este
módulo lo crea con valores por defecto la primera vez que se consulta. Así el
programa arranca sin pasos previos y las cámaras se añaden desde la interfaz.
"""

import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# ---------------------------------------------------------------------------
# Valores por defecto
#
# Definidos en un solo sitio y reutilizados tanto al crear el archivo como al
# completar los ajustes que falten en uno ya existente. Antes cada función
# llevaba su propia copia, que podía quedar desincronizada.
# ---------------------------------------------------------------------------
DEFAULT_DISPLAY_SETTINGS = {
    "show_fps": True,
    "show_labels": True,
    "show_speed": False,
    # Recuadros discontinuos con la posición predicha de un objeto ocluido.
    # Apagados por defecto: son una ayuda de diagnóstico y en escenas con
    # varios objetos ensucian la imagen. Ocultarlos no afecta al seguimiento,
    # que mantiene los tracks perdidos por su cuenta.
    "show_ghost_boxes": False,
}

DEFAULT_ALPR_SETTINGS = {
    "plate_format": "generic",
    "min_confidence": 0.5,
}

DEFAULT_SECURITY_SETTINGS = {
    "session_timeout_minutes": 15,
}

DEFAULT_DETECTION_MODE = "monitored"

DEFAULT_CONFIG = {
    # Sin cámaras: se registran desde la interfaz
    "cameras": [],
    "display_settings": DEFAULT_DISPLAY_SETTINGS,
    "detection_mode": DEFAULT_DETECTION_MODE,
    "alpr_settings": DEFAULT_ALPR_SETTINGS,
    "security_settings": DEFAULT_SECURITY_SETTINGS,
}


def default_config():
    """Copia profunda de la configuración por defecto, segura de modificar."""
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config():
    """
    Devuelve la configuración, creando el archivo si aún no existe.

    Crear el archivo desde una función de lectura es deliberado: es el único
    punto por el que pasan todos los accesos, así que garantiza que una
    instalación nueva quede configurada en el primer arranque sin que el
    usuario tenga que copiar ninguna plantilla.
    """
    if not os.path.exists(CONFIG_FILE):
        config = default_config()
        save_config(config)
        print(f"[Config] Archivo de configuración creado: {CONFIG_FILE}")
        return config

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        # No se sobrescribe un archivo ilegible: puede contener las cámaras del
        # usuario y ser recuperable a mano. Se trabaja con los valores por
        # defecto durante esta ejecución.
        print(f"[Config] No se pudo leer {CONFIG_FILE}: {e}")
        print("[Config] Se usarán los valores por defecto sin tocar el archivo.")
        return default_config()

    # Migrate old "streams" format to "cameras"
    if "streams" in data:
        data["cameras"] = []
        for i, s in enumerate(data.pop("streams")):
            data["cameras"].append({
                "id": f"cam_migrated_{i}",
                "name": f"Camera {i+1}",
                "type": "IP" if "://" in str(s) or "." in str(s) else "USB",
                "source": s
            })
        save_config(data)

    return data


def save_config(config):
    try:
        # ensure_ascii=False mantiene legibles los nombres con tildes o eñes
        # en lugar de escaparlos como á.
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_display_settings():
    config = load_config()
    stored = config.get("display_settings", {})
    return {**DEFAULT_DISPLAY_SETTINGS, **stored}

def save_display_settings(settings):
    config = load_config()
    config["display_settings"] = settings
    save_config(config)

def get_alpr_settings():
    """
    Configuración del motor de matrículas.

    plate_format:   clave de plate_format.PLATE_PATTERNS ("generic", "mx",
                    "us", "es", "ar", "co", "cl", "pe") o una expresión
                    regular propia. "generic" acepta 4-8 alfanuméricos con al
                    menos un dígito, adecuado si se vigilan varios países.
    min_confidence: confianza mínima del OCR para considerar un texto.
    """
    config = load_config()
    stored = config.get("alpr_settings", {})
    return {**DEFAULT_ALPR_SETTINGS, **stored}


def save_alpr_settings(settings):
    config = load_config()
    config["alpr_settings"] = settings
    save_config(config)


def get_security_settings():
    """
    Ajustes de sesión, definidos por el administrador.

    session_timeout_minutes: minutos SIN visualizar ningún stream tras los
                             cuales la sesión caduca y hay que volver a entrar.
                             Mientras haya una cámara abierta no caduca nunca.
    """
    config = load_config()
    stored = config.get("security_settings", {})
    return {**DEFAULT_SECURITY_SETTINGS, **stored}


def save_security_settings(settings):
    config = load_config()
    config["security_settings"] = settings
    save_config(config)


def get_detection_mode():
    config = load_config()
    return config.get("detection_mode", DEFAULT_DETECTION_MODE)

def save_detection_mode(mode):
    if mode not in ["monitored", "all"]:
        mode = "monitored"
    config = load_config()
    config["detection_mode"] = mode
    save_config(config)

