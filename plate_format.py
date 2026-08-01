"""
plate_format.py
---------------
Validación y puntuación de candidatos a matrícula.

El detector de matrículas recorta la placa, pero dentro de ese recorte EasyOCR
devuelve varias regiones de texto: el nombre del estado o país, el lema, el
marco publicitario del concesionario y el número real. Aceptarlas todas llena
la base de datos de palabras ("DUNCAN", "RILE") que no son matrículas.

Este módulo aporta dos cosas:

1. Validación de formato — una matrícula real es una combinación corta de
   letras y dígitos. Las palabras del marco o del estado no lo son.

2. Puntuación de candidatos — cuando varias regiones pasan la validación, se
   elige la más plausible: la matrícula es el texto más grande y más centrado
   de la placa, mientras que el estado va arriba y el concesionario abajo.
"""

import re

# ---------------------------------------------------------------------------
# Patrones por país
#
# Cada patrón se aplica al texto ya normalizado: solo A-Z y 0-9, en mayúsculas
# y sin espacios ni guiones. Los formatos reales admiten variantes, así que los
# patrones son deliberadamente permisivos: el objetivo es descartar palabras,
# no rechazar matrículas legítimas poco comunes.
# ---------------------------------------------------------------------------
PLATE_PATTERNS = {
    # Genérico: 4 a 8 caracteres alfanuméricos con al menos un dígito.
    # Es el más seguro cuando se vigilan cámaras de varios países.
    "generic": r"^(?=.*\d)[A-Z0-9]{4,8}$",

    # ---------------------------- América ----------------------------
    # Argentina: 2 letras + 3 dígitos + 2 letras (Mercosur), o el anterior de
    # 3 letras + 3 dígitos.
    "ar": r"^([A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$",

    # Bolivia: 3 o 4 dígitos + 3 letras.
    "bo": r"^\d{3,4}[A-Z]{3}$",

    # Brasil: Mercosur de 3 letras + dígito + letra + 2 dígitos, y el anterior
    # de 3 letras + 4 dígitos.
    "br": r"^([A-Z]{3}\d[A-Z]\d{2}|[A-Z]{3}\d{4})$",

    # Canadá: varía por provincia. De 5 a 8 alfanuméricos con al menos un
    # dígito, igual de permisivo que el de Estados Unidos.
    "ca": r"^(?=.*\d)[A-Z0-9]{5,8}$",

    # Chile: 4 letras + 2 dígitos (desde 2007), o 2 letras + 4 dígitos.
    "cl": r"^([A-Z]{4}\d{2}|[A-Z]{2}\d{4})$",

    # Colombia: 3 letras + 3 dígitos (automóviles), 3 letras + 2 dígitos + 1
    # letra (motocicletas), y 3 dígitos + 3 letras (motocarros y tricimóviles).
    "co": r"^([A-Z]{3}\d{3}|[A-Z]{3}\d{2}[A-Z]|\d{3}[A-Z]{3})$",

    # Costa Rica: 6 dígitos (particulares) o 3 letras + 3 dígitos.
    "cr": r"^(\d{6}|[A-Z]{3}\d{3})$",

    # Ecuador: 3 letras + 3 o 4 dígitos.
    "ec": r"^[A-Z]{3}\d{3,4}$",

    # Estados Unidos: muy variable entre estados. De 5 a 8 alfanuméricos con al
    # menos un dígito; es lo más restrictivo que se puede exigir sin perder
    # matrículas válidas.
    "us": r"^(?=.*\d)[A-Z0-9]{5,8}$",

    # Guatemala: letra de categoría + 3 dígitos + 3 letras.
    "gt": r"^[A-Z]\d{3}[A-Z]{3}$",

    # México: 3 letras + 3 dígitos (ABC123), o 3 dígitos + 3 letras en algunos
    # estados, más el formato de carga con 2 letras.
    "mx": r"^([A-Z]{3}\d{3}|\d{3}[A-Z]{3}|[A-Z]{2}\d{4,5})$",

    # Panamá: 2 letras + 4 dígitos, o 6 dígitos.
    "pa": r"^([A-Z]{2}\d{4}|\d{6})$",

    # Paraguay: Mercosur de 4 letras + 3 dígitos, y el anterior de 3 y 3.
    "py": r"^([A-Z]{4}\d{3}|[A-Z]{3}\d{3})$",

    # Perú: 3 letras + 3 dígitos, con variante de 3 dígitos + 3 letras.
    "pe": r"^([A-Z]{3}\d{3}|\d{3}[A-Z]{3})$",

    # República Dominicana: letra de categoría + 6 dígitos.
    "do": r"^[A-Z]\d{6}$",

    # Uruguay: Mercosur de 3 letras + 4 dígitos.
    "uy": r"^[A-Z]{3}\d{4}$",

    # Venezuela: combinaciones de 2 o 3 letras con 2 o 3 dígitos y sufijo
    # opcional de letras. Es de los formatos más heterogéneos de la región.
    "ve": r"^([A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{2}[A-Z]|[A-Z]{3}\d{3})$",

    # ----------------------------- Europa -----------------------------
    # Alemania: 1-3 letras de distrito + 1-2 letras + 1-4 dígitos.
    "de": r"^[A-Z]{2,5}\d{1,4}$",

    # Austria: 1-2 letras de distrito + dígitos + letras finales opcionales.
    "at": r"^[A-Z]{1,2}\d{1,5}[A-Z]{0,2}$",

    # Bélgica: dígito + 3 letras + 3 dígitos (formato actual).
    "be": r"^(\d[A-Z]{3}\d{3}|[A-Z]{3}\d{3})$",

    # Dinamarca: 2 letras + 5 dígitos.
    "dk": r"^[A-Z]{2}\d{5}$",

    # España: 4 dígitos + 3 letras (formato desde 2000), y el antiguo
    # provincial de 1 o 2 letras + 4 dígitos + 1 o 2 letras.
    "es": r"^(\d{4}[A-Z]{3}|[A-Z]{1,2}\d{4}[A-Z]{1,2})$",

    # Francia: 2 letras + 3 dígitos + 2 letras (desde 2009), y el anterior
    # de 1-4 dígitos + 1-3 letras + 2 dígitos.
    "fr": r"^([A-Z]{2}\d{3}[A-Z]{2}|\d{1,4}[A-Z]{1,3}\d{2})$",

    # Grecia: 3 letras + 4 dígitos.
    "gr": r"^[A-Z]{3}\d{4}$",

    # Irlanda: año de 2 o 3 dígitos + 1 o 2 letras de condado + serie.
    "ie": r"^\d{2,3}[A-Z]{1,2}\d{1,5}$",

    # Italia: 2 letras + 3 dígitos + 2 letras.
    "it": r"^[A-Z]{2}\d{3}[A-Z]{2}$",

    # Noruega: 2 letras + 5 dígitos.
    "no": r"^[A-Z]{2}\d{5}$",

    # Países Bajos: seis caracteres en grupos alternos de letras y dígitos.
    "nl": r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9]{6}$",

    # Polonia: 2 o 3 letras de provincia + 4 o 5 alfanuméricos. La serie lleva
    # siempre algún dígito; sin exigirlo, el patrón aceptaría palabras enteras.
    "pl": r"^(?=.*\d)[A-Z]{2,3}[A-Z0-9]{4,5}$",

    # Portugal: tres pares alternos de letras y dígitos.
    "pt": r"^([A-Z]{2}\d{2}[A-Z]{2}|\d{2}[A-Z]{2}\d{2}|\d{2}\d{2}[A-Z]{2}|[A-Z]{2}\d{4})$",

    # Reino Unido: 2 letras + 2 dígitos + 3 letras (desde 2001), y el anterior
    # de letra + 1-3 dígitos + 3 letras.
    "gb": r"^([A-Z]{2}\d{2}[A-Z]{3}|[A-Z]\d{1,3}[A-Z]{3}|[A-Z]{3}\d{1,3}[A-Z])$",

    # República Checa: dígito + letra + 5 alfanuméricos.
    "cz": r"^\d[A-Z][A-Z0-9]\d{4}$",

    # Rumanía: 1 o 2 letras de condado + 2 o 3 dígitos + 3 letras.
    "ro": r"^[A-Z]{1,2}\d{2,3}[A-Z]{3}$",

    # Suecia: 3 letras + 2 dígitos + dígito o letra.
    "se": r"^[A-Z]{3}\d{2}[A-Z0-9]$",

    # Suiza: 2 letras de cantón + hasta 6 dígitos.
    "ch": r"^[A-Z]{2}\d{1,6}$",
}

