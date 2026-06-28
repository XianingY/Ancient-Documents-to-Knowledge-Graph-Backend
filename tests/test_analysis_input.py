from types import SimpleNamespace


def test_analysis_uses_corrected_text_when_available():
    from app.services.analysis_service import _ocr_text_for_analysis

    ocr_result = SimpleNamespace(
        raw_text="OCR 原文",
        corrected_text="人工修订文本",
        human_corrected=True,
    )

    assert _ocr_text_for_analysis(ocr_result) == "人工修订文本"


def test_analysis_falls_back_to_raw_text_without_correction():
    from app.services.analysis_service import _ocr_text_for_analysis

    ocr_result = SimpleNamespace(
        raw_text="OCR 原文",
        corrected_text="",
        human_corrected=True,
    )

    assert _ocr_text_for_analysis(ocr_result) == "OCR 原文"
