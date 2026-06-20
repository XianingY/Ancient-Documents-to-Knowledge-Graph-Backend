"""Integration test for OCR optimization pipeline composition."""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestOCRPipelineComposition:
    @patch("app.services.ocr_service._run_api_predict")
    @patch("app.services.ocr_service.settings")
    def test_full_pipeline_mock(self, mock_settings, mock_predict):
        """Test that preprocess → ensemble → clean → correct can be composed."""
        mock_settings.DASHSCOPE_API_KEY = "test-key"
        mock_settings.ENSEMBLE_PASSES = 2
        mock_settings.REAL_ESRGAN_MODEL_PATH = ""
        mock_settings.ENSEMBLE_DOWNSCALE = 0.5
        mock_settings.ENSEMBLE_NOISE_SIGMA = 1.0
        mock_predict.return_value = "立契人張三今將田產賣與李四"

        from app.services.ocr_service import _ensemble_ocr, _clean_vl_output, _correct_ocr_text

        img = Image.new("RGB", (200, 300), (200, 200, 200))
        text = _ensemble_ocr(img)
        cleaned = _clean_vl_output(text)
        corrected = _correct_ocr_text(cleaned)

        assert isinstance(corrected, str)
        assert len(corrected) > 0
        assert mock_predict.call_count == 2

    def test_config_values_loaded(self):
        from app.core.config import settings
        assert settings.REAL_ESRGAN_MODEL_PATH is not None
        assert settings.ENSEMBLE_PASSES >= 2
        assert 0.0 < settings.ENSEMBLE_DOWNSCALE < 1.0
        assert settings.ENSEMBLE_NOISE_SIGMA > 0
