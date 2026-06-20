"""Tests for qwen-vl-ocr-latest model upgrade."""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestQwenVlOcrUpgrade:
    @patch("dashscope.MultiModalConversation")
    @patch("app.services.ocr_service.settings")
    def test_uses_ocr_model_name(self, mock_settings, mock_mmc):
        mock_settings.DASHSCOPE_API_KEY = "test-key"
        mock_mmc.call.return_value = MagicMock(
            status_code=200,
            output=MagicMock(
                choices=[MagicMock(message=MagicMock(content=[{"text": "测试"}]))]
            ),
        )
        from app.services.ocr_service import _run_api_predict

        _run_api_predict("test.png")
        call_kwargs = mock_mmc.call.call_args
        model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert model == "qwen-vl-ocr-latest"

    @patch("dashscope.MultiModalConversation")
    @patch("app.services.ocr_service.settings")
    def test_no_system_role(self, mock_settings, mock_mmc):
        mock_settings.DASHSCOPE_API_KEY = "test-key"
        mock_mmc.call.return_value = MagicMock(
            status_code=200,
            output=MagicMock(
                choices=[MagicMock(message=MagicMock(content=[{"text": "ok"}]))]
            ),
        )
        from app.services.ocr_service import _run_api_predict

        _run_api_predict("test.png")
        messages = (
            mock_mmc.call.call_args.kwargs.get("messages")
            or mock_mmc.call.call_args[1].get("messages")
        )
        for msg in messages:
            assert msg.get("role") != "system"

    @patch("dashscope.MultiModalConversation")
    @patch("app.services.ocr_service.settings")
    def test_deterministic_params(self, mock_settings, mock_mmc):
        mock_settings.DASHSCOPE_API_KEY = "test-key"
        mock_mmc.call.return_value = MagicMock(
            status_code=200,
            output=MagicMock(
                choices=[MagicMock(message=MagicMock(content=[{"text": "ok"}]))]
            ),
        )
        from app.services.ocr_service import _run_api_predict

        _run_api_predict("test.png")
        kwargs = mock_mmc.call.call_args.kwargs
        assert kwargs.get("temperature") == 0.01
        assert kwargs.get("top_p") == 0.001
        assert kwargs.get("top_k") == 1

    @patch("dashscope.MultiModalConversation")
    @patch("app.services.ocr_service.settings")
    def test_extracts_text(self, mock_settings, mock_mmc):
        mock_settings.DASHSCOPE_API_KEY = "test-key"
        mock_mmc.call.return_value = MagicMock(
            status_code=200,
            output=MagicMock(
                choices=[
                    MagicMock(message=MagicMock(content=[{"text": "契约内容"}]))
                ]
            ),
        )
        from app.services.ocr_service import _run_api_predict

        result = _run_api_predict("test.png")
        assert result == "契约内容"
