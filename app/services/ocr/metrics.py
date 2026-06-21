import re
from typing import Any


_PLACEHOLDERS = {"□", "■", "囗"}


def normalize_visible_text(text: str) -> str:
    """Remove layout whitespace and uncertainty placeholders for accuracy scoring."""
    return "".join(
        char
        for char in (text or "")
        if not char.isspace() and char not in _PLACEHOLDERS
    )


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


def char_level_metrics(prediction: str, ground_truth: str) -> dict[str, Any]:
    """Return order-sensitive OCR metrics with placeholders scored separately."""
    prediction_visible = normalize_visible_text(prediction)
    ground_truth_visible = normalize_visible_text(ground_truth)
    output_chars = "".join(char for char in (prediction or "") if not char.isspace())
    lcs_length = _lcs_length(ground_truth_visible, prediction_visible)
    alignment = _minimum_edit_alignment(ground_truth_visible, prediction_visible)

    precision = lcs_length / max(len(prediction_visible), 1)
    recall = lcs_length / max(len(ground_truth_visible), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)
    placeholder_count = sum(char in _PLACEHOLDERS for char in output_chars)

    return {
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
