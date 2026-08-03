import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image
import numpy as np
import json
import database
import io
import math
import threading
import time

print("Loading FR Engine (Facial Recognition)...")
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
# MTCNN is used to detect and align faces
mtcnn = MTCNN(keep_all=False, device=device)
# InceptionResnetV1 is used to extract facial embeddings
resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)
print("FR Engine loaded.")

# ---------------------------------------------------------------------------
# ¿Hay rostros que buscar?
#
# El reconocimiento facial solo puede encontrar a alguien previamente
# registrado. Sin ningún rostro en la base de datos, ejecutar MTCNN y la red de
# embeddings sobre cada persona detectada es cálculo íntegramente desperdiciado:
# el resultado se compara contra una lista vacía y siempre da cero coincidencias.
#
# La respuesta se cachea porque esto se consulta por cada persona en cada
# fotograma, y consultar la base de datos a ese ritmo sería peor que el propio
# análisis que se pretende evitar.
# ---------------------------------------------------------------------------
_CACHE_TTL = 10.0          # segundos antes de volver a preguntar a la BD
_cache_hay_rostros = None
_cache_momento = 0.0
_cache_lock = threading.Lock()


def has_known_faces() -> bool:
    """True si hay al menos un rostro registrado contra el que comparar."""
    global _cache_hay_rostros, _cache_momento

    ahora = time.time()
    with _cache_lock:
        if _cache_hay_rostros is not None and ahora - _cache_momento < _CACHE_TTL:
            return _cache_hay_rostros

    # La consulta se hace fuera del cerrojo: si dos hilos coinciden, ambos
    # preguntan una vez y el resultado es el mismo, que es preferible a
    # serializar todos los hilos de análisis contra la base de datos.
    try:
        hay = len(database.get_all_known_faces()) > 0
    except Exception as e:
        print(f"[FR] No se pudo comprobar los rostros registrados: {e}")
        hay = True          # Ante la duda, no desactivar el reconocimiento

    with _cache_lock:
        _cache_hay_rostros = hay
        _cache_momento = ahora
    return hay


def invalidate_known_faces_cache():
    """
    Fuerza a releer la base de datos en la próxima comprobación.

    Se llama al registrar o eliminar rostros, para que activar o desactivar el
    reconocimiento sea inmediato y no dependa del vencimiento de la caché.
    """
    global _cache_hay_rostros
    with _cache_lock:
        _cache_hay_rostros = None


def extract_embedding(image_array):
    """
    Given a numpy array image (RGB), finds the face and returns its embedding vector.
    """
    try:
        img = Image.fromarray(image_array)
        # Get cropped face tensor
        face_tensor = mtcnn(img)
        if face_tensor is None:
            return None
        
        # Calculate embedding
        # face_tensor is of shape (3, 160, 160) for one face
        # We need a batch dimension
        face_tensor = face_tensor.unsqueeze(0).to(device)
        embedding = resnet(face_tensor).detach().cpu().numpy()[0]
        return embedding
    except Exception as e:
        print(f"Error extracting embedding: {e}")
        return None

def register_face(image_bytes, name):
    """
    Registers a face from raw image bytes and saves it to the database.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        img_array = np.array(img)
        embedding = extract_embedding(img_array)
        
        if embedding is not None:
            emb_list = embedding.tolist()
            face_id = database.insert_known_face(name, json.dumps(emb_list))
            return True, "Rostro registrado exitosamente.", face_id
        else:
            return False, "No se detectó ningún rostro en la imagen.", None
    except Exception as e:
        return False, str(e), None

def update_face(face_id, image_bytes, name):
    """
    Updates an existing face's name and recalculates its embedding from a new image.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        img_array = np.array(img)
        embedding = extract_embedding(img_array)
        
        if embedding is not None:
            emb_list = embedding.tolist()
            database.update_known_face_full(face_id, name, json.dumps(emb_list))
            return True, "Rostro actualizado exitosamente."
        else:
            return False, "No se detectó ningún rostro en la nueva imagen."
    except Exception as e:
        return False, str(e)

def compute_cosine_similarity(vec1, vec2):
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0
    return dot_product / (norm1 * norm2)

def process_person_image(person_crop, camera_id, threshold=0.75):
    """
    Processes a cropped image of a person (from YOLO).
    Extracts the face embedding and matches it against known faces.
    """
    try:
        # Salvaguarda: aunque quien llama debería filtrar antes, si no hay
        # rostros registrados no se ejecutan las redes neuronales para nada.
        known_faces = database.get_all_known_faces()
        if not known_faces:
            return []

        embedding = extract_embedding(person_crop)
        if embedding is None:
            return []

        matches = []
        
        for face_record in known_faces:
            known_emb = np.array(json.loads(face_record['embedding']))
            similarity = compute_cosine_similarity(embedding, known_emb)
            
            if similarity >= threshold:
                # We found a match
                match_name = face_record['name']
                database.insert_face_detection(camera_id, match_name, float(similarity))
                matches.append((match_name, similarity))
                
        return matches
    except Exception as e:
        print(f"FR Processing Error: {e}")
        return []
