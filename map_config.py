"""
map_config.py
-------------
Ajustes del mapa y ubicación de las cámaras sobre él.

Hay dos clases de mapa, y la diferencia condiciona cómo se guardan las
posiciones:

    Mapa de teselas   OpenStreetMap y equivalentes. El mundo real, así que cada
                      cámara se sitúa por latitud y longitud.

    Imagen propia     Un plano del edificio, un croquis del recinto… lo que el
                      administrador suba. No hay coordenadas geográficas, así
                      que la posición se guarda como fracción del ancho y del
                      alto (0 a 1). Usar fracciones y no píxeles permite
                      sustituir la imagen por otra de distinta resolución sin
                      que las cámaras se descoloquen.

Las dos posiciones se guardan por separado en cada cámara. Alternar entre un
mapa geográfico y un plano no borra lo colocado en el otro, que es lo que
pasaría con un único par de coordenadas reinterpretado según el modo.
"""

import os

from config_manager import load_config, save_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Dónde se guarda el plano que suba el administrador
MAPS_DIR = os.path.join(BASE_DIR, "static", "maps")

# Proveedores de teselas ofrecidos de serie. El administrador puede además
# escribir una plantilla de URL propia, para un servidor de teselas interno.
PROVEEDORES = {
    "osm": {
        "nombre": "OpenStreetMap",
        "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; colaboradores de OpenStreetMap",
        "max_zoom": 19,
    },
    "carto_light": {
        "nombre": "Carto claro",
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap · &copy; CARTO",
        "max_zoom": 20,
    },
    "carto_dark": {
        "nombre": "Carto oscuro",
        "url": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap · &copy; CARTO",
        "max_zoom": 20,
    },
    "opentopo": {
        "nombre": "OpenTopoMap (relieve)",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap · SRTM · OpenTopoMap",
        "max_zoom": 17,
    },
}

MODO_TESELAS = "tiles"
MODO_IMAGEN = "image"

# Extensiones admitidas para el plano propio
EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".webp")

# Tope del plano subido. Una imagen enorme no mejora nada y el navegador la
# tiene que cargar entera de una vez, a diferencia de las teselas.
MAX_IMAGEN_BYTES = 20 * 1024 * 1024

DEFECTOS = {
    "mode": MODO_TESELAS,
    "provider": "osm",
    # Plantilla propia, para un servidor de teselas de la red interna
    "custom_url": "",
    "custom_attribution": "",
    # Vista inicial al abrir el mapa
    "center": [40.4168, -3.7038],
    "zoom": 6,
    # Plano propio: nombre del archivo dentro de static/maps y su tamaño
    "image": "",
    "image_width": 0,
    "image_height": 0,
}


def maps_dir() -> str:
    os.makedirs(MAPS_DIR, exist_ok=True)
    return MAPS_DIR


def get_settings() -> dict:
    """Ajustes del mapa, completados con los valores por defecto."""
    guardados = load_config().get("map", {}) or {}
    ajustes = {**DEFECTOS, **guardados}

    # La lista de proveedores viaja con los ajustes: la interfaz construye el
    # desplegable sin tener que repetirla en JavaScript.
    ajustes["providers"] = [
        {"key": k, "name": v["nombre"], "max_zoom": v["max_zoom"]}
        for k, v in PROVEEDORES.items()
    ]
    ajustes["tile_url"], ajustes["attribution"], ajustes["max_zoom"] = _capa(ajustes)
    return ajustes


def _capa(ajustes):
    """URL de teselas, atribución y zoom máximo según lo configurado."""
    if ajustes.get("custom_url"):
        return (ajustes["custom_url"],
                ajustes.get("custom_attribution", ""),
                ajustes.get("max_zoom", 19))
    p = PROVEEDORES.get(ajustes.get("provider"), PROVEEDORES["osm"])
    return p["url"], p["attribution"], p["max_zoom"]


