"""OCR 路由：获取 OCR 结果 / 获取某个 OCR 对应的结构化结果列表"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import Image, OcrResult, StructuredResult, get_beijing_time, get_db
from app.core.deps import get_current_user_id
from app.worker.tasks import task_analyze_ocr_result

router = APIRouter(prefix="/api/v1/ocr-results", tags=["OCR结果"])


class SegmentEdit(BaseModel):
    segment_id: str | int
    text: str


class UpdateOcrResultRequest(BaseModel):
    raw_text: str
    segment_edits: list[SegmentEdit] | None = None


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


def _encode_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _merge_segment_edits(existing_json: str | None, edits: list[SegmentEdit]):
    existing = {}
    for item in _decode_json_list(existing_json):
        if isinstance(item, dict) and "segment_id" in item:
            existing[str(item["segment_id"])] = item

    updated_at = get_beijing_time().isoformat()
    for edit in edits:
        existing[str(edit.segment_id)] = {
            "segment_id": str(edit.segment_id),
            "text": edit.text,
            "updated_at": updated_at,
        }
    return list(existing.values())


def _ocr_payload(ocr_result: OcrResult) -> dict:
    return {
        "id": ocr_result.id,
        "image_id": ocr_result.image_id,
        "raw_text": ocr_result.raw_text,
        "original_raw_text": getattr(ocr_result, "original_raw_text", None)
        or ocr_result.raw_text,
        "status": ocr_result.status.value,
        "confidence": getattr(ocr_result, "confidence", 0.0),
        "coverage": getattr(ocr_result, "coverage", 0.0),
        "engine": getattr(ocr_result, "engine", None),
        "model_versions": getattr(ocr_result, "model_versions", None),
        "segments": _decode_json_list(getattr(ocr_result, "segments_json", None)),
        "corrected_segments": _decode_json_list(
            getattr(ocr_result, "corrected_segments_json", None)
        ),
        "correction_metadata": (
            _decode_json_list(getattr(ocr_result, "correction_metadata_json", None))[0]
            if _decode_json_list(getattr(ocr_result, "correction_metadata_json", None))
            else {}
        ),
        "rejection_reasons": _decode_rejection_reasons(
            getattr(ocr_result, "rejection_reasons", None)
        ),
        "crop_bbox": _decode_json_list(getattr(ocr_result, "crop_bbox_json", None)),
        "image_size": _decode_json_list(getattr(ocr_result, "image_size_json", None)),
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

    if not getattr(ocr_result, "original_raw_text", None):
        ocr_result.original_raw_text = ocr_result.raw_text

    ocr_result.raw_text = request.raw_text
    if request.segment_edits is not None:
        corrected_segments = _merge_segment_edits(
            getattr(ocr_result, "corrected_segments_json", None),
            request.segment_edits,
        )
        ocr_result.corrected_segments_json = _encode_json(corrected_segments)
        correction_mode = "segments"
    else:
        correction_mode = "text"

    ocr_result.correction_metadata_json = _encode_json({
        "mode": correction_mode,
        "updated_at": get_beijing_time().isoformat(),
        "edit_count": len(request.segment_edits or []),
    })
    ocr_result.human_corrected = True
    db.commit()
    db.refresh(ocr_result)

    return {
        "success": True,
        "message": "修改成功",
        "data": _ocr_payload(ocr_result),
    }


@router.post("/{ocr_id}/reanalyze")
async def reanalyze_ocr_result(
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

    task_analyze_ocr_result.delay(ocr_id)
    return {
        "success": True,
        "message": f"OCR结果 {ocr_id} 的结构化分析任务已提交到队列",
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
        .order_by(StructuredResult.id.desc())
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
