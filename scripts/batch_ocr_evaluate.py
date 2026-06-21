#!/usr/bin/env python3
"""Evaluate the conservative local OCR pipeline against human ground truth."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ocr import char_level_metrics, run_paddle_consensus


REGRESSION_KEYS = {
    "3·141",
    "3·142",
    "3·143",
    "3·144",
    "3·145",
    "3·158",
    "3·167",
    "3·191",
    "3·221",
    "3·235",
}


def load_ground_truth() -> dict:
    with (ROOT / "docs" / "ground_truth.json").open(encoding="utf-8") as file:
        return json.load(file)


def _canonical_image(key: str) -> Path | None:
    stem = key.replace("·", ".")
    candidates = [
        path
        for path in (ROOT / "img").glob(f"{stem}*")
        if path.is_file() and "背面" not in path.stem
    ]
    candidates.sort(
        key=lambda path: (
            path.stem != stem,
            len(path.stem),
            path.name.lower(),
        )
    )
    return candidates[0] if candidates else None


def _split_for_key(key: str) -> str:
    if key in REGRESSION_KEYS:
        return "regression"
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % 5
    if bucket == 0:
        return "test"
    if bucket == 1:
        return "dev"
    return "audit"


def match_images_to_gt(ground_truth: dict, split: str) -> list[dict]:
    matched = []
    for key in sorted(
        ground_truth,
        key=lambda value: tuple(int(part) for part in value.replace("·", ".").split(".")),
    ):
        if split != "all" and _split_for_key(key) != split:
            continue
        image_path = _canonical_image(key)
        if not image_path:
            continue
        item = ground_truth[key]
        matched.append({
            "gt_key": key,
            "gt_title": item["title"],
            "gt_text": item["body"],
            "image_path": image_path,
        })
    return matched


def _aggregate(results: list[dict]) -> dict:
    totals = {
        "gt_len": 0,
        "pred_len": 0,
        "output_len": 0,
        "lcs_len": 0,
        "edit_distance": 0,
        "insertions": 0,
        "substitutions": 0,
        "deletions": 0,
        "placeholder_count": 0,
    }
    for result in results:
        metrics = result["metrics"]
        for key in totals:
            totals[key] += metrics[key]

    precision = totals["lcs_len"] / max(totals["pred_len"], 1)
    recall = totals["lcs_len"] / max(totals["gt_len"], 1)
    return {
        "processed_images": len(results),
        "overall_precision": round(precision, 4),
        "overall_recall": round(recall, 4),
        "overall_f1": round(
            2 * precision * recall / max(precision + recall, 1e-10),
            4,
        ),
        "overall_cer": round(
            totals["edit_distance"] / max(totals["gt_len"], 1),
            4,
        ),
        "overall_extra_hallucination_rate": round(
            totals["insertions"] / max(totals["pred_len"], 1),
            4,
        ),
        "overall_fabricated_char_rate": round(
            (totals["insertions"] + totals["substitutions"])
            / max(totals["pred_len"], 1),
            4,
        ),
        "overall_placeholder_rate": round(
            totals["placeholder_count"] / max(totals["output_len"], 1),
            4,
        ),
        **{f"total_{key}": value for key, value in totals.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="保守 OCR 批量评估")
    parser.add_argument(
        "--split",
        choices=("regression", "dev", "test", "audit", "all"),
        default="regression",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default="ocr_eval_local_consensus.json")
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    ground_truth = load_ground_truth()
    matched = match_images_to_gt(ground_truth, args.split)
    if args.limit > 0:
        matched = matched[:args.limit]

    print(f"Ground truth: {len(ground_truth)}")
    print(f"Split: {args.split}; matched images: {len(matched)}")
    results = []
    started_at = time.time()

    for index, item in enumerate(matched, start=1):
        print(f"[{index}/{len(matched)}] {item['gt_key']} {item['image_path'].name}")
        try:
            pipeline = run_paddle_consensus(str(item["image_path"]))
        except Exception as exc:
            print(f"  failed: {exc}")
            results.append({
                "gt_key": item["gt_key"],
                "filename": item["image_path"].name,
                "error": str(exc),
            })
            continue

        metrics = char_level_metrics(pipeline.text, item["gt_text"])
        results.append({
            "gt_key": item["gt_key"],
            "gt_title": item["gt_title"],
            "filename": item["image_path"].name,
            "prediction": pipeline.text,
            "engine": pipeline.engine,
            "model_versions": pipeline.model_versions,
            "confidence": pipeline.confidence,
            "coverage": pipeline.coverage,
            "rejection_reasons": pipeline.rejection_reasons,
            "metrics": metrics,
        })
        print(
            f"  P={metrics['precision']:.1%} R={metrics['recall']:.1%} "
            f"F1={metrics['f1']:.1%} CER={metrics['cer']:.1%} "
            f"extra={metrics['extra_hallucination_rate']:.1%} "
            f"wrong+extra={metrics['fabricated_char_rate']:.1%} "
            f"placeholder={metrics['placeholder_rate']:.1%}"
        )
        if args.delay:
            time.sleep(args.delay)

    successful = [result for result in results if "metrics" in result]
    report = {
        "summary": {
            "split": args.split,
            "matched_images": len(matched),
            "elapsed_seconds": round(time.time() - started_at, 1),
            **_aggregate(successful),
        },
        "per_image": results,
    }
    output_path = ROOT / args.output
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = report["summary"]
    print(
        f"Overall P={summary['overall_precision']:.1%} "
        f"R={summary['overall_recall']:.1%} "
        f"F1={summary['overall_f1']:.1%} "
        f"extra={summary['overall_extra_hallucination_rate']:.1%} "
        f"wrong+extra={summary['overall_fabricated_char_rate']:.1%}"
    )
    print(f"Report: {output_path}")


if __name__ == "__main__":
    main()
