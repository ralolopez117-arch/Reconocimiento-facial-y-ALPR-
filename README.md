# Reconocimiento facial y ALPR

Sistema de videovigilancia multi-cámara con detección y seguimiento de
personas y vehículos, reconocimiento facial y lectura automática de
matrículas (ALPR) en tiempo real.

## Características

- **Múltiples cámaras**: soporte para cámaras IP (RTSP) y USB, con
  seguimiento (tracking) independiente por cámara.
- **Reconocimiento facial**: detección con MTCNN y embeddings con
  InceptionResnetV1 (`facenet-pytorch`) contra una base de rostros
  registrados.
- **ALPR**: detección de matrículas con un modelo YOLO dedicado y lectura
  del texto con EasyOCR, con validación de formato por país/tipo de placa.
- **Detección y seguimiento**: modelos YOLO (Ultralytics) compartidos entre
  cámaras, con tracking vía `supervision`/ByteTrack.
- **Control PTZ**: control de cámaras PTZ por ONVIF sobre HTTP.
- **Interfaz web**: servidor Flask con streaming de vídeo, autenticación de
  usuarios, roles de administrador y expiración de sesión configurable.
- **Modo escritorio**: ventana nativa con `pywebview` (o interfaz PyQt6
  antigua) además del acceso remoto vía navegador.
- **Aceleración GPU**: detección automática de GPU NVIDIA (CUDA) sin
  configuración adicional, con resolución adaptada si está disponible.

## Requisitos

Ver [`requirements.txt`](requirements.txt). Por defecto se instala PyTorch
para CPU; para usar GPU NVIDIA sigue las instrucciones incluidas en ese
archivo.

## Configuración

Copia `config.example.json` como `config.json` y ajusta las cámaras
(URLs RTSP y credenciales ONVIF). Si `config.json` no existe, la
aplicación arranca sin cámaras y estas se pueden añadir desde la interfaz.

## Uso

```bash
python app.py
```

o en modo escritorio:

```bash
python desktop.py
```
