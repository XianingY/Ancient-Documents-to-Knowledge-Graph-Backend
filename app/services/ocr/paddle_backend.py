import os
import re
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
_tiny_recognizer = None
_tiny_recognizer_attempted = False
_cropper = None
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _load_models():
    global _medium_pipeline, _small_recognizer, _tiny_recognizer
    global _tiny_recognizer_attempted, _cropper
    if all(
        model is not None
        for model in (_medium_pipeline, _small_recognizer, _cropper)
    ):
        return _medium_pipeline, _small_recognizer, _tiny_recognizer, _cropper

    with _model_lock:
        if all(
            model is not None
            for model in (_medium_pipeline, _small_recognizer, _cropper)
        ):
            return _medium_pipeline, _small_recognizer, _tiny_recognizer, _cropper
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
        tiny_recognizer = None
        if settings.OCR_CHAR_RESCUE_USE_TINY and not _tiny_recognizer_attempted:
            try:
                tiny_recognizer = TextRecognition(model_name="PP-OCRv6_tiny_rec")
            except Exception:
                tiny_recognizer = None
            finally:
                _tiny_recognizer_attempted = True
        cropper = CropByPolys()
        _medium_pipeline = medium_pipeline
        _small_recognizer = small_recognizer
        _tiny_recognizer = tiny_recognizer
        _cropper = cropper
        return _medium_pipeline, _small_recognizer, _tiny_recognizer, _cropper


def _result_payload(result: Any) -> dict[str, Any]:
    value = result.json if hasattr(result, "json") else result
    return value.get("res", value)


def _plain(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _safe_index(values: Any, index: int, default: Any) -> Any:
    if values is None:
        return default
    try:
        return values[index]
    except (IndexError, TypeError, KeyError):
        return default


def _medium_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    texts = payload.get("rec_texts", [])
    scores = payload.get("rec_scores", [])
    boxes = payload.get("rec_boxes", [])
    polygons = payload.get("rec_polys", [])
    word_texts = payload.get("text_word", [])
    word_boxes = payload.get("text_word_boxes", [])
    return [
        {
            "text": text,
            "score": score,
            "bbox": _plain(box),
            "poly": _plain(_safe_index(polygons, index, [])),
            "word_text": _plain(_safe_index(word_texts, index, [])),
            "word_boxes": _plain(_safe_index(word_boxes, index, [])),
        }
        for index, (text, score, box) in enumerate(zip(texts, scores, boxes))
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


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _needs_char_rescue(
    medium: dict[str, Any],
    small: dict[str, Any] | None,
) -> bool:
    medium_text = _clean_text(str(medium.get("text", "")))
    small_text = _clean_text(str((small or {}).get("text", "")))
    if not medium_text:
        return False

    medium_score = float(medium.get("score", 0.0) or 0.0)
    small_score = float((small or {}).get("score", 0.0) or 0.0)
    max_len = max(len(medium_text), len(small_text))
    if (
        small_text
        and medium_text != small_text
        and max_len <= 4
        and min(medium_score, small_score) >= settings.OCR_CONSENSUS_MIN_SCORE
    ):
        return True

    if len(medium_text) < 2 or len(medium_text) > 4:
        return False
    if not small_text:
        return True
    return min(medium_score, small_score) < settings.OCR_CONSENSUS_MIN_SCORE


def _flatten_numbers(value: Any) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if hasattr(value, "tolist"):
        return _flatten_numbers(value.tolist())
    if isinstance(value, (list, tuple)):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item))
        return flattened
    return []


def _text_chars(value: Any, fallback_text: str, expected_len: int) -> list[str]:
    chars: list[str] = []
    if isinstance(value, str):
        chars = [char for char in value if not char.isspace()]
    elif isinstance(value, (list, tuple)):
        for item in value:
            chars.extend(_text_chars(item, "", 0))
    if len(chars) != expected_len:
        chars = [char for char in fallback_text if not char.isspace()]
    if len(chars) != expected_len:
        chars = [""] * expected_len
    return chars[:expected_len]


def _crop_word_image(image: Any, word_box: Any) -> Any | None:
    numbers = _flatten_numbers(word_box)
    if len(numbers) < 4:
        return None
    if len(numbers) == 4:
        xs = [numbers[0], numbers[2]]
        ys = [numbers[1], numbers[3]]
    else:
        xs = numbers[0::2]
        ys = numbers[1::2]

    height, width = image.shape[:2]
    x1 = max(0, int(min(xs)))
    x2 = min(width, int(max(xs)))
    y1 = max(0, int(min(ys)))
    y2 = min(height, int(max(ys)))
    if x2 <= x1 or y2 <= y1:
        return None

    pad = max(2, int(max(x2 - x1, y2 - y1) * 0.08))
    x1 = max(0, x1 - pad)
    x2 = min(width, x2 + pad)
    y1 = max(0, y1 - pad)
    y2 = min(height, y2 + pad)
    crop = image[y1:y2, x1:x2]
    return crop if crop.size else None


def _single_visible_char(text: str) -> str:
    clean = "".join(char for char in (text or "") if not char.isspace())
    if len(clean) != 1:
        return ""
    return clean if _CJK_CHAR_RE.fullmatch(clean) or clean.isdigit() else ""


