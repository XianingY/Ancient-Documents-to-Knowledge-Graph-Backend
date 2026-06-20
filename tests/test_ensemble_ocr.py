"""Tests for multi-pass ensemble OCR with N-W alignment."""
import os
import sys
import pytest
import numpy as np
from PIL import Image
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

class TestEnsembleOCR:
    def test_augment_image_produces_variants(self):
        from app.services.ocr_service import _augment_image
        img = Image.new("RGB", (200, 300), (200, 200, 200))
        pixels = np.array(img)
        pixels[50:53, 50:150] = [30, 30, 30]
        img = Image.fromarray(pixels)
        variants = _augment_image(img, num_variants=3)
        assert len(variants) == 3
        for v in variants:
            assert isinstance(v, Image.Image)
            assert v.size == img.size

    @patch("app.services.ocr_service._run_api_predict")
    def test_ensemble_ocr_calls_api_multiple_times(self, mock_predict):
        mock_predict.return_value = "测试文字"
        from app.services.ocr_service import _ensemble_ocr
        img = Image.new("RGB", (200, 300), (200, 200, 200))
        _ensemble_ocr(img, num_passes=3)
        assert mock_predict.call_count == 3

    @patch("app.services.ocr_service._run_api_predict")
    def test_ensemble_ocr_returns_string(self, mock_predict):
        mock_predict.return_value = "立契人 張三"
        from app.services.ocr_service import _ensemble_ocr
        img = Image.new("RGB", (200, 300), (200, 200, 200))
        result = _ensemble_ocr(img, num_passes=2)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nw_consensus_basic(self):
        from app.services.ocr_service import _nw_consensus
        texts = ["立契人張三今將田產", "立契人張三今将田产", "立契人張三今將田產"]
        result = _nw_consensus(texts)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "立" in result

    def test_ensemble_config_present(self):
        from app.core.config import settings
        assert hasattr(settings, "ENSEMBLE_PASSES")
        assert settings.ENSEMBLE_PASSES >= 1

    @patch("app.services.ocr_service._run_api_predict")
    def test_ensemble_handles_api_failure(self, mock_predict):
        mock_predict.side_effect = ["成功1", "Error: API timeout", "成功3"]
        from app.services.ocr_service import _ensemble_ocr
        img = Image.new("RGB", (200, 300), (200, 200, 200))
        result = _ensemble_ocr(img, num_passes=3)
        assert isinstance(result, str)
