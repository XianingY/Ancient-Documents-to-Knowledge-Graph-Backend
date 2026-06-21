from app.services.ocr.consensus import agreement_text, build_consensus_result


def _row(text, score, bbox, **extra):
    return {"text": text, "score": score, "bbox": bbox, **extra}


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


def test_char_rescue_restores_verified_short_line_characters():
    result = build_consensus_result(
        [
            _row(
                "孔珍",
                0.9,
                [700, 10, 760, 120],
                char_rescue_text="孔□",
                char_rescue_confidence=0.82,
                char_rescue_visible_ratio=0.5,
            )
        ],
        [{"text": "孔", "score": 0.9}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "孔□"
    assert result.segments[0]["status"] == "partial"
    assert "rescued:char_consensus" in result.rejection_reasons
    assert "masked:char_rescue_disagreement" in result.rejection_reasons


def test_char_rescue_uses_line_agreement_for_same_length_short_lines():
    result = build_consensus_result(
        [
            _row(
                "車亨福",
                0.9,
                [700, 10, 760, 160],
                char_rescue_text="車□福",
                char_rescue_confidence=0.82,
                char_rescue_visible_ratio=0.67,
            )
        ],
        [{"text": "東亨福", "score": 0.9}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□亨福"
    assert result.segments[0]["safe_char_rescue_text"] == "□亨福"


def test_low_confidence_char_rescue_is_ignored():
    result = build_consensus_result(
        [
            _row(
                "孔珍",
                0.9,
                [700, 10, 760, 120],
                char_rescue_text="孔珍",
                char_rescue_confidence=0.6,
                char_rescue_visible_ratio=1.0,
            )
        ],
        [{"text": "孔", "score": 0.9}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert "rescued:char_consensus" not in result.rejection_reasons


def test_long_short_line_disagreement_stays_masked_even_with_char_rescue():
    result = build_consensus_result(
        [
            _row(
                "請憑親中明運",
                0.9,
                [700, 10, 760, 320],
                char_rescue_text="請□親中□□",
                char_rescue_confidence=0.88,
                char_rescue_visible_ratio=0.5,
            )
        ],
        [{"text": "請迈親中明", "score": 0.9}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert "rescued:char_consensus" not in result.rejection_reasons


def test_low_score_char_rescue_requires_complete_medium_agreement():
    result = build_consensus_result(
        [
            _row(
                "其田四止",
                0.93,
                [700, 10, 760, 220],
                char_rescue_text="真田四止",
                char_rescue_confidence=0.88,
                char_rescue_visible_ratio=1.0,
            )
        ],
        [{"text": "基田自", "score": 0.3}],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "□"
    assert result.segments[0]["safe_char_rescue_text"] == ""


def test_char_rescue_can_restore_missing_verifier_line():
    result = build_consensus_result(
        [
            _row(
                "其田四止",
                0.93,
                [700, 10, 760, 220],
                char_rescue_text="其田四止",
                char_rescue_confidence=0.88,
                char_rescue_visible_ratio=1.0,
            )
        ],
        [],
        (800, 600),
        [0, 0, 800, 600],
    )

    assert result.text == "其田四止"
    assert result.segments[0]["status"] == "accepted"
    assert "uncertain:missing_verifier" in result.rejection_reasons
    assert "rescued:char_consensus" in result.rejection_reasons


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
