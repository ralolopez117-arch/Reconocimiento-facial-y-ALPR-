"""
nvr/recorder.py
---------------
Grabación continua: un proceso ffmpeg por cámara, más las tareas de
mantenimiento que indexan lo grabado y aplican la caducidad.

Cada cámara vive en su propio proceso a propósito. Si una se cuelga o su
codificación falla, las demás siguen grabando; con un único proceso para todas,
un fallo dejaría el sistema entero sin grabación.
"""

import os
import subprocess
import threading
import time

from . import ffmpeg_tools, storage
from .config import load_config, save_config

# Cada cuánto se revisa el disco para indexar segmentos nuevos y caducar días
INTERVALO_MANTENIMIENTO = 60.0

# Espera antes de reintentar una cámara cuya grabación terminó
ESPERA_REINTENTO = 10.0

# Si ffmpeg muere antes de este tiempo se considera un fallo real, no un corte
# pasajero, y se espera más entre reintentos para no castigar a la cámara.
UMBRAL_FALLO_RAPIDO = 20.0


class CameraRecorder(threading.Thread):
    """Mantiene grabando una cámara, reintentando mientras esté habilitada."""

    def __init__(self, camera_id: str, info: dict, ajustes: dict):
        super().__init__(daemon=True, name=f"rec-{camera_id[:8]}")
        self.camera_id = camera_id
        self.info = info
        self.ajustes = ajustes

        self._parar = threading.Event()
        self._proceso = None
        self.ultimo_error = ""
        self.reintentos = 0
        self.grabando_desde = None
        self.modo = "?"

    def stop(self):
        self._parar.set()
        self._terminar_proceso()

    def _terminar_proceso(self):
        p = self._proceso
        if p is None or p.poll() is not None:
            return
        try:
            # 'q' cierra ffmpeg de forma ordenada y deja el último segmento
            # bien terminado. Matarlo a secas dejaría un mp4 sin cerrar, que el
            # reproductor no puede abrir.
            p.stdin.write(b"q")
            p.stdin.flush()
            p.wait(timeout=8)
        except Exception:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def run(self):
        carpeta = storage.camera_dir(self.camera_id)
        patron = os.path.join(carpeta, "%Y-%m-%d_%H-%M-%S.mp4")
        fuente = self.info.get("source", "")

        # Si la cámara ya emite H.264 se copia el flujo tal cual: sin
        # recodificar no hay pérdida de calidad y el consumo es casi nulo.
        codec = ffmpeg_tools.probe_video_codec(fuente)
        copiar = (codec == "h264")
        self.modo = "copia directa" if copiar else ffmpeg_tools.detect_encoder()
        print(f"[NVR] {self.info.get('name', self.camera_id)}: "
              f"fuente {codec or 'desconocida'} -> {self.modo}")

        while not self._parar.is_set():
            try:
                cmd = ffmpeg_tools.build_record_command(
                    fuente, patron,
                    self.ajustes.get("segment_seconds", 300),
                    copiar=copiar,
                    fps_max=None if copiar else self.ajustes.get("record_fps"),
                    calidad=self.ajustes.get("quality", 26),
                )
                inicio = time.time()
                self.grabando_desde = inicio
                self._proceso = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

                _, err = self._proceso.communicate()
                duracion = time.time() - inicio
                self.grabando_desde = None

                if self._parar.is_set():
                    break

                mensaje = (err or b"").decode("utf-8", "replace").strip()
                self.ultimo_error = mensaje.splitlines()[-1] if mensaje else ""
                self.reintentos += 1

                if duracion < UMBRAL_FALLO_RAPIDO:
                    # La cámara no responde o rechaza la conexión: se espera
                    # más para no martillearla con reintentos.
                    espera = min(ESPERA_REINTENTO * min(self.reintentos, 6), 120)
                    print(f"[NVR] {self.camera_id[:8]} falló en {duracion:.0f} s "
                          f"({self.ultimo_error[:90]}). Reintento en {espera:.0f} s")
                else:
                    espera = ESPERA_REINTENTO
                    self.reintentos = 0

                if self._parar.wait(espera):
                    break

            except Exception as e:
                self.ultimo_error = f"{type(e).__name__}: {e}"
                print(f"[NVR] Error grabando {self.camera_id[:8]}: {self.ultimo_error}")
                if self._parar.wait(ESPERA_REINTENTO):
                    break

        self._terminar_proceso()
        print(f"[NVR] Grabación detenida: {self.info.get('name', self.camera_id)}")

    @property
    def activo(self) -> bool:
        return self._proceso is not None and self._proceso.poll() is None

    def estado(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "name": self.info.get("name", ""),
            "recording": self.activo,
            "mode": self.modo,
            "since": self.grabando_desde,
            "retries": self.reintentos,
            "last_error": self.ultimo_error[:200],
        }


