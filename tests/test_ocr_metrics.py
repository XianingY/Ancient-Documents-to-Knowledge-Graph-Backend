from app.services.ocr.metrics import char_level_metrics


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
