# Argos

Sistema de videovigilancia multi-cámara con detección y seguimiento de
personas y vehículos, reconocimiento facial y lectura automática de
matrículas (ALPR) en tiempo real.

> **Antes de ponerlo en servicio**
>
> El usuario de fábrica es `Admin` / `1234`. **Cámbialo en cuanto entres**
> (Configuración → Usuarios): una instalación que se deja con la contraseña
> que figura aquí es una instalación abierta.
>
> El programa no cifra el tráfico. En la red local es aceptable; si lo publicas
> fuera, ponlo detrás de un proxy con HTTPS y arranca con `COOKIE_SECURE=1`
> para que la cookie de sesión no viaje en claro.

## Características

- **Múltiples cámaras**: soporte para cámaras IP (RTSP, MJPEG, HTTP) y USB,
  con seguimiento (tracking) independiente por cámara.
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
- **Modo escritorio**: ventana nativa con `pywebview` además del acceso
  remoto vía navegador.
- **Aceleración GPU**: detección automática de GPU NVIDIA (CUDA) sin
  configuración adicional, con resolución adaptada si está disponible.

### Servidor de grabaciones (NVR)

- Grabación continua en segmentos MP4 por cámara, con rotación automática
  por espacio en disco y límite de días configurable.
- Selección automática del mejor codificador disponible: copia directa de
  H.264, `h264_nvenc` (NVIDIA), `h264_qsv` (Intel), `h264_amf` (AMD) o
  `libx264` como respaldo.
- Corrección de marcas de tiempo para fuentes MJPEG (evita que las
  grabaciones se reproduzcan a velocidad incorrecta).
- Reconexión automática ante cortes de red sin perder el archivo en curso.

### Reproductor de grabaciones

- Línea de tiempo interactiva con zoom (desde 2 minutos hasta el día
  completo) y miniatura de navegación.
- **Actualización automática cada 5 segundos**: la línea de tiempo refleja
  las grabaciones nuevas sin necesidad de recargar la página.
- Reproducción de segmentos encadenados, salto por tiempo, velocidades
  ×1 / ×2 / ×4 / ×6 / ×8 y rebobinado rápido.
- Marcado de tramo con tijera o introducción directa de fecha y hora para
  exportar a MP4.
- **Exportar sin recorte previo**: el botón "Exportar" está siempre
  disponible; si no hay un tramo seleccionado, se sugieren los últimos
  5 minutos del instante actual y se pueden ajustar manualmente.

### Registro de auditoría

- Historial inmutable de todas las acciones que modifican el sistema: altas
  y bajas de cámaras, cambios de configuración, gestión de usuarios, exportación
  de vídeo, borrado de grabaciones, inicio/cierre de sesión, etc.
- Registro de eventos "Stream iniciado" cuando un usuario abre el stream en
  vivo de una cámara, incluyendo nombre de cámara, usuario, rol e IP. Se anota
  una vez por sesión y cámara (con una ventana de 30 minutos), no una vez por
  petición HTTP: recargar la página, cambiar la distribución de la cuadrícula o
  reconectar generan peticiones nuevas que no deben multiplicar el registro.
- Desplegable de filtros que muestra **todas las categorías de acciones
  definidas** desde el primer uso, aunque aún no haya registros de ese tipo.
- Búsqueda libre por usuario, acción o fecha.
- **Caducidad automática**: las acciones que modifican el sistema se conservan
  365 días y los eventos de visionado 30, con un tope de seguridad de 200 000
  entradas. Los plazos son constantes del código y no se exponen en la interfaz,
  para que nadie pueda acortarlos y borrar el rastro de sus propias acciones.

### Permisos por operador

- Además del rol (administrador / operador), cada operador tiene permisos
  independientes sobre el material grabado: **ver grabaciones** y **exportar
  vídeo**, configurables por el administrador desde Ajustes → Usuarios.
- Se comprueban en el servidor, no solo ocultando botones en la interfaz.
- Un operador nuevo puede ver grabaciones pero no exportarlas: la exportación
  genera un archivo que ya vive fuera del sistema, donde ni la retención ni la
  auditoría alcanzan.

## Requisitos

Ver [`requirements.txt`](requirements.txt). Por defecto se instala PyTorch
para CPU; para usar GPU NVIDIA sigue las instrucciones incluidas en ese
archivo.

## Configuración

Copia `config.example.json` como `config.json` y ajusta las cámaras
(URLs RTSP y credenciales ONVIF). Si `config.json` no existe, la
aplicación arranca sin cámaras y estas se pueden añadir desde la interfaz.

Para habilitar el servidor de grabaciones lanza `nvr_server.py` de forma
independiente y configura su URL y clave de acceso desde la interfaz web
(Ajustes → Grabaciones).

## Uso

```bash
# Interfaz web
python app.py

# Servidor de grabaciones (proceso separado)
python nvr_server.py
```

o en modo escritorio:

```bash
python desktop.py
```

## Notas sobre cámaras MJPEG

Algunas cámaras IP (p. ej. Panasonic WebView Livescope) generan URLs con
identificadores de sesión temporales (`s=...`) que caducan. Si el NVR
reporta `Invalid data found when processing input`, comprueba que la URL
de la cámara sea la ruta persistente del flujo MJPEG (sin parámetros de
sesión) o usa la URL RTSP si el modelo la admite.


## Licencia

[AGPL-3.0](LICENSE).

La elección no es libre: el programa usa **Ultralytics YOLO**, que se
distribuye bajo AGPL-3.0, en el propio motor de detección y seguimiento
(`streamer.py`, `model_cache.py`, `tracking_utils.py`). La interfaz de
escritorio antigua usa además PyQt6, también copyleft. Publicar Argos bajo una
licencia permisiva como MIT sería incompatible con esas dependencias.

En la práctica, la AGPL permite lo que cabe esperar: descargar, ejecutar,
estudiar y modificar el programa, y desplegarlo donde haga falta. Lo que exige
es que quien distribuya una versión modificada —o la ofrezca como servicio a
través de la red— publique también su código fuente.

Quien necesite condiciones distintas puede adquirir una licencia comercial de
Ultralytics y sustituir esa dependencia; el resto del código de este
repositorio no lo impide.
