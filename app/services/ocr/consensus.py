import difflib
import re
from typing import Any

from app.core.config import settings

from .types import OcrPipelineResult


_LATIN_RE = re.compile(r"[A-Za-z]")
_EDGE_NUMBER_RE = re.compile(r"[\d.\s]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PLACEHOLDER_RE = re.compile(r"□+")
_ROLE_LINE_RE = re.compile(r"(中人|代書|代书|筆|笔)")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _cjk_ratio(text: str) -> float:
    clean = _clean_text(text)
    if not clean:
        return 0.0
    accepted = sum(bool(_CJK_RE.fullmatch(ch)) or ch.isdigit() for ch in clean)
    return accepted / len(clean)


def _is_usable_text(text: str, box: list[int], image_size: tuple[int, int]) -> bool:
    clean = _clean_text(text)
    if not clean or _LATIN_RE.search(clean):
        return False
    width, _ = image_size
    if _EDGE_NUMBER_RE.fullmatch(clean) and len(clean) <= 5:
        if box[0] < width * 0.08 or box[2] > width * 0.92:
            return False
    return _cjk_ratio(clean) >= 0.5


def _box_iou(a: list[int], b: list[int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if not intersection:
        return 0.0
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / (area_a + area_b - intersection)


def _deduplicate(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for segment in sorted(segments, key=lambda item: item["medium_score"], reverse=True):
        if any(_box_iou(segment["bbox"], existing["bbox"]) >= 0.85 for existing in kept):
            continue
        kept.append(segment)
    return kept


def agreement_text(primary: str, verifier: str) -> tuple[str, float]:
    """Keep exact aligned runs and replace each disagreement run with one placeholder."""
    primary = _clean_text(primary)
    verifier = _clean_text(verifier)
    if not primary or not verifier:
        return "", 0.0
    if primary == verifier:
        return primary, 1.0

    matcher = difflib.SequenceMatcher(None, primary, verifier, autojunk=False)
    parts: list[str] = []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        parts.append(primary[i1:i2] if tag == "equal" else "□")
    text = _PLACEHOLDER_RE.sub("□", "".join(parts))
    return text, matcher.ratio()


def _char_rescue(medium: dict[str, Any]) -> tuple[str, float, float]:
    text = _clean_text(str(medium.get("char_rescue_text", "")))
    if not text:
        return "", 0.0, 0.0
    visible_count = len(text.replace("□", ""))
    if not visible_count:
        return "", 0.0, 0.0
    confidence = float(medium.get("char_rescue_confidence", 0.0) or 0.0)
    visible_ratio = float(medium.get("char_rescue_visible_ratio", 0.0) or 0.0)
    return text, confidence, visible_ratio


def _has_visible_chars(text: str) -> bool:
    return bool(text and text.replace("□", ""))


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    position = 0
    for char in haystack:
        if char == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def _agreement_with_medium(medium_text: str, rescue_text: str) -> str:
    if len(medium_text) == len(rescue_text):
        return "".join(
            medium_char if medium_char == rescue_char else "□"
            for medium_char, rescue_char in zip(medium_text, rescue_text)
        )
    text, _ = agreement_text(medium_text, rescue_text)
    return text


def _safe_char_rescue_text(
    medium_text: str,
    small_text: str,
    rescue_text: str,
    rescue_confidence: float,
    reasons: list[str],
) -> str:
    if (
        not rescue_text
        or rescue_confidence < settings.OCR_CHAR_RESCUE_MIN_LINE_CONFIDENCE
    ):
        return ""

    reason_text = " ".join(reasons)
    if (
        "uncertain:low_model_score" in reason_text
        or "uncertain:missing_verifier" in reason_text
    ):
        text = _agreement_with_medium(medium_text, rescue_text)
        if len(text) < 2 or len(text) > 4 or "□" in text:
            return ""
        return text

    if "uncertain:short_or_role_line_disagreement" in reason_text:
        max_len = max(len(medium_text), len(small_text), len(rescue_text))
        if max_len > 4:
            return ""
        if small_text and len(medium_text) == len(small_text):
            text, similarity = agreement_text(medium_text, small_text)
            return text if similarity >= 0.3 else ""

        if (
            not small_text
            or _is_subsequence(small_text, medium_text)
            or _is_subsequence(small_text, rescue_text)
        ):
            return rescue_text

        return ""

    return rescue_text


def build_consensus_result(
    medium_rows: list[dict[str, Any]],
    small_rows: list[dict[str, Any]],
    image_size: tuple[int, int],
    crop_bbox: list[int],
) -> OcrPipelineResult:
    segments: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    for index, medium in enumerate(medium_rows):
        small = small_rows[index] if index < len(small_rows) else {"text": "", "score": 0.0}
        medium_text = _clean_text(str(medium.get("text", "")))
        small_text = _clean_text(str(small.get("text", "")))
        medium_score = float(medium.get("score", 0.0) or 0.0)
        small_score = float(small.get("score", 0.0) or 0.0)
        bbox = [int(value) for value in medium["bbox"]]
        reasons: list[str] = []
        status = "accepted"
        similarity = 0.0
        text = ""
        segment_confidence = 0.0
        char_rescue_text, char_rescue_confidence, char_rescue_ratio = (
            _char_rescue(medium)
        )

        if not _is_usable_text(medium_text, bbox, image_size):
            reasons.append("rejected:non_document_text")
            status = "rejected"
        elif (
            small_text
            and _is_usable_text(small_text, bbox, image_size)
            and min(medium_score, small_score) >= settings.OCR_CONSENSUS_MIN_SCORE
        ):
            text, similarity = agreement_text(medium_text, small_text)
            high_risk_short_line = (
                medium_text != small_text
                and (
                    max(len(medium_text), len(small_text)) <= 6
                    or (
                        max(len(medium_text), len(small_text)) <= 12
                        and _ROLE_LINE_RE.search(medium_text + small_text)
                    )
                )
            )
            if high_risk_short_line:
                text = "□"
                status = "uncertain"
                reasons.append("uncertain:short_or_role_line_disagreement")
            elif similarity < settings.OCR_CONSENSUS_MIN_SIMILARITY or not text:
                text = "□"
                status = "uncertain"
                reasons.append(f"uncertain:low_model_similarity:{similarity:.2f}")
            elif "□" in text:
                status = "partial"
                segment_confidence = min(medium_score, small_score) * similarity
                reasons.append("masked:model_disagreement")
            else:
                segment_confidence = min(medium_score, small_score)
        else:
            text = "□"
            status = "uncertain"
            if small_text:
                reasons.append(
                    "uncertain:low_model_score:"
                    f"{min(medium_score, small_score):.2f}"
                )
            else:
                reasons.append("uncertain:missing_verifier")

        safe_rescue_text = _safe_char_rescue_text(
            medium_text,
            small_text,
            char_rescue_text,
            char_rescue_confidence,
            reasons,
        )
        if status == "uncertain" and _has_visible_chars(safe_rescue_text):
            text = safe_rescue_text
            status = "partial" if "□" in text else "accepted"
            segment_confidence = char_rescue_confidence * max(char_rescue_ratio, 0.5)
            reasons.append("rescued:char_consensus")
            if "□" in text:
                reasons.append("masked:char_rescue_disagreement")

        segment = {
            "bbox": bbox,
            "text": text,
            "status": status,
            "medium_text": medium_text,
            "medium_score": round(medium_score, 4),
            "small_text": small_text,
            "small_score": round(small_score, 4),
            "similarity": round(similarity, 4),
            "confidence": round(segment_confidence, 4),
            "char_rescue_text": char_rescue_text,
            "safe_char_rescue_text": safe_rescue_text,
            "char_rescue_confidence": round(char_rescue_confidence, 4),
            "char_rescue_visible_ratio": round(char_rescue_ratio, 4),
            "rejection_reasons": reasons,
        }
        segments.append(segment)
        rejection_reasons.extend(reason for reason in reasons if not reason.startswith("accepted:"))

    segments = _deduplicate(segments)
    segments.sort(key=lambda item: (-item["bbox"][0], item["bbox"][1]))
    output_lines = [segment["text"] for segment in segments if segment["text"]]
    text = "\n".join(output_lines)

    medium_chars = sum(len(segment["medium_text"]) for segment in segments)
    visible_chars = sum(len(segment["text"].replace("□", "")) for segment in segments)
    coverage = visible_chars / max(medium_chars, 1)
    confidence = sum(
        len(segment["medium_text"]) * segment["confidence"]
        for segment in segments
    ) / max(medium_chars, 1)

    return OcrPipelineResult(
        text=text,
        confidence=round(min(confidence, 1.0), 4),
        coverage=round(min(coverage, 1.0), 4),
        engine="paddle_v6_consensus",
        model_versions="PP-OCRv6_medium_det+medium_rec+small_rec",
        segments=segments,
        rejection_reasons=sorted(set(rejection_reasons)),
        crop_bbox=crop_bbox,
    )
