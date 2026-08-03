"""
benchmark_gpu.py
----------------
Comprueba que PyTorch usa la GPU y mide cuánto se gana frente a CPU.

Uso:
    python tools/benchmark_gpu.py

Mide sobre un fotograma real de la primera cámara configurada, para que los
números correspondan a la resolución con la que trabaja de verdad el sistema y
no a una imagen sintética.
"""

import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPETICIONES = 15
IMGSZ = 480


def cabecera(texto):
    print()
    print(texto)
    print("-" * len(texto))


def comprobar_torch():
    cabecera("PyTorch y CUDA")
    import torch
    print(f"  torch                : {torch.__version__}")
    print(f"  compilado con CUDA   : {torch.version.cuda or 'NO (build solo CPU)'}")
    disponible = torch.cuda.is_available()
    print(f"  cuda disponible      : {disponible}")
    if disponible:
        props = torch.cuda.get_device_properties(0)
        print(f"  GPU                  : {props.name}")
        print(f"  VRAM total           : {props.total_memory / 1024**3:.1f} GB")
    return disponible


def obtener_frame():
    """Un fotograma real; si la cámara no responde, uno sintético de 720p."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.json")
    try:
        camaras = json.load(open(ruta, encoding="utf-8")).get("cameras", [])
        for cam in camaras:
            cap = cv2.VideoCapture(cam["source"])
            ok, frame = cap.read()
            cap.release()
            if ok:
                print(f"  fotograma de         : {cam['name']} {frame.shape[1]}x{frame.shape[0]}")
                return frame
    except Exception:
        pass
    print("  fotograma            : sintético 1280x720 (ninguna cámara respondió)")
    return np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)


def medir(modelo, frame, device):
    modelo.to(device)
    modelo(frame, verbose=False, imgsz=IMGSZ, device=device)      # calentamiento
    if device == "cuda":
        import torch
        torch.cuda.synchronize()
    inicio = time.perf_counter()
    for _ in range(REPETICIONES):
        modelo(frame, verbose=False, imgsz=IMGSZ, device=device)
    if device == "cuda":
        import torch
        torch.cuda.synchronize()      # sin esto se mide el encolado, no el cálculo
    return (time.perf_counter() - inicio) / REPETICIONES * 1000


def main():
    hay_gpu = comprobar_torch()

    cabecera("Fotograma de prueba")
    frame = obtener_frame()

    from ultralytics import YOLO
    modelo = YOLO("yolov8n.pt")

    cabecera(f"Inferencia (imgsz={IMGSZ}, media de {REPETICIONES})")
    cpu_ms = medir(modelo, frame, "cpu")
    print(f"  CPU                  : {cpu_ms:7.1f} ms   {1000/cpu_ms:5.1f} FPS")

    if not hay_gpu:
        print()
        print("  La GPU no está disponible: PyTorch sigue siendo la compilación de CPU.")
        print("  Instálala con:")
        print("    pip install --index-url https://download.pytorch.org/whl/cu126 \\")
        print("        torch==2.13.0+cu126 torchvision==0.28.0")
        return

    gpu_ms = medir(modelo, frame, "cuda")
    print(f"  GPU                  : {gpu_ms:7.1f} ms   {1000/gpu_ms:5.1f} FPS")

    cabecera("Resultado")
    print(f"  aceleración          : {cpu_ms/gpu_ms:.1f}x")
    print(f"  con 4 cámaras        : {1000/cpu_ms/4:.1f} -> {1000/gpu_ms/4:.1f} FPS por cámara")
    print(f"  con 8 cámaras        : {1000/cpu_ms/8:.1f} -> {1000/gpu_ms/8:.1f} FPS por cámara")

    import torch
    usada = torch.cuda.max_memory_allocated() / 1024**2
    print(f"  VRAM usada por YOLO  : {usada:.0f} MB")
    print()
    print("  Recuerda que EasyOCR y facenet también pasarán a GPU: con 4 GB")
    print("  conviene vigilar el total con nvidia-smi durante el uso real.")


if __name__ == "__main__":
    main()
