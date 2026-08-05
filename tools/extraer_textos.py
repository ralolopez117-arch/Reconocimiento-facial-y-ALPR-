"""
tools/extraer_textos.py
-----------------------
Reúne el texto visible de la interfaz para traducirlo.

Se recorren las plantillas y se recoge lo que ve el usuario: el contenido de las
etiquetas y los atributos que se muestran (title, placeholder, aria-label). El
resultado es la lista de cadenas en español, que es el idioma de partida y hace
de clave en los catálogos de traducción.

Usar el propio texto como clave, en vez de un identificador tipo
"boton.guardar", evita tener que tocar cada plantilla para etiquetar cientos de
elementos, y hace que un idioma sin traducir se lea en español en lugar de
mostrar la clave en crudo.

Uso:
    python tools/extraer_textos.py            # muestra el recuento
    python tools/extraer_textos.py --json     # vuelca la lista
"""

import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLAS = ["templates/index.html", "templates/login.html"]

# Atributos cuyo valor lee el usuario
ATRIBUTOS = ("title", "placeholder", "aria-label")

# Se descartan las cadenas sin nada que traducir: números sueltos, símbolos,
# iconos y separadores.
def _traducible(texto: str) -> bool:
    t = texto.strip()
    if len(t) < 2:
        return False
    if not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", t):
        return False
    # Fragmentos de Jinja o de código que se hayan colado
    if "{{" in t or "{%" in t:
        return False
    return True


def _limpiar(texto: str) -> str:
    """Colapsa los espacios: el HTML sangrado mete saltos de línea dentro del texto."""
    return re.sub(r"\s+", " ", texto).strip()


def extraer_de_plantilla(ruta: str):
    with open(os.path.join(BASE_DIR, ruta), encoding="utf-8") as f:
        html = f.read()

    # Fuera lo que no es interfaz
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    encontradas = []

    # Texto entre etiquetas. Se excluyen los tramos con Jinja: llevan valores
    # que se rellenan en el servidor y no son texto fijo.
    for bruto in re.findall(r">([^<>]+)<", html):
        t = _limpiar(bruto)
        if _traducible(t):
            encontradas.append(t)

    for atributo in ATRIBUTOS:
        for bruto in re.findall(r'%s="([^"]*)"' % atributo, html):
            t = _limpiar(bruto)
            if _traducible(t):
                encontradas.append(t)

    return encontradas


def extraer_todo():
    vistas = {}
    for ruta in PLANTILLAS:
        for t in extraer_de_plantilla(ruta):
            vistas.setdefault(t, []).append(ruta)
    return vistas


if __name__ == "__main__":
    vistas = extraer_todo()
    if "--json" in sys.argv:
        print(json.dumps(sorted(vistas), ensure_ascii=False, indent=1))
    else:
        print("cadenas distintas:", len(vistas))
        for t in sorted(vistas)[:400]:
            print("   ", t)
