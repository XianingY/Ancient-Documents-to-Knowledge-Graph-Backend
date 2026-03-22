"""智能问答路由（DB 上下文 + qwen-turbo；见 rag_service）"""
import asyncio
import json as _json
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Image, OcrResult, OcrStatus, StructuredResult, get_db
from app.core.deps import get_current_user_id
from app.core.logger import get_logger
from app.core.rate_limit import rate_limit
from app.services.rag_service import rag_pipeline

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["智能问答"])

# 专用线程池，用于在 async 上下文中驱动同步流式生成器
_stream_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rag_stream")


class HistoryTurn(BaseModel):
    role: str     # "user" | "assistant"
    content: str


class ChatQueryRequest(BaseModel):
    question: str
    history: Optional[List[HistoryTurn]] = None


@router.post(
    "/query",
    summary="智能问答（非流式）",
    description=(
        "从数据库按上传时间取当前用户最近 8 条已完成 OCR 的文书拼上下文，"
        "调用 qwen-turbo 生成回答；非 Chroma 向量检索。"
        "支持多轮 history（服务端最多取最近 6 轮）与 sources 引用溯源。"
    ),
)
@rate_limit("30/minute")
async def chat_query(
    request: Request,
    body: ChatQueryRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        history = (
            [{"role": h.role, "content": h.content} for h in body.history]
            if body.history else None
        )
        logger.info("chat_query", extra={"user_id": user_id, "question_len": len(body.question)})
        result = await rag_pipeline(body.question, db, history, user_id=user_id)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("chat_query_error", extra={"error": str(e), "traceback": tb})
        raise HTTPException(status_code=500, detail="问答服务异常，请稍后重试")


@router.post(
    "/query-stream",
    summary="智能问答（SSE 流式）",
    description=(
        "与 /query 相同的上下文构建与模型，通过 Server-Sent Events 流式推送回答。\n\n"
        "事件格式（每条以 `data: ` 开头，两个换行结束）：\n"
        "- `{\"type\": \"sources\", \"sources\": [...]}` — 先发送引用来源\n"
        "- `{\"type\": \"text\", \"delta\": \"...\"}` — 逐块推送答案增量\n"
        "- `{\"type\": \"done\"}` — 流结束\n"
        "- `{\"type\": \"error\", \"message\": \"...\"}` — 错误"
    ),
)
async def chat_query_stream(
    request: ChatQueryRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        history = (
            [{"role": h.role, "content": h.content} for h in request.history]
            if request.history else None
        )
        logger.info("chat_stream_query", extra={"user_id": user_id, "question_len": len(request.question)})

        from app.services.rag_service import (
            _fetch_latest_docs_sync,
            _generate_answer_stream_chunks,
            _build_sources,
        )
        from fastapi.concurrency import run_in_threadpool

        # 直接从 DB 取最新 8 份文书，无需向量嵌入，更快更准
        context_items = await run_in_threadpool(
            _fetch_latest_docs_sync, db, user_id or 0, 8
        )
        sources = _build_sources(context_items)

        question = request.question
        loop = asyncio.get_event_loop()

        async def event_stream():
            # 先推送引用来源，让前端可以立即展示
            yield f"data: {_json.dumps({'type': 'sources', 'sources': sources}, ensure_ascii=False)}\n\n"

            # 用队列桥接同步生成器与异步推送
            queue: asyncio.Queue = asyncio.Queue(maxsize=500)

            def producer():
                try:
                    for chunk in _generate_answer_stream_chunks(question, context_items, history):
                        asyncio.run_coroutine_threadsafe(
                            queue.put(("text", chunk)), loop
                        ).result(timeout=30)
                except Exception as exc:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("error", str(exc))), loop
                    ).result(timeout=5)
                finally:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("done", None)), loop
                    ).result(timeout=5)

            loop.run_in_executor(_stream_executor, producer)

            while True:
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=90)
                except asyncio.TimeoutError:
                    yield f"data: {_json.dumps({'type': 'error', 'message': '生成超时，请重试'}, ensure_ascii=False)}\n\n"
                    break

                if event_type == "text":
                    yield f"data: {_json.dumps({'type': 'text', 'delta': data}, ensure_ascii=False)}\n\n"
                elif event_type == "error":
                    yield f"data: {_json.dumps({'type': 'error', 'message': data}, ensure_ascii=False)}\n\n"
                    break
                elif event_type == "done":
                    yield f"data: {_json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    break

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("chat_stream_error", extra={"error": str(e), "traceback": tb})
        raise HTTPException(status_code=500, detail="流式问答服务异常，请稍后重试")


