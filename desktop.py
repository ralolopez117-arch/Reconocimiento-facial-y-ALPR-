"""
desktop.py
----------
Lanzador del Centro de Monitoreo Inteligente.

Una sola base de código sirve los dos modos de uso:

  Escritorio  El servidor arranca en local y se muestra dentro de una ventana
              nativa. El usuario no ve un navegador ni tiene que escribir una
              URL: es una aplicación de escritorio normal.

  Remoto      El servidor se expone en la red local y cualquier equipo o móvil
              accede por navegador a la IP del anfitrión.

Ambos modos comparten exactamente la misma aplicación Flask (app.py), la misma
interfaz (templates/ y static/) y la misma configuración (config.json). No hay
funcionalidad que exista en uno y falte en el otro.

Uso
───
    python desktop.py                  Ventana de escritorio, solo local
    python desktop.py --remote         Ventana + acceso desde la red local
    python desktop.py --headless       Solo servidor, sin ventana (para un PC
                                       que hace de servidor permanente)
    python desktop.py --port 8080      Puerto alternativo
"""

import argparse
import functools
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# La consola de Windows suele usar cp1252, que no puede representar los acentos
# de los mensajes de esta aplicación. Sin esto, "detección" sale corrupto o
# lanza UnicodeEncodeError. Se fuerza UTF-8 con reemplazo tolerante.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass    # Salida ya redirigida o sin soporte: se continúa igualmente

# Al redirigir la salida a un archivo, Python la almacena en búfer por bloques y
# los mensajes de arranque no aparecen hasta que el proceso termina. Como este
# proceso es de larga duración, se fuerza el vaciado en cada mensaje.
print = functools.partial(print, flush=True)      # noqa: A001

# Título de la ventana nativa
WINDOW_TITLE = "Centro de Monitoreo Inteligente"

# Tamaño inicial de la ventana de escritorio
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

# Segundos máximos a esperar a que el servidor responda antes de rendirse
SERVER_STARTUP_TIMEOUT = 30.0


def get_lan_ip() -> str:
    """
    Devuelve la IP de este equipo en la red local.

    Abre un socket UDP hacia una dirección externa para que el sistema elija la
    interfaz de salida. No se envía ningún paquete ni hace falta conexión real
    a internet; solo sirve para preguntarle al SO qué IP usaría.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"      # Sin red: solo queda el bucle local
    finally:
        sock.close()


def port_is_free(host: str, port: int) -> bool:
    """True si se puede abrir un socket de escucha en host:port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def serve(host: str, port: int) -> None:
    """
    Arranca la aplicación Flask con waitress.

    Se usa waitress en lugar del servidor de desarrollo de Flask porque los
    streams MJPEG mantienen una conexión HTTP abierta y de larga duración por
    cada cámara visible. waitress gestiona ese patrón con un pool de hilos real
    y no imprime la advertencia de "servidor de desarrollo".
    """
    from waitress import serve as waitress_serve
    from app import app

    # threads: una conexión queda ocupada de forma permanente por cada cámara en
    # pantalla, más las peticiones normales de la API. Con 16 caben las 8 cámaras
    # de la cuadrícula más grande y sobra margen para la interfaz.
    waitress_serve(app, host=host, port=port, threads=16, _quiet=True)


def wait_until_ready(url: str, timeout: float = SERVER_STARTUP_TIMEOUT) -> bool:
    """
    Espera a que el servidor responda.

    El primer arranque carga YOLO, EasyOCR y el motor de reconocimiento facial,
    lo que puede tardar bastante en CPU. Sin esta espera, la ventana nativa se
    abriría sobre un servidor que aún no escucha y mostraría un error de
    conexión.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0):
                return True
        except urllib.error.HTTPError:
            return True          # Responde algo: el servidor ya está vivo
        except (urllib.error.URLError, socket.timeout, ConnectionError):
            time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Centro de Monitoreo Inteligente — escritorio y acceso remoto",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--remote", action="store_true",
        help="exponer el servidor en la red local para acceder desde otros equipos",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="no abrir ventana; solo servir (implica --remote)",
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="puerto de escucha (por defecto: 5000)",
    )
    args = parser.parse_args()

    # Sin ventana, el único sentido de arrancar es servir a otros equipos
    remote = args.remote or args.headless

    # En modo local se escucha solo en el bucle interno: nada sale del equipo.
    # En modo remoto se escucha en todas las interfaces.
    bind_host = "0.0.0.0" if remote else "127.0.0.1"
    local_url = f"http://127.0.0.1:{args.port}"

    if not port_is_free(bind_host, args.port):
        print(f"ERROR: el puerto {args.port} ya está en uso.")
        print("Puede que la aplicación ya se esté ejecutando, o que otro")
        print(f"programa lo ocupe. Prueba con: python desktop.py --port {args.port + 1}")
        return 1

    print(f"Iniciando {WINDOW_TITLE}...")
    print("Cargando modelos de detección (puede tardar en el primer arranque)")

    # El servidor va en un hilo demonio: al cerrarse la ventana, el proceso
    # termina sin dejar el servidor colgado.
    server_thread = threading.Thread(
        target=serve, args=(bind_host, args.port), daemon=True
    )
    server_thread.start()

    if not wait_until_ready(local_url):
        print(f"ERROR: el servidor no respondió tras {SERVER_STARTUP_TIMEOUT:.0f} segundos.")
        return 1

    print(f"\n  Local:  {local_url}")
    if remote:
        print(f"  Red:    http://{get_lan_ip()}:{args.port}")
        print("\n  Acceso remoto activado. Si otros equipos no conectan, permite")
        print(f"  el puerto {args.port} en el Firewall de Windows.")
    print()

    if args.headless:
        print("Modo servidor. Pulsa Ctrl+C para detener.")
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=1.0)
        except KeyboardInterrupt:
            print("\nDeteniendo...")
        return 0

    try:
        import webview
    except ImportError:
        print("ERROR: falta pywebview, necesario para la ventana de escritorio.")
        print("  Instálalo con:  pip install pywebview")
        print(f"  O usa el modo servidor:  python desktop.py --headless")
        return 1

    webview.create_window(
        WINDOW_TITLE, local_url,
        width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
        min_size=(1000, 700),
    )
    # Bloquea hasta que el usuario cierra la ventana; entonces el hilo demonio
    # del servidor muere con el proceso.
    webview.start()
    print("Aplicación cerrada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