class RecorderManager:
    """
    Arranca y para grabadores según la configuración, e indexa y caduca lo
    grabado en segundo plano.
    """

    def __init__(self):
        self._grabadores = {}
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._mantenimiento = None
        self.ultimo_barrido = None

    # -- Ciclo de vida ------------------------------------------------------

    def start(self):
        storage.init_db()
        self.sync()
        if self._mantenimiento is None or not self._mantenimiento.is_alive():
            self._parar.clear()
            self._mantenimiento = threading.Thread(
                target=self._bucle_mantenimiento, daemon=True, name="nvr-mant")
            self._mantenimiento.start()

    def stop(self):
        self._parar.set()
        with self._lock:
            for g in self._grabadores.values():
                g.stop()
            self._grabadores.clear()

    def sync(self):
        """Ajusta los grabadores en marcha a lo que dice la configuración."""
        config = load_config()
        camaras = config.get("cameras", {})
        ajustes = {k: config.get(k) for k in
                   ("segment_seconds", "record_fps", "quality")}

        with self._lock:
            deseadas = {cid for cid, info in camaras.items()
                        if info.get("enabled") and info.get("source")}

            # Parar las que ya no deben grabarse
            for cid in list(self._grabadores):
                if cid not in deseadas or not self._grabadores[cid].is_alive():
                    self._grabadores[cid].stop()
                    del self._grabadores[cid]

            # Arrancar las que falten
            for cid in deseadas:
                if cid not in self._grabadores:
                    g = CameraRecorder(cid, camaras[cid], ajustes)
                    self._grabadores[cid] = g
                    g.start()
                    print(f"[NVR] Grabando: {camaras[cid].get('name', cid)}")

    # -- Mantenimiento -------------------------------------------------------

    def _bucle_mantenimiento(self):
        # Primer barrido enseguida para que el índice refleje lo que ya hubiera
        # en disco de una ejecución anterior.
        if not self._parar.wait(5):
            self._barrido()
        while not self._parar.wait(INTERVALO_MANTENIMIENTO):
            self._barrido()

    def _barrido(self):
        try:
            config = load_config()
            camaras = config.get("cameras", {})

            # Se indexan también las cámaras deshabilitadas: sus grabaciones
            # antiguas siguen ahí y deben poder consultarse y caducar.
            for cid, info in camaras.items():
                storage.scan_camera(cid)
                dias = int(info.get("retention_days",
                                    config.get("retention_days", 3)) or 0)
                storage.apply_retention(cid, dias)

            storage.enforce_global_limit(float(config.get("max_total_gb", 0) or 0))
            self.ultimo_barrido = time.time()
        except Exception as e:
            # Nunca dejar morir el mantenimiento: sin él, el disco se llenaría
            print(f"[NVR] Error en el mantenimiento: {type(e).__name__}: {e}")

    def force_maintenance(self):
        """Ejecuta un barrido ya, sin esperar al ciclo."""
        self._barrido()

    # -- Estado --------------------------------------------------------------

    def status(self):
        with self._lock:
            return [g.estado() for g in self._grabadores.values()]


recorder_manager = RecorderManager()
