import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass
class PreparedDocument:
    path: str
    crop_bbox: list[int]
    original_size: tuple[int, int]
    prepared_size: tuple[int, int]


def _odd_kernel(value: float, minimum: int) -> int:
    size = max(minimum, int(round(value)))
    return size if size % 2 else size + 1


def detect_paper_bbox(rgb: np.ndarray) -> tuple[int, int, int, int]:
    """Find the largest paper-colored region while removing thin grid lines."""
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = ((hsv[:, :, 2] > 60) & (hsv[:, :, 1] < 150)).astype(np.uint8) * 255

    short_side = min(width, height)
    open_size = _odd_kernel(short_side * 0.0125, 7)
    close_size = _odd_kernel(short_side * 0.026, 17)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size)),
    )
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)),
        iterations=2,
    )

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return 0, 0, width, height

    component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[component, cv2.CC_STAT_AREA])
    if area < width * height * 0.15:
        return 0, 0, width, height

    ys, xs = np.where(labels == component)
    if len(xs) < 10:
        return 0, 0, width, height

    x1, x2 = np.quantile(xs, [0.002, 0.998]).astype(int)
    y1, y2 = np.quantile(ys, [0.002, 0.998]).astype(int)
    margin_x = max(8, round((x2 - x1) * 0.02))
    margin_y = max(8, round((y2 - y1) * 0.02))
    return (
        max(0, int(x1) - margin_x),
        max(0, int(y1) - margin_y),
        min(width, int(x2) + margin_x + 1),
        min(height, int(y2) + margin_y + 1),
    )


@contextmanager
def prepare_document_image(
    image_path: str,
    target_long_side: int = 2400,
) -> Iterator[PreparedDocument]:
    """Crop the paper, preserve color, and create a job-scoped temporary image."""
    temp_dir = tempfile.mkdtemp(prefix="ancient-ocr-")
    try:
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        rgb = np.asarray(image)
        x1, y1, x2, y2 = detect_paper_bbox(rgb)
        cropped = image.crop((x1, y1, x2, y2))

        scale = min(2.5, target_long_side / max(cropped.size))
        if abs(scale - 1.0) > 0.01:
            cropped = cropped.resize(
                (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))),
                Image.Resampling.LANCZOS,
            )

        prepared_path = os.path.join(temp_dir, "document.jpg")
        cropped.save(prepared_path, "JPEG", quality=95, subsampling=0)
        yield PreparedDocument(
            path=prepared_path,
            crop_bbox=[x1, y1, x2, y2],
            original_size=image.size,
            prepared_size=cropped.size,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
