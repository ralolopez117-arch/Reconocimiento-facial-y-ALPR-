import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image
import numpy as np
import json
import database
import io
import math

print("Loading FR Engine (Facial Recognition)...")
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
# MTCNN is used to detect and align faces
mtcnn = MTCNN(keep_all=False, device=device)
# InceptionResnetV1 is used to extract facial embeddings
resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)
print("FR Engine loaded.")

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
        embedding = extract_embedding(person_crop)
        if embedding is None:
            return []
        
        known_faces = database.get_all_known_faces()
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
