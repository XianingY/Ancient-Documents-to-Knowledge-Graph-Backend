#!/usr/bin/env python3
"""Evaluate the latest completed database OCR result for each canonical image."""
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ocr.metrics import aggregate_char_metrics, char_metric_modes
from database import Image, OcrResult, OcrStatus, SessionLocal


def _document_key(filename: str) -> str | None:
    match = re.search(r"(?<!\d)3\.(\d+)(?!\d)", filename or "")
    return f"3·{match.group(1)}" if match else None


def main() -> None:
    ground_truth = json.loads(
        (ROOT / "docs" / "ground_truth.json").read_text(encoding="utf-8")
    )
    db = SessionLocal()
    try:
        rows = (
            db.query(OcrResult, Image)
            .join(Image, OcrResult.image_id == Image.id)
            .filter(OcrResult.status == OcrStatus.DONE)
            .order_by(OcrResult.id.desc())
            .all()
        )
        latest: dict[str, tuple[OcrResult, Image]] = {}
        for ocr_result, image in rows:
            key = _document_key(image.filename)
            if key in ground_truth and "背面" not in image.filename:
                latest.setdefault(key, (ocr_result, image))

        results = []
        for key, (ocr_result, image) in latest.items():
            metric_modes = char_metric_modes(
                ocr_result.raw_text or "",
                ground_truth[key]["body"],
            )
            results.append({
                "gt_key": key,
                "title": ground_truth[key]["title"],
                "filename": image.filename,
                "ocr_result_id": ocr_result.id,
                "engine": getattr(ocr_result, "engine", None),
                "metrics": metric_modes["content"],
                "metric_modes": metric_modes,
            })
    finally:
        db.close()

    if not results:
        print("No completed OCR results matched ground truth.")
        return

    results.sort(key=lambda item: item["metrics"]["f1"], reverse=True)
    summaries = {
        mode: aggregate_char_metrics([
            item["metric_modes"][mode] for item in results
        ])
        for mode in ("raw", "faithful", "content")
    }

    print(f"Matched latest OCR results: {len(results)}")
    for mode in ("content", "faithful", "raw"):
        summary = summaries[mode]
        print(
            f"{mode.title():8s} P={summary['overall_precision']:.1%} "
            f"R={summary['overall_recall']:.1%} "
            f"F1={summary['overall_f1']:.1%} "
            f"CER={summary['overall_cer']:.1%} "
            f"extra={summary['overall_extra_hallucination_rate']:.1%} "
            f"wrong+extra={summary['overall_fabricated_char_rate']:.1%}"
        )
    print("\nBest 10")
    for item in results[:10]:
        metrics = item["metrics"]
        print(
            f"  {item['gt_key']:8s} F1={metrics['f1']:.1%} "
            f"extra={metrics['extra_hallucination_rate']:.1%} "
            f"{item['filename']}"
        )
    print("\nWorst 10")
    for item in results[-10:]:
        metrics = item["metrics"]
        print(
            f"  {item['gt_key']:8s} F1={metrics['f1']:.1%} "
            f"extra={metrics['extra_hallucination_rate']:.1%} "
            f"{item['filename']}"
        )


if __name__ == "__main__":
    main()
