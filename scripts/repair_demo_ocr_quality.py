#!/usr/bin/env python3
"""Re-run OCR for demo documents to restore true OCR quality metadata."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.services.ocr.types import OcrPipelineResult
from app.services.ocr_service import _run_configured_ocr
from database import Image, OcrResult, OcrStatus, SessionLocal, User


DEMO_USERNAME = "demo_web"


@dataclass(frozen=True)
class RepairSummary:
    username: str
    scanned: int
    repaired: int
    skipped: int
    failed: int


def _latest_done_ocr(db: Session, image_id: int) -> OcrResult | None:
    return (
        db.query(OcrResult)
        .filter(OcrResult.image_id == image_id, OcrResult.status == OcrStatus.DONE)
        .order_by(OcrResult.id.desc())
        .first()
    )


def _legacy_corrected_text(ocr_result: OcrResult) -> str | None:
    corrected = getattr(ocr_result, "corrected_text", None)
    if corrected:
        return corrected
    original = getattr(ocr_result, "original_raw_text", None)
    raw = getattr(ocr_result, "raw_text", None)
    if getattr(ocr_result, "human_corrected", False) and original and raw and raw != original:
        return raw
    return None


def _apply_pipeline_result(
    ocr_result: OcrResult,
    pipeline_result: OcrPipelineResult,
    corrected_text: str | None,
) -> None:
    cleaned_text = pipeline_result.text.strip() or "□"
    ocr_result.raw_text = cleaned_text
    ocr_result.original_raw_text = cleaned_text
    ocr_result.corrected_text = corrected_text
    ocr_result.human_corrected = bool(corrected_text)
    ocr_result.confidence = pipeline_result.confidence
    ocr_result.coverage = pipeline_result.coverage
    ocr_result.engine = pipeline_result.engine
    ocr_result.model_versions = pipeline_result.model_versions
    ocr_result.segments_json = (
        json.dumps(pipeline_result.segments, ensure_ascii=False)
        if pipeline_result.segments
        else None
    )
    ocr_result.rejection_reasons = (
        json.dumps(pipeline_result.rejection_reasons, ensure_ascii=False)
        if pipeline_result.rejection_reasons
        else None
    )
    ocr_result.crop_bbox_json = (
        json.dumps(pipeline_result.crop_bbox, ensure_ascii=False)
        if pipeline_result.crop_bbox
        else None
    )
    ocr_result.image_size_json = (
        json.dumps(pipeline_result.image_size, ensure_ascii=False)
        if pipeline_result.image_size
        else None
    )


def repair_demo_ocr_quality(
    db: Session,
    *,
    username: str = DEMO_USERNAME,
    limit: int | None = None,
    allow_non_demo: bool = False,
    ocr_runner: Callable[[str], OcrPipelineResult] = _run_configured_ocr,
) -> RepairSummary:
    if username != DEMO_USERNAME and not allow_non_demo:
        raise ValueError("Refusing to repair non-demo data without --allow-non-demo")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise ValueError(f"User not found: {username}")

    query = (
        db.query(Image)
        .filter(Image.user_id == user.id)
        .order_by(Image.upload_time.desc(), Image.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)

    scanned = repaired = skipped = failed = 0
    for image in query.all():
        scanned += 1
        if not image.path or not Path(image.path).exists():
            skipped += 1
            continue
        ocr_result = _latest_done_ocr(db, image.id)
        if not ocr_result:
            skipped += 1
            continue
        corrected_text = _legacy_corrected_text(ocr_result)
        try:
            pipeline_result = ocr_runner(str(image.path))
        except Exception as exc:
            failed += 1
            print(f"failed image_id={image.id}: {exc}", file=sys.stderr)
            continue
        _apply_pipeline_result(ocr_result, pipeline_result, corrected_text)
        repaired += 1
        db.commit()

    return RepairSummary(
        username=username,
        scanned=scanned,
        repaired=repaired,
        skipped=skipped,
        failed=failed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=DEMO_USERNAME)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--allow-non-demo", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = repair_demo_ocr_quality(
            db,
            username=args.username,
            limit=args.limit,
            allow_non_demo=args.allow_non_demo,
        )
    finally:
        db.close()

    print(
        "OCR quality repair complete: "
        f"username={summary.username} scanned={summary.scanned} "
        f"repaired={summary.repaired} skipped={summary.skipped} failed={summary.failed}"
    )
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
