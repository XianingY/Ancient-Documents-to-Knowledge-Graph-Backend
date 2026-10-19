"""分块 OCR 的切片、坐标合并与去重逻辑测试。"""
import importlib.util
import sys
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "experiments" / "tiled_ocr.py"


def load_tiled_ocr():
    spec = importlib.util.spec_from_file_location("tiled_ocr", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_tiles_covers_original_with_overlap(tmp_path):
    module = load_tiled_ocr()
    image_path = tmp_path / "source.png"
    Image.new("RGB", (1000, 750), "white").save(image_path)

    tiles = module.build_tiles(image_path, tile_width=300, tile_height=300, overlap=50)

    assert len(tiles) == 12
    assert tiles[0] == {
        "index": 0,
        "left": 0,
        "top": 0,
        "right": 300,
        "bottom": 300,
        "width": 300,
        "height": 300,
    }
    assert tiles[-1]["right"] == 1000
    assert tiles[-1]["bottom"] == 750


def test_tile_image_uses_source_window(tmp_path):
    module = load_tiled_ocr()
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(source)
    tile = {
        "index": 3,
        "left": 20,
        "top": 40,
        "right": 70,
        "bottom": 80,
        "width": 50,
        "height": 40,
    }

    with module.tile_image(source, tile, tmp_path) as tile_path:
        with Image.open(tile_path) as image:
            assert image.size == (50, 40)


def test_merge_segments_maps_coordinates_and_deduplicates():
    module = load_tiled_ocr()
    tile = {
        "index": 0,
        "left": 100,
        "top": 200,
        "right": 400,
        "bottom": 500,
        "width": 300,
        "height": 300,
    }
    first = {
        "text": "光緒辛丑",
        "confidence": 0.9,
        "image_bbox": [10, 20, 100, 40],
    }
    duplicate = {
        "text": "光緒辛丑",
        "confidence": 0.7,
        "image_bbox": [12, 21, 102, 41],
    }
    second = {
        "text": "字廣源",
        "confidence": 0.8,
        "image_bbox": [10, 60, 100, 80],
    }

    segments = module.merge_segments([([first, duplicate, second], tile)])

    assert len(segments) == 2
    assert segments[0]["image_bbox"] == [110, 220, 200, 240]
    assert segments[0]["tile_index"] == 0
    assert segments[0]["text"] == "光緒辛丑"


def test_merge_segments_deduplicates_overlapping_boxes_with_different_text():
    module = load_tiled_ocr()
    tile = {
        "index": 0,
        "left": 0,
        "top": 0,
        "right": 300,
        "bottom": 300,
        "width": 300,
        "height": 300,
    }
    low_confidence_garbage = {
        "text": "号民销",
        "confidence": 0.07,
        "image_bbox": [10, 20, 100, 40],
    }
    high_confidence_text = {
        "text": "先欽",
        "confidence": 0.97,
        "image_bbox": [11, 21, 101, 41],
    }

    segments = module.merge_segments(
        [([low_confidence_garbage, high_confidence_text], tile)]
    )

    assert len(segments) == 1
    assert segments[0]["text"] == "先欽"


def test_filter_segments_keeps_high_confidence_only():
    module = load_tiled_ocr()

    result = module.filter_segments(
        [
            {"text": "嗣興", "confidence": 0.97},
            {"text": "号民销", "confidence": 0.07},
            {"text": "賀氏", "confidence": 0.99},
        ],
        min_confidence=0.8,
    )

    assert [item["text"] for item in result] == ["嗣興", "賀氏"]


def test_summarize_ocr_counts_placeholders():
    module = load_tiled_ocr()

    summary = module.summarize_ocr("光緒□\n字廣源□□")

    assert summary == {
        "visible_characters": 5,
        "placeholder_count": 3,
        "placeholder_ratio": round(3 / 5, 4),
    }
