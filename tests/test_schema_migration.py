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
    assert "segments_json" in columns
    assert "rejection_reasons" in columns
    assert "human_corrected" in columns


def test_rejection_reasons_decode_for_api():
    from app.routers.ocr import _decode_json_list, _decode_rejection_reasons

    assert _decode_rejection_reasons('["hard_reject:v3_only_ratio"]') == [
        "hard_reject:v3_only_ratio"
    ]
    assert _decode_rejection_reasons(None) == []
    assert _decode_json_list('[{"text":"立永賣"}]') == [{"text": "立永賣"}]
