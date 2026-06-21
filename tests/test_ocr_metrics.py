from app.services.ocr.metrics import (
    aggregate_char_metrics,
    char_level_metrics,
    char_metric_modes,
    normalize_metric_text,
)


def test_true_edit_alignment_counts_insertions():
    metrics = char_level_metrics("甲乙编造丙", "甲乙丙")

    assert metrics["edit_distance"] == 2
    assert metrics["insertions"] == 2
    assert metrics["substitutions"] == 0
    assert metrics["extra_hallucination_rate"] == 0.4
    assert metrics["fabricated_char_rate"] == 0.4


def test_placeholders_are_not_scored_as_visible_hallucinations():
    metrics = char_level_metrics("甲□丙", "甲乙丙")

    assert metrics["pred_len"] == 2
    assert metrics["insertions"] == 0
    assert metrics["fabricated_char_rate"] == 0.0
    assert metrics["placeholder_count"] == 1
    assert metrics["placeholder_rate"] == 0.3333


def test_metrics_are_order_sensitive():
    metrics = char_level_metrics("丙乙甲", "甲乙丙")

    assert metrics["precision"] < 1.0
    assert metrics["recall"] < 1.0
    assert metrics["edit_distance"] > 0


def test_faithful_mode_ignores_editorial_punctuation():
    raw = char_level_metrics("立永賣田約人", "立永賣田約人，", mode="raw")
    faithful = char_level_metrics("立永賣田約人", "立永賣田約人，", mode="faithful")

    assert raw["recall"] < 1.0
    assert faithful["recall"] == 1.0
    assert faithful["precision"] == 1.0


def test_content_mode_folds_common_simplified_traditional_variants():
    faithful = char_level_metrics("永賣與熊篤敘堂為業", "永卖与熊篤敘堂为业", mode="faithful")
    content = char_level_metrics("永賣與熊篤敘堂為業", "永卖与熊篤敘堂为业", mode="content")

    assert faithful["f1"] < 1.0
    assert content["f1"] == 1.0


def test_metric_modes_and_aggregation_report_all_modes():
    metric_modes = char_metric_modes("甲□為業", "甲乙为业。")
    summary = aggregate_char_metrics([metric_modes["content"]])

    assert set(metric_modes) == {"raw", "faithful", "content"}
    assert metric_modes["content"]["recall"] > metric_modes["raw"]["recall"]
    assert summary["mode"] == "content"
    assert summary["processed_images"] == 1


def test_metric_normalization_rejects_unknown_mode():
    try:
        normalize_metric_text("甲", "surprise")
    except ValueError as exc:
        assert "Unknown OCR metric mode" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown metric mode")