# Patrón usado si la configuración no indica otro
DEFAULT_PATTERN = "generic"

# ---------------------------------------------------------------------------
# Descripción de cada formato para la interfaz
#
# El orden de este diccionario es el que se muestra en el selector: primero el
# genérico, luego los países por nombre. Cada entrada lleva un ejemplo real para
# que el usuario reconozca su formato sin conocer la expresión regular.
# ---------------------------------------------------------------------------
PLATE_FORMAT_LABELS = {
    "generic": {
        "region": "General",
        "name": "Genérico (varios países)",
        "example": "4-8 caracteres con al menos un dígito",
        "description": "Acepta cualquier matrícula alfanumérica. Úsalo si vigilas "
                       "cámaras de países distintos o no estás seguro del formato.",
    },

    # ---------------------------- América ----------------------------
    "ar": {
        "region": "América",
        "name": "Argentina",
        "example": "AB123CD · ABC123",
        "description": "Formato Mercosur y el anterior de 3 letras y 3 dígitos.",
    },
    "bo": {
        "region": "América",
        "name": "Bolivia",
        "example": "1234ABC · 123ABC",
        "description": "Dígitos seguidos de tres letras.",
    },
    "br": {
        "region": "América",
        "name": "Brasil",
        "example": "ABC1D23 · ABC1234",
        "description": "Formato Mercosur y el anterior de 3 letras y 4 dígitos.",
    },
    "ca": {
        "region": "América",
        "name": "Canadá",
        "example": "ABC123 · 123ABCD",
        "description": "De 5 a 8 caracteres con al menos un dígito. Cada provincia "
                       "tiene su propio formato, así que es poco restrictivo.",
    },
    "cl": {
        "region": "América",
        "name": "Chile",
        "example": "BBCC12 · AB1234",
        "description": "Formato desde 2007 y el anterior de 2 letras y 4 dígitos.",
    },
    "co": {
        "region": "América",
        "name": "Colombia",
        "example": "ABC123 · ABC12D · 123ABC",
        "description": "Formato de automóviles, el de motocicletas y el de "
                       "motocarros y tricimóviles.",
    },
    "cr": {
        "region": "América",
        "name": "Costa Rica",
        "example": "123456 · ABC123",
        "description": "Particulares de seis dígitos y series con letras.",
    },
    "ec": {
        "region": "América",
        "name": "Ecuador",
        "example": "ABC1234 · ABC123",
        "description": "Tres letras seguidas de tres o cuatro dígitos.",
    },
    "us": {
        "region": "América",
        "name": "Estados Unidos",
        "example": "8XKR204",
        "description": "De 5 a 8 caracteres con al menos un dígito. Los formatos "
                       "varían mucho entre estados, así que es poco restrictivo.",
    },
    "gt": {
        "region": "América",
        "name": "Guatemala",
        "example": "P123ABC",
        "description": "Letra de categoría, tres dígitos y tres letras.",
    },
    "mx": {
        "region": "América",
        "name": "México",
        "example": "ABC123 · 123ABC",
        "description": "Formatos de automóvil particular y de carga.",
    },
    "pa": {
        "region": "América",
        "name": "Panamá",
        "example": "AB1234 · 123456",
        "description": "Dos letras con cuatro dígitos, o seis dígitos.",
    },
    "py": {
        "region": "América",
        "name": "Paraguay",
        "example": "ABCD123 · ABC123",
        "description": "Formato Mercosur y el anterior de 3 letras y 3 dígitos.",
    },
    "pe": {
        "region": "América",
        "name": "Perú",
        "example": "ABC123 · 123ABC",
        "description": "Formato de 3 letras y 3 dígitos, y su variante invertida.",
    },
    "do": {
        "region": "América",
        "name": "República Dominicana",
        "example": "A123456",
        "description": "Letra de categoría seguida de seis dígitos.",
    },
    "uy": {
        "region": "América",
        "name": "Uruguay",
        "example": "ABC1234",
        "description": "Formato Mercosur de tres letras y cuatro dígitos.",
    },
    "ve": {
        "region": "América",
        "name": "Venezuela",
        "example": "AB123CD · ABC12D",
        "description": "Combinaciones de letras y dígitos; es de los formatos más "
                       "heterogéneos de la región.",
    },

    # ----------------------------- Europa -----------------------------
    "de": {
        "region": "Europa",
        "name": "Alemania",
        "example": "MAB1234 · BMW1",
        "description": "Letras de distrito y de serie seguidas de hasta cuatro dígitos.",
    },
    "at": {
        "region": "Europa",
        "name": "Austria",
        "example": "W12345A",
        "description": "Letras de distrito, dígitos y letras finales opcionales.",
    },
    "be": {
        "region": "Europa",
        "name": "Bélgica",
        "example": "1ABC123",
        "description": "Dígito inicial, tres letras y tres dígitos.",
    },
    "dk": {
        "region": "Europa",
        "name": "Dinamarca",
        "example": "AB12345",
        "description": "Dos letras seguidas de cinco dígitos.",
    },
    "es": {
        "region": "Europa",
        "name": "España",
        "example": "1234BCD · M1234AB",
        "description": "Formato desde 2000 y el antiguo provincial.",
    },
    "fr": {
        "region": "Europa",
        "name": "Francia",
        "example": "AB123CD · 123ABC45",
        "description": "Formato desde 2009 y el anterior por departamento.",
    },
    "gr": {
        "region": "Europa",
        "name": "Grecia",
        "example": "ABC1234",
        "description": "Tres letras seguidas de cuatro dígitos.",
    },
    "ie": {
        "region": "Europa",
        "name": "Irlanda",
        "example": "191D12345",
        "description": "Año, letra de condado y número de serie.",
    },
    "it": {
        "region": "Europa",
        "name": "Italia",
        "example": "AB123CD",
        "description": "Dos letras, tres dígitos y dos letras.",
    },
    "no": {
        "region": "Europa",
        "name": "Noruega",
        "example": "AB12345",
        "description": "Dos letras seguidas de cinco dígitos.",
    },
    "nl": {
        "region": "Europa",
        "name": "Países Bajos",
        "example": "AB123C · 12ABC3",
        "description": "Seis caracteres en grupos alternos de letras y dígitos.",
    },
    "pl": {
        "region": "Europa",
        "name": "Polonia",
        "example": "WA12345",
        "description": "Letras de provincia seguidas de la serie local.",
    },
    "pt": {
        "region": "Europa",
        "name": "Portugal",
        "example": "AA12BC · 12AB34",
        "description": "Tres pares alternos de letras y dígitos.",
    },
    "gb": {
        "region": "Europa",
        "name": "Reino Unido",
        "example": "AB12CDE",
        "description": "Formato desde 2001 y los anteriores con letra de año.",
    },
    "cz": {
        "region": "Europa",
        "name": "República Checa",
        "example": "1AB2345",
        "description": "Dígito de región, letras y serie numérica.",
    },
    "ro": {
        "region": "Europa",
        "name": "Rumanía",
        "example": "B123ABC",
        "description": "Letras de condado, dígitos y tres letras.",
    },
    "se": {
        "region": "Europa",
        "name": "Suecia",
        "example": "ABC12D · ABC123",
        "description": "Tres letras, dos dígitos y un dígito o letra final.",
    },
    "ch": {
        "region": "Europa",
        "name": "Suiza",
        "example": "ZH123456",
        "description": "Dos letras de cantón seguidas de hasta seis dígitos.",
    },
}


