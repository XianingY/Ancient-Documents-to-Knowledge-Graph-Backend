from app.services.ocr.consensus import (
    build_consensus_result,
    build_multiview_consensus_result,
)
from app.core.config import settings


def _row(text, score, bbox, **extra):
    return {"text": text, "score": score, "bbox": bbox, "poly": [], **extra}


def _single_result(text, bbox, view="original", score=0.95):
    return build_consensus_result(
        [_row(text, score, bbox)],
        [{"text": text, "score": score}],
        (800, 600),
        [100, 50, 900, 650],
        original_size=(1000, 800),
        view_name=view,
    )


def test_horizontal_layout_rows_sort_top_to_bottom_left_to_right(monkeypatch):
    monkeypatch.setattr(settings, "OCR_LAYOUT_ORIENTATION", "auto")
    result = build_consensus_result(
        [
            _row("下左", 0.95, [80, 220, 240, 260]),
            _row("上右", 0.95, [360, 80, 520, 120]),
            _row("上左", 0.95, [80, 80, 240, 120]),
        ],
        [
            {"text": "下左", "score": 0.95},
            {"text": "上右", "score": 0.95},
            {"text": "上左", "score": 0.95},
        ],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "上左\n上右\n下左"
    assert [segment["order_index"] for segment in result.segments] == [0, 1, 2]
    assert result.segments[0]["layout_orientation"] == "horizontal"


def test_segment_coordinates_map_back_to_original_image():
    result = build_consensus_result(
        [_row("立永賣", 0.95, [200, 100, 240, 160])],
        [{"text": "立永賣", "score": 0.95}],
        (400, 300),
        [100, 50, 900, 650],
        original_size=(1000, 800),
    )

    assert result.image_size == [1000, 800]
    assert result.segments[0]["image_bbox"] == [500, 250, 580, 370]


def test_multiview_keeps_text_when_two_views_agree():
    original = _single_result("立永賣", [700, 10, 760, 180], "original")
    contrast = _single_result("立永賣", [702, 12, 762, 182], "contrast")

    result = build_multiview_consensus_result(
        [original, contrast],
        (800, 600),
        [100, 50, 900, 650],
        (1000, 800),
    )

    assert result.text == "立永賣"
    assert result.segments[0]["source_count"] == 2
    assert result.segments[0]["source_views"] == ["contrast", "original"]


def test_multiview_rejects_single_view_long_text():
    original = _single_result("今日核計子便祖遺田地", [700, 10, 760, 360], "original")

    result = build_multiview_consensus_result(
        [original],
        (800, 600),
        [100, 50, 900, 650],
        (1000, 800),
    )

    assert result.text == "□"
    assert "uncertain:single_view_long_or_low_confidence" in result.rejection_reasons


def test_multiview_keeps_single_view_short_high_confidence_text():
    original = _single_result("孔珍", [700, 10, 760, 120], "original")

    result = build_multiview_consensus_result(
        [original],
        (800, 600),
        [100, 50, 900, 650],
        (1000, 800),
    )

    assert result.text == "孔珍"
    assert "accepted:single_view_short_high_confidence" not in result.rejection_reasons
