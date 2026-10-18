"""用户文档必须覆盖当前部署与测试入口。"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_current_runtime_and_tests():
    content = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "requirements-api.txt" in content
    assert "requirements-worker.txt" in content
    assert "Dockerfile.api" in content
    assert "Dockerfile.worker" in content
    assert "alembic upgrade head" in content
    assert "pytest -m live_api" in content
    assert "分段校对" in content
    assert "智能问答" in content


def test_env_example_documents_ocr_configuration():
    content = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    for key in (
        "OCR_ENGINE",
        "OCR_FALLBACK_ENGINE",
        "OCR_MULTIVIEW_ENABLED",
        "OCR_CONSENSUS_MIN_SCORE",
        "OCR_CONSENSUS_MIN_SIMILARITY",
        "OCR_CHAR_RESCUE_ENABLED",
    ):
        assert key in content


def test_api_doc_documents_segment_edits_and_user_isolation():
    content = (PROJECT_ROOT / "doc.md").read_text(encoding="utf-8")

    assert "segment_edits" in content
    assert "corrected_segments_json" in content
    assert "user_id" in content
    assert "不会检索其他用户" in content
