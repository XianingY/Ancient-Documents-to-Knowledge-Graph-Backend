import unicodedata
from typing import Any


_PLACEHOLDERS = {"□", "■", "囗"}
_METRIC_MODES = ("raw", "faithful", "content")

_CONTENT_VARIANT_MAP = str.maketrans({
    "为": "為",
    "业": "業",
    "与": "與",
    "写": "寫",
    "卖": "賣",
    "买": "買",
    "乡": "鄉",
    "产": "產",
    "亲": "親",
    "众": "衆",
    "体": "體",
    "银": "銀",
    "钱": "錢",
    "时": "時",
    "议": "議",
    "价": "價",
    "约": "約",
    "契": "契",
    "证": "證",
    "转": "轉",
    "让": "讓",
    "孙": "孫",
    "号": "號",
    "礼": "禮",
    "铺": "鋪",
    "头": "頭",
    "万": "萬",
    "庄": "莊",
    "连": "連",
    "兑": "兌",
    "归": "歸",
    "将": "將",
    "经": "經",
    "应": "應",
    "实": "實",
    "现": "現",
    "这": "這",
    "后": "後",
    "无": "無",
    "义": "義",
    "长": "長",
    "发": "發",
    "兴": "興",
    "开": "開",
    "达": "達",
    "运": "運",
    "选": "選",
    "荣": "榮",
})


def normalize_visible_text(text: str) -> str:
    """Remove layout whitespace and uncertainty placeholders for accuracy scoring."""
    return "".join(
        char
        for char in (text or "")
        if not char.isspace() and char not in _PLACEHOLDERS
    )


def _is_editorial_punctuation(char: str) -> bool:
    if not char:
        return False
    return unicodedata.category(char).startswith("P")


def normalize_metric_text(text: str, mode: str = "raw") -> str:
    """
    Normalize OCR text for a metric mode.

    ``raw`` preserves the first-stage behavior: only whitespace and uncertainty
    placeholders are removed. ``faithful`` additionally removes editorial
    punctuation from human transcripts while preserving glyph variants.
    ``content`` is for semantic OCR progress tracking and folds a small set of
    common simplified/traditional variants.
    """
    if mode not in _METRIC_MODES:
        raise ValueError(f"Unknown OCR metric mode: {mode}")

    source = text or ""
    if mode != "raw":
        source = unicodedata.normalize("NFKC", source)

    normalized_chars: list[str] = []
    for char in source:
        if char.isspace() or char in _PLACEHOLDERS:
            continue
        if mode != "raw" and _is_editorial_punctuation(char):
            continue
        normalized_chars.append(char)

    normalized = "".join(normalized_chars)
    if mode == "content":
        normalized = normalized.translate(_CONTENT_VARIANT_MAP)
    return normalized


def _lcs_length(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(current[-1], previous[index]))
        previous = current
    return previous[-1]


def _minimum_edit_alignment(
    reference: str,
    prediction: str,
) -> dict[str, Any]:
    """
    Compute a true Wagner-Fischer alignment.

    When several minimum-distance paths exist, prefer the one with fewer
    insertions. This makes the hallucination count conservative and stable.
    """
    rows = len(reference) + 1
    columns = len(prediction) + 1
    costs: list[list[tuple[int, int, int, int]]] = [
        [(0, 0, 0, 0)] * columns for _ in range(rows)
    ]
    operations = [[""] * columns for _ in range(rows)]

    for row in range(1, rows):
        costs[row][0] = (row, 0, 0, row)
        operations[row][0] = "delete"
    for column in range(1, columns):
        costs[0][column] = (column, column, 0, 0)
        operations[0][column] = "insert"

    for row in range(1, rows):
        for column in range(1, columns):
            if reference[row - 1] == prediction[column - 1]:
                costs[row][column] = costs[row - 1][column - 1]
                operations[row][column] = "match"
                continue

            previous = costs[row - 1][column - 1]
            substitute = (
                previous[0] + 1,
                previous[1],
                previous[2] + 1,
                previous[3],
            )
            previous = costs[row][column - 1]
            insert = (
                previous[0] + 1,
                previous[1] + 1,
                previous[2],
                previous[3],
            )
            previous = costs[row - 1][column]
            delete = (
                previous[0] + 1,
                previous[1],
                previous[2],
                previous[3] + 1,
            )
            best_cost, best_operation = min(
                (substitute, "substitute"),
                (insert, "insert"),
                (delete, "delete"),
                key=lambda item: item[0],
            )
            costs[row][column] = best_cost
            operations[row][column] = best_operation

    row = len(reference)
    column = len(prediction)
    missing_chars: list[str] = []
    extra_chars: list[str] = []
    while row or column:
        operation = operations[row][column]
        if operation == "match":
            row -= 1
            column -= 1
        elif operation == "substitute":
            missing_chars.append(reference[row - 1])
            extra_chars.append(prediction[column - 1])
            row -= 1
            column -= 1
        elif operation == "insert":
            extra_chars.append(prediction[column - 1])
            column -= 1
        elif operation == "delete":
            missing_chars.append(reference[row - 1])
            row -= 1
        else:
            raise RuntimeError("Invalid edit-alignment backtrace")

    distance, insertions, substitutions, deletions = costs[-1][-1]
    return {
        "edit_distance": distance,
        "insertions": insertions,
        "substitutions": substitutions,
        "deletions": deletions,
        "missing_chars": "".join(reversed(missing_chars)),
        "extra_chars": "".join(reversed(extra_chars)),
    }


