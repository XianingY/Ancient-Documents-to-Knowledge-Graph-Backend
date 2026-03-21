"""OCR 路由：获取 OCR 结果 / 获取某个 OCR 对应的结构化结果列表"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import OcrResult, StructuredResult, get_db
from app.core.security import security, verify_token

router = APIRouter(prefix="/api/v1/ocr-results", tags=["OCR结果"])


@router.get("/{ocr_id}")
async def get_ocr_result(
    ocr_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    verify_token(credentials.credentials)
    ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_id).first()
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OCR结果不存在")

    return {
        "success": True,
        "data": {
            "id": ocr_result.id,
            "image_id": ocr_result.image_id,
            "raw_text": ocr_result.raw_text,
            "status": ocr_result.status.value,
            "created_at": ocr_result.created_at.isoformat(),
        },
    }


@router.get("/{ocr_result_id}/structured-results")
async def get_ocr_structured_results(
    ocr_result_id: int,
    skip: int = 0,
    limit: int = 10,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    verify_token(credentials.credentials)
    ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_result_id).first()
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
