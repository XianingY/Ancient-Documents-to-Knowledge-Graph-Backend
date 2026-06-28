"""Tests for OCR optimization config settings."""
import pytest
from app.core.config import settings


class TestOCRConfig:
    def test_real_esrgan_model_path_default(self):
        assert settings.REAL_ESRGAN_MODEL_PATH == "weights/realesr-general-x4v3.pth"

    def test_ensemble_passes_default(self):
        assert settings.ENSEMBLE_PASSES == 1

    def test_ensemble_passes_positive(self):
        assert settings.ENSEMBLE_PASSES > 0

    def test_ensemble_downscale_default(self):
        assert 0.5 < settings.ENSEMBLE_DOWNSCALE < 1.0

    def test_ensemble_noise_sigma_default(self):
        assert settings.ENSEMBLE_NOISE_SIGMA > 0

    def test_layout_ocr_defaults_preserve_stable_single_view_baseline(self):
        assert settings.OCR_MULTIVIEW_ENABLED is False
        assert settings.OCR_LAYOUT_ORIENTATION == "vertical"
