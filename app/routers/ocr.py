"""OCR 路由：获取 OCR 结果 / 获取某个 OCR 对应的结构化结果列表"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import Image, OcrResult, StructuredResult, get_db
from app.core.deps import get_current_user_id

router = APIRouter(prefix="/api/v1/ocr-results", tags=["OCR结果"])

class UpdateOcrResultRequest(BaseModel):
    raw_text: str


def _decode_json_list(value: str | None):
    if not value:
        return []
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, list) else [decoded]
    except (TypeError, json.JSONDecodeError):
        return [value]


def _decode_rejection_reasons(value: str | None):
    return _decode_json_list(value)


def _ocr_payload(ocr_result: OcrResult) -> dict:
    return {
        "id": ocr_result.id,
        "image_id": ocr_result.image_id,
        "raw_text": ocr_result.raw_text,
        "status": ocr_result.status.value,
        "confidence": getattr(ocr_result, "confidence", 0.0),
        "coverage": getattr(ocr_result, "coverage", 0.0),
        "engine": getattr(ocr_result, "engine", None),
        "model_versions": getattr(ocr_result, "model_versions", None),
        "segments": _decode_json_list(getattr(ocr_result, "segments_json", None)),
        "rejection_reasons": _decode_rejection_reasons(
            getattr(ocr_result, "rejection_reasons", None)
        ),
        "human_corrected": bool(getattr(ocr_result, "human_corrected", False)),
        "created_at": ocr_result.created_at.isoformat(),
    }


@router.patch("/{ocr_id}")
async def update_ocr_result(
    ocr_id: int,
    request: UpdateOcrResultRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ocr_result = (
        db.query(OcrResult)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(OcrResult.id == ocr_id, Image.user_id == user_id)
        .first()
    )
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OCR结果不存在")

    ocr_result.raw_text = request.raw_text
    ocr_result.confidence = 1.0
    ocr_result.coverage = 1.0
    ocr_result.human_corrected = True
    db.commit()
    db.refresh(ocr_result)

    return {
        "success": True,
        "message": "修改成功",
        "data": _ocr_payload(ocr_result),
    }

@router.get("/{ocr_id}")
async def get_ocr_result(
    ocr_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ocr_result = (
        db.query(OcrResult)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(OcrResult.id == ocr_id, Image.user_id == user_id)
        .first()
    )
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OCR结果不存在")

    return {
        "success": True,
        "data": _ocr_payload(ocr_result),
    }


@router.get("/{ocr_result_id}/structured-results")
async def get_ocr_structured_results(
    ocr_result_id: int,
    skip: int = 0,
    limit: int = 10,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ocr_result = (
        db.query(OcrResult)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(OcrResult.id == ocr_result_id, Image.user_id == user_id)
        .first()
    )
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OcrResult不存在")

    structured_results = (
        db.query(StructuredResult.id)
        .filter(StructuredResult.ocr_result_id == ocr_result_id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = (
        db.query(StructuredResult)
        .filter(StructuredResult.ocr_result_id == ocr_result_id)
        .count()
    )

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [r[0] for r in structured_results],
        },
    }