def list_formats():
    """
    Devuelve los formatos disponibles para el selector de la interfaz.

    Returns:
        Lista de dicts con las claves: key, region, name, example, description.
        El orden es el de PLATE_FORMAT_LABELS: primero el genérico, luego cada
        región con sus países en orden alfabético.
    """
    return [
        {"key": key, **labels}
        for key, labels in PLATE_FORMAT_LABELS.items()
        if key in PLATE_PATTERNS
    ]

# Longitudes absolutas admisibles, sea cual sea el patrón. Actúan como red de
# seguridad frente a un patrón personalizado demasiado laxo.
MIN_PLATE_LEN = 4

# Nueve caracteres cubre incluso los formatos más largos admitidos, como el
# irlandés (191D12345). El patrón "generic" es más estricto por su cuenta y se
# queda en ocho; este tope solo actúa como red de seguridad general.
MAX_PLATE_LEN = 9

# Longitud mínima exigida cuando el texto no contiene ninguna letra. Las
# matrículas puramente numéricas existen, pero una cadena corta de dígitos suele
# ser una lectura parcial o un número de la carrocería.
MIN_NUMERIC_PLATE_LEN = 5

# Palabras que aparecen con frecuencia en marcos de concesionario, lemas y
# nombres de estado, y que un patrón permisivo podría dejar pasar si contienen
# algún dígito. Se comparan sobre el texto ya normalizado.
COMMON_NON_PLATE_WORDS = {
    # Términos del sector que suelen ir impresos en el marco de la placa
    "AUTO", "AUTOS", "MOTORS", "MOTOR", "DEALER", "SALES", "SERVICE",
    "TRUCK", "TRUCKS", "CAR", "CARS", "USED", "NEW", "RENT", "RENTAL",
    "FORD", "CHEVY", "TOYOTA", "HONDA", "NISSAN", "MAZDA", "KIA", "HYUNDAI",
    "JEEP", "DODGE", "RAM", "GMC", "BUICK", "LEXUS", "SUBARU", "VOLVO",
    # Lemas y textos institucionales habituales en matrículas
    "STATE", "COUNTY", "CITY", "GOV", "OFFICIAL", "EXEMPT", "TEMP",
    "TEMPORARY", "TAG", "PLATE", "REGISTRATION", "EXPIRES", "VALID",
    "AMERICA", "UNITED", "STATES", "MEXICO", "ESPANA", "REPUBLICA",
}


