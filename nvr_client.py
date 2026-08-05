"""
nvr_client.py
-------------
Cliente del servidor de grabaciones, para la aplicación principal.

El navegador nunca habla directamente con el NVR: todo pasa por la aplicación.
Así la clave de acceso no sale del servidor, funciona aunque el NVR solo sea
alcanzable desde el equipo de la aplicación, y se reutiliza la sesión que ya
existe en lugar de inventar otra autenticación para el reproductor.
"""

import requests

from config_manager import load_config, save_config

# Tiempos de espera. Cortos a propósito: si el NVR está apagado, la interfaz
# debe decirlo enseguida en vez de quedarse colgada.
TIMEOUT_CONEXION = 3.0
TIMEOUT_LECTURA = 10.0

DEFAULT_NVR_SETTINGS = {
    "enabled": False,
    # Dirección del servidor de grabaciones. Puede ser este mismo equipo o
    # cualquier otro de la red local.
    "url": "http://127.0.0.1:8001",
    "token": "",
}


def get_nvr_settings():
    config = load_config()
    stored = config.get("nvr_settings", {})
    return {**DEFAULT_NVR_SETTINGS, **stored}


def save_nvr_settings(settings):
    config = load_config()
    config["nvr_settings"] = settings
    save_config(config)


def _base_url() -> str:
    url = get_nvr_settings()["url"].strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _headers():
    return {"X-NVR-Token": get_nvr_settings()["token"]}


class NvrError(Exception):
    """Fallo al hablar con el servidor de grabaciones."""

    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


def _request(metodo: str, ruta: str, timeout: float = None, **kwargs):
    """
    Llamada al NVR.

    Args:
        timeout: espera de lectura en segundos para las operaciones que tardan
                 más de lo normal, como borrar cientos de archivos de disco.
    """
    url = _base_url()
    if not url:
        raise NvrError("No hay dirección de servidor de grabaciones configurada")

    espera = (TIMEOUT_CONEXION, timeout or TIMEOUT_LECTURA)
    try:
        r = requests.request(
            metodo, f"{url}{ruta}", headers=_headers(),
            timeout=espera, **kwargs)
    except requests.exceptions.ConnectTimeout:
        raise NvrError(f"El servidor de grabaciones no responde en {url}")
    except requests.exceptions.ConnectionError:
        raise NvrError(f"No se pudo conectar con {url}. "
                       "Comprueba que está encendido y que el puerto está permitido.")
    except requests.exceptions.RequestException as e:
        raise NvrError(f"Error de comunicación: {type(e).__name__}")

    if r.status_code == 401:
        raise NvrError("La clave de acceso no es correcta", 401)
    if r.status_code >= 400:
        detalle = ""
        if r.status_code == 404:
            detalle = "La ruta o cámara no existe en el servidor de grabaciones (404)."
        else:
            try:
                detalle = r.json().get("message", "")
            except Exception:
                detalle = r.text[:120]
        raise NvrError(detalle or f"El servidor devolvió {r.status_code}",
                       r.status_code)

    try:
        return r.json()
    except ValueError:
        raise NvrError("El servidor devolvió una respuesta ilegible")


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def health():
    """
    Comprueba que el servidor responde.

    No usa la clave: permite distinguir "no responde" de "responde pero la
    clave es incorrecta", que son dos problemas con soluciones distintas.
    """
    url = _base_url()
    if not url:
        raise NvrError("No hay dirección configurada")
    try:
        r = requests.get(f"{url}/api/health",
                         timeout=(TIMEOUT_CONEXION, TIMEOUT_LECTURA))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException:
        raise NvrError(f"No se pudo conectar con {url}")


def status():
    return _request("GET", "/api/status")


def get_cameras():
    return _request("GET", "/api/cameras")


def set_cameras(camaras):
    """
    Indica al NVR qué cámaras grabar y con cuánta retención.

    Args:
        camaras: lista de dicts con camera_id, name, source, enabled y
                 retention_days
    """
    return _request("PUT", "/api/cameras", json={"cameras": camaras})


def delete_camera_recordings(camera_id):
    """Borra en el NVR todo el material grabado de una cámara."""
    return _request("DELETE", f"/api/cameras/{camera_id}/recordings",
                    timeout=120)


def set_settings(ajustes):
    return _request("PUT", "/api/settings", json=ajustes)


def recording_days(camera_id: str):
    return _request("GET", "/api/recordings/days",
                    params={"camera_id": camera_id})


def recording_segments(camera_id: str, day=None, desde=None, hasta=None):
    params = {"camera_id": camera_id}
    if day:
        params["day"] = day
    if desde:
        params["from"] = desde
    if hasta:
        params["to"] = hasta
    return _request("GET", "/api/recordings/segments", params=params)


def recording_at(camera_id: str, momento: str):
    return _request("GET", "/api/recordings/at",
                    params={"camera_id": camera_id, "at": momento})


def create_export(camera_id: str, desde: str, hasta: str, nombre: str = ""):
    """Encola la exportación de un intervalo a MP4."""
    return _request("POST", "/api/export", json={
        "camera_id": camera_id, "from": desde, "to": hasta, "name": nombre})


def export_status(job_id: str):
    return _request("GET", f"/api/export/{job_id}")


def export_download(job_id: str):
    """
    Abre la descarga del archivo exportado para reenviarla al navegador.

    Se devuelve sin consumir: una exportación puede pesar cientos de megabytes
    y cargarla en memoria para reenviarla agotaría el servidor.
    """
    url = _base_url()
    if not url:
        raise NvrError("No hay dirección configurada")
    try:
        r = requests.get(f"{url}/api/export/{job_id}/download",
                         headers=_headers(), stream=True,
                         timeout=(TIMEOUT_CONEXION, 120))
    except requests.exceptions.RequestException:
        raise NvrError("No se pudo descargar la exportación")
    if r.status_code >= 400:
        raise NvrError(f"El servidor devolvió {r.status_code}", r.status_code)
    return r


def run_maintenance():
    return _request("POST", "/api/maintenance")


def segment_stream(segment_id: int, rango: str = None):
    """
    Abre la descarga de un segmento para reenviarla al navegador.

    Devuelve la respuesta sin consumir para poder retransmitirla por trozos: un
    segmento de cinco minutos son decenas de megabytes y cargarlo entero en
    memoria por cada salto en la línea de tiempo agotaría el servidor.
    """
    url = _base_url()
    if not url:
        raise NvrError("No hay dirección configurada")

    cabeceras = dict(_headers())
    if rango:
        cabeceras["Range"] = rango

    try:
        r = requests.get(f"{url}/api/segment/{segment_id}", headers=cabeceras,
                         stream=True, timeout=(TIMEOUT_CONEXION, 30))
    except requests.exceptions.RequestException:
        raise NvrError("No se pudo obtener la grabación del servidor")

    if r.status_code == 401:
        raise NvrError("La clave de acceso no es correcta", 401)
    if r.status_code >= 400:
        raise NvrError(f"El servidor devolvió {r.status_code}", r.status_code)
    return r


def is_configured() -> bool:
    ajustes = get_nvr_settings()
    return bool(ajustes["enabled"] and ajustes["url"] and ajustes["token"])
