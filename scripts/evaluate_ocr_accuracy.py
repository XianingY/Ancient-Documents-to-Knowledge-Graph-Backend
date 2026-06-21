#!/usr/bin/env python3
"""Evaluate the latest completed database OCR result for each canonical image."""
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ocr.metrics import char_level_metrics
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
            metrics = char_level_metrics(
                ocr_result.raw_text or "",
                ground_truth[key]["body"],
            )
            results.append({
                "gt_key": key,
                "title": ground_truth[key]["title"],
                "filename": image.filename,
                "ocr_result_id": ocr_result.id,
                "engine": getattr(ocr_result, "engine", None),
                "metrics": metrics,
            })
    finally:
        db.close()

    if not results:
        print("No completed OCR results matched ground truth.")
        return

    results.sort(key=lambda item: item["metrics"]["f1"], reverse=True)
    total_gt = sum(item["metrics"]["gt_len"] for item in results)
    total_pred = sum(item["metrics"]["pred_len"] for item in results)
    total_lcs = sum(item["metrics"]["lcs_len"] for item in results)
    total_edits = sum(item["metrics"]["edit_distance"] for item in results)
    total_insertions = sum(item["metrics"]["insertions"] for item in results)
    total_substitutions = sum(
        item["metrics"]["substitutions"] for item in results
    )
    precision = total_lcs / max(total_pred, 1)
    recall = total_lcs / max(total_gt, 1)

    print(f"Matched latest OCR results: {len(results)}")
    print(f"Micro precision: {precision:.1%}")
    print(f"Micro recall:    {recall:.1%}")
    print(f"Micro F1:        {2 * precision * recall / max(precision + recall, 1e-10):.1%}")
    print(f"Micro CER:       {total_edits / max(total_gt, 1):.1%}")
    print(f"Extra rate:      {total_insertions / max(total_pred, 1):.1%}")
    print(
        "Wrong+extra:     "
        f"{(total_insertions + total_substitutions) / max(total_pred, 1):.1%}"
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