def normalize(text: str) -> str:
    """
    Deja solo caracteres alfanuméricos, en mayúsculas.

    Se eliminan espacios, guiones y signos, que varían según cómo el OCR
    interprete la separación visual de la matrícula.
    """
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def is_valid_plate(text: str, pattern_key: str = DEFAULT_PATTERN) -> bool:
    """
    Comprueba si un texto normalizado tiene forma de matrícula.

    Args:
        text:        texto ya pasado por normalize()
        pattern_key: clave de PLATE_PATTERNS, o una expresión regular propia

    Returns:
        True si el texto puede ser una matrícula del formato indicado.
    """
    if not text:
        return False

    if not (MIN_PLATE_LEN <= len(text) <= MAX_PLATE_LEN):
        return False

    # Un texto de un solo carácter repetido ("0000", "AAAA") es casi siempre
    # ruido del OCR sobre un borde o un reflejo.
    if len(set(text)) == 1:
        return False

    if text in COMMON_NON_PLATE_WORDS:
        return False

    # Un texto de solo dígitos y muy corto ("0210") casi siempre es una lectura
    # parcial de la matrícula, o un número suelto de la carrocería. Existen
    # matrículas puramente numéricas, así que no se rechazan del todo: se les
    # exige algo más de longitud.
    if text.isdigit() and len(text) < MIN_NUMERIC_PLATE_LEN:
        return False

    # pattern_key puede ser una clave conocida o una regex personalizada
    pattern = PLATE_PATTERNS.get(pattern_key, pattern_key)
    try:
        return re.match(pattern, text) is not None
    except re.error:
        # Regex personalizada inválida: se recurre al patrón genérico en lugar
        # de dejar de detectar matrículas por un error de configuración.
        return re.match(PLATE_PATTERNS[DEFAULT_PATTERN], text) is not None


