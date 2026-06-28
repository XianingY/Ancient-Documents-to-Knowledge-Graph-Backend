import asyncio
from types import SimpleNamespace


class _Query:
    def __init__(self, result):
        self.result = result

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class _Db:
    def __init__(self, result):
        self.result = result
        self.committed = False
        self.refreshed = None

    def query(self, *args, **kwargs):
        return _Query(self.result)

    def commit(self):
        self.committed = True

    def refresh(self, item):
        self.refreshed = item


def test_update_ocr_result_preserves_model_quality_scores():
    from app.routers.ocr import UpdateOcrResultRequest, update_ocr_result

    ocr_result = SimpleNamespace(
        id=7,
        image_id=3,
        raw_text="原始 OCR",
        original_raw_text=None,
        status=SimpleNamespace(value="done"),
        confidence=0.42,
        coverage=0.58,
        engine="paddle_v6_consensus",
        model_versions=None,
        segments_json=None,
        corrected_segments_json=None,
        correction_metadata_json=None,
        rejection_reasons=None,
        crop_bbox_json=None,
        image_size_json=None,
        human_corrected=False,
        created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
    )
    db = _Db(ocr_result)

    response = asyncio.run(
        update_ocr_result(
            7,
            UpdateOcrResultRequest(raw_text="人工修订文本"),
            user_id=1,
            db=db,
        )
    )

    assert db.committed is True
    assert db.refreshed is ocr_result
    assert ocr_result.raw_text == "人工修订文本"
    assert ocr_result.original_raw_text == "原始 OCR"
    assert ocr_result.human_corrected is True
    assert ocr_result.confidence == 0.42
    assert ocr_result.coverage == 0.58
    assert response["data"]["confidence"] == 0.42
    assert response["data"]["coverage"] == 0.58
