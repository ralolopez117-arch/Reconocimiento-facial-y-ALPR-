"""
label_mapper.py
---------------
Mapa de etiquetas en español para las 80 clases COCO usadas por YOLOv8.
También define umbrales de confianza mínima por clase para suprimir
detecciones improbables en contextos de cámaras de tráfico/videovigilancia urbana.
"""

# ---------------------------------------------------------------------------
# Traducción de las 80 clases COCO → Español
# ---------------------------------------------------------------------------
COCO_ES = {
    # Personas y animales
    0:  "Persona",
    1:  "Bicicleta",
    2:  "Auto",
    3:  "Motocicleta",
    4:  "Avión",
    5:  "Autobús",
    6:  "Tren",
    7:  "Camión",
    8:  "Barco",
    9:  "Semáforo",
    10: "Hidrante",
    11: "Señal de alto",
    12: "Parquímetro",
    13: "Banco",
    14: "Pájaro",
    15: "Gato",
    16: "Perro",
    17: "Caballo",
    18: "Oveja",
    19: "Vaca",
    20: "Elefante",
    21: "Oso",
    22: "Cebra",
    23: "Jirafa",
    24: "Mochila",
    25: "Paraguas",
    26: "Bolso",
    27: "Corbata",
    28: "Maleta",
    29: "Frisbee",
    30: "Esquís",
    31: "Snowboard",
    32: "Pelota",
    33: "Cometa",
    34: "Bate de béisbol",
    35: "Guante de béisbol",
    36: "Patineta",
    37: "Tabla de surf",
    38: "Raqueta de tenis",
    39: "Botella",
    40: "Copa de vino",
    41: "Taza",
    42: "Tenedor",
    43: "Cuchillo",
    44: "Cuchara",
    45: "Tazón",
    46: "Plátano",
    47: "Manzana",
    48: "Sándwich",
    49: "Naranja",
    50: "Brócoli",
    51: "Zanahoria",
    52: "Perro caliente",
    53: "Pizza",
    54: "Dona",
    55: "Pastel",
    56: "Silla",
    57: "Sofá",
    58: "Maceta",
    59: "Cama",
    60: "Comedor",
    61: "Inodoro",
    62: "Televisor",
    63: "Laptop",
    64: "Ratón",
    65: "Control remoto",
    66: "Teclado",
    67: "Teléfono celular",
    68: "Microondas",
    69: "Horno",
    70: "Tostador",
    71: "Lavabo",
    72: "Refrigerador",
    73: "Libro",
    74: "Reloj",
    75: "Jarrón",
    76: "Tijeras",
    77: "Peluche",
    78: "Secadora",
    79: "Cepillo de dientes",
}

# ---------------------------------------------------------------------------
# Clases que interesan en videovigilancia
#
# YOLOv8 reconoce las 80 clases de COCO, la mayoría irrelevantes aquí: semáforos,
# tazas, sillas, plátanos. Detectarlas ensucia la imagen, gasta identificadores
# de seguimiento y distrae de lo que importa.
#
# Se filtra en la propia inferencia, no después: ultralytics acepta la lista de
# clases y así se ahorra también el descarte posterior de cajas.
# ---------------------------------------------------------------------------

# Personas
CLASES_PERSONAS = {0}

# Vehículos de transporte, terrestres y no terrestres. Tren, avión y barco
# tienen umbrales altos más abajo, porque en cámaras urbanas casi siempre son
# confusiones con camiones o estructuras.
CLASES_VEHICULOS = {
    1,   # Bicicleta
    2,   # Auto
    3,   # Motocicleta
    4,   # Avión
    5,   # Autobús
    # Tren: nunca se muestra como tal —disambiguate_class lo convierte siempre
    # en camión— pero debe seguir aquí. Si se retira, la caja desaparece en vez
    # de reetiquetarse, porque ultralytics elige la clase por máximo sobre las
    # 80 y filtra después.
    6,   # Tren -> se mostrará como Camión
    7,   # Camión
    8,   # Barco
}

# Animales. Los exóticos llevan umbral alto: en un entorno urbano, una
# detección de cebra o jirafa es casi con seguridad un error.
CLASES_ANIMALES = {
    14,  # Pájaro
    15,  # Gato
    16,  # Perro
    17,  # Caballo
    18,  # Oveja
    19,  # Vaca
    20,  # Elefante
    21,  # Oso
    22,  # Cebra
    23,  # Jirafa
}

# Objetos potencialmente peligrosos.
#
# COCO NO incluye armas de fuego: no hay clase de pistola ni de rifle, así que
# el modelo es incapaz de detectarlas por mucho que se ajuste. Lo más cercano
# es el cuchillo, una clase pensada para escenas de cocina, poco fiable a
# distancia y en exteriores. Se incluye con un umbral muy alto para que no
# genere ruido, pero no debe confundirse con detección de armas real: eso exige
# un modelo entrenado específicamente para ello.
CLASES_OBJETOS_PELIGROSOS = {
    43,  # Cuchillo
}

# Conjunto final que se pide al modelo
ALLOWED_CLASS_IDS = sorted(
    CLASES_PERSONAS | CLASES_VEHICULOS | CLASES_ANIMALES | CLASES_OBJETOS_PELIGROSOS
)