def score_candidate(text: str, prob: float, bbox, crop_shape) -> float:
    """
    Puntúa un candidato para elegir el más plausible dentro de una placa.

    Se combinan cuatro señales:

      Confianza    la del propio OCR.
      Altura       la matrícula es el texto más grande de la placa; el estado y
                   el marco del concesionario van en letra mucho menor.
      Centrado     la matrícula ocupa la franja central; el estado va arriba y
                   el concesionario abajo.
      Mezcla       una matrícula combina letras y dígitos. Una palabra no.

    Args:
        text:       texto normalizado del candidato
        prob:       confianza devuelta por EasyOCR (0..1)
        bbox:       4 vértices [[x,y], ...] tal como los devuelve EasyOCR
        crop_shape: forma (alto, ancho, ...) del recorte de la placa

    Returns:
        Puntuación; a mayor valor, más plausible como matrícula.
    """
    score = float(prob)

    try:
        ys = [float(point[1]) for point in bbox]
        crop_h = float(crop_shape[0])
        if crop_h <= 0:
            return score

        text_h = max(ys) - min(ys)
        y_center_rel = ((max(ys) + min(ys)) / 2.0) / crop_h

        # Altura relativa: hasta +0.5 para el texto que domina la placa
        score += min(text_h / crop_h, 1.0) * 0.5

        # Cercanía al centro vertical: hasta +0.3, decreciendo hacia los bordes
        score += (1.0 - min(abs(y_center_rel - 0.5) * 2.0, 1.0)) * 0.3
    except (TypeError, IndexError, ValueError):
        # Geometría inesperada: se puntúa solo por confianza y composición
        pass

    # Mezcla de letras y dígitos: +0.4. Es la señal que mejor separa una
    # matrícula real ("8XKR204") de una palabra del marco ("DUNCAN").
    has_digit = any(char.isdigit() for char in text)
    has_alpha = any(char.isalpha() for char in text)
    if has_digit and has_alpha:
        score += 0.4

    return score


def select_best_plate(ocr_results, crop_shape, min_confidence: float = 0.5,
                      pattern_key: str = DEFAULT_PATTERN):
    """
    Elige el mejor candidato a matrícula de entre las regiones de texto que
    EasyOCR encontró dentro de una placa.

    Un vehículo tiene una sola matrícula, así que se devuelve como mucho un
    resultado. Esto evita registrar a la vez el número, el estado y el marco
    del concesionario como si fueran tres detecciones distintas.

    Args:
        ocr_results:    lista de (bbox, texto, confianza) de EasyOCR
        crop_shape:     forma del recorte de la placa
        min_confidence: confianza mínima del OCR para considerar un candidato
        pattern_key:    formato de matrícula a exigir

    Returns:
        (texto, confianza) del mejor candidato, o None si ninguno es válido.
    """
    best = None
    best_score = float("-inf")

    for bbox, raw_text, prob in ocr_results:
        if prob < min_confidence:
            continue

        text = normalize(raw_text)
        if not is_valid_plate(text, pattern_key):
            continue

        candidate_score = score_candidate(text, prob, bbox, crop_shape)
        if candidate_score > best_score:
            best_score = candidate_score
            best = (text, float(prob))

    return best
