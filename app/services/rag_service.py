
import json
import os
import dashscope
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database import StructuredResult
from app.core.config import settings
from fastapi.concurrency import run_in_threadpool

# RAG 配置
if settings.DASHSCOPE_API_KEY:
    dashscope.api_key = settings.DASHSCOPE_API_KEY

def _get_text_embeddings_sync(text: str):
    """调用 DashScope 获取文本向量 (Sync)"""
    if not settings.DASHSCOPE_API_KEY:
        # 模拟向量
        return [0.1] * 1536
        
    try:
        resp = dashscope.TextEmbedding.call(
            model=dashscope.TextEmbedding.Models.text_embedding_v1,
            input=text
        )
        if resp.status_code == 200:
            return resp.output['embeddings'][0]['embedding']
        else:
            print(f"Embedding failed: {resp}")
            return [0.1] * 1536
    except Exception as e:
        print(f"Embedding error: {e}")
        return [0.1] * 1536

async def get_text_embeddings(text: str):
    return await run_in_threadpool(_get_text_embeddings_sync, text)

def cosine_similarity(vec1, vec2):
    """计算余弦相似度"""
    import math
    dot_product = sum(a*b for a,b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a*a for a in vec1))
    norm_b = math.sqrt(sum(b*b for b in vec2))
    return dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0

from app.services.vector_store.chroma import get_collection

async def retrieve_context(question_vec, db: Session, top_k=3):
    """
    使用 ChromaDB 进行向量检索
    """
    collection = get_collection()
    
    # 查询向量数据库
    results = collection.query(
        query_embeddings=[question_vec],
        n_results=top_k
    )
    
    if results['documents'] and results['documents'][0]:
        return results['documents'][0]
    
    # Fallback if no results in vector db (e.g. first run)
    return []

def index_document(doc_id: str, text: str, embedding: list):
    """
    将文档索引到 ChromaDB
    """
    collection = get_collection()
    collection.add(
        documents=[text],
        embeddings=[embedding],
        ids=[doc_id]
    )


def _generate_answer_sync(question: str, context_list: list):
    """调用 LLM 生成回答 (Sync)"""
    if not settings.DASHSCOPE_API_KEY:
        return "由于未配置 DASHSCOPE_API_KEY，无法生成智能回答。这里是模拟回复。"
        
    context_str = "\n".join([f"- {c}" for c in context_list])
    
    prompt = f"""
    你是一个专业的古籍研究助手。请根据以下参考资料回答用户的问题。
    如果参考资料中没有相关信息，请如实告知。
    
    参考资料：
    {context_str}
    
    用户问题：{question}
    
    回答：
    """
    
    try:
        response = dashscope.Generation.call(
            model=dashscope.Generation.Models.qwen_turbo,
            prompt=prompt,
            result_format='message'
        )
        if response.status_code == 200:
            return response.output.choices[0].message.content
        else:
            return "生成回答失败，请稍后再试。"
    except Exception as e:
        return f"生成过程发生错误: {str(e)}"

async def generate_answer(question: str, context_list: list):
    return await run_in_threadpool(_generate_answer_sync, question, context_list)

async def rag_pipeline(question: str, db: Session):
    """RAG 主流程"""
    # 1. 问题向量化
    q_vec = await get_text_embeddings(question)
    
    # 2. 检索相关文档
    context = await retrieve_context(q_vec, db)
    
    # 3. 生成回答
    answer = await generate_answer(question, context)
    
    return {
        "answer": answer,
        "sources": context
    }