def char_level_metrics(
    prediction: str,
    ground_truth: str,
    *,
    mode: str = "raw",
) -> dict[str, Any]:
    """Return order-sensitive OCR metrics with placeholders scored separately."""
    prediction_visible = normalize_metric_text(prediction, mode)
    ground_truth_visible = normalize_metric_text(ground_truth, mode)
    output_chars = "".join(char for char in (prediction or "") if not char.isspace())
    lcs_length = _lcs_length(ground_truth_visible, prediction_visible)
    alignment = _minimum_edit_alignment(ground_truth_visible, prediction_visible)

    precision = lcs_length / max(len(prediction_visible), 1)
    recall = lcs_length / max(len(ground_truth_visible), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)
    placeholder_count = sum(char in _PLACEHOLDERS for char in output_chars)

    return {
        "mode": mode,
        "gt_len": len(ground_truth_visible),
        "pred_len": len(prediction_visible),
        "output_len": len(output_chars),
        "lcs_len": lcs_length,
        "exact_matches": lcs_length,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "cer": round(
            alignment["edit_distance"] / max(len(ground_truth_visible), 1),
            4,
        ),
        "edit_distance": alignment["edit_distance"],
        "insertions": alignment["insertions"],
        "substitutions": alignment["substitutions"],
        "deletions": alignment["deletions"],
        "extra_hallucination_rate": round(
            alignment["insertions"] / max(len(prediction_visible), 1),
            4,
        ),
        "fabricated_char_rate": round(
            (alignment["insertions"] + alignment["substitutions"])
            / max(len(prediction_visible), 1),
            4,
        ),
        "placeholder_count": placeholder_count,
        "placeholder_rate": round(
            placeholder_count / max(len(output_chars), 1),
            4,
        ),
        "missing_count": len(alignment["missing_chars"]),
        "extra_count": len(alignment["extra_chars"]),
        "missing_chars": alignment["missing_chars"][:20],
        "extra_chars": alignment["extra_chars"][:20],
    }


def char_metric_modes(
    prediction: str,
    ground_truth: str,
    modes: tuple[str, ...] = _METRIC_MODES,
) -> dict[str, dict[str, Any]]:
    return {
        mode: char_level_metrics(prediction, ground_truth, mode=mode)
        for mode in modes
    }


def aggregate_char_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "gt_len": 0,
        "pred_len": 0,
        "output_len": 0,
        "lcs_len": 0,
        "edit_distance": 0,
        "insertions": 0,
        "substitutions": 0,
        "deletions": 0,
        "placeholder_count": 0,
    }
    for item in metrics:
        for key in totals:
            totals[key] += item[key]

    precision = totals["lcs_len"] / max(totals["pred_len"], 1)
    recall = totals["lcs_len"] / max(totals["gt_len"], 1)
    mode = metrics[0].get("mode", "unknown") if metrics else "unknown"
    return {
        "mode": mode,
        "processed_images": len(metrics),
        "overall_precision": round(precision, 4),
        "overall_recall": round(recall, 4),
        "overall_f1": round(
            2 * precision * recall / max(precision + recall, 1e-10),
            4,
        ),
        "overall_cer": round(
            totals["edit_distance"] / max(totals["gt_len"], 1),
            4,
        ),
        "overall_extra_hallucination_rate": round(
            totals["insertions"] / max(totals["pred_len"], 1),
            4,
        ),
        "overall_fabricated_char_rate": round(
            (totals["insertions"] + totals["substitutions"])
            / max(totals["pred_len"], 1),
            4,
        ),
        "overall_placeholder_rate": round(
            totals["placeholder_count"] / max(totals["output_len"], 1),
            4,
        ),
        **{f"total_{key}": value for key, value in totals.items()},
    }
