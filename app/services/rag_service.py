"""
问答服务（含引用溯源、多轮对话、流式输出）。

混合 RAG 检索策略：
  1. 向量检索（ChromaDB）：将用户问题嵌入向量，检索语义最相关的文书
  2. 时序补充（DB）：从数据库取最新上传的文书，确保新数据不遗漏
  3. 合并去重：按 image_id 去重后合并，优先保留向量检索的结果
  4. 构建 Prompt；history 最多最近 6 轮（`_build_messages`）
  5. DashScope qwen-turbo 生成回答（流式/非流式）
  6. 返回 answer + sources
"""
import json
import os
import re
from typing import Generator

import dashscope
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

if settings.DASHSCOPE_API_KEY:
    dashscope.api_key = settings.DASHSCOPE_API_KEY

# 保留向量化函数供重建索引使用
def _get_text_embeddings_sync(text: str) -> list:
    if not settings.DASHSCOPE_API_KEY:
        return [0.1] * 1536
    try:
        resp = dashscope.TextEmbedding.call(
            model=dashscope.TextEmbedding.Models.text_embedding_v1,
            input=text,
        )
        if resp.status_code == 200:
            return resp.output["embeddings"][0]["embedding"]
        logger.warning("embedding_failed", extra={"code": resp.status_code})
        return [0.1] * 1536
    except Exception as e:
        logger.warning("embedding_error", extra={"error": str(e)})
        return [0.1] * 1536


async def get_text_embeddings(text: str) -> list:
    return await run_in_threadpool(_get_text_embeddings_sync, text)


_EMPTY_VALS = {"未识别", "未记载", "None", "null", "none", ""}

# 向量检索返回的最大文书数（语义相关）
_VECTOR_TOP_K = 15
# DB 补充的最新文书数（时序保底）
_DB_RECENT_N = 5
# 最终送入 Prompt 的最大文书数
_MAX_CONTEXT = 20


# ── 向量检索 ────────────────────────────────────────────────────

def _vector_search_sync(question: str, user_id: int, top_k: int = _VECTOR_TOP_K) -> list:
    """通过 ChromaDB 进行语义向量检索，返回与问题最相关的文书。"""
    from app.services.vector_store.chroma import query_documents

    embedding = _get_text_embeddings_sync(question)
    where = {"user_id": user_id}
    results = query_documents(embedding, top_k, where)
    if not results:
        results = query_documents(embedding, top_k, None)
    return results


async def retrieve_context(
    question_vec: list,
    top_k: int = _VECTOR_TOP_K,
    user_id: int | None = None,
) -> list:
    """ChromaDB 向量检索的异步包装。"""
    from app.services.vector_store.chroma import query_documents
    where = {"user_id": user_id} if user_id is not None else None
    results = await run_in_threadpool(query_documents, question_vec, top_k, where)
    if not results and where:
        results = await run_in_threadpool(query_documents, question_vec, top_k, None)
    return results


# ── DB 取最新文书（时序补充）────────────────────────────────────

def _fetch_latest_docs_sync(db: Session, user_id: int, top_n: int = _DB_RECENT_N) -> list:
    """
    从数据库获取当前用户最新 top_n 份已完成 OCR 的文书。
    每张图片只取最新的一条 OCR 结果（按 OcrResult.id DESC）。
    """
    from database import Image, OcrResult, StructuredResult, OcrStatus
    from sqlalchemy import func

    latest_ocr_subq = (
        db.query(
            OcrResult.image_id,
            func.max(OcrResult.id).label("max_ocr_id"),
        )
        .join(Image, OcrResult.image_id == Image.id)
        .filter(
            OcrResult.status == OcrStatus.DONE,
            Image.user_id == user_id,
            OcrResult.raw_text.isnot(None),
        )
        .group_by(OcrResult.image_id)
        .subquery()
    )

    ocr_list = (
        db.query(OcrResult)
        .join(latest_ocr_subq, OcrResult.id == latest_ocr_subq.c.max_ocr_id)
        .join(Image, OcrResult.image_id == Image.id)
        .order_by(Image.upload_time.desc())
        .limit(top_n)
        .all()
    )

    return [_ocr_to_context_item(db, ocr, user_id) for ocr in ocr_list]


def _ocr_to_context_item(db: Session, ocr, user_id: int) -> dict:
    """将一条 OcrResult 转换为统一的 context_item 格式。"""
    from database import StructuredResult, OcrStatus

    struct = (
        db.query(StructuredResult)
        .filter(
            StructuredResult.ocr_result_id == ocr.id,
            StructuredResult.status == OcrStatus.DONE,
        )
        .order_by(StructuredResult.id.desc())
        .first()
    )

    meta: dict = {
        "user_id": ocr.image.user_id if ocr.image else user_id,
        "ocr_result_id": ocr.id,
        "image_id": ocr.image_id,
        "filename": ocr.image.filename if ocr.image else "",
        "structured_result_id": "",
        "time": "", "location": "", "seller": "",
        "buyer": "", "price": "", "subject": "",
    }
    text = ocr.raw_text or ""

    if struct and struct.content:
        try:
            sd = json.loads(struct.content)
        except Exception:
            sd = {}

        def _f(k: str) -> str:
            v = str(sd.get(k, "")).strip()
            return v if v not in _EMPTY_VALS else ""

        meta.update({
            "structured_result_id": struct.id,
            "time": _f("Time"),
            "location": _f("Location"),
            "seller": _f("Seller"),
            "buyer": _f("Buyer"),
            "price": _f("Price"),
            "subject": _f("Subject"),
        })

    return {"text": text, "metadata": meta, "distance": 0.0}


