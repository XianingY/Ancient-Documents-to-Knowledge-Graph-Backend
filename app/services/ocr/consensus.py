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


def _box_overlap_ratio(a: list[int], b: list[int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if not intersection:
        return 0.0
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / min(area_a, area_b)


def _box_center(box: list[int]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _median(values: list[float], default: float = 0.0) -> float:
    if not values:
        return default
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _detect_layout_orientation(
    segments: list[dict[str, Any]],
    image_size: tuple[int, int],
) -> str:
    configured = getattr(settings, "OCR_LAYOUT_ORIENTATION", "auto")
    if configured in {"vertical", "horizontal"}:
        return configured

    boxes = [segment["bbox"] for segment in segments if segment.get("bbox")]
    ratios = []
    for box in boxes:
        width = max(1, box[2] - box[0])
        height = max(1, box[3] - box[1])
        ratios.append(height / width)
    if not ratios:
        return "vertical"
    return "vertical" if _median(ratios, 1.0) >= 1.15 else "horizontal"


def _group_by_axis(
    segments: list[dict[str, Any]],
    axis: str,
    reverse_groups: bool,
    tolerance: float,
) -> list[list[dict[str, Any]]]:
    center_index = 0 if axis == "x" else 1
    groups: list[dict[str, Any]] = []
    sorted_segments = sorted(
        segments,
        key=lambda segment: _box_center(segment["bbox"])[center_index],
        reverse=reverse_groups,
    )
    for segment in sorted_segments:
        center = _box_center(segment["bbox"])[center_index]
        matched = None
        for group in groups:
            if abs(center - group["center"]) <= tolerance:
                matched = group
                break
        if matched is None:
            groups.append({"center": center, "segments": [segment]})
            continue
        matched["segments"].append(segment)
        matched["center"] = sum(
            _box_center(item["bbox"])[center_index] for item in matched["segments"]
        ) / len(matched["segments"])

    groups.sort(key=lambda group: group["center"], reverse=reverse_groups)
    return [group["segments"] for group in groups]


def _sort_segments_by_layout(
    segments: list[dict[str, Any]],
    image_size: tuple[int, int],
) -> tuple[list[dict[str, Any]], str]:
    if not segments:
        return [], _detect_layout_orientation([], image_size)

    orientation = _detect_layout_orientation(segments, image_size)
    widths = [max(1, segment["bbox"][2] - segment["bbox"][0]) for segment in segments]
    heights = [max(1, segment["bbox"][3] - segment["bbox"][1]) for segment in segments]
    image_width, image_height = image_size

    if orientation == "vertical":
        tolerance = max(18.0, image_width * 0.025, _median(widths, 18.0) * 1.5)
        columns = _group_by_axis(segments, "x", True, tolerance)
        ordered = []
        for column in columns:
            ordered.extend(sorted(column, key=lambda segment: segment["bbox"][1]))
        return ordered, orientation

    tolerance = max(18.0, image_height * 0.025, _median(heights, 18.0) * 1.5)
    rows = _group_by_axis(segments, "y", False, tolerance)
    ordered = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda segment: segment["bbox"][0]))
    return ordered, orientation


def _map_point_to_original(
    x: float,
    y: float,
    crop_bbox: list[int],
    prepared_size: tuple[int, int],
) -> list[int]:
    crop_x1, crop_y1, crop_x2, crop_y2 = crop_bbox
    scale_x = (crop_x2 - crop_x1) / max(prepared_size[0], 1)
    scale_y = (crop_y2 - crop_y1) / max(prepared_size[1], 1)
    return [int(round(crop_x1 + x * scale_x)), int(round(crop_y1 + y * scale_y))]


def _map_bbox_to_original(
    bbox: list[int],
    crop_bbox: list[int],
    prepared_size: tuple[int, int],
) -> list[int]:
    left_top = _map_point_to_original(bbox[0], bbox[1], crop_bbox, prepared_size)
    right_bottom = _map_point_to_original(bbox[2], bbox[3], crop_bbox, prepared_size)
    return [left_top[0], left_top[1], right_bottom[0], right_bottom[1]]


def _map_poly_to_original(
    poly: Any,
    crop_bbox: list[int],
    prepared_size: tuple[int, int],
) -> list[list[int]]:
    if not isinstance(poly, list):
        return []
    if poly and all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in poly):
        return [
            _map_point_to_original(float(point[0]), float(point[1]), crop_bbox, prepared_size)
            for point in poly
        ]
    if len(poly) >= 4 and all(isinstance(point, (int, float)) for point in poly):
        return [
            _map_point_to_original(float(poly[index]), float(poly[index + 1]), crop_bbox, prepared_size)
            for index in range(0, len(poly) - 1, 2)
        ]
    return []


