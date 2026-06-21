from pathlib import Path

import numpy as np
from PIL import Image

from app.services.ocr.preprocess import detect_paper_bbox, prepare_document_image


def test_detect_paper_bbox_ignores_dark_background_and_grid():
    image = np.full((600, 800, 3), 25, dtype=np.uint8)
    image[80:540, 140:690] = (210, 198, 170)
    image[80:540:40, :] = 20
    image[:, 140:690:40] = 20

    x1, y1, x2, y2 = detect_paper_bbox(image)

    assert 110 <= x1 <= 160
    assert 50 <= y1 <= 100
    assert 670 <= x2 <= 720
    assert 520 <= y2 <= 570


def test_prepare_document_image_scales_and_cleans_temp_file(tmp_path):
    source_path = tmp_path / "source.jpg"
    Image.new("RGB", (1200, 800), (220, 210, 180)).save(source_path)

    with prepare_document_image(str(source_path), target_long_side=600) as prepared:
        generated_path = Path(prepared.path)
        assert generated_path.exists()
        assert max(prepared.prepared_size) == 600

    assert not generated_path.exists()
