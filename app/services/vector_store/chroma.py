"""
ChromaDB 向量存储封装（含元数据支持）
持久化路径由 settings.UPLOAD_DIR/chromadb 决定，重启后数据不丢失

兼容 chromadb 0.3.x（Client + Settings）与 0.4+（PersistentClient）。
"""
import os
from typing import Any

import chromadb
from app.core.config import settings


_client: Any = None


def get_chroma_client() -> Any:
    global _client
    if _client is None:
        persist_directory = os.path.join(settings.UPLOAD_DIR, "chromadb")
        os.makedirs(persist_directory, exist_ok=True)

        persistent_cls = getattr(chromadb, "PersistentClient", None)
        if persistent_cls is not None:
            try:
                _client = persistent_cls(path=persist_directory)
            except TypeError:
                _client = persistent_cls(persist_directory=persist_directory)
        else:
            from chromadb.config import Settings

            _client = chromadb.Client(
                Settings(
                    persist_directory=persist_directory,
                    chroma_db_impl="duckdb+parquet",
                )
            )
    return _client


def get_collection(name: str = "ancient_docs") -> Any:
    client = get_chroma_client()
    return client.get_or_create_collection(name=name)


def upsert_document(
    doc_id: str,
    text: str,
    embedding: list,
    metadata: dict | None = None,
) -> None:
    """
    向集合中插入或更新一条文档。
    metadata 应包含 filename、structured_result_id 等便于前端溯源的字段。
    """
    collection = get_collection()
    collection.upsert(
        documents=[text],
        embeddings=[embedding],
        ids=[doc_id],
        metadatas=[metadata or {}],
    )


def query_documents(
    query_embedding: list,
    top_k: int = 3,
    where: dict | None = None,
) -> list[dict]:
    """
    向量检索，返回列表，每条包含 text、metadata 和 distance。
    where: ChromaDB 元数据过滤条件（如 {"user_id": 1}），None 表示不过滤。
    集合为空或出错时返回空列表。
    """
    try:
        collection = get_collection()
        count = collection.count()
        if count == 0:
            return []

        actual_top_k = min(top_k, count)
        query_kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results": actual_top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        results = collection.query(**query_kwargs)

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {
                "text": doc,
                "metadata": meta or {},
                "distance": dist,
            }
            for doc, meta, dist in zip(docs, metas, distances)
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("chroma_query_error: %s", e)
        return []


def count_documents(where: dict | None = None) -> int:
    """
    统计满足条件的文档数量。
    where: 元数据过滤条件（如 {"user_id": 1}），None 时统计全部。
    """
    try:
        collection = get_collection()
        if where:
            results = collection.get(where=where, include=[])
            return len(results.get("ids", []))
        return collection.count()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("chroma_count_error: %s", e)
        return 0


def delete_documents(doc_ids: list[str]) -> None:
    """
    从集合中批量删除文档（用于图片删除时同步清理向量索引）。
    doc_ids 不存在时静默忽略。
    """
    if not doc_ids:
        return
    try:
        collection = get_collection()
        collection.delete(ids=doc_ids)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("chroma_delete_error: %s", e)