def is_allowed_class(class_id: int) -> bool:
    """True si la clase entra en el filtro de videovigilancia."""
    return int(class_id) in ALLOWED_CLASS_IDS


# ---------------------------------------------------------------------------
# Umbrales de confianza mínima por clase COCO
#
# Clases poco comunes en contextos urbanos/tráfico requieren mayor confianza
# para evitar falsos positivos por objetos visualmente similares:
#   - train (6):  tracto-camiones largos o tanques se confunden con trenes
#   - boat (8):   tanques de agua, tubos, objetos cilíndricos se confunden con barcos
#   - airplane(4): rara en cámaras de tierra
#   - elephant, bear, zebra, giraffe: fauna no urbana
# ---------------------------------------------------------------------------
CLASS_CONFIDENCE_THRESHOLDS = {
    # Clases de vehículos raros/improbables en videovigilancia terrestre
    4:  0.80,   # Avión — necesita alta confianza (podría confundirse con drones/pájaros)
    # Tren ya no figura: nunca llega a evaluarse, porque la desambiguación lo
    # convierte en camión antes del filtro de confianza. Mantener aquí el 0.85
    # solo confundiría.
    8:  0.80,   # Barco — alta confianza para evitar tanques de agua/tubos

    # Fauna salvaje — muy poco probable en cámaras de tráfico urbano
    20: 0.85,   # Elefante
    21: 0.85,   # Oso
    22: 0.85,   # Cebra
    23: 0.85,   # Jirafa

    # Objetos potencialmente peligrosos. El cuchillo de COCO se entrenó sobre
    # escenas de cocina, así que en exteriores confunde con facilidad barras,
    # antenas y reflejos alargados. Umbral muy alto para que solo pase lo
    # evidente.
    43: 0.85,   # Cuchillo
}

# Nota: aquí solo tiene sentido listar clases incluidas en ALLOWED_CLASS_IDS.
# Las demás ni siquiera llegan a evaluarse, porque se descartan durante la
# inferencia. Antes había umbrales para parquímetros, frisbees y alimentos que
# ya no pueden aparecer, y se han retirado para no dar a entender lo contrario.

# Umbral global predeterminado para clases no listadas arriba
DEFAULT_CONFIDENCE_THRESHOLD = 0.35

# ---------------------------------------------------------------------------
# Histéresis de confianza
#
# Un umbral único produce parpadeo: cuando un objeto se ocluye parcialmente
# (pasa bajo un semáforo colgante, tras un cable o un poste) su confianza cae
# unas décimas y la detección se descarta, aunque el tracker siga siguiéndolo
# sin problema. Al despejarse la oclusión vuelve a superar el umbral y el
# recuadro reaparece.
#
# Con histéresis se usan dos umbrales:
#   - ACTIVAR:  el umbral por clase de arriba. Exigente, evita falsos positivos.
#   - MANTENER: KEEP_RATIO del anterior. Permisivo, sostiene el recuadro de un
#               track ya confirmado durante caídas transitorias de confianza.
# ---------------------------------------------------------------------------
KEEP_THRESHOLD_RATIO = 0.55

# Suelo absoluto: por muy bajo que quede el umbral de mantenimiento, nunca se
# sostiene un recuadro por debajo de este valor.
MIN_KEEP_THRESHOLD = 0.15


def get_label_es(class_id: int, model_names: dict = None) -> str:
    """
    Devuelve el nombre en español de una clase COCO.
    Si no hay traducción disponible, usa el nombre original del modelo.
    """
    es_name = COCO_ES.get(class_id)
    if es_name:
        return es_name
    if model_names:
        return model_names.get(class_id, f"cls_{class_id}")
    return f"cls_{class_id}"


def get_activation_threshold(class_id: int) -> float:
    """
    Umbral de confianza necesario para ACTIVAR (empezar a mostrar) una
    detección de esta clase.
    """
    return CLASS_CONFIDENCE_THRESHOLDS.get(class_id, DEFAULT_CONFIDENCE_THRESHOLD)


def get_keep_threshold(class_id: int) -> float:
    """
    Umbral de confianza necesario para MANTENER visible una detección cuyo
    track ya fue confirmado. Más permisivo que el de activación: sostiene el
    recuadro durante oclusiones parciales breves.
    """
    return max(MIN_KEEP_THRESHOLD,
               get_activation_threshold(class_id) * KEEP_THRESHOLD_RATIO)


def should_show_detection(class_id: int, confidence: float,
                          is_confirmed: bool = False) -> bool:
    """
    Determina si una detección debe mostrarse según el umbral de confianza
    por clase. Suprime falsos positivos de clases poco probables.

    Args:
        class_id:     índice de clase COCO
        confidence:   confianza de la detección
        is_confirmed: True si el track ya venía mostrándose. En ese caso se
                      aplica el umbral de mantenimiento (más permisivo) en
                      lugar del de activación.
    """
    threshold = get_keep_threshold(class_id) if is_confirmed \
        else get_activation_threshold(class_id)
    return confidence >= threshold
