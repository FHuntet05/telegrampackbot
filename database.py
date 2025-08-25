# database.py
import os
import pymongo
import logging
from datetime import datetime, timezone
from bson import ObjectId # Importante para buscar y manejar IDs únicos

# Configurar logging
logger = logging.getLogger(__name__)

# --- Conexión a la Base de Datos ---
client = None
db = None
packs_collection = None

def setup_database():
    """Establece la conexión con MongoDB Atlas y obtiene la colección."""
    global client, db, packs_collection
    
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        raise ValueError("MONGO_URI no está configurada en el entorno.")
    
    try:
        client = pymongo.MongoClient(mongo_uri)
        db = client.get_database("telegramBotDB") 
        packs_collection = db.get_collection("packs")
        packs_collection.create_index([("name", 1), ("user_id", 1)], unique=True)
        logger.info("Conexión a MongoDB establecida correctamente.")
    except Exception as e:
        logger.error(f"No se pudo conectar a MongoDB: {e}")
        raise

# --- Operaciones CRUD de Packs ---

def create_pack(pack_name, user_id):
    """Crea un nuevo documento de pack."""
    try:
        packs_collection.insert_one({
            "name": pack_name,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc),
            "content": []
        })
        return True, f"Pack '{pack_name}' creado."
    except pymongo.errors.DuplicateKeyError:
        return False, f"Ya existe un pack con el nombre '{pack_name}'."

def add_photo_to_pack(pack_name, photo_file_id):
    """Añade un objeto de foto al array 'content' de un pack, dándole un ID único."""
    photo_document = {
        "photo_id": ObjectId(),
        "photo_file_id": photo_file_id,
        "videos": []
    }
    result = packs_collection.update_one(
        {"name": pack_name},
        {"$push": {"content": photo_document}}
    )
    return result.modified_count > 0, photo_document["photo_id"]

def add_video_to_photo(pack_name, photo_id, video_file_id, caption):
    """Añade un video a una foto específica dentro de un pack."""
    video_document = {
        "file_id": video_file_id,
        "caption": caption
    }
    result = packs_collection.update_one(
        {"name": pack_name, "content.photo_id": photo_id},
        {"$push": {"content.$.videos": video_document}}
    )
    return result.modified_count > 0

def list_all_packs(user_id):
    """Lista los nombres de todos los packs de un usuario."""
    packs_cursor = packs_collection.find({"user_id": user_id}, {"name": 1, "_id": 0}).sort("created_at", -1)
    return [pack['name'] for pack in packs_cursor]

def get_pack_for_sending(pack_name):
    """Obtiene el contenido de un pack para ser enviado."""
    pack_data = packs_collection.find_one({"name": pack_name})
    return pack_data.get("content", []) if pack_data else None

def get_pack_details(pack_name, user_id):
    """Obtiene el documento completo de un pack para edición."""
    return packs_collection.find_one({"name": pack_name, "user_id": user_id})

def delete_pack(pack_name, user_id):
    """Elimina un pack completo."""
    result = packs_collection.delete_one({"name": pack_name, "user_id": user_id})
    return result.deleted_count > 0

def delete_photo_from_pack(pack_name, photo_id_str):
    """Elimina una foto específica de un pack usando su ID como string."""
    try:
        photo_id = ObjectId(photo_id_str)
        result = packs_collection.update_one(
            {"name": pack_name},
            {"$pull": {"content": {"photo_id": photo_id}}}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error al intentar borrar foto con ID {photo_id_str}: {e}")
        return False