# ── 混合检索（向量 + 时序）────────────────────────────────────

def _hybrid_retrieve_sync(
    question: str,
    db: Session,
    user_id: int,
    vector_top_k: int = _VECTOR_TOP_K,
    db_recent_n: int = _DB_RECENT_N,
    max_context: int = _MAX_CONTEXT,
) -> list:
    """
    混合检索策略：
      1. 向量检索：获取与问题语义最相关的 vector_top_k 篇文书
      2. DB 补充：获取最新上传的 db_recent_n 篇文书（确保新文书不遗漏）
      3. 按 image_id 去重合并，向量检索结果优先
      4. 最终返回不超过 max_context 条
    """
    seen_image_ids: set = set()
    merged: list = []

    vector_results = _vector_search_sync(question, user_id, vector_top_k)
    for item in vector_results:
        img_id = item.get("metadata", {}).get("image_id")
        if img_id and img_id in seen_image_ids:
            continue
        if img_id:
            seen_image_ids.add(img_id)
        merged.append(item)

    db_results = _fetch_latest_docs_sync(db, user_id, db_recent_n)
    for item in db_results:
        img_id = item.get("metadata", {}).get("image_id")
        if img_id and img_id in seen_image_ids:
            continue
        if img_id:
            seen_image_ids.add(img_id)
        merged.append(item)

    return merged[:max_context]


async def hybrid_retrieve(
    question: str,
    db: Session,
    user_id: int,
    vector_top_k: int = _VECTOR_TOP_K,
    db_recent_n: int = _DB_RECENT_N,
    max_context: int = _MAX_CONTEXT,
) -> list:
    """混合检索的异步包装。"""
    return await run_in_threadpool(
        _hybrid_retrieve_sync, question, db, user_id,
        vector_top_k, db_recent_n, max_context,
    )


# ── 上下文格式化 ──────────────────────────────────────────────

def _format_context(context_items: list) -> str:
    """
    将文书列表格式化为紧凑的参考上下文。
    根据文书数量动态调整每条摘录长度，确保总 token 可控。
    """
    n = len(context_items)
    if n <= 5:
        max_excerpt = 400
    elif n <= 10:
        max_excerpt = 250
    else:
        max_excerpt = 150

    parts = []
    for i, item in enumerate(context_items):
        meta = item.get("metadata", {})
        text = item.get("text", "")
        tags = []
        for key, label in [("time", "时间"), ("location", "地点"),
                            ("seller", "卖方"), ("buyer", "买方"),
                            ("price", "价格"), ("subject", "标的")]:
            v = str(meta.get(key, "")).strip()
            if v and v not in _EMPTY_VALS:
                tags.append(f"{label}:{v}")
        tag_str = " ".join(tags)
        header = f"[参考{i+1}]" + (f"({tag_str})" if tag_str else "")
        excerpt = text[:max_excerpt] + ("…" if len(text) > max_excerpt else "")
        parts.append(f"{header}\n{excerpt}")
    return "\n\n".join(parts)


# ── 消息构建 ──────────────────────────────────────────────────

