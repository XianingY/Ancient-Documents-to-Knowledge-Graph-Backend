"""Tests for conservative V3/V4 OCR fusion."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestConservativeFusion:
    def test_v4_only_result_is_preserved(self):
        from app.services.ocr_service import _fuse_v3_v4

        fused, confidence = _fuse_v3_v4("", "立永賣白田約")

        assert fused == "立永賣白田約"
        assert confidence == 1.0

    def test_api_error_does_not_participate(self):
        from app.services.ocr_service import _fuse_v3_v4

        fused, confidence = _fuse_v3_v4("Error: timeout", "立永賣")

        assert fused == "立永賣"
        assert confidence == 1.0

    def test_v3_only_long_span_is_dropped(self):
        from app.services.ocr_service import _fuse_v3_v4

        v3 = "立永賣白田約人黃的楊建全因移就信福國富王家屬戴正教己辦"
        v4 = "立永賣白田約人"

        fused, _ = _fuse_v3_v4(v3, v4)

        assert fused == v4
        assert "黃的楊建全" not in fused

    def test_short_anchored_v3_gap_can_fill(self):
        from app.services.ocr_service import _fuse_v3_v4

        fused, _ = _fuse_v3_v4("立永賣白田約", "立永賣田約")

        assert fused == "立永賣白田約"

    def test_mismatch_prefers_v4(self):
        from app.services.ocr_service import _fuse_v3_v4

        fused, _ = _fuse_v3_v4("女永賣今日核計子便", "立永賣今因移就不便")

        assert fused == "立永賣今因移就不便"
