"""
i18n.py
-------
Idioma de la interfaz.

El idioma de partida es el español: el texto original de las plantillas y del
JavaScript hace de clave, y cada catálogo es simplemente un diccionario del
español al idioma de destino.

Se usa el propio texto como clave, y no un identificador tipo "boton.guardar",
por dos razones. Evita marcar a mano cientos de elementos en las plantillas, con
el riesgo de romperlas; y cuando falta una traducción la frase se lee en español
en lugar de mostrar la clave en crudo, que es un fallo mucho más feo y difícil
de diagnosticar para quien usa el programa.

Los catálogos son archivos JSON sueltos en translations/. Añadir un idioma es
dejar caer un archivo ahí; corregir una frase mal traducida es editar una línea,
sin tocar código.
"""

import json
import os
import re
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSLATIONS_DIR = os.path.join(BASE_DIR, "translations")

# Idioma de las plantillas: no tiene catálogo porque es el texto original.
IDIOMA_ORIGEN = "es"

# Idiomas ofrecidos, con su nombre en su propia lengua: alguien que no entienda
# la interfaz actual necesita reconocer el suyo en la lista.
#
# 'rtl' marca las escrituras de derecha a izquierda, que además de traducirse
# necesitan invertir la disposición.
IDIOMAS = [
    {"codigo": "es", "nombre": "Español",    "rtl": False},
    {"codigo": "en", "nombre": "English",    "rtl": False},
    {"codigo": "pt", "nombre": "Português",  "rtl": False},
    {"codigo": "fr", "nombre": "Français",   "rtl": False},
    {"codigo": "de", "nombre": "Deutsch",    "rtl": False},
    {"codigo": "it", "nombre": "Italiano",   "rtl": False},
    {"codigo": "zh", "nombre": "中文",        "rtl": False},
    {"codigo": "ja", "nombre": "日本語",       "rtl": False},
    {"codigo": "ru", "nombre": "Русский",    "rtl": False},
    {"codigo": "ar", "nombre": "العربية",     "rtl": True},
    {"codigo": "hi", "nombre": "हिन्दी",        "rtl": False},
]

CODIGOS = tuple(i["codigo"] for i in IDIOMAS)

# Los catálogos se leen del disco una vez y se guardan en memoria: se consultan
# en cada carga de página y son archivos pequeños que no cambian en caliente.
_cache = {}
_cerrojo = threading.Lock()


def es_valido(codigo) -> bool:
    return codigo in CODIGOS


def info(codigo) -> dict:
    """Ficha del idioma, con el español como respaldo."""
    for i in IDIOMAS:
        if i["codigo"] == codigo:
            return i
    return IDIOMAS[0]


def catalogo(codigo) -> dict:
    """
    Diccionario español -> idioma pedido.

    Devuelve un diccionario vacío para el español y para cualquier idioma sin
    archivo: en ambos casos el texto se queda como está, que es justo lo
    correcto.
    """
    if not es_valido(codigo) or codigo == IDIOMA_ORIGEN:
        return {}

    with _cerrojo:
        if codigo in _cache:
            return _cache[codigo]

        ruta = os.path.join(TRANSLATIONS_DIR, f"{codigo}.json")
        datos = {}
        try:
            with open(ruta, encoding="utf-8") as f:
                cargado = json.load(f)
            # Se descartan las entradas vacías: una traducción a medias debe
            # caer al español, no dejar el hueco en blanco.
            datos = {k: v for k, v in cargado.items()
                     if isinstance(v, str) and v.strip()}
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as e:
            print(f"[i18n] No se pudo leer el catálogo {codigo}: {e}")

        _cache[codigo] = datos
        return datos


def traducir(texto: str, codigo: str) -> str:
    """Traduce una cadena suelta, devolviéndola tal cual si no hay traducción."""
    return catalogo(codigo).get(texto, texto)


def cobertura(codigo: str, totales: int) -> float:
    """Porcentaje traducido, para avisar de los idiomas incompletos."""
    if codigo == IDIOMA_ORIGEN:
        return 100.0
    return round(100.0 * len(catalogo(codigo)) / max(1, totales), 1)


