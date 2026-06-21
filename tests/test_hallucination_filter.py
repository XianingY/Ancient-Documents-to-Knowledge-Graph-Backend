"""Tests for deterministic OCR hallucination filters."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestHallucinationFilter:
    def test_length_gate_rejects_extremely_long_text(self):
        from app.services.ocr_service import _length_ratio_gate

        passed, reason = _length_ratio_gate("字" * 700)

        assert not passed
        assert "hard_reject:too_long" in reason

    def test_v3_only_ratio_falls_back_to_v4(self):
        from app.services.ocr_service import _filter_hallucinations

        v4 = "立永賣白田約人楊大選"
        fused = v4 + "黃的楊建全因移就信福國富王家屬戴正教己辦"

        filtered, confidence, reasons = _filter_hallucinations(fused, fused, v4, 0.4)

        assert filtered == v4
        assert confidence == 0.4
        assert any("v3_only_ratio" in reason for reason in reasons)

    def test_template_density_falls_back_when_confidence_is_low(self):
        from app.services.ocr_service import _filter_hallucinations

        v4 = "立永賣白田約"
        fused = (
            "今因移就三面言定親手領訖任從買主陰陽兩便"
            "百為無阻恐口無憑立此為據永遠為業"
        )

        filtered, _, reasons = _filter_hallucinations(fused, fused, v4, 0.2)

        assert filtered == v4
        assert any("template_density" in reason for reason in reasons)

    def test_known_3_142_like_hallucinated_span_is_not_kept(self):
        from app.services.ocr_service import _fuse_v3_v4

        v3 = "立永賣白田約人楊大選黃的楊建全因移就信福國富王家屬戴正教己辦"
        v4 = "立永賣白田約人楊大選"

        fused, _ = _fuse_v3_v4(v3, v4)

        assert fused == v4
        assert "黃的楊建全" not in fused

    def test_low_model_agreement_masks_uncertain_text(self):
        from app.services.ocr_service import _filter_hallucinations

        v3 = "立永賣白田約人楊大選"
        v4 = "永黃白田約人楊大連横山原詩"

        filtered, confidence, reasons = _filter_hallucinations(v4, v3, v4, 0.3)

        assert confidence == 0.3
        assert "□" in filtered
        assert filtered != v4
        assert any("low_model_agreement" in reason for reason in reasons)
