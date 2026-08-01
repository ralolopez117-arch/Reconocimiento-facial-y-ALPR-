"""
plate_types.py
--------------
Tipos de placa por país, expresados como subformatos de texto.

Cuando el usuario registra una matrícula en la lista de vigilancia puede indicar
de qué tipo es (particular, servicio público, remolque, diplomática...). Eso
aporta precisión en dos momentos:

1. Al registrar — se avisa si la matrícula escrita no encaja con el tipo
   elegido, normalmente por una errata.

2. Al detectar — una lectura solo genera alerta si además de coincidir con el
   patrón vigilado encaja con el subformato de su tipo. Una placa de remolque
   colombiana empieza por R; si el texto leído no empieza por R, no es esa placa.

Alcance honesto
───────────────
Solo unos pocos tipos se distinguen realmente por el TEXTO. El caso claro es el
remolque colombiano (prefijo R) y las placas diplomáticas de varios países.

En el resto, los tipos se diferencian por el COLOR de la placa, no por su
formato: en el bloque Mercosur un particular y un vehículo de servicio público
comparten exactamente el mismo patrón de caracteres y solo cambia el color de
los dígitos. Para esos casos el tipo se guarda como etiqueta descriptiva y no
añade validación: su `pattern` es None y se usa el formato base del país.

Esta distinción es deliberada. Inventar subformatos que no existen provocaría
que se descartaran matrículas legítimas.
"""

import re

from plate_format import PLATE_PATTERNS, normalize

# Clave del tipo "sin especificar". Es el valor por defecto y no impone ninguna
# restricción adicional sobre el formato base del país.
ANY_TYPE = "any"

_ANY_TYPE_ENTRY = {
    "key": ANY_TYPE,
    "name": "Cualquier tipo",
    "color": "",
    "pattern": None,
    "description": "No restringe el formato. Úsalo si no conoces el tipo de placa.",
}

# ---------------------------------------------------------------------------
# Tipos genéricos
#
# Se aplican a los países sin entrada propia en PLATE_TYPES. Ninguno lleva
# patrón: sirven para etiquetar la entrada de la lista de vigilancia, no para
# validar, porque en la mayoría de países estos tipos se distinguen por el color
# de la placa y no por su combinación de caracteres.
# ---------------------------------------------------------------------------
GENERIC_TYPES = [
    {
        "key": "particular",
        "name": "Particular",
        "color": "Varía según el país",
        "pattern": None,
        "description": "Vehículo de uso privado.",
    },
    {
        "key": "publico",
        "name": "Servicio público / comercial",
        "color": "Varía según el país",
        "pattern": None,
        "description": "Taxis, autobuses y transporte de carga.",
    },
    {
        "key": "oficial",
        "name": "Oficial / diplomática",
        "color": "Varía según el país",
        "pattern": None,
        "description": "Vehículos gubernamentales y del cuerpo diplomático.",
    },
]

# ---------------------------------------------------------------------------
# Tipos específicos por país
#
# Solo se define `pattern` cuando el tipo tiene realmente un formato de texto
# propio. En los demás casos vale None y se aplica el formato base del país.
# ---------------------------------------------------------------------------
PLATE_TYPES = {
    # ------------------------------- Colombia -------------------------------
    # El único país de la lista donde un tipo (remolque) tiene un formato de
    # texto inequívoco, gracias al prefijo R.
    "co": [
        {
            "key": "particular",
            "name": "Particular",
            "color": "Amarillo, letras negras",
            "pattern": r"^([A-Z]{3}\d{3}|[A-Z]{3}\d{2}[A-Z]|\d{3}[A-Z]{3})$",
            "description": "Carros y motos de uso privado. Incluye el formato "
                           "123ABC de motocarros y tricimóviles.",
        },
        {
            "key": "publico",
            "name": "Servicio público",
            "color": "Blanco, letras negras",
            "pattern": r"^([A-Z]{3}\d{3}|[A-Z]{3}\d{2}[A-Z])$",
            "description": "Taxis, buses y transporte de carga.",
        },
        {
            "key": "remolque",
            "name": "Remolque / semirremolque",
            "color": "Verde, letras blancas",
            "pattern": r"^R\d{5}$",
            "description": "Prefijo R seguido de cinco dígitos. Es el único tipo "
                           "que se reconoce con certeza por el texto.",
        },
        {
            "key": "diplomatico",
            "name": "Cuerpo diplomático o consular",
            "color": "Azul y blanco, letras negras",
            "pattern": r"^[A-Z]{2}\d{3,4}$",
            "description": "Placas del cuerpo diplomático y consular.",
        },
        {
            "key": "clasico",
            "name": "Clásico / antiguo",
            "color": "Blanco con franja azul",
            "pattern": None,
            "description": "Vehículos clásicos y antiguos. Conserva el formato "
                           "de su época, así que no se restringe.",
        },
    ],
}

