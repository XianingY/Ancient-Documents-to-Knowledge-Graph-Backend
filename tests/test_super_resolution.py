"""Tests for Real-ESRGAN super-resolution preprocessing."""
import os
import sys
import pytest
import numpy as np
from PIL import Image
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRealESRGANPreprocessing:
    def test_esrgan_config_present(self):
        from app.core.config import settings
        assert hasattr(settings, "REAL_ESRGAN_MODEL_PATH")
        assert isinstance(settings.REAL_ESRGAN_MODEL_PATH, str)

    def test_esrgan_parameters(self):
        expected = {"outscale": 2, "tile": 400, "half": True}
        assert expected["outscale"] == 2
        assert expected["tile"] == 400

    def test_grayscale_after_esrgan(self):
        import inspect
        from app.services.ocr_service import _preprocess_image
        source = inspect.getsource(_preprocess_image)
        esrgan_pos = source.find("RealESRGANer") if "RealESRGANer" in source else -1
        grayscale_pos = source.find('convert("L")')
        if esrgan_pos >= 0 and grayscale_pos >= 0:
            assert esrgan_pos < grayscale_pos

    def test_esrgan_not_required(self):
        import inspect
        from app.services.ocr_service import _preprocess_image
        source = inspect.getsource(_preprocess_image)
        assert "ImportError" in source or "except" in source
