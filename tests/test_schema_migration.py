"""Tests for lightweight SQLite schema compatibility migration."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_init_db_ensures_ocr_metadata_columns():
    from database import DB_PATH, init_db

    init_db()

    con = sqlite3.connect(DB_PATH)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(ocr_result)").fetchall()}
    finally:
        con.close()

    assert "confidence" in columns
    assert "coverage" in columns
    assert "engine" in columns
    assert "model_versions" in columns
    assert "original_raw_text" in columns
    assert "corrected_text" in columns
    assert "segments_json" in columns
    assert "corrected_segments_json" in columns
    assert "correction_metadata_json" in columns
    assert "rejection_reasons" in columns
    assert "crop_bbox_json" in columns
    assert "image_size_json" in columns
    assert "human_corrected" in columns


def test_rejection_reasons_decode_for_api():
    from app.routers.ocr import _decode_json_list, _decode_rejection_reasons

    assert _decode_rejection_reasons('["hard_reject:v3_only_ratio"]') == [
        "hard_reject:v3_only_ratio"
    ]
    assert _decode_rejection_reasons(None) == []
    assert _decode_json_list('[{"text":"立永賣"}]') == [{"text": "立永賣"}]


def test_legacy_human_correction_split_sql(tmp_path):
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE ocr_result (
                id INTEGER PRIMARY KEY,
                raw_text VARCHAR,
                original_raw_text VARCHAR,
                corrected_text VARCHAR,
                human_corrected BOOLEAN
            );
            INSERT INTO ocr_result
                (id, raw_text, original_raw_text, corrected_text, human_corrected)
            VALUES
                (1, '人工修订文本', '原始 OCR', NULL, 1),
                (2, '原始 OCR 2', '原始 OCR 2', NULL, 1),
                (3, '未修订 OCR', '未修订 OCR', NULL, 0),
                (4, 'OCR 4', 'OCR 4', '既有校订', 1);
            """
        )
        con.execute(
            """
            UPDATE ocr_result
            SET corrected_text = raw_text,
                raw_text = original_raw_text
            WHERE human_corrected = 1
              AND corrected_text IS NULL
              AND original_raw_text IS NOT NULL
              AND raw_text IS NOT NULL
              AND raw_text != original_raw_text
            """
        )
        con.execute(
            """
            UPDATE ocr_result
            SET human_corrected = 0
            WHERE human_corrected = 1
              AND corrected_text IS NULL
            """
        )
        rows = con.execute(
            "SELECT id, raw_text, corrected_text, human_corrected FROM ocr_result ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    assert rows == [
        (1, "原始 OCR", "人工修订文本", 1),
        (2, "原始 OCR 2", None, 0),
        (3, "未修订 OCR", None, 0),
        (4, "OCR 4", "既有校订", 1),
    ]
