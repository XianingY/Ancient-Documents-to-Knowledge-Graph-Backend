"""结构化结果路由：触发分析 / 获取结果 / 获取对应关系图列表"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Image, OcrResult, RelationGraph, StructuredResult, get_db
from app.core.deps import get_current_user_id
from app.core.logger import get_logger
from app.worker.tasks import task_analyze_ocr_result

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/structured-results", tags=["结构化结果"])


class CreateStructuredResultRequest(BaseModel):
    ocr_result_id: int


@router.post("")
async def create_structured_result(
    request: CreateStructuredResultRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ocr_result = (
        db.query(OcrResult)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(OcrResult.id == request.ocr_result_id, Image.user_id == user_id)
        .first()
    )
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OcrResult不存在")

    task_analyze_ocr_result.delay(request.ocr_result_id)
    logger.info("structured_analysis_triggered", extra={"ocr_result_id": request.ocr_result_id})

    return {
        "success": True,
        "message": f"OCR结果 {request.ocr_result_id} 的结构化分析任务已提交到队列",
    }


@router.get("/{structured_result_id}")
async def get_structured_result(
    structured_result_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    structured_result = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(StructuredResult.id == structured_result_id, Image.user_id == user_id)
        .first()
    )
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")

    try:
        content = json.loads(structured_result.content)
    except Exception:
        content = structured_result.content

    return {
        "success": True,
        "data": {
            "id": structured_result.id,
            "ocr_result_id": structured_result.ocr_result_id,
            "content": content,
            "status": structured_result.status.value,
            "created_at": structured_result.created_at.isoformat(),
        },
    }


@router.get("/{structured_result_id}/relation-graphs")
async def get_structured_relation_graphs(
    structured_result_id: int,
    skip: int = 0,
    limit: int = 10,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    structured_result = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(StructuredResult.id == structured_result_id, Image.user_id == user_id)
        .first()
    )
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")

    relation_graphs = (
        db.query(RelationGraph.id)
        .filter(RelationGraph.structured_result_id == structured_result_id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = (
        db.query(RelationGraph)
        .filter(RelationGraph.structured_result_id == structured_result_id)
        .count()
    )

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [g[0] for g in relation_graphs],
        },
    }
