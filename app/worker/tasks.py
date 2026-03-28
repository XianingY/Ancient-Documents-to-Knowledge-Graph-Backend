"""
Celery 异步任务定义
- 全部为同步任务，避免 asyncio.run() 与 Celery 事件循环冲突
- 失败后自动重试（最多3次，指数退避间隔）
- 每个任务记录结构化日志
"""
from app.core.celery_app import celery_app
from app.core.logger import get_logger
from app.services.ocr_service import ocr_image_by_id
from app.services.analysis_service import (
    analyze_ocr_result_sync,
    analyze_structured_result_sync,
    analyze_multi_task_sync,
)
from database import OcrResult, OcrStatus, SessionLocal, StructuredResult

logger = get_logger(__name__)

# 重试间隔（秒）：第1次10s、第2次20s、第3次40s
_RETRY_DELAYS = [10, 20, 40]


def _retry_delay(retries: int) -> int:
    return _RETRY_DELAYS[min(retries, len(_RETRY_DELAYS) - 1)]


@celery_app.task(bind=True, max_retries=3)
def task_ocr_image(self, image_id: int):
    """OCR 异步任务（含自动重试）"""
    logger.info("task_ocr_started", extra={"image_id": image_id, "attempt": self.request.retries + 1})
    db = SessionLocal()
    try:
        ok = ocr_image_by_id(image_id, db)
        if not ok:
            logger.warning("task_ocr_no_chain", extra={"image_id": image_id})
            return
        logger.info("task_ocr_done", extra={"image_id": image_id})
        # 上传/OCR 成功后自动排队结构化分析（与 App「记录」里识别结果自动出现一致）
        latest = (
            db.query(OcrResult)
            .filter(OcrResult.image_id == image_id, OcrResult.status == OcrStatus.DONE)
            .order_by(OcrResult.id.desc())
            .first()
        )
        text = (latest.raw_text or "").strip() if latest else ""
        if text and not text.startswith("Error:"):
            task_analyze_ocr_result.delay(latest.id)
            logger.info("task_analyze_queued_after_ocr", extra={"ocr_result_id": latest.id})
    except Exception as exc:
        logger.error(
            "task_ocr_failed",
            extra={"image_id": image_id, "attempt": self.request.retries + 1, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3)
def task_analyze_ocr_result(self, ocr_result_id: int):
    """结构化分析异步任务（含自动重试）"""
    logger.info(
        "task_analyze_started",
        extra={"ocr_result_id": ocr_result_id, "attempt": self.request.retries + 1},
    )
    db = SessionLocal()
    try:
        analyze_ocr_result_sync(ocr_result_id, db)
        logger.info("task_analyze_done", extra={"ocr_result_id": ocr_result_id})
        sr = (
            db.query(StructuredResult)
            .filter(StructuredResult.ocr_result_id == ocr_result_id)
            .order_by(StructuredResult.id.desc())
            .first()
        )
        if sr and sr.status == OcrStatus.DONE:
            task_analyze_structured_result.delay(sr.id)
            logger.info("task_graph_queued_after_structured", extra={"structured_result_id": sr.id})
    except Exception as exc:
        logger.error(
            "task_analyze_failed",
            extra={"ocr_result_id": ocr_result_id, "attempt": self.request.retries + 1, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3)
def task_analyze_structured_result(self, structured_result_id: int):
    """关系图生成异步任务（含自动重试）"""
    logger.info(
        "task_graph_started",
        extra={"structured_result_id": structured_result_id, "attempt": self.request.retries + 1},
    )
    db = SessionLocal()
    try:
        analyze_structured_result_sync(structured_result_id, db)
        logger.info("task_graph_done", extra={"structured_result_id": structured_result_id})
    except Exception as exc:
        logger.error(
            "task_graph_failed",
            extra={
                "structured_result_id": structured_result_id,
                "attempt": self.request.retries + 1,
                "error": str(exc),
            },
        )
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def task_analyze_multi_task(self, multi_task_id: int):
    """跨文档分析异步任务（含自动重试）"""
    logger.info(
        "task_multi_started",
        extra={"multi_task_id": multi_task_id, "attempt": self.request.retries + 1},
    )
    db = SessionLocal()
    try:
        analyze_multi_task_sync(multi_task_id, db)
        logger.info("task_multi_done", extra={"multi_task_id": multi_task_id})
    except Exception as exc:
        logger.error(
            "task_multi_failed",
            extra={"multi_task_id": multi_task_id, "attempt": self.request.retries + 1, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
    finally:
        db.close()
