
import os
import chromadb
from chromadb.config import Settings
from app.core.config import settings

# ChromaDB Client Singleton
_client = None

def get_chroma_client():
    global _client
    if _client is None:
        # 使用持久化存储
        persist_directory = os.path.join(settings.UPLOAD_DIR, "chromadb")
        os.makedirs(persist_directory, exist_ok=True)
        
        _client = chromadb.PersistentClient(path=persist_directory)
    return _client

def get_collection(name: str = "ancient_docs"):
    client = get_chroma_client()
    return client.get_or_create_collection(name=name)
