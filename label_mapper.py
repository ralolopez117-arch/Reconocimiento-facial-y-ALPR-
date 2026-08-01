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
    6:  0.85,   # Tren — alta confianza para evitar tracto-camiones mal clasificados
    8:  0.80,   # Barco — alta confianza para evitar tanques de agua/tubos
    12: 0.70,   # Parquímetro

    # Fauna salvaje — muy poco probable en cámaras de tráfico urbano
    20: 0.85,   # Elefante
    21: 0.85,   # Oso
    22: 0.85,   # Cebra
    23: 0.85,   # Jirafa

    # Artículos deportivos que pueden confundirse con otros objetos
    29: 0.70,   # Frisbee
    30: 0.70,   # Esquís
    31: 0.70,   # Snowboard
    37: 0.70,   # Tabla de surf

    # Alimentos raramente visibles en cámaras de vigilancia exterior
    46: 0.80,   # Plátano
    47: 0.80,   # Manzana
    48: 0.80,   # Sándwich
    49: 0.80,   # Naranja
    50: 0.80,   # Brócoli
    51: 0.80,   # Zanahoria
    52: 0.80,   # Perro caliente
    53: 0.80,   # Pizza
    54: 0.80,   # Dona
    55: 0.80,   # Pastel
}

# Umbral global predeterminado para clases no listadas arriba
DEFAULT_CONFIDENCE_THRESHOLD = 0.35


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


def should_show_detection(class_id: int, confidence: float) -> bool:
    """
    Determina si una detección debe mostrarse según el umbral de confianza
    por clase. Suprime falsos positivos de clases poco probables.
    """
    threshold = CLASS_CONFIDENCE_THRESHOLDS.get(class_id, DEFAULT_CONFIDENCE_THRESHOLD)
    return confidence >= threshold