def idiomas_disponibles():
    """
    Idiomas que se pueden ofrecer de verdad.

    Solo los que tienen catálogo, más el español, que es el original. Listar un
    idioma sin traducir sería peor que no listarlo: la interfaz seguiría en
    español mientras el selector afirma estar en otra lengua, y quien lo elija
    pensará que el programa está roto.

    Basta con dejar caer el archivo en translations/ para que aparezca.
    """
    return [i for i in IDIOMAS
            if i["codigo"] == IDIOMA_ORIGEN or catalogo(i["codigo"])]


def normalizar(codigo):
    """Devuelve el idioma pedido si está disponible, o el original si no."""
    disponibles = {i["codigo"] for i in idiomas_disponibles()}
    return codigo if codigo in disponibles else IDIOMA_ORIGEN


def recargar():
    """Olvida los catálogos en memoria. Útil tras editar un archivo."""
    with _cerrojo:
        _cache.clear()


# ---------------------------------------------------------------------------
# Traducción del HTML montado
# ---------------------------------------------------------------------------
# Atributos cuyo valor lee el usuario. El resto —clases, identificadores,
# rutas— se deja intacto: traducirlos rompería la página.
_ATRIBUTOS_VISIBLES = ("title", "placeholder", "aria-label")

# Los bloques de script y de estilo se apartan antes de tocar nada. Dentro hay
# cadenas y selectores que se parecen a texto de interfaz, y sustituirlos
# rompería el JavaScript o el CSS.
_BLOQUES_INTOCABLES = re.compile(r"<(script|style)\b[^>]*>.*?</\1>",
                                 re.S | re.I)

_TEXTO_ENTRE_ETIQUETAS = re.compile(r">([^<>]+)<")
_ATRIBUTO = re.compile(r'\b(%s)="([^"]*)"' % "|".join(_ATRIBUTOS_VISIBLES))


def _escapar(texto: str) -> str:
    """
    Protege el marcado frente a una traducción con < o >.

    No se toca el &: el texto original ya lleva entidades como &copy; y
    escaparlas las mostraría en crudo.
    """
    return texto.replace("<", "&lt;").replace(">", "&gt;")


def traducir_html(html: str, codigo: str) -> str:
    """
    Traduce el texto visible de una página ya renderizada.

    Se trabaja sobre el HTML final, en el servidor, para que la página llegue al
    navegador ya en su idioma. Traducirla en el navegador obligaría a pintarla
    primero en español, y ese parpadeo se ve en cada carga.
    """
    cat = catalogo(codigo)
    if not cat:
        return html

    # 1. Apartar lo que no se debe tocar
    apartados = []

    def _guardar(m):
        apartados.append(m.group(0))
        return "\x00BLOQUE%d\x00" % (len(apartados) - 1)

    html = _BLOQUES_INTOCABLES.sub(_guardar, html)

    # 2. Texto entre etiquetas. Se conservan los espacios de alrededor: el HTML
    #    va sangrado y quitarlos alteraría la separación entre elementos.
    def _texto(m):
        bruto = m.group(1)
        limpio = re.sub(r"\s+", " ", bruto).strip()
        traducido = cat.get(limpio)
        if not traducido:
            return m.group(0)
        izquierda = bruto[:len(bruto) - len(bruto.lstrip())]
        derecha = bruto[len(bruto.rstrip()):]
        return ">%s%s%s<" % (izquierda, _escapar(traducido), derecha)

    html = _TEXTO_ENTRE_ETIQUETAS.sub(_texto, html)

    # 3. Atributos visibles
    def _attr(m):
        nombre, valor = m.group(1), m.group(2)
        traducido = cat.get(re.sub(r"\s+", " ", valor).strip())
        if not traducido:
            return m.group(0)
        return '%s="%s"' % (nombre, traducido.replace('"', "&quot;"))

    html = _ATRIBUTO.sub(_attr, html)

    # 4. Devolver los bloques apartados
    def _restaurar(m):
        return apartados[int(m.group(1))]

    return re.sub(r"\x00BLOQUE(\d+)\x00", _restaurar, html)
