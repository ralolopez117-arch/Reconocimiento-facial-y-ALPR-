"""
frame_source.py
---------------
Lectura de fotogramas en un hilo propio, desacoplada de la inferencia.

Medido sobre las cámaras del proyecto, cap.read() tarda entre 1 y 138 ms según
la fuente, mientras que la inferencia en GPU ronda los 20 ms. Leyendo dentro del
bucle principal, el acelerador se queda parado durante toda la espera de red.

Aquí un hilo lee sin descanso y conserva únicamente el fotograma más reciente.
Descartar los intermedios es lo correcto en vigilancia en directo: mostrar
imagen actual importa más que no perder fotogramas, y acumularlos solo añadiría
retardo creciente.
"""

import threading
import time

import cv2


class FrameGrabber:
    """
    Lector de una fuente de vídeo con reconexión automática.

    Uso:
        grabber = FrameGrabber(source)
        grabber.start()
        while ...:
            frame = grabber.read(timeout=5.0)
        grabber.stop()
    """

    def __init__(self, source, reconnect_delay: float = 2.0):
        self.source = self._normalizar(source)
        self.reconnect_delay = reconnect_delay

        self._frame = None
        self._contador = 0          # se incrementa con cada fotograma nuevo
        self._condicion = threading.Condition()
        self._parar = threading.Event()
        self._hilo = None
        self._abierto = False

    # -- Apertura de la fuente ----------------------------------------------

    @staticmethod
    def _normalizar(source):
        """Convierte a int los índices de cámara USB indicados como texto."""
        if isinstance(source, str) and source.isdigit():
            return int(source)
        return source

    def _abrir(self):
        """
        Abre la fuente. Si viene sin esquema, prueba las variantes habituales,
        que es como estaba resuelto antes en el generador de vídeo.
        """
        candidatas = [self.source]
        if isinstance(self.source, str) and not str(self.source).startswith(
                ("rtsp://", "http://", "https://")):
            candidatas = [
                f"http://{self.source}",
                f"http://{self.source}/video",
                f"rtsp://{self.source}",
                f"rtsp://{self.source}/video",
            ]

        for candidata in candidatas:
            cap = cv2.VideoCapture(candidata)
            # Búfer mínimo: interesa el fotograma actual, no el histórico
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if cap.isOpened():
                self.source = candidata
                return cap
            cap.release()
        return None

    # -- Ciclo de vida -------------------------------------------------------

    def start(self):
        if self._hilo is not None and self._hilo.is_alive():
            return self
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        return self

    def stop(self):
        """Detiene el hilo y espera brevemente a que suelte la fuente."""
        self._parar.set()
        with self._condicion:
            self._condicion.notify_all()
        if self._hilo is not None:
            self._hilo.join(timeout=3.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- Lectura -------------------------------------------------------------

    def read(self, timeout: float = 5.0):
        """
        Devuelve el siguiente fotograma NUEVO, o None si no llega a tiempo.

        Se espera a que cambie el contador en lugar de devolver lo que haya:
        si la inferencia va más rápida que la cámara, reprocesar el mismo
        fotograma consumiría GPU sin aportar nada.
        """
        limite = time.monotonic() + timeout
        with self._condicion:
            visto = self._contador
            while self._contador == visto and not self._parar.is_set():
                restante = limite - time.monotonic()
                if restante <= 0:
                    return None
                self._condicion.wait(timeout=restante)
            return self._frame

    @property
    def is_open(self) -> bool:
        return self._abierto

    # -- Interior ------------------------------------------------------------

    def _bucle(self):
        cap = None
        try:
            while not self._parar.is_set():
                if cap is None:
                    cap = self._abrir()
                    if cap is None:
                        self._abierto = False
                        # Espera interrumpible para no retrasar el cierre
                        if self._parar.wait(self.reconnect_delay):
                            break
                        continue
                    self._abierto = True

                ok, frame = cap.read()
                if not ok:
                    # Corte de emisión: se cierra y se reintenta desde cero
                    self._abierto = False
                    cap.release()
                    cap = None
                    if self._parar.wait(self.reconnect_delay):
                        break
                    continue

                with self._condicion:
                    self._frame = frame
                    self._contador += 1
                    self._condicion.notify_all()
        finally:
            # Sin esto la fuente quedaba abierta al desconectarse el cliente,
            # dejando conexiones colgando contra la cámara.
            if cap is not None:
                cap.release()
            self._abierto = False
            with self._condicion:
                self._condicion.notify_all()