def _build_messages(question: str, context_items: list, history=None) -> list:
    """构建 LLM messages 列表（简洁版）。"""
    system_msg = {
        "role": "system",
        "content": (
            "你是古代地契文书智能问答助手。"
            "根据参考文书直接作答，引用用[参考N]标注。"
            "回答简洁专业，条目清晰。"
            "参考资料不足时如实说明，不编造内容。"
            "人名地名保持原文。"
        ),
    }

    messages = [system_msg]

    if history:
        for turn in history[-6:]:
            if isinstance(turn, dict):
                role, content = turn.get("role", "user"), turn.get("content", "")
            else:
                role, content = getattr(turn, "role", "user"), getattr(turn, "content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    if context_items:
        context_str = _format_context(context_items)
        user_content = f"参考文书：\n{context_str}\n\n问题：{question}"
    else:
        user_content = f"（知识库暂无文书）问题：{question}"

    messages.append({"role": "user", "content": user_content})
    return messages


# ── 同步生成（非流式）────────────────────────────────────────

def _generate_answer_sync(question: str, context_items: list, history=None) -> str:
    if not settings.DASHSCOPE_API_KEY:
        return "未配置 DASHSCOPE_API_KEY，无法生成回答。"

    messages = _build_messages(question, context_items, history)
    try:
        response = dashscope.Generation.call(
            model="qwen-turbo",
            messages=messages,
            result_format="message",
            max_tokens=1024,
        )
        if response.status_code == 200:
            try:
                return response.output.choices[0].message.content
            except (AttributeError, IndexError, TypeError):
                return response.output["choices"][0]["message"]["content"]
        logger.warning("llm_failed", extra={"code": response.code})
        return f"生成回答失败（{response.code}），请稍后再试。"
    except Exception as e:
        logger.error("llm_error", extra={"error": str(e)})
        return "生成过程发生错误，请稍后再试。"


async def generate_answer(question: str, context_items: list, history=None) -> str:
    return await run_in_threadpool(_generate_answer_sync, question, context_items, history)


# ── 流式生成（SSE）────────────────────────────────────────────

def _generate_answer_stream_chunks(
    question: str,
    context_items: list,
    history=None,
) -> Generator[str, None, None]:
    """同步生成器，逐块 yield 文本增量。"""
    if not settings.DASHSCOPE_API_KEY:
        yield "未配置 DASHSCOPE_API_KEY，无法生成回答。"
        return

    messages = _build_messages(question, context_items, history)
    try:
        responses = dashscope.Generation.call(
            model="qwen-turbo",
            messages=messages,
            result_format="message",
            max_tokens=1024,
            stream=True,
            incremental_output=True,
        )
        for response in responses:
            if response.status_code == 200:
                try:
                    delta = response.output.choices[0].message.content
                    if delta:
                        yield delta
                except (AttributeError, IndexError, TypeError):
                    pass
            else:
                logger.warning("stream_chunk_failed", extra={"code": response.status_code})
                break
    except Exception as e:
        logger.error("stream_error", extra={"error": str(e)})
        yield "生成过程发生错误，请稍后再试。"


# ── 文件名工具 ────────────────────────────────────────────────

_GENERIC_NAME_RE = re.compile(
    r'^(img|image|photo|dsc|pic|screenshot|scan|capture|frame|file|\d+|'
    r'img_\d+|dsc_\d+|photo_\d+)$',
    re.IGNORECASE,
)


def _friendly_filename(raw_filename: str, image_id) -> str:
    if not raw_filename:
        return f"文书 #{image_id}" if image_id else "未知文书"
    base = os.path.splitext(raw_filename)[0]
    clean = re.sub(r'_[0-9a-f]{8}$', '', base).strip()
    if not clean or _GENERIC_NAME_RE.fullmatch(clean):
        return f"文书 #{image_id}" if image_id else "未知文书"
    return clean


# ── Sources 构建 ──────────────────────────────────────────────

def _build_sources(context_items: list) -> list:
    sources = []
    for i, item in enumerate(context_items):
        meta = item.get("metadata", {})
        full_text = item.get("text", "")
        ocr_only = full_text.split("\n【时间】")[0].split("\n【卖方】")[0]
        excerpt = ocr_only[:80].strip() + ("..." if len(ocr_only) > 80 else "")
        image_id = meta.get("image_id", "")
        raw_filename = meta.get("filename", "")
        friendly_name = _friendly_filename(raw_filename, image_id)
        display_filename = f"#{image_id} {friendly_name}" if image_id else friendly_name
        sources.append({
            "index": i + 1,
            "doc_id": meta.get("structured_result_id", meta.get("ocr_result_id", "")),
            "image_id": image_id,
            "filename": display_filename,
            "time": meta.get("time", ""),
            "location": meta.get("location", ""),
            "seller": meta.get("seller", ""),
            "buyer": meta.get("buyer", ""),
            "price": meta.get("price", ""),
            "subject": meta.get("subject", ""),
            "excerpt": excerpt,
        })
    return sources


# ── 问答主流程 ────────────────────────────────────────────────

async def rag_pipeline(
    question: str,
    db: Session,
    history=None,
    user_id: int | None = None,
) -> dict:
    """
    混合 RAG 管线：向量语义检索 + DB 时序补充，去重合并后送入 LLM。
    向量检索确保语义相关性，DB 补充确保最新文书不遗漏。
    """
    try:
        context_items = await hybrid_retrieve(
            question, db, user_id or 0,
            vector_top_k=_VECTOR_TOP_K,
            db_recent_n=_DB_RECENT_N,
            max_context=_MAX_CONTEXT,
        )
        answer = await generate_answer(question, context_items, history)
    except Exception as e:
        logger.error("rag_pipeline_error", extra={"error": str(e)})
        answer = "抱歉，处理您的问题时出现了意外错误，请稍后再试。"
        context_items = []

    return {"answer": answer, "sources": _build_sources(context_items)}


# 兼容旧调用
def index_document(doc_id: str, text: str, embedding: list) -> None:
    from app.services.vector_store.chroma import upsert_document
    upsert_document(doc_id=doc_id, text=text, embedding=embedding)
