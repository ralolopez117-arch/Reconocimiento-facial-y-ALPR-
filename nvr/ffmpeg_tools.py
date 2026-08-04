"""
nvr/ffmpeg_tools.py
-------------------
Localización de ffmpeg y construcción de los comandos de grabación.

El NVR delega en ffmpeg toda la escritura de vídeo. Es lo que hacen los
grabadores reales: gestiona la reconexión, el troceado en segmentos y la
codificación por hardware mucho mejor de lo que se puede improvisar sobre
OpenCV, y permite copiar el flujo tal cual cuando la cámara ya emite H.264.
"""

import os
import shutil
import subprocess

# Rutas donde buscar ffmpeg si no está en el PATH.
#
# winget modifica el PATH al instalar, pero las terminales y servicios ya
# abiertos conservan el antiguo, así que buscarlo solo en el PATH falla justo
# después de instalarlo.
_RUTAS_CANDIDATAS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"),
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    "/usr/bin",
    "/usr/local/bin",
]

_cache = {}


def find_binary(nombre: str):
    """
    Devuelve la ruta de ffmpeg o ffprobe, o None si no se encuentra.

    Se cachea porque se consulta al arrancar cada grabación.
    """
    if nombre in _cache:
        return _cache[nombre]

    ruta = shutil.which(nombre)
    if ruta:
        _cache[nombre] = ruta
        return ruta

    ejecutable = nombre + (".exe" if os.name == "nt" else "")
    for base in _RUTAS_CANDIDATAS:
        if not os.path.isdir(base):
            continue
        directo = os.path.join(base, ejecutable)
        if os.path.isfile(directo):
            _cache[nombre] = directo
            return directo
        # winget instala bajo Packages/<paquete>/<version>/bin
        for raiz, _, archivos in os.walk(base):
            if ejecutable in archivos:
                _cache[nombre] = os.path.join(raiz, ejecutable)
                return _cache[nombre]

    _cache[nombre] = None
    return None


def ffmpeg_path():
    return find_binary("ffmpeg")


def ffprobe_path():
    return find_binary("ffprobe")


def is_available() -> bool:
    return ffmpeg_path() is not None


def version() -> str:
    ruta = ffmpeg_path()
    if not ruta:
        return "no disponible"
    try:
        salida = subprocess.run([ruta, "-version"], capture_output=True,
                                text=True, timeout=10).stdout
        return salida.splitlines()[0] if salida else "desconocida"
    except Exception:
        return "desconocida"


# ---------------------------------------------------------------------------
# Codificador
# ---------------------------------------------------------------------------
def detect_encoder() -> str:
    """
    Elige el mejor codificador H.264 disponible, comprobándolo de verdad.

    No basta con que ffmpeg liste h264_nvenc: aparece aunque no haya GPU
    compatible o el driver no lo permita. Se codifica un fragmento de prueba
    para asegurarse, porque descubrirlo al arrancar una grabación real dejaría
    la cámara sin grabar.
    """
    if "encoder" in _cache:
        return _cache["encoder"]

    ruta = ffmpeg_path()
    elegido = "libx264"
    if ruta:
        for candidato in ("h264_nvenc", "h264_qsv", "h264_amf"):
            try:
                r = subprocess.run(
                    [ruta, "-hide_banner", "-loglevel", "error",
                     "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                     "-t", "1", "-c:v", candidato, "-f", "null",
                     "NUL" if os.name == "nt" else "/dev/null"],
                    capture_output=True, timeout=45)
                if r.returncode == 0:
                    elegido = candidato
                    break
            except Exception:
                continue

    _cache["encoder"] = elegido
    return elegido


