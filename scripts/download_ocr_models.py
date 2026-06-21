#!/usr/bin/env python3
"""Download the OCR models during image build instead of on the first request."""
import os


os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from paddleocr import PaddleOCR, TextRecognition


def main() -> None:
    PaddleOCR(
        lang="ch",
        ocr_version="PP-OCRv6",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        text_rec_score_thresh=0.0,
        return_word_box=True,
    )
    TextRecognition(model_name="PP-OCRv6_small_rec")
    print("PP-OCRv6 models are ready.")


if __name__ == "__main__":
    main()
