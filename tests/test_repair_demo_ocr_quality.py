from types import SimpleNamespace

from app.services.ocr.types import OcrPipelineResult
from scripts.repair_demo_ocr_quality import _apply_pipeline_result, _legacy_corrected_text


def test_legacy_corrected_text_preserves_existing_correction():
    ocr_result = SimpleNamespace(
        raw_text="旧 raw",
        original_raw_text="旧 original",
        corrected_text="已有校订",
        human_corrected=True,
    )

    assert _legacy_corrected_text(ocr_result) == "已有校订"


def test_legacy_corrected_text_recovers_old_overwritten_raw_text():
    ocr_result = SimpleNamespace(
        raw_text="人工校订",
        original_raw_text="OCR 原文",
        corrected_text=None,
        human_corrected=True,
    )

    assert _legacy_corrected_text(ocr_result) == "人工校订"


def test_apply_pipeline_result_keeps_corrected_text_and_updates_quality():
    ocr_result = SimpleNamespace(
        raw_text="旧 OCR",
        original_raw_text="旧 OCR",
        corrected_text="人工校订",
        confidence=1.0,
        coverage=1.0,
        engine="old",
        model_versions="old",
        segments_json=None,
        rejection_reasons=None,
        crop_bbox_json=None,
        image_size_json=None,
        human_corrected=True,
    )
    pipeline = OcrPipelineResult(
        text="十万卖与",
        confidence=0.43,
        coverage=0.62,
        engine="paddle_v6_consensus",
        model_versions="test",
        segments=[{"text": "十万卖与"}],
        rejection_reasons=["uncertain"],
        crop_bbox=[0, 0, 10, 10],
        image_size=[100, 200],
    )

    _apply_pipeline_result(ocr_result, pipeline, "人工校订")

    assert ocr_result.raw_text == "十万卖与"
    assert ocr_result.original_raw_text == "十万卖与"
    assert ocr_result.corrected_text == "人工校订"
    assert ocr_result.human_corrected is True
    assert ocr_result.confidence == 0.43
    assert ocr_result.coverage == 0.62
    assert '"十万卖与"' in ocr_result.segments_json


def test_apply_pipeline_result_clears_human_flag_without_correction():
    ocr_result = SimpleNamespace(
        raw_text="旧 OCR",
        original_raw_text="旧 OCR",
        corrected_text=None,
        confidence=1.0,
        coverage=1.0,
        engine="old",
        model_versions="old",
        segments_json=None,
        rejection_reasons=None,
        crop_bbox_json=None,
        image_size_json=None,
        human_corrected=True,
    )
    pipeline = OcrPipelineResult(
        text="OCR 原文",
        confidence=0.5,
        coverage=0.6,
        engine="paddle_v6_consensus",
        model_versions="test",
    )

    _apply_pipeline_result(ocr_result, pipeline, None)

    assert ocr_result.corrected_text is None
    assert ocr_result.human_corrected is False
