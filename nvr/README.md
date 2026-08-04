# Servidor de grabaciones (NVR)

Servicio independiente que graba las cámaras de forma continua, conserva los
últimos N días de cada una y sirve las grabaciones al reproductor de la
aplicación principal.

Puede ejecutarse **en el mismo equipo** que la aplicación o **en otro de la red
local**; la aplicación se conecta a él por su IP.

## Requisitos

- **ffmpeg** en el PATH o instalado con winget (`winget install Gyan.FFmpeg`).
  El servicio lo localiza aunque el PATH aún no se haya actualizado.
- Espacio en disco. Ver la sección de dimensionado.

## Arranque

```
python nvr_server.py
```

Opciones:

| Opción | Efecto |
|---|---|
| `--port 9000` | Otro puerto (por defecto 8001) |
| `--host 127.0.0.1` | Escuchar solo en local (por defecto en toda la red) |
| `--storage D:/videovigilancia` | Carpeta donde guardar las grabaciones |
| `--token` | Muestra la clave de acceso y termina |

La primera vez se crea `nvr_config.json` con una **clave de acceso** generada al
azar. Hay que copiarla en la configuración de la aplicación principal: sin ella
la API rechaza toda petición, y el NVR escucha en la red local.

## Cómo graba

Un proceso ffmpeg por cámara, en archivos de 5 minutos con el nombre
`aaaa-mm-dd_hh-mm-ss.mp4`. Cada cámara en su propio proceso: si una falla, las
demás siguen grabando.

Si la cámara ya emite **H.264**, el flujo se copia sin recodificar: consumo
prácticamente nulo y sin pérdida de calidad. Si emite MJPEG u otro formato, se
recodifica usando el mejor codificador disponible, preferiendo la GPU
(`h264_nvenc`, `h264_qsv`, `h264_amf`) sobre la CPU.

## Retención

Cada cámara tiene sus propios días de retención. Al superarlos **no se borra
todo**: se elimina únicamente el día más antiguo, de forma que la grabación
nueva va sustituyendo a la vieja y siempre quedan los últimos N días completos.

Existe además un **tope global de disco** (`max_total_gb`). Al superarlo se
recorta el día más antiguo de todas las cámaras, aunque no haya cumplido su
retención. Sin ese tope, configurar muchos días llenaría la unidad y la
grabación se detendría sin aviso.

## Dimensionado

Medido con las cámaras del proyecto:

| Tipo de fuente | Aproximado por cámara y día |
|---|---|
| H.264 copiado sin recodificar | ~12 GB |
| MJPEG recodificado a H.264 | ~6 GB |

Multiplica por el número de cámaras y los días de retención antes de decidir.
Con 4 cámaras y 7 días hacen falta del orden de 300 GB.

## API

Todas las rutas salvo `/api/health` exigen la cabecera `X-NVR-Token`.

| Ruta | Método | Para qué |
|---|---|---|
| `/api/health` | GET | Comprobar que responde y si hay ffmpeg |
| `/api/status` | GET | Estado de cada grabador y uso de disco |
| `/api/cameras` | GET / PUT | Consultar y definir qué se graba y con qué retención |
| `/api/settings` | PUT | Duración de segmento, fps, calidad, tope de disco |
| `/api/recordings/days` | GET | Días con grabación de una cámara |
| `/api/recordings/segments` | GET | Segmentos y tramos continuos para la línea de tiempo |
| `/api/recordings/at` | GET | Segmento que contiene un instante, con su desplazamiento |
| `/api/segment/<id>` | GET | Descarga del vídeo, con soporte de rangos HTTP |
| `/api/maintenance` | POST | Fuerza indexado y caducidad sin esperar al ciclo |

El soporte de rangos es lo que permite arrastrar la línea de tiempo sin
descargar el segmento entero.

## Archivos

```
nvr_server.py          Punto de entrada y API
nvr/config.py          Configuración propia del servicio
nvr/ffmpeg_tools.py    Localización de ffmpeg y comandos de grabación
nvr/recorder.py        Un proceso por cámara, más indexado y caducidad
nvr/storage.py         Índice de segmentos y política de retención
```

Las grabaciones y `nvr_config.json` no se versionan: son datos y credenciales
propios de cada instalación.