def _finalize_segments(
    segments: list[dict[str, Any]],
    image_size: tuple[int, int],
    crop_bbox: list[int],
    original_size: tuple[int, int] | None,
) -> list[dict[str, Any]]:
    ordered, orientation = _sort_segments_by_layout(segments, image_size)
    for index, segment in enumerate(ordered):
        segment["segment_id"] = f"s{index:04d}"
        segment["order_index"] = index
        segment["layout_orientation"] = orientation
        if original_size:
            segment["image_bbox"] = _map_bbox_to_original(
                segment["bbox"],
                crop_bbox,
                image_size,
            )
            segment["image_poly"] = _map_poly_to_original(
                segment.get("poly", []),
                crop_bbox,
                image_size,
            )
        else:
            segment["image_bbox"] = segment["bbox"]
            segment["image_poly"] = segment.get("poly", [])
    return ordered


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
    original_size: tuple[int, int] | None = None,
    view_name: str = "original",
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
            "view": view_name,
            "source_views": [view_name],
            "source_count": 1,
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
    segments = _finalize_segments(segments, image_size, crop_bbox, original_size)
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
        image_size=list(original_size) if original_size else None,
    )


def _cluster_segments(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    threshold = float(getattr(settings, "OCR_LAYOUT_CLUSTER_IOU", 0.45))
    for segment in sorted(
        segments,
        key=lambda item: float(item.get("confidence", item.get("medium_score", 0.0)) or 0.0),
        reverse=True,
    ):
        bbox = segment.get("bbox")
        if not bbox:
            continue
        matched = None
        for cluster in clusters:
            if any(
                _box_iou(bbox, existing["bbox"]) >= threshold
                or _box_overlap_ratio(bbox, existing["bbox"]) >= max(0.72, threshold)
                for existing in cluster
            ):
                matched = cluster
                break
        if matched is None:
            clusters.append([segment])
        else:
            matched.append(segment)
    return clusters


def _union_bbox(segments: list[dict[str, Any]]) -> list[int]:
    boxes = [segment["bbox"] for segment in segments]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _best_segment(segments: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        segments,
        key=lambda segment: (
            float(segment.get("confidence", 0.0) or 0.0),
            float(segment.get("medium_score", 0.0) or 0.0),
            len(_clean_text(str(segment.get("text", ""))).replace("□", "")),
        ),
    )


def _choose_cluster_text(cluster: list[dict[str, Any]]) -> tuple[str, str, float, list[str]]:
    reasons: list[str] = []
    candidates = [
        segment
        for segment in cluster
        if segment.get("status") != "rejected"
        and _clean_text(str(segment.get("text", ""))).replace("□", "")
    ]
    if not candidates:
        reasons.append("uncertain:multiview_no_verified_text")
        return "□", "uncertain", 0.0, reasons

    by_text: dict[str, list[dict[str, Any]]] = {}
    for segment in candidates:
        by_text.setdefault(_clean_text(str(segment.get("text", ""))), []).append(segment)
    agreed_text, agreed_segments = max(
        by_text.items(),
        key=lambda item: (
            len({segment.get("view") for segment in item[1]}),
            len(item[1]),
            sum(float(segment.get("confidence", 0.0) or 0.0) for segment in item[1]),
        ),
    )
    agreed_view_count = len({segment.get("view") for segment in agreed_segments})
    if agreed_view_count >= 2:
        confidence = min(float(segment.get("confidence", 0.0) or 0.0) for segment in agreed_segments)
        return agreed_text, "accepted", confidence, reasons

    if len(candidates) >= 2:
        ranked = sorted(
            candidates,
            key=lambda segment: float(segment.get("confidence", 0.0) or 0.0),
            reverse=True,
        )
        text, similarity = agreement_text(
            str(ranked[0].get("text", "")),
            str(ranked[1].get("text", "")),
        )
        if text and text.replace("□", "") and similarity >= float(getattr(settings, "OCR_CONSENSUS_MIN_SIMILARITY", 0.4)):
            reasons.append("masked:multiview_disagreement")
            confidence = min(
                float(ranked[0].get("confidence", 0.0) or 0.0),
                float(ranked[1].get("confidence", 0.0) or 0.0),
            ) * similarity
            return text, "partial" if "□" in text else "accepted", confidence, reasons

    best = _best_segment(candidates)
    best_text = _clean_text(str(best.get("text", "")))
    best_confidence = float(best.get("confidence", 0.0) or 0.0)
    max_chars = int(getattr(settings, "OCR_LAYOUT_SINGLE_VIEW_MAX_CHARS", 6))
    min_confidence = float(getattr(settings, "OCR_LAYOUT_SINGLE_VIEW_MIN_CONFIDENCE", 0.75))
    if len(best_text) <= max_chars and best_confidence >= min_confidence:
        reasons.append("accepted:single_view_short_high_confidence")
        return best_text, "accepted", best_confidence, reasons

    reasons.append("uncertain:single_view_long_or_low_confidence")
    return "□", "uncertain", 0.0, reasons


def build_multiview_consensus_result(
    view_results: list[OcrPipelineResult],
    image_size: tuple[int, int],
    crop_bbox: list[int],
    original_size: tuple[int, int],
) -> OcrPipelineResult:
    all_segments = [
        segment
        for result in view_results
        for segment in result.segments
        if segment.get("status") != "rejected" or segment.get("text")
    ]
    if not all_segments:
        return OcrPipelineResult(
            text="",
            confidence=0.0,
            coverage=0.0,
            engine="paddle_v6_layout_multiview",
            model_versions="PP-OCRv6_medium_det+medium_rec+small_rec+layout_multiview",
            rejection_reasons=sorted({
                reason for result in view_results for reason in result.rejection_reasons
            } or {"no_text_detected"}),
            crop_bbox=crop_bbox,
            image_size=list(original_size),
        )

    final_segments: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    for cluster_index, cluster in enumerate(_cluster_segments(all_segments)):
        best = _best_segment(cluster)
        text, status, confidence, reasons = _choose_cluster_text(cluster)
        source_views = sorted({
            str(segment.get("view", "unknown")) for segment in cluster
        })
        candidate_texts = [
            {
                "view": segment.get("view"),
                "text": segment.get("text"),
                "medium_text": segment.get("medium_text"),
                "small_text": segment.get("small_text"),
                "confidence": segment.get("confidence"),
                "status": segment.get("status"),
            }
            for segment in sorted(cluster, key=lambda item: str(item.get("view", "")))
        ]
        merged_reasons = sorted({
            reason
            for segment in cluster
            for reason in segment.get("rejection_reasons", [])
        } | set(reasons))
        final_segments.append({
            "segment_id": f"s{cluster_index:04d}",
            "bbox": _union_bbox(cluster),
            "poly": best.get("poly", []),
            "text": text,
            "status": status,
            "confidence": round(confidence, 4),
            "medium_text": best.get("medium_text", ""),
            "medium_score": best.get("medium_score", 0.0),
            "small_text": best.get("small_text", ""),
            "small_score": best.get("small_score", 0.0),
            "similarity": best.get("similarity", 0.0),
            "view": "multiview",
            "source_views": source_views,
            "source_count": len(source_views),
            "candidate_texts": candidate_texts,
            "rejection_reasons": merged_reasons,
        })
        rejection_reasons.extend(reason for reason in merged_reasons if not reason.startswith("accepted:"))

    final_segments = _finalize_segments(final_segments, image_size, crop_bbox, original_size)
    output_lines = [segment["text"] for segment in final_segments if segment["text"]]
    text = "\n".join(output_lines)

    medium_chars = sum(
        max(len(str(segment.get("medium_text", ""))), len(str(segment.get("text", ""))))
        for segment in final_segments
    )
    visible_chars = sum(len(str(segment.get("text", "")).replace("□", "")) for segment in final_segments)
    coverage = visible_chars / max(medium_chars, 1)
    confidence = sum(
        max(len(str(segment.get("medium_text", ""))), len(str(segment.get("text", ""))))
        * float(segment.get("confidence", 0.0) or 0.0)
        for segment in final_segments
    ) / max(medium_chars, 1)

    model_versions = sorted({
        result.model_versions for result in view_results if result.model_versions
    })
    return OcrPipelineResult(
        text=text,
        confidence=round(min(confidence, 1.0), 4),
        coverage=round(min(coverage, 1.0), 4),
        engine="paddle_v6_layout_multiview",
        model_versions=f"{model_versions[0]}+layout_multiview" if model_versions else "layout_multiview",
        segments=final_segments,
        rejection_reasons=sorted(set(rejection_reasons)),
        crop_bbox=crop_bbox,
        image_size=list(original_size),
    )
