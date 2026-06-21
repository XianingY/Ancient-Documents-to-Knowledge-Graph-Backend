from app.services.ocr.consensus import agreement_text, build_consensus_result


def _row(text, score, bbox):
    return {"text": text, "score": score, "bbox": bbox}


def test_agreement_masks_disagreement_including_edges():
    text, similarity = agreement_text("女永賣契", "立永賣約")

    assert text == "□永賣□"
    assert 0 < similarity < 1


def test_identical_models_keep_line():
    result = build_consensus_result(
        [_row("立永賣田契", 0.95, [700, 10, 760, 300])],
        [{"text": "立永賣田契", "score": 0.94}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "立永賣田契"
    assert result.coverage == 1.0
    assert result.confidence == 0.94


def test_long_low_similarity_line_becomes_placeholder():
    result = build_consensus_result(
        [_row("黃的楊建全因移就信福國富", 0.8, [700, 10, 760, 500])],
        [{"text": "祖遺田地積圓高正錢分", "score": 0.8}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert "uncertain:low_model_similarity:0.00" in result.rejection_reasons


def test_unverified_short_primary_becomes_placeholder():
    result = build_consensus_result(
        [_row("道光十年", 0.96, [700, 10, 760, 200])],
        [],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert result.segments[0]["confidence"] == 0.0
    assert "uncertain:missing_verifier" in result.rejection_reasons


def test_short_role_line_disagreement_is_fully_masked():
    result = build_consensus_result(
        [_row("冊名楊大貴選中人", 0.9, [700, 10, 760, 200])],
        [{"text": "冊名楊大貴憑中人", "score": 0.9}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert "uncertain:short_or_role_line_disagreement" in result.rejection_reasons


def test_vertical_lines_are_sorted_right_to_left():
    result = build_consensus_result(
        [
            _row("左行", 0.95, [100, 10, 150, 200]),
            _row("右行", 0.95, [700, 10, 750, 200]),
        ],
        [
            {"text": "左行", "score": 0.95},
            {"text": "右行", "score": 0.95},
        ],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "右行\n左行"


def test_edge_numeric_ruler_label_is_rejected():
    result = build_consensus_result(
        [_row("3.141", 0.99, [0, 10, 50, 100])],
        [{"text": "3.141", "score": 0.99}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == ""
    assert "rejected:non_document_text" in result.rejection_reasons