# ---------------------------------------------------------------------------
# Bloque Mercosur
#
# Argentina, Brasil, Uruguay y Paraguay comparten el diseño: fondo blanco con
# franja azul superior. El tipo de servicio se distingue por el color de los
# caracteres, no por el formato, así que ningún tipo lleva patrón propio.
# ---------------------------------------------------------------------------
_MERCOSUR_TYPES = [
    {
        "key": "particular",
        "name": "Particular",
        "color": "Fondo blanco con franja azul, caracteres negros",
        "pattern": None,
        "description": "Vehículo de uso privado.",
    },
    {
        "key": "publico",
        "name": "Servicio público / comercial",
        "color": "Mismo diseño, caracteres de otro color según el país",
        "pattern": None,
        "description": "Comparte el formato de caracteres con las particulares; "
                       "solo cambia el color, así que no añade validación.",
    },
    {
        "key": "oficial",
        "name": "Oficial / diplomática",
        "color": "Mismo diseño, caracteres de otro color",
        "pattern": None,
        "description": "Vehículos gubernamentales y del cuerpo diplomático.",
    },
]

for _country in ("ar", "br", "uy", "py"):
    PLATE_TYPES[_country] = _MERCOSUR_TYPES


def list_types(country_key: str):
    """
    Devuelve los tipos de placa disponibles para un país.

    El primer elemento siempre es "Cualquier tipo", que es el valor por defecto.

    Args:
        country_key: clave de país de plate_format.PLATE_PATTERNS

    Returns:
        Lista de dicts con las claves: key, name, color, pattern, description.
    """
    specific = PLATE_TYPES.get(country_key)
    return [_ANY_TYPE_ENTRY] + (specific if specific is not None else GENERIC_TYPES)


def get_type(country_key: str, type_key: str):
    """Devuelve la definición de un tipo, o None si no existe para ese país."""
    for entry in list_types(country_key):
        if entry["key"] == type_key:
            return entry
    return None


def is_known_type(country_key: str, type_key: str) -> bool:
    """True si el tipo existe para el país indicado."""
    return get_type(country_key, type_key) is not None


def matches_type(text: str, country_key: str, type_key: str) -> bool:
    """
    Comprueba si un texto encaja con el subformato de un tipo de placa.

    Un tipo sin patrón propio (porque se distingue por color y no por texto) no
    impone restricción: se acepta cualquier texto que ya sea válido para el país.

    Args:
        text:        texto normalizado de la matrícula
        country_key: país configurado
        type_key:    tipo de placa a comprobar

    Returns:
        True si encaja, o si el tipo no impone formato.
    """
    if not text:
        return False

    entry = get_type(country_key, type_key)
    if entry is None or entry["pattern"] is None:
        # Tipo desconocido o sin formato propio: no se restringe nada aquí. La
        # validación del formato base del país se hace en plate_format.
        return True

    try:
        return re.match(entry["pattern"], normalize(text)) is not None
    except re.error:
        # Un patrón mal escrito no debe impedir el registro ni las alertas
        return True


def describe_mismatch(text: str, country_key: str, type_key: str):
    """
    Devuelve un aviso legible si el texto no encaja con el tipo elegido.

    Se usa al registrar en la lista de vigilancia, donde no se bloquea el
    guardado: solo se informa, porque los patrones no cubren todas las variantes
    históricas y especiales de cada país.

    Returns:
        Cadena con el aviso, o None si no hay discrepancia.
    """
    if matches_type(text, country_key, type_key):
        return None

    entry = get_type(country_key, type_key)
    name = entry["name"] if entry else type_key
    return (f'"{normalize(text)}" no encaja con el formato habitual de '
            f'"{name}". Se guardó igualmente por si es una variante válida.')


def country_has_specific_types(country_key: str) -> bool:
    """True si el país tiene tipos propios en lugar de los genéricos."""
    return country_key in PLATE_TYPES


# Comprobación de coherencia al importar: cada patrón de tipo debe compilar.
# Un fallo aquí es un error de programación, no de configuración del usuario.
for _country_key, _entries in PLATE_TYPES.items():
    assert _country_key in PLATE_PATTERNS, f"País sin formato base: {_country_key}"
    for _entry in _entries:
        if _entry["pattern"] is not None:
            re.compile(_entry["pattern"])
