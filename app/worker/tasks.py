
import asyncio
from app.core.celery_app import celery_app
from app.services.ocr_service import ocr_image_by_id
from app.services.analysis_service import analyze_ocr_result, analyze_structured_result, analyze_multi_task
from database import SessionLocal

def run_async(coro):
    """Run an async function in a sync context (Celery worker)."""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # This should ideally not happen in a standard Celery worker, but if we reuse loop
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)

@celery_app.task
def task_ocr_image(image_id: int):
    """Celery task for OCR."""
    db = SessionLocal()
    try:
        # Since ocr_image_by_id is async, we need to run it synchronously here
        # Creating a new event loop for each task to avoid closed loop issues
        asyncio.run(ocr_image_by_id(image_id, db))
    finally:
        db.close()

@celery_app.task
def task_analyze_ocr_result(ocr_result_id: int):
    """Celery task for structured analysis."""
    db = SessionLocal()
    try:
        asyncio.run(analyze_ocr_result(ocr_result_id, db))
    finally:
        db.close()

@celery_app.task
def task_analyze_structured_result(structured_result_id: int):
    """Celery task for relation graph analysis."""
    db = SessionLocal()
    try:
        asyncio.run(analyze_structured_result(structured_result_id, db))
    finally:
        db.close()

@celery_app.task
def task_analyze_multi_task(multi_task_id: int):
    """Celery task for multi-document analysis."""
    db = SessionLocal()
    try:
        asyncio.run(analyze_multi_task(multi_task_id, db))
    finally:
        db.close()