def probe_video_codec(source: str, timeout: float = 15.0):
    """
    Códec de vídeo de la fuente, o None si no se puede averiguar.

    Sirve para decidir si se puede copiar el flujo sin recodificar: una cámara
    IP que ya emite H.264 no necesita pasar por el codificador, lo que reduce
    el consumo a prácticamente nada y evita perder calidad.
    """
    ruta = ffprobe_path()
    if not ruta:
        return None
    try:
        r = subprocess.run(
            [ruta, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", source],
            capture_output=True, text=True, timeout=timeout)
        nombre = (r.stdout or "").strip()
        return nombre or None
    except Exception:
        return None


def build_record_command(source: str, patron_salida: str, segundos_segmento: int,
                         encoder: str = None, copiar: bool = False,
                         fps_max: int = None, calidad: int = 26):
    """
    Construye el comando de grabación por segmentos.

    Args:
        source:             URL o índice de la cámara
        patron_salida:      ruta con plantilla strftime, p. ej. .../%Y-%m-%d_%H-%M-%S.mp4
        segundos_segmento:  duración de cada archivo
        encoder:            codificador H.264; si es None se detecta
        copiar:             copiar el flujo sin recodificar
        fps_max:            limitar fotogramas por segundo al grabar
        calidad:            factor de calidad; a mayor número, menos tamaño

    Returns:
        Lista de argumentos lista para subprocess.
    """
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg no está disponible")

    cmd = [
        ff, "-hide_banner", "-loglevel", "warning",
        # Reconexión automática ante cortes de la cámara. Sin esto, un corte de
        # red terminaría el proceso y la cámara dejaría de grabarse.
        "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-rw_timeout", "15000000",          # 15 s sin datos: se reintenta
    ]

    if not copiar:
        # Marca cada fotograma con la hora en que llega.
        #
        # Las cámaras MJPEG sobre HTTP no envían marcas de tiempo, así que sin
        # esto ffmpeg asume que el flujo va a la velocidad nominal de salida.
        # Con una cámara que entrega 5 fps reales y una salida etiquetada a 10,
        # media hora de reloj quedaba guardada como quince minutos de vídeo:
        # las grabaciones se reproducían al doble de velocidad y la línea de
        # tiempo situaba los sucesos en momentos equivocados.
        cmd += ["-use_wallclock_as_timestamps", "1"]

    if str(source).lower().startswith("rtsp://"):
        # TCP evita la pérdida de paquetes típica de RTSP sobre UDP, que se
        # traduce en artefactos y segmentos corruptos.
        cmd += ["-rtsp_transport", "tcp"]

    cmd += ["-i", str(source)]

    if copiar:
        cmd += ["-c:v", "copy"]
    else:
        enc = encoder or detect_encoder()
        cmd += ["-c:v", enc]
        if enc == "libx264":
            cmd += ["-preset", "veryfast", "-crf", str(calidad)]
        elif enc == "h264_nvenc":
            cmd += ["-preset", "p4", "-rc", "vbr", "-cq", str(calidad)]
        else:
            cmd += ["-q:v", str(calidad)]

        if fps_max:
            # El filtro fps respeta las marcas de tiempo de entrada: si la
            # cámara va más rápida descarta fotogramas, y si va más lenta los
            # duplica, pero la duración del archivo sigue coincidiendo con el
            # tiempo real transcurrido.
            #
            # Con "-r" pasaba lo contrario: imponía la cadencia sin mirar
            # cuándo llegó cada fotograma, y comprimía el tiempo.
            cmd += ["-vf", f"fps={fps_max}"]
        else:
            cmd += ["-fps_mode", "passthrough"]
        # Un fotograma clave cada 2 segundos: permite buscar en la línea de
        # tiempo con precisión sin inflar demasiado el archivo.
        cmd += ["-g", "50", "-force_key_frames", "expr:gte(t,n_forced*2)"]

    cmd += [
        "-an",                               # sin audio: no se usa y ocupa
        "-f", "segment",
        "-segment_time", str(segundos_segmento),
        "-segment_format", "mp4",
        # Cortar en fotograma clave: sin esto el segmento empieza con imagen
        # incompleta y el reproductor muestra basura al saltar a él.
        "-segment_atclocktime", "1",
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-movflags", "+faststart",
        patron_salida,
    ]
    return cmd
