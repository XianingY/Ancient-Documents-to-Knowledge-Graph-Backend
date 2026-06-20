"""Shared test fixtures for OCR optimization tests."""
import os
import sys
import tempfile
import pytest
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

@pytest.fixture
def sample_document_image(tmp_path):
    """Create a synthetic ancient document image for testing."""
    img = Image.new("RGB", (800, 1200), (240, 230, 210))
    pixels = np.array(img)
    noise = np.random.RandomState(42).normal(0, 5, pixels.shape).astype(np.int16)
    pixels = np.clip(pixels.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    for y in range(100, 1100, 80):
        x_start = 200 + np.random.RandomState(y).randint(-20, 20)
        x_end = 600 + np.random.RandomState(y + 1).randint(-20, 20)
        pixels[y:y + 3, x_start:x_end] = [30, 25, 20]
    img = Image.fromarray(pixels)
    path = tmp_path / "test_document.png"
    img.save(str(path))
    return str(path)

@pytest.fixture
def degraded_document_image(tmp_path):
    """Create a degraded document image (faded, low contrast)."""
    img = Image.new("RGB", (400, 600), (215, 210, 205))
    pixels = np.array(img)
    for y in range(50, 550, 40):
        pixels[y:y + 1, 100:300] = [195, 190, 185]
    noise = np.random.RandomState(42).normal(0, 10, pixels.shape).astype(np.int16)
    pixels = np.clip(pixels.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(pixels)
    path = tmp_path / "test_degraded_document.png"
    img.save(str(path))
    return str(path)

@pytest.fixture
def mock_dashscope_response_ocr():
    """Mock successful DashScope OCR response."""
    class MockResponse:
        status_code = 200
        class output:
            class choices:
                @staticmethod
                def __iter__():
                    class Message:
                        content = [{"text": "立契人 張三\n今將祖遺田產壹處\n坐落於..."}]
                    return iter([type('obj', (object,), {'message': Message()})()])
            choices = choices()
    return MockResponse()

@pytest.fixture
def mock_dashscope_response_error():
    """Mock failed DashScope API response."""
    class MockResponse:
        status_code = 400
        code = "InvalidApiKey"
        message = "API key is invalid"
    return MockResponse()

@pytest.fixture
def sample_ocr_texts():
    """Multiple OCR results for ensemble testing."""
    return [
        "立契人 張三\n今將祖遺田產壹處\n坐落於湖北武昌府",
        "立契人 張三\n今将祖遺田产壹处\n坐落於湖北武昌府",
        "立契人 張三\n今將祖遺田產壹處\n坐器於湖北武昌府",
    ]

@pytest.fixture
def ground_truth_text():
    """Reference ground truth for accuracy comparison."""
    return "立契人 張三\n今將祖遺田產壹處\n坐落於湖北武昌府"
