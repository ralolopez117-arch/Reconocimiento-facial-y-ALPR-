"""
nvr/export.py
-------------
Exportación de un intervalo de grabación a un único archivo MP4.

El vídeo está troceado en segmentos de varios minutos, así que exportar
"de las 10:15 a las 10:40" significa unir varios archivos y recortar los
extremos.

Se hace copiando el flujo sin recodificar. Los segmentos ya están en H.264, de
modo que la exportación es casi instantánea y no pierde calidad. La
contrapartida es que el corte solo puede caer en un fotograma clave; durante la
grabación se fuerza uno cada dos segundos, y medido sobre grabaciones reales el
recorte queda a medio segundo de lo pedido. Recodificar daría un corte exacto,
pero tardaría bastante más y degradaría la imagen.

Los trabajos se ejecutan en un hilo aparte. Una exportación de una hora tarda
lo suyo en escribirse a disco, y hacerlo dentro de la petición HTTP agotaría el
tiempo de espera del navegador antes de terminar.
"""

import datetime
import os
import re
import subprocess
import threading
import time
import uuid

from . import ffmpeg_tools, storage
from .config import get_storage_path

# Duración máxima exportable de una vez. Sin tope, una selección accidental de
# varios días generaría un archivo de decenas de gigabytes.
MAX_EXPORT_SECONDS = 6 * 3600

# Los archivos generados se borran pasado este tiempo. El usuario ya se los ha
# descargado y ocupan tanto como la grabación original.
EXPORT_TTL_SECONDS = 30 * 60

_JOBS = {}
_LOCK = threading.Lock()


def exports_dir() -> str:
    ruta = os.path.join(get_storage_path(), "_exports")
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _parse(momento: str):
    try:
        return datetime.datetime.strptime(momento, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


class ExportJob:
    """Un trabajo de exportación y su progreso."""

    def __init__(self, camera_id, desde, hasta, nombre_camara=""):
        self.id = uuid.uuid4().hex[:12]
        self.camera_id = camera_id
        self.desde = desde
        self.hasta = hasta
        self.nombre_camara = nombre_camara

        self.estado = "pendiente"     # pendiente | procesando | listo | error
        self.progreso = 0.0
        self.mensaje = ""
        self.ruta = None
        self.bytes = 0
        self.duracion = 0.0
        self.creado = time.time()

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.estado,
            "progress": round(self.progreso, 1),
            "message": self.mensaje,
            "size_bytes": self.bytes,
            "duration": round(self.duracion, 1),
            "camera_id": self.camera_id,
            "from": self.desde,
            "to": self.hasta,
            "filename": self.nombre_sugerido(),
        }

    def nombre_sugerido(self) -> str:
        """
        Nombre por defecto: cámara y momento, sin caracteres problemáticos.

        Windows rechaza \\ / : * ? " < > | en los nombres de archivo, y la hora
        lleva dos puntos, así que hay que sustituirlos.
        """
        base = (self.nombre_camara or self.camera_id or "grabacion").strip()
        base = re.sub(r'[\\/:*?"<>|]', "-", base)
        base = re.sub(r"\s+", "_", base)

        d = _parse(self.desde)
        h = _parse(self.hasta)
        if d and h:
            sufijo = (f"_{d.strftime('%Y-%m-%d')}_{d.strftime('%H-%M-%S')}"
                      f"_a_{h.strftime('%H-%M-%S')}")
        else:
            sufijo = ""
        return f"{base}{sufijo}.mp4"


def crear_trabajo(camera_id, desde, hasta, nombre_camara=""):
    """
    Valida el intervalo y encola la exportación.

    Returns:
        (job, error) — uno de los dos siempre es None.
    """
    d, h = _parse(desde), _parse(hasta)
    if not d or not h:
        return None, "Fechas no válidas. Formato esperado: aaaa-mm-dd hh:mm:ss"
    if h <= d:
        return None, "La hora final debe ser posterior a la inicial"

    duracion = (h - d).total_seconds()
    if duracion > MAX_EXPORT_SECONDS:
        return None, (f"El intervalo es de {duracion/3600:.1f} h y el máximo "
                      f"es {MAX_EXPORT_SECONDS/3600:.0f} h")

    segmentos = _segmentos_del_intervalo(camera_id, d, h)
    if not segmentos:
        return None, "No hay grabación en ese intervalo"

    job = ExportJob(camera_id, desde, hasta, nombre_camara)
    job.duracion = duracion
    with _LOCK:
        _JOBS[job.id] = job

    threading.Thread(target=_ejecutar, args=(job, segmentos, d, h),
                     daemon=True, name=f"export-{job.id}").start()
    return job, None


def obtener_trabajo(job_id):
    with _LOCK:
        return _JOBS.get(job_id)


