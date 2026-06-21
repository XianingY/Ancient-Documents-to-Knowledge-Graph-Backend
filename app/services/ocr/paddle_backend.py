import os
import threading
from typing import Any

from app.core.config import settings

from .consensus import build_consensus_result
from .preprocess import prepare_document_image
from .types import OcrPipelineResult


class OcrBackendUnavailable(RuntimeError):
    pass


_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_medium_pipeline = None
_small_recognizer = None
_cropper = None


def _load_models():
    global _medium_pipeline, _small_recognizer, _cropper
    if all(
        model is not None
        for model in (_medium_pipeline, _small_recognizer, _cropper)
    ):
        return _medium_pipeline, _small_recognizer, _cropper

    with _model_lock:
        if all(
            model is not None
            for model in (_medium_pipeline, _small_recognizer, _cropper)
        ):
            return _medium_pipeline, _small_recognizer, _cropper
        try:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import PaddleOCR, TextRecognition
            from paddlex.inference.pipelines.components.common import CropByPolys
        except ImportError as exc:
            raise OcrBackendUnavailable(
                f"PaddleOCR runtime import failed: {exc}"
            ) from exc

        medium_pipeline = PaddleOCR(
            lang="ch",
            ocr_version="PP-OCRv6",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            text_det_limit_side_len=settings.OCR_TARGET_LONG_SIDE,
            text_det_limit_type="max",
            text_det_thresh=settings.OCR_DETECTION_THRESHOLD,
            text_det_box_thresh=settings.OCR_BOX_THRESHOLD,
            text_det_unclip_ratio=settings.OCR_UNCLIP_RATIO,
            text_rec_score_thresh=0.0,
            return_word_box=True,
        )
        small_recognizer = TextRecognition(model_name="PP-OCRv6_small_rec")
        cropper = CropByPolys()
        _medium_pipeline = medium_pipeline
        _small_recognizer = small_recognizer
        _cropper = cropper
        return _medium_pipeline, _small_recognizer, _cropper


def _result_payload(result: Any) -> dict[str, Any]:
    value = result.json if hasattr(result, "json") else result
    return value.get("res", value)


def _medium_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    texts = payload.get("rec_texts", [])
    scores = payload.get("rec_scores", [])
    boxes = payload.get("rec_boxes", [])
    return [
        {"text": text, "score": score, "bbox": box}
        for text, score, box in zip(texts, scores, boxes)
    ]


def _small_rows(results: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        payload = _result_payload(result)
        rows.append({
            "text": payload.get("rec_text", ""),
            "score": payload.get("rec_score", 0.0),
        })
    return rows


def run_paddle_consensus(image_path: str) -> OcrPipelineResult:
    medium_pipeline, small_recognizer, cropper = _load_models()
    with prepare_document_image(
        image_path,
        target_long_side=settings.OCR_TARGET_LONG_SIDE,
    ) as prepared:
        try:
            import cv2
        except ImportError as exc:
            raise OcrBackendUnavailable("OpenCV runtime is not installed") from exc

        with _inference_lock:
            medium_results = list(medium_pipeline.predict(prepared.path))
            if not medium_results:
                return OcrPipelineResult(
                    text="",
                    confidence=0.0,
                    coverage=0.0,
                    engine="paddle_v6_consensus",
                    model_versions="PP-OCRv6_medium_det+medium_rec+small_rec",
                    rejection_reasons=["no_text_detected"],
                    crop_bbox=prepared.crop_bbox,
                )

            payload = _result_payload(medium_results[0])
            polygons = payload.get("rec_polys", [])
            image = cv2.imread(prepared.path)
            if image is None:
                raise RuntimeError("Failed to read prepared OCR image")
            crops = cropper(image, polygons) if polygons else []
            small_results = list(
                small_recognizer.predict(
                    crops,
                    batch_size=settings.OCR_RECOGNITION_BATCH_SIZE,
                )
            ) if crops else []

        return build_consensus_result(
            _medium_rows(payload),
            _small_rows(small_results),
            prepared.prepared_size,
            prepared.crop_bbox,
        )
