"""
model_cache.py
--------------
Instancias compartidas de los modelos YOLO.

Antes cada generador de vídeo creaba su propio YOLO, de modo que con la
cuadrícula de 8 cámaras había 8 copias del mismo modelo en memoria compitiendo
por el mismo procesador. En CPU eso desperdicia memoria y rendimiento; en una
GPU de 4 GB agota la VRAM y la aplicación falla.

Aquí se mantiene una única instancia por ruta de modelo, y se serializa la
inferencia con un cerrojo. Serializar no es una pérdida: el acelerador —sea CPU
o GPU— ejecuta una inferencia cada vez de todas formas, así que varias copias
solo añadían consumo de memoria y cambios de contexto. Además evita que dos
hilos entren a la vez en el predictor de ultralytics, que guarda estado interno
entre llamadas y no está pensado para uso concurrente.
"""

import threading

from ultralytics import YOLO

# {ruta_modelo: SharedModel}
_modelos = {}

# Protege la creación del diccionario, no la inferencia
_lock_registro = threading.Lock()


def get_device():
    """
    Dispositivo a usar para la inferencia.

    Se consulta en tiempo de ejecución en lugar de fijarlo, para que instalar
    una compilación de PyTorch con CUDA baste para pasar a GPU sin tocar código.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class SharedModel:
    """
    Envoltorio de un modelo YOLO con acceso serializado.

    El cerrojo es imprescindible: ultralytics reutiliza un objeto predictor
    entre llamadas, y dos hilos entrando a la vez corrompen su estado con
    resultados mezclados o excepciones difíciles de reproducir.
    """

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._modelo = YOLO(ruta)
        self._lock = threading.Lock()
        self.device = get_device()

    def predict(self, frame, **kwargs):
        """Ejecuta la inferencia y devuelve el primer resultado."""
        with self._lock:
            return self._modelo(frame, **kwargs)[0]

    @property
    def names(self):
        """Nombres de clase del modelo, para las etiquetas."""
        return self._modelo.model.names


def get_model(ruta: str) -> SharedModel:
    """
    Devuelve la instancia compartida del modelo indicado, creándola si hace
    falta. Se puede llamar desde cualquier hilo.
    """
    modelo = _modelos.get(ruta)
    if modelo is not None:
        return modelo

    with _lock_registro:
        # Segunda comprobación: otro hilo pudo crearlo mientras se esperaba
        modelo = _modelos.get(ruta)
        if modelo is None:
            modelo = SharedModel(ruta)
            _modelos[ruta] = modelo
            print(f"[ModelCache] Modelo cargado en {modelo.device}: {ruta}")
        return modelo


def loaded_models():
    """Rutas de los modelos actualmente cargados. Útil para diagnóstico."""
    return list(_modelos)
