from app.services.ocr.paddle_backend import _choose_char, _needs_char_rescue


def test_char_rescue_requires_two_sources():
    char, score = _choose_char({
        "medium_char": "珍",
        "medium_score": 0.95,
    })

    assert char == "□"
    assert score == 0.0


def test_char_rescue_accepts_medium_and_crop_agreement():
    char, score = _choose_char({
        "medium_char": "珍",
        "medium_score": 0.95,
        "small_char": "珍",
        "small_score": 0.81,
    })

    assert char == "珍"
    assert score == 0.81


def test_char_rescue_accepts_crop_models_against_medium_guess():
    char, score = _choose_char({
        "medium_char": "琦",
        "medium_score": 0.95,
        "small_char": "珍",
        "small_score": 0.81,
        "tiny_char": "珍",
        "tiny_score": 0.76,
    })

    assert char == "珍"
    assert score == 0.76


def test_char_rescue_candidate_keeps_short_disagreement():
    assert _needs_char_rescue(
        {"text": "孔珍", "score": 0.9},
        {"text": "孔", "score": 0.9},
    )


def test_char_rescue_candidate_skips_long_disagreement():
    assert not _needs_char_rescue(
        {"text": "請憑親中明運", "score": 0.9},
        {"text": "請迈親中明", "score": 0.9},
    )


def test_char_rescue_candidate_skips_single_low_score_char():
    assert not _needs_char_rescue(
        {"text": "永", "score": 0.9},
        {"text": "K", "score": 0.3},
    )


def test_char_rescue_candidate_keeps_short_low_score_line():
    assert _needs_char_rescue(
        {"text": "其田四止", "score": 0.93},
        {"text": "基田自", "score": 0.3},
    )
