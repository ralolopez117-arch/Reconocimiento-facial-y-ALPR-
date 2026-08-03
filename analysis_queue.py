"""
analysis_queue.py
-----------------
Ejecuta la lectura de matrículas y el reconocimiento facial fuera del bucle de
vídeo.

Ambos análisis tardan cientos de milisegundos. Llamándolos dentro del bucle,
cada vehículo o persona que entra en el encuadre congela la emisión: los
fotogramas se acumulan, la imagen da un salto y el seguidor pierde la
asociación entre objetos por el hueco temporal.

Aquí los recortes se encolan y los procesa un grupo de hilos aparte. El bucle
de vídeo solo paga el coste de copiar el recorte.

La cola es acotada a propósito: si el análisis no da abasto se descartan
recortes nuevos en lugar de acumularlos. En vigilancia en directo, analizar un
vehículo que pasó hace medio minuto no aporta nada y la memoria crecería sin
límite.
"""

import queue
import threading

# Tamaño de la cola. Suficiente para absorber ráfagas —varios vehículos
# entrando a la vez— sin llegar a acumular trabajo obsoleto.
MAX_PENDIENTES = 24

# Hilos de análisis. Con más de dos, EasyOCR y facenet compiten entre sí por el
# mismo acelerador y no se gana rendimiento.
NUM_TRABAJADORES = 2


class AnalysisQueue:
    """Despacha recortes a los motores de ALPR y reconocimiento facial."""

    def __init__(self, max_pendientes: int = MAX_PENDIENTES,
                 num_trabajadores: int = NUM_TRABAJADORES):
        self._cola = queue.Queue(maxsize=max_pendientes)
        self._num_trabajadores = num_trabajadores
        self._hilos = []
        self._parar = threading.Event()
        self._iniciado = threading.Lock()

        # Contadores para diagnóstico
        self.encolados = 0
        self.descartados = 0
        self.procesados = 0
        self.fallidos = 0

    def start(self):
        with self._iniciado:
            if self._hilos:
                return self
            self._parar.clear()
            for i in range(self._num_trabajadores):
                h = threading.Thread(target=self._bucle, name=f"analisis-{i}",
                                     daemon=True)
                h.start()
                self._hilos.append(h)
            return self

    def stop(self):
        self._parar.set()
        for _ in self._hilos:
            try:
                self._cola.put_nowait(None)     # despertar a los trabajadores
            except queue.Full:
                pass
        for h in self._hilos:
            h.join(timeout=2.0)
        self._hilos = []

    def submit_plate(self, crop, camera_id):
        """Encola un recorte de vehículo para leer su matrícula."""
        return self._encolar(("plate", crop, camera_id))

    def submit_face(self, crop_rgb, camera_id):
        """Encola un recorte de persona para reconocimiento facial."""
        return self._encolar(("face", crop_rgb, camera_id))

    def _encolar(self, tarea) -> bool:
        """
        Returns:
            True si se encoló, False si se descartó por cola llena.
        """
        if not self._hilos:
            self.start()
        try:
            self._cola.put_nowait(tarea)
            self.encolados += 1
            return True
        except queue.Full:
            self.descartados += 1
            return False

    @property
    def pendientes(self) -> int:
        return self._cola.qsize()

    def stats(self) -> dict:
        return {
            "encolados": self.encolados,
            "procesados": self.procesados,
            "descartados": self.descartados,
            "fallidos": self.fallidos,
            "pendientes": self.pendientes,
        }

    def _bucle(self):
        # Import diferido: los motores cargan modelos pesados al importarse, y
        # hacerlo aquí evita encadenar esa carga con la de este módulo.
        from alpr_engine import process_plate_image
        from fr_engine import process_person_image

        while not self._parar.is_set():
            try:
                tarea = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            if tarea is None:
                break

            tipo, crop, camera_id = tarea
            try:
                if tipo == "plate":
                    process_plate_image(crop, camera_id)
                else:
                    process_person_image(crop, camera_id)
                self.procesados += 1
            except Exception as e:
                # Un recorte problemático no debe tumbar al trabajador: sin
                # esto, un fallo dejaría el análisis muerto para toda la sesión.
                self.fallidos += 1
                print(f"[AnalysisQueue] Error analizando ({tipo}): "
                      f"{type(e).__name__}: {e}")
            finally:
                self._cola.task_done()


# Instancia global compartida por todas las cámaras
analysis_queue = AnalysisQueue()