@router.get(
    "/kb-status",
    summary="知识库状态",
    description=(
        "返回当前用户可用于智能问答的文书数量。"
        "与问答主流程一致：统计数据库中 OCR 已完成且有正文的文书，而非仅 Chroma 向量条数。"
    ),
)
async def kb_status(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        n_eligible = (
            db.query(OcrResult)
            .join(Image, OcrResult.image_id == Image.id)
            .filter(
                OcrResult.status == OcrStatus.DONE,
                Image.user_id == user_id,
                OcrResult.raw_text.isnot(None),
            )
            .count()
        )
        return {"success": True, "data": {"indexed_count": n_eligible}}
    except Exception:
        return {"success": True, "data": {"indexed_count": 0}}


def _reindex_all_sync(user_id: int):
    """
    遍历当前用户所有 OCR 完成的文档，将其写入 ChromaDB（upsert）。
    只索引属于 user_id 的图片，并将 user_id 写入 metadata 实现资料库隔离。
    在后台任务中运行，创建独立的 DB session 避免使用已关闭的请求级 session。
    """
    from app.services.rag_service import _get_text_embeddings_sync
    from app.services.vector_store.chroma import upsert_document
    from database import Image, SessionLocal

    db = SessionLocal()
    try:
        ocr_results = (
            db.query(OcrResult)
            .join(Image, OcrResult.image_id == Image.id)
            .filter(OcrResult.status == OcrStatus.DONE, Image.user_id == user_id)
            .all()
        )
        indexed, skipped = 0, 0
        _EMPTY = {"未识别", "未记载", "None", "null", ""}

        for ocr in ocr_results:
            if not ocr.raw_text:
                skipped += 1
                continue
            try:
                struct = (
                    db.query(StructuredResult)
                    .filter(
                        StructuredResult.ocr_result_id == ocr.id,
                        StructuredResult.status == OcrStatus.DONE,
                    )
                    .order_by(StructuredResult.id.desc())
                    .first()
                )

                metadata: dict = {
                    "user_id": user_id,
                    "ocr_result_id": ocr.id,
                    "image_id": ocr.image_id,
                    "filename": ocr.image.filename if ocr.image else "",
                    "structured_result_id": "",
                    "time": "", "location": "", "seller": "",
                    "buyer": "", "price": "", "subject": "",
                }
                rich_text = ocr.raw_text

                if struct and struct.content:
                    import json as _json_inner
                    try:
                        sd = _json_inner.loads(struct.content)
                    except Exception:
                        sd = {}

                    def _f(k: str) -> str:
                        v = str(sd.get(k, "")).strip()
                        return v if v not in _EMPTY else ""

                    metadata.update({
                        "structured_result_id": struct.id,
                        "filename": sd.get("filename", metadata["filename"]),
                        "time": _f("Time"), "location": _f("Location"),
                        "seller": _f("Seller"), "buyer": _f("Buyer"),
                        "price": _f("Price"), "subject": _f("Subject"),
                    })
                    tags = [(k, metadata[k]) for k in
                            ("time", "location", "seller", "buyer", "price", "subject")
                            if metadata[k]]
                    if tags:
                        tag_str = "　".join(f"【{label_map[k]}】{v}" for k, v in tags)
                        rich_text = ocr.raw_text + "\n" + tag_str

                embedding = _get_text_embeddings_sync(rich_text)
                upsert_document(
                    doc_id=f"ocr_{ocr.id}",
                    text=rich_text,
                    embedding=embedding,
                    metadata=metadata,
                )
                indexed += 1
            except Exception as e:
                logger.warning("reindex_doc_failed", extra={"ocr_id": ocr.id, "error": str(e)})
                skipped += 1

        return {"total": len(ocr_results), "indexed": indexed, "skipped": skipped}
    finally:
        db.close()


label_map = {
    "time": "时间", "location": "地点", "seller": "卖方",
    "buyer": "买方", "price": "价格", "subject": "标的",
}


@router.post(
    "/reindex",
    summary="重建知识库索引",
    description=(
        "将所有 OCR 识别完成的文书重新写入向量数据库。"
        "操作在后台异步执行，接口立即返回。"
    ),
)
async def reindex(
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
):
    background_tasks.add_task(_reindex_all_sync, user_id)
    logger.info("reindex_triggered", extra={"user_id": user_id})
    return {
        "success": True,
        "message": "知识库重建已在后台启动，所有已识别文书将陆续写入，请稍后刷新知识库状态。",
    }