def save_settings(datos) -> str:
    """
    Guarda los ajustes del mapa.

    Returns:
        error (str) o None si fue bien.
    """
    ajustes = {k: v for k, v in (load_config().get("map", {}) or {}).items()}

    modo = datos.get("mode", ajustes.get("mode", MODO_TESELAS))
    if modo not in (MODO_TESELAS, MODO_IMAGEN):
        return f"Modo de mapa no válido: {modo}"
    ajustes["mode"] = modo

    if "provider" in datos:
        if datos["provider"] not in PROVEEDORES:
            return f"Proveedor no válido: {datos['provider']}"
        ajustes["provider"] = datos["provider"]

    if "custom_url" in datos:
        url = (datos["custom_url"] or "").strip()
        # Se exige el esquema para no acabar con una ruta relativa que el
        # navegador resolvería contra la propia aplicación.
        if url and not url.startswith(("http://", "https://")):
            return "La dirección de teselas debe empezar por http:// o https://"
        if url and "{z}" not in url:
            return ("La plantilla debe llevar {z}, {x} e {y}, "
                    "por ejemplo https://servidor/tiles/{z}/{x}/{y}.png")
        ajustes["custom_url"] = url

    if "custom_attribution" in datos:
        ajustes["custom_attribution"] = (datos["custom_attribution"] or "").strip()[:200]

    if "center" in datos:
        try:
            lat, lng = float(datos["center"][0]), float(datos["center"][1])
        except (TypeError, ValueError, IndexError):
            return "Centro del mapa no válido"
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return "El centro del mapa está fuera de rango"
        ajustes["center"] = [lat, lng]

    if "zoom" in datos:
        try:
            ajustes["zoom"] = max(0, min(22, int(datos["zoom"])))
        except (TypeError, ValueError):
            return "Nivel de zoom no válido"

    config = load_config()
    config["map"] = ajustes
    save_config(config)
    return None


def save_image(nombre_archivo: str, ancho: int, alto: int):
    """Registra el plano propio recién subido y cambia a modo imagen."""
    config = load_config()
    ajustes = config.get("map", {}) or {}
    ajustes["image"] = nombre_archivo
    ajustes["image_width"] = int(ancho)
    ajustes["image_height"] = int(alto)
    ajustes["mode"] = MODO_IMAGEN
    config["map"] = ajustes
    save_config(config)


# ---------------------------------------------------------------------------
# Posición de las cámaras
# ---------------------------------------------------------------------------
def set_position(camera_id: str, modo: str, x, y) -> str:
    """
    Coloca una cámara en el mapa.

    Args:
        modo: MODO_TESELAS (x=lng, y=lat) o MODO_IMAGEN (fracciones 0..1)

    Returns:
        error (str) o None si fue bien.
    """
    if modo not in (MODO_TESELAS, MODO_IMAGEN):
        return f"Modo de mapa no válido: {modo}"
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return "Coordenadas no numéricas"

    if modo == MODO_TESELAS:
        if not (-90 <= y <= 90):
            return "La latitud debe estar entre -90 y 90"
        if not (-180 <= x <= 180):
            return "La longitud debe estar entre -180 y 180"
        clave, valor = "map_geo", {"lat": y, "lng": x}
    else:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return "La posición debe caer dentro de la imagen"
        clave, valor = "map_image", {"x": x, "y": y}

    config = load_config()
    for cam in config.get("cameras", []):
        if cam["id"] == camera_id:
            cam[clave] = valor
            save_config(config)
            return None
    return "Esa cámara no existe"


def clear_position(camera_id: str, modo: str) -> str:
    """Quita una cámara del mapa, sin tocar la cámara en sí."""
    clave = "map_geo" if modo == MODO_TESELAS else "map_image"
    config = load_config()
    for cam in config.get("cameras", []):
        if cam["id"] == camera_id:
            cam.pop(clave, None)
            save_config(config)
            return None
    return "Esa cámara no existe"