def _segmentos_del_intervalo(camera_id, d, h):
    """
    Segmentos que se solapan con el intervalo pedido.

    Se consulta desde bastante antes del inicio porque el segmento que contiene
    ese instante empezó antes: filtrar por started_at >= desde se dejaría fuera
    justo el primero.
    """
    margen = d - datetime.timedelta(hours=2)
    candidatos = storage.list_segments(
        camera_id,
        desde=margen.strftime("%Y-%m-%d %H:%M:%S"),
        hasta=h.strftime("%Y-%m-%d %H:%M:%S"))

    dentro = []
    for s in candidatos:
        ini = _parse(s["started_at"])
        fin = _parse(s["ended_at"]) if s["ended_at"] else None
        if ini is None:
            continue
        if fin is None:
            fin = ini + datetime.timedelta(seconds=s["duration"] or 300)
        if fin > d and ini < h:
            dentro.append((s, ini, fin))
    return dentro


def _ejecutar(job, segmentos, d, h):
    try:
        job.estado = "procesando"
        carpeta = storage.camera_dir(job.camera_id)
        salida = os.path.join(exports_dir(), f"{job.id}.mp4")

        # Lista para el demuxer concat. Las rutas se escriben entre comillas
        # simples y con barras normales: en Windows, las barras invertidas se
        # interpretan como escapes y el archivo no se encuentra.
        lista = os.path.join(exports_dir(), f"{job.id}.txt")
        with open(lista, "w", encoding="utf-8") as f:
            for s, _, _ in segmentos:
                ruta = os.path.join(carpeta, s["filename"]).replace("\\", "/")
                f.write(f"file '{ruta}'\n")

        # Desplazamiento desde el inicio del primer segmento
        primer_inicio = segmentos[0][1]
        desplazamiento = max(0.0, (d - primer_inicio).total_seconds())
        duracion = (h - d).total_seconds()

        ff = ffmpeg_tools.ffmpeg_path()
        # Colocación de -ss y -t medida sobre grabaciones reales, pidiendo 80 s:
        #
        #   -ss tras -i, con avoid_negative_ts     77,3 s   (error 2,7 s)
        #   -ss antes de -i, con avoid_negative_ts 102,3 s  (error 22,3 s)
        #   -ss antes de -i, sin avoid_negative_ts  80,3 s  (error 0,3 s)
        #   recodificando                           80,0 s  (exacto, pero lento)
        #
        # Se usa la tercera: instantánea y con un error de tres décimas, muy por
        # debajo del intervalo entre fotogramas clave. avoid_negative_ts, que
        # parecía una precaución razonable, resultó ser justo lo que descuadraba
        # el recorte.
        cmd = [
            ff, "-hide_banner", "-loglevel", "error",
            "-ss", f"{desplazamiento:.3f}",
            "-f", "concat", "-safe", "0",
            "-i", lista,
            "-t", f"{duracion:.3f}",
            "-c", "copy",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            "-y", salida,
        ]

        proceso = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)

        # ffmpeg informa del avance con out_time_ms; se traduce a porcentaje
        for linea in proceso.stdout:
            if linea.startswith("out_time_ms="):
                try:
                    ms = int(linea.split("=", 1)[1].strip())
                    job.progreso = min(99.0, (ms / 1000000.0) / duracion * 100)
                except (ValueError, ZeroDivisionError):
                    pass

        proceso.wait()
        err = proceso.stderr.read() if proceso.stderr else ""

        try:
            os.remove(lista)
        except OSError:
            pass

        if proceso.returncode != 0 or not os.path.exists(salida):
            job.estado = "error"
            job.mensaje = (err or "ffmpeg terminó con error").strip()[:300]
            return

        job.ruta = salida
        job.bytes = os.path.getsize(salida)
        if job.bytes == 0:
            job.estado = "error"
            job.mensaje = "El archivo resultante quedó vacío"
            return

        job.progreso = 100.0
        job.estado = "listo"

    except Exception as e:
        job.estado = "error"
        job.mensaje = f"{type(e).__name__}: {e}"
    finally:
        limpiar_antiguos()


def limpiar_antiguos():
    """Borra exportaciones caducadas y sus trabajos."""
    ahora = time.time()
    with _LOCK:
        caducados = [j for j in _JOBS.values()
                     if ahora - j.creado > EXPORT_TTL_SECONDS]
        for j in caducados:
            if j.ruta and os.path.exists(j.ruta):
                try:
                    os.remove(j.ruta)
                except OSError:
                    pass
            _JOBS.pop(j.id, None)

    # Restos de ejecuciones anteriores del servicio, que no están en _JOBS
    try:
        carpeta = exports_dir()
        for nombre in os.listdir(carpeta):
            ruta = os.path.join(carpeta, nombre)
            try:
                if ahora - os.path.getmtime(ruta) > EXPORT_TTL_SECONDS:
                    os.remove(ruta)
            except OSError:
                pass
    except OSError:
        pass
