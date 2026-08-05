"""
nvr_server.py
-------------
Servidor de grabaciones (NVR).

Servicio independiente de la aplicación principal: puede correr en el mismo
equipo o en otro de la red local. Graba las cámaras que la aplicación le
indique, mantiene los últimos N días de cada una y sirve las grabaciones al
reproductor.

Uso
───
    python nvr_server.py                  Arranca en el puerto 8001
    python nvr_server.py --port 9000      Otro puerto
    python nvr_server.py --token          Muestra la clave de acceso y sale
    python nvr_server.py --storage D:/videovigilancia

La clave de acceso se genera sola la primera vez y hay que copiarla en la
configuración de la aplicación principal.
"""

import argparse
import functools
import os
import re
import sys
import mimetypes

from flask import Flask, jsonify, request, Response, send_file

from nvr import export, ffmpeg_tools, storage
from nvr.config import load_config, save_config, get_storage_path
from nvr.recorder import recorder_manager

# La consola de Windows usa cp1252 y corrompe los acentos de los mensajes
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
print = functools.partial(print, flush=True)     # noqa: A001

app = Flask(__name__)

# Los archivos de vídeo se sirven en trozos; sin esto Flask cargaría el
# segmento entero en memoria por cada petición de salto en la línea de tiempo.
CHUNK = 512 * 1024


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
def token_required(view):
    """
    Exige la clave compartida.

    El NVR escucha en la red local, así que sin esto cualquiera del wifi podría
    leer las grabaciones o reconfigurar qué se graba.
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        esperado = load_config().get("api_token", "")
        recibido = (request.headers.get("X-NVR-Token")
                    or request.args.get("token", ""))
        if not esperado or recibido != esperado:
            return jsonify({"status": "error",
                            "message": "Clave de acceso no válida"}), 401
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    """
    Comprobación de vida. No exige clave a propósito: la aplicación necesita
    poder decir "el servidor responde pero la clave es incorrecta" en lugar de
    un genérico "no se puede conectar".
    """
    return jsonify({
        "status": "ok",
        "service": "nvr",
        "ffmpeg": ffmpeg_tools.is_available(),
        "ffmpeg_version": ffmpeg_tools.version(),
        "encoder": ffmpeg_tools.detect_encoder() if ffmpeg_tools.is_available() else None,
    })


@app.route("/api/status", methods=["GET"])
@token_required
def status():
    config = load_config()
    return jsonify({
        "recorders": recorder_manager.status(),
        "storage": storage.storage_stats(),
        "settings": {k: config[k] for k in
                     ("segment_seconds", "record_fps", "quality",
                      "max_total_gb", "storage_path")},
        "last_maintenance": recorder_manager.ultimo_barrido,
    })


# ---------------------------------------------------------------------------
# Configuración de cámaras
# ---------------------------------------------------------------------------
@app.route("/api/cameras", methods=["GET"])
@token_required
def get_cameras():
    config = load_config()
    camaras = []
    for cid, info in config.get("cameras", {}).items():
        dias = storage.list_days(cid)
        camaras.append({
            "camera_id": cid,
            "name": info.get("name", ""),
            "enabled": bool(info.get("enabled")),
            "retention_days": int(info.get("retention_days", 3)),
            "days_recorded": len(dias),
            "oldest_day": dias[-1]["day"] if dias else None,
            "newest_day": dias[0]["day"] if dias else None,
            "bytes": sum((d["bytes"] or 0) for d in dias),
        })
    return jsonify({"cameras": camaras})


@app.route("/api/cameras", methods=["PUT"])
@token_required
def set_cameras():
    """
    Recibe de la aplicación qué cámaras grabar y con cuánta retención.

    Se reemplaza la lista completa, pero conservando las cámaras que ya no
    vengan: quedan deshabilitadas y sus grabaciones siguen en disco, intactas.
    Dejar de grabar nunca borra lo grabado; para eso está DELETE
    /api/cameras/<id>/recordings.
    """
    datos = request.json or {}
    entrantes = datos.get("cameras", [])
    if not isinstance(entrantes, list):
        return jsonify({"status": "error",
                        "message": "Se esperaba una lista de cámaras"}), 400

    config = load_config()
    previas = config.get("cameras", {})
    nuevas = {}

    for cam in entrantes:
        cid = str(cam.get("camera_id") or cam.get("id") or "").strip()
        if not cid:
            continue
        try:
            dias = int(cam.get("retention_days", 3))
        except (TypeError, ValueError):
            return jsonify({"status": "error",
                            "message": f"Retención no numérica en {cid}"}), 400
        if not 1 <= dias <= 365:
            return jsonify({"status": "error",
                            "message": "La retención debe estar entre 1 y 365 días"}), 400

        nuevas[cid] = {
            "name": cam.get("name", previas.get(cid, {}).get("name", "")),
            "source": cam.get("source", previas.get(cid, {}).get("source", "")),
            "enabled": bool(cam.get("enabled", True)),
            "retention_days": dias,
        }

    # Las que desaparecen quedan deshabilitadas, no borradas
    for cid, info in previas.items():
        if cid not in nuevas:
            info = dict(info)
            info["enabled"] = False
            nuevas[cid] = info

    config["cameras"] = nuevas
    save_config(config)
    recorder_manager.sync()

    return jsonify({"status": "success", "cameras": len(nuevas)})


@app.route("/api/cameras/<camera_id>/recordings", methods=["DELETE"])
@token_required
def delete_camera_recordings(camera_id):
    """
    Borra todas las grabaciones de una cámara, dejando su configuración intacta.

    Si la cámara está grabando, se para antes: ffmpeg tiene abierto el segmento
    en curso y en Windows un archivo en uso no se puede borrar. Al terminar se
    vuelve a sincronizar, de modo que la grabación se reanuda sola y lo único
    que se pierde es el material antiguo, que es lo que se pidió.
    """
    config = load_config()
    info = config.get("cameras", {}).get(camera_id)
    if info is None:
        return jsonify({"status": "error",
                        "message": "Esa cámara no está registrada"}), 404

    estaba_grabando = bool(info.get("enabled"))
    if estaba_grabando:
        info["enabled"] = False
        save_config(config)
        recorder_manager.sync()

    try:
        dias, bytes_liberados = storage.delete_camera_recordings(camera_id)
    finally:
        if estaba_grabando:
            info["enabled"] = True
            save_config(config)
            recorder_manager.sync()

    return jsonify({"status": "success", "days_deleted": dias,
                    "bytes_freed": bytes_liberados})


@app.route("/api/settings", methods=["PUT"])
@token_required
def set_settings():
    """Ajustes globales de grabación."""
    datos = request.json or {}
    config = load_config()

    numericos = {
        "segment_seconds": (30, 3600),
        "record_fps": (1, 60),
        "quality": (0, 51),
        "max_total_gb": (1, 100000),
    }
    for clave, (minimo, maximo) in numericos.items():
        if clave in datos:
            try:
                valor = float(datos[clave])
            except (TypeError, ValueError):
                return jsonify({"status": "error",
                                "message": f"{clave} no es numérico"}), 400
            if not minimo <= valor <= maximo:
                return jsonify({"status": "error",
                                "message": f"{clave} fuera de rango "
                                           f"({minimo}-{maximo})"}), 400
            config[clave] = int(valor) if clave != "max_total_gb" else valor

    save_config(config)
    recorder_manager.sync()
    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Consulta de grabaciones
# ---------------------------------------------------------------------------
@app.route("/api/recordings/days", methods=["GET"])
@token_required
def recording_days():
    """Días con grabación de una cámara, para el calendario del reproductor."""
    cid = request.args.get("camera_id", "")
    if not cid:
        return jsonify({"status": "error", "message": "Falta camera_id"}), 400
    return jsonify({"camera_id": cid, "days": storage.list_days(cid)})


@app.route("/api/recordings/segments", methods=["GET"])
@token_required
def recording_segments():
    """
    Segmentos de una cámara. Es lo que dibuja la línea de tiempo y lo que el
    reproductor va encadenando.
    """
    cid = request.args.get("camera_id", "")
    if not cid:
        return jsonify({"status": "error", "message": "Falta camera_id"}), 400

    segmentos = storage.list_segments(
        cid, day=request.args.get("day") or None,
        desde=request.args.get("from") or None,
        hasta=request.args.get("to") or None)

    return jsonify({
        "camera_id": cid,
        "segments": segmentos,
        # Tramos continuos, para pintar la línea de tiempo sin recorrer
        # segmento a segmento en el navegador
        "ranges": _agrupar_en_tramos(segmentos),
    })


def _agrupar_en_tramos(segmentos, hueco_maximo: float = 30.0):
    """
    Une segmentos consecutivos en tramos continuos.

    La línea de tiempo debe mostrar dónde HAY grabación. Dibujar cada archivo
    por separado la llenaría de cortes falsos: entre un segmento y el
    siguiente hay siempre una décima de diferencia que no es un hueco real.
    """
    tramos = []
    actual = None
    for s in segmentos:
        inicio, fin = s["started_at"], s["ended_at"]
        if not fin:
            # Segmento en curso: se estima con su duración nominal
            fin = inicio
        if actual is None:
            actual = {"start": inicio, "end": fin, "segments": 1}
            continue

        import datetime as _dt
        try:
            hueco = (_dt.datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S")
                     - _dt.datetime.strptime(actual["end"], "%Y-%m-%d %H:%M:%S")
                     ).total_seconds()
        except (ValueError, TypeError):
            hueco = hueco_maximo + 1

        if hueco <= hueco_maximo:
            actual["end"] = fin
            actual["segments"] += 1
        else:
            tramos.append(actual)
            actual = {"start": inicio, "end": fin, "segments": 1}

    if actual is not None:
        tramos.append(actual)
    return tramos


@app.route("/api/recordings/at", methods=["GET"])
@token_required
def recording_at():
    """
    Segmento que contiene un instante, para saltar a una hora concreta.

    Devuelve además el desplazamiento dentro del archivo, que es lo que el
    reproductor necesita para posicionar el vídeo en el punto exacto.
    """
    cid = request.args.get("camera_id", "")
    momento = request.args.get("at", "")
    if not cid or not momento:
        return jsonify({"status": "error",
                        "message": "Faltan camera_id o at"}), 400

    seg = storage.find_segment_at(cid, momento)
    if not seg:
        return jsonify({"status": "empty", "segment": None})

    import datetime as _dt
    desfase = 0.0
    try:
        desfase = max(0.0, (_dt.datetime.strptime(momento, "%Y-%m-%d %H:%M:%S")
                            - _dt.datetime.strptime(seg["started_at"],
                                                    "%Y-%m-%d %H:%M:%S")
                            ).total_seconds())
    except (ValueError, TypeError):
        pass

    return jsonify({"status": "ok", "segment": seg, "offset": desfase})


@app.route("/api/segment/<int:segment_id>", methods=["GET"])
@token_required
def serve_segment(segment_id):
    """
    Entrega un archivo de vídeo con soporte de rangos HTTP.

    El soporte de rangos es imprescindible: sin él el navegador tendría que
    descargar el segmento entero antes de poder saltar dentro de él, y
    arrastrar la línea de tiempo sería inservible.
    """
    seg = storage.get_segment(segment_id)
    if not seg:
        return jsonify({"status": "error", "message": "Segmento no encontrado"}), 404

    ruta = os.path.join(storage.camera_dir(seg["camera_id"]), seg["filename"])
    if not os.path.isfile(ruta):
        return jsonify({"status": "error",
                        "message": "El archivo ya no está en disco"}), 410

    tam = os.path.getsize(ruta)
    tipo = mimetypes.guess_type(ruta)[0] or "video/mp4"
    rango = request.headers.get("Range", "")

    if not rango:
        return send_file(ruta, mimetype=tipo, conditional=True)

    m = re.match(r"bytes=(\d*)-(\d*)", rango)
    if not m:
        return jsonify({"status": "error", "message": "Rango no válido"}), 416

    inicio = int(m.group(1)) if m.group(1) else 0
    fin = int(m.group(2)) if m.group(2) else tam - 1
    inicio = max(0, min(inicio, tam - 1))
    fin = max(inicio, min(fin, tam - 1))
    longitud = fin - inicio + 1

    def generar():
        with open(ruta, "rb") as f:
            f.seek(inicio)
            restante = longitud
            while restante > 0:
                trozo = f.read(min(CHUNK, restante))
                if not trozo:
                    break
                restante -= len(trozo)
                yield trozo

    respuesta = Response(generar(), status=206, mimetype=tipo,
                         direct_passthrough=True)
    respuesta.headers["Content-Range"] = f"bytes {inicio}-{fin}/{tam}"
    respuesta.headers["Accept-Ranges"] = "bytes"
    respuesta.headers["Content-Length"] = str(longitud)
    return respuesta


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------
@app.route("/api/export", methods=["POST"])
@token_required
def crear_exportacion():
    """
    Encola la exportación de un intervalo a un único MP4.

    Se responde enseguida con el identificador del trabajo en lugar de esperar
    a que termine: una exportación larga agotaría el tiempo de espera del
    navegador antes de generar el archivo.
    """
    datos = request.json or {}
    job, error = export.crear_trabajo(
        str(datos.get("camera_id", "")),
        str(datos.get("from", "")),
        str(datos.get("to", "")),
        str(datos.get("name", "")))

    if error:
        return jsonify({"status": "error", "message": error}), 400
    return jsonify({"status": "ok", "job": job.to_dict()})


@app.route("/api/export/<job_id>", methods=["GET"])
@token_required
def estado_exportacion(job_id):
    job = export.obtener_trabajo(job_id)
    if not job:
        return jsonify({"status": "error",
                        "message": "La exportación caducó o no existe"}), 404
    return jsonify({"status": "ok", "job": job.to_dict()})


@app.route("/api/export/<job_id>/download", methods=["GET"])
@token_required
def descargar_exportacion(job_id):
    job = export.obtener_trabajo(job_id)
    if not job:
        return jsonify({"status": "error",
                        "message": "La exportación caducó o no existe"}), 404
    if job.estado != "listo" or not job.ruta or not os.path.isfile(job.ruta):
        return jsonify({"status": "error",
                        "message": "La exportación aún no está lista"}), 409

    return send_file(job.ruta, mimetype="video/mp4", as_attachment=True,
                     download_name=job.nombre_sugerido(), conditional=True)


@app.route("/api/maintenance", methods=["POST"])
@token_required
def run_maintenance():
    """Fuerza un barrido de indexado y caducidad, sin esperar al ciclo."""
    recorder_manager.force_maintenance()
    return jsonify({"status": "success", "storage": storage.storage_stats()})


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Servidor de grabaciones (NVR)")
    parser.add_argument("--port", type=int, help="puerto de escucha")
    parser.add_argument("--host", help="interfaz de escucha")
    parser.add_argument("--storage", help="carpeta donde guardar las grabaciones")
    parser.add_argument("--token", action="store_true",
                        help="muestra la clave de acceso y termina")
    args = parser.parse_args()

    config = load_config()

    if args.token:
        print(config["api_token"])
        return 0

    cambiado = False
    for clave, valor in (("port", args.port), ("host", args.host),
                         ("storage_path", args.storage)):
        if valor:
            config[clave] = valor
            cambiado = True
    if cambiado:
        save_config(config)

    if not ffmpeg_tools.is_available():
        print("ERROR: no se encontró ffmpeg, imprescindible para grabar.")
        print("  Instálalo con:  winget install Gyan.FFmpeg")
        print("  Y abre una terminal nueva para que tome el PATH.")
        return 1

    os.makedirs(config["storage_path"], exist_ok=True)

    print("Servidor de grabaciones (NVR)")
    print(f"  ffmpeg        : {ffmpeg_tools.version()}")
    print(f"  codificador   : {ffmpeg_tools.detect_encoder()}")
    print(f"  grabaciones en: {config['storage_path']}")
    print(f"  escuchando en : http://{config['host']}:{config['port']}")
    print(f"  clave         : {config['api_token']}")
    print()
    print("  Copia esa clave en la configuración de la aplicación principal.")
    print()

    recorder_manager.start()

    from waitress import serve
    serve(app, host=config["host"], port=config["port"], threads=12, _quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
