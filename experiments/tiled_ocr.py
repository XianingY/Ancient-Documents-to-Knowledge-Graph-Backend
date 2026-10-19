"""超大文献图的高分辨率分块 OCR 实验。

整图 OCR 会把 12800×9600 行传图缩到最长边 2400，导致小字笔画损失。
本脚本把原图切成若干高分辨率局部图，再逐块调用现有 PaddleOCR 共识管线，
最后把局部坐标映射回原图并按重叠区域去重。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from PIL import Image


Image.MAX_IMAGE_PIXELS = None

# 允许 `python experiments/tiled_ocr.py` 直接运行时导入项目内 app 包。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_tiles(
    image_path: Path | str,
    tile_width: int,
    tile_height: int,
    overlap: int,
) -> list[dict]:
    """按固定窗口和重叠区域生成覆盖整图的切片描述。"""
    if tile_width <= overlap or tile_height <= overlap:
        raise ValueError("tile dimensions must be larger than overlap")

    with Image.open(image_path) as image:
        width, height = image.size

    x_starts = _starts(width, tile_width, overlap)
    y_starts = _starts(height, tile_height, overlap)
    tiles = []
    for top in y_starts:
        for left in x_starts:
            right = min(width, left + tile_width)
            bottom = min(height, top + tile_height)
            tiles.append(
                {
                    "index": len(tiles),
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "width": right - left,
                    "height": bottom - top,
                }
            )
    return tiles


def _starts(size: int, tile_size: int, overlap: int) -> list[int]:
    if size <= tile_size:
        return [0]
    step = tile_size - overlap
    starts = list(range(0, size - tile_size + 1, step))
    last = size - tile_size
    if starts[-1] != last:
        starts.append(last)
    return starts


@contextmanager
def tile_image(
    source: Path | str,
    tile: dict,
    work_dir: Path | str,
):
    """从原图裁出切片并保存为临时 PNG。"""
    output_dir = Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tile-{tile['index']:04d}.png"
    with Image.open(source) as image:
        crop = image.crop(
            (tile["left"], tile["top"], tile["right"], tile["bottom"])
        )
        crop.save(path)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _box_iou(first: list[int], second: list[int]) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (first[2] - first[0]) * (first[3] - first[1])
    area2 = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / (area1 + area2 - intersection)


def merge_segments(
    tile_results: Iterable[tuple[list[dict], dict]],
    iou_threshold: float = 0.5,
) -> list[dict]:
    """把局部 OCR 片段映射回原图坐标，并去掉重叠区域的重复文本。"""
    candidates = []
    for segments, tile in tile_results:
        for segment in segments:
            box = segment.get("image_bbox") or segment.get("bbox")
            if not box or len(box) < 4:
                continue
            merged = dict(segment)
            merged["image_bbox"] = [
                tile["left"] + int(box[0]),
                tile["top"] + int(box[1]),
                tile["left"] + int(box[2]),
                tile["top"] + int(box[3]),
            ]
            merged["tile_index"] = tile["index"]
            candidates.append(merged)

    candidates.sort(
        key=lambda item: (
            -float(item.get("confidence", 0.0) or 0.0),
            item["image_bbox"][1],
            item["image_bbox"][0],
        )
    )
    kept = []
    for candidate in candidates:
        duplicate = any(
            _box_iou(candidate["image_bbox"], existing["image_bbox"])
            >= iou_threshold
            for existing in kept
        )
        if not duplicate:
            kept.append(candidate)

    return sorted(
        kept,
        key=lambda item: (item["image_bbox"][1], item["image_bbox"][0]),
    )


def filter_segments(segments: list[dict], min_confidence: float = 0.8) -> list[dict]:
    return [
        segment
        for segment in segments
        if float(segment.get("confidence", 0.0) or 0.0) >= min_confidence
    ]


def summarize_ocr(text: str) -> dict:
    """统计可见字符、不确定占位符和占位符比例。"""
    compact = "".join(text.split())
    placeholder_count = compact.count("□")
    visible = len(compact) - placeholder_count
    return {
        "visible_characters": visible,
        "placeholder_count": placeholder_count,
        "placeholder_ratio": round(placeholder_count / visible, 4)
        if visible
        else 0.0,
    }


def compose_text(segments: list[dict]) -> str:
    return "\n".join(str(segment.get("text", "")).strip() for segment in segments if segment.get("text"))


def run_medium_ocr(
    image_path: Path | str,
    min_confidence: float = 0.8,
) -> list[dict]:
    """使用 PP-OCRv6 medium 的原始识别结果，不做双模型共识屏蔽。"""
    from app.services.ocr.paddle_backend import (
        _load_models,
        _medium_rows,
        _result_payload,
    )

    medium_pipeline, _small_recognizer, _tiny_recognizer, _cropper = _load_models()
    results = list(medium_pipeline.predict(str(image_path)))
    if not results:
        return []
    payload = _result_payload(results[0])
    segments = [
        {
            "text": row.get("text", ""),
            "confidence": row.get("score", 0.0),
            "image_bbox": row.get("bbox"),
            "medium_text": row.get("text", ""),
            "medium_score": row.get("score", 0.0),
            "status": "raw_medium",
        }
        for row in _medium_rows(payload)
    ]
    return filter_segments(segments, min_confidence=min_confidence)


def run_tiled_ocr(
    image_path: Path | str,
    output_dir: Path | str,
    tile_width: int = 3200,
    tile_height: int = 2400,
    overlap: int = 256,
    work_dir: Path | str | None = None,
    engine: str = "consensus",
    min_confidence: float = 0.8,
) -> dict:
    """对一张超大图执行高分辨率分块 OCR。"""
    os.environ.setdefault(
        "SECRET_KEY", "tiled-ocr-local-secret-key-with-at-least-32-chars"
    )
    from app.services.ocr import run_paddle_consensus

    source = Path(image_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(work_dir) if work_dir else output / ".tiles"

    tiles = build_tiles(source, tile_width, tile_height, overlap)
    tile_results = []
    for tile in tiles:
        with tile_image(source, tile, temp_dir) as tile_path:
            if engine == "medium":
                segments = run_medium_ocr(
                    tile_path,
                    min_confidence=min_confidence,
                )
            elif engine == "consensus":
                result = run_paddle_consensus(str(tile_path))
                segments = result.segments
            else:
                raise ValueError(f"Unsupported OCR engine: {engine}")
        tile_results.append((segments, tile))

    segments = merge_segments(tile_results)
    text = compose_text(segments)
    summary = summarize_ocr(text)
    document_id = source.stem

    text_path = output / f"{document_id}.tiled.txt"
    json_path = output / f"{document_id}.tiled.json"
    text_path.write_text(text, encoding="utf-8")
    payload = {
        "document_id": document_id,
        "source": source.name,
        "tile": {
            "width": tile_width,
            "height": tile_height,
            "overlap": overlap,
            "count": len(tiles),
        },
        "engine": engine,
        "min_confidence": min_confidence if engine == "medium" else None,
        "summary": summary,
        "segments": segments,
        "text_path": text_path.name,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="超大文献图分块 OCR")
    parser.add_argument("images", nargs="+", help="待 OCR 的 PNG/JPG 图片")
    parser.add_argument("--output-dir", default="experiment_outputs/tiled_ocr")
    parser.add_argument("--tile-width", type=int, default=3200)
    parser.add_argument("--tile-height", type=int, default=2400)
    parser.add_argument("--overlap", type=int, default=256)
    parser.add_argument(
        "--engine",
        choices=["consensus", "medium"],
        default="consensus",
        help="consensus 使用双模型共识；medium 只保留 PP-OCRv6 medium 原始结果",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="medium 引擎保留片段的最低置信度",
    )
    args = parser.parse_args()

    results = []
    for image in args.images:
        result = run_tiled_ocr(
            image,
            args.output_dir,
            tile_width=args.tile_width,
            tile_height=args.tile_height,
            overlap=args.overlap,
            engine=args.engine,
            min_confidence=args.min_confidence,
        )
        results.append(
            {
                "document_id": result["document_id"],
                "tiles": result["tile"]["count"],
                **result["summary"],
            }
        )
        print(
            json.dumps(
                {
                    "document_id": result["document_id"],
                    "tiles": result["tile"]["count"],
                    **result["summary"],
                },
                ensure_ascii=False,
            )
        )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