def _char_recognition_rows(results: list[Any]) -> list[tuple[str, float]]:
    rows = []
    for result in results:
        payload = _result_payload(result)
        text = _single_visible_char(payload.get("rec_text", ""))
        score = float(payload.get("rec_score", 0.0) or 0.0)
        rows.append((text, score))
    return rows


def _choose_char(slot: dict[str, Any]) -> tuple[str, float]:
    votes: dict[str, list[float]] = {}
    medium_char = _single_visible_char(slot.get("medium_char", ""))
    if medium_char:
        votes.setdefault(medium_char, []).append(float(slot.get("medium_score", 0.0)))
    for source in ("small", "tiny"):
        char = slot.get(f"{source}_char", "")
        score = float(slot.get(f"{source}_score", 0.0) or 0.0)
        if char and score >= settings.OCR_CHAR_RESCUE_MIN_SCORE:
            votes.setdefault(char, []).append(score)

    candidates = [
        (char, scores)
        for char, scores in votes.items()
        if len(scores) >= 2
    ]
    if not candidates:
        return "□", 0.0
    char, scores = max(candidates, key=lambda item: (len(item[1]), sum(item[1])))
    return char, min(scores)


def _attach_char_rescue(
    image: Any,
    medium_rows: list[dict[str, Any]],
    small_rows: list[dict[str, Any]],
    small_recognizer: Any,
    tiny_recognizer: Any | None,
) -> None:
    if not settings.OCR_CHAR_RESCUE_ENABLED:
        return

    jobs: list[dict[str, int]] = []
    crops: list[Any] = []
    rescue_slots: dict[int, list[dict[str, Any]]] = {}
    for row_index, row in enumerate(medium_rows):
        small = small_rows[row_index] if row_index < len(small_rows) else None
        if not _needs_char_rescue(row, small):
            continue
        word_boxes = row.get("word_boxes") or []
        if (
            not word_boxes
            or len(word_boxes) > settings.OCR_CHAR_RESCUE_MAX_LINE_CHARS
        ):
            continue

        chars = _text_chars(row.get("word_text"), str(row.get("text", "")), len(word_boxes))
        slots = [
            {
                "medium_char": chars[char_index],
                "medium_score": float(row.get("score", 0.0) or 0.0),
            }
            for char_index in range(len(word_boxes))
        ]
        rescue_slots[row_index] = slots
        for char_index, word_box in enumerate(word_boxes):
            crop = _crop_word_image(image, word_box)
            if crop is None:
                continue
            jobs.append({"row_index": row_index, "char_index": char_index})
            crops.append(crop)

    if not jobs:
        return

    small_rows = _char_recognition_rows(list(
        small_recognizer.predict(
            crops,
            batch_size=settings.OCR_RECOGNITION_BATCH_SIZE,
        )
    ))
    tiny_rows: list[tuple[str, float]] = []
    if tiny_recognizer is not None:
        tiny_rows = _char_recognition_rows(list(
            tiny_recognizer.predict(
                crops,
                batch_size=settings.OCR_RECOGNITION_BATCH_SIZE,
            )
        ))

    for job_index, job in enumerate(jobs):
        slot = rescue_slots[job["row_index"]][job["char_index"]]
        if job_index < len(small_rows):
            slot["small_char"], slot["small_score"] = small_rows[job_index]
        if job_index < len(tiny_rows):
            slot["tiny_char"], slot["tiny_score"] = tiny_rows[job_index]

    for row_index, slots in rescue_slots.items():
        chosen = [_choose_char(slot) for slot in slots]
        text = "".join(char for char, _ in chosen)
        visible_count = sum(char != "□" for char, _ in chosen)
        if not visible_count:
            continue
        visible_ratio = visible_count / max(len(chosen), 1)
        if visible_ratio < 0.5 and len(chosen) > 2:
            continue
        scores = [score for char, score in chosen if char != "□"]
        medium_rows[row_index]["char_rescue_text"] = text
        medium_rows[row_index]["char_rescue_confidence"] = (
            sum(scores) / max(len(scores), 1)
        )
        medium_rows[row_index]["char_rescue_visible_ratio"] = visible_ratio


def run_paddle_consensus(image_path: str) -> OcrPipelineResult:
    medium_pipeline, small_recognizer, tiny_recognizer, cropper = _load_models()
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
            medium_rows = _medium_rows(payload)
            small_results = list(
                small_recognizer.predict(
                    crops,
                    batch_size=settings.OCR_RECOGNITION_BATCH_SIZE,
                )
            ) if crops else []
            small_line_rows = _small_rows(small_results)
            _attach_char_rescue(
                image,
                medium_rows,
                small_line_rows,
                small_recognizer,
                tiny_recognizer,
            )

        result = build_consensus_result(
            medium_rows,
            small_line_rows,
            prepared.prepared_size,
            prepared.crop_bbox,
        )
        if tiny_recognizer is not None and settings.OCR_CHAR_RESCUE_ENABLED:
            result.model_versions = (
                "PP-OCRv6_medium_det+medium_rec+small_rec+tiny_rec_char_rescue"
            )
        elif settings.OCR_CHAR_RESCUE_ENABLED:
            result.model_versions = (
                "PP-OCRv6_medium_det+medium_rec+small_rec+char_rescue"
            )
        return result
