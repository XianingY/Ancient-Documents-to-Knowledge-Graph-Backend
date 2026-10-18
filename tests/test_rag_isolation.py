"""RAG 检索必须保持用户数据隔离。"""
import sys
from types import ModuleType

from app.services import rag_service


def test_vector_search_does_not_fallback_to_unfiltered_collection(monkeypatch):
    """当前用户没有向量结果时，也不能检索其他用户的数据。"""
    calls = []

    def fake_query_documents(embedding, top_k, where=None):
        calls.append(where)
        if where == {"user_id": 42}:
            return []
        return [
            {
                "text": "other user document",
                "metadata": {"user_id": 7, "image_id": 1},
                "distance": 0.1,
            }
        ]

    monkeypatch.setattr(rag_service, "_get_text_embeddings_sync", lambda text: [0.1])
    fake_chroma = ModuleType("app.services.vector_store.chroma")
    fake_chroma.query_documents = fake_query_documents
    monkeypatch.setitem(sys.modules, "app.services.vector_store.chroma", fake_chroma)

    result = rag_service._vector_search_sync("谁买了田地？", user_id=42, top_k=3)

    assert result == []
    assert calls == [{"user_id": 42}]
