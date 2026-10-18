"""Alembic 迁移可用性测试。"""
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_creates_current_schema(tmp_path):
    database_path = tmp_path / "migration-test.db"
    environment = os.environ.copy()
    environment.update(
        {
            "SECRET_KEY": "alembic-test-secret-key-with-at-least-32-chars",
            "DATABASE_URL": f"sqlite:///{database_path}",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"alembic upgrade failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "user",
        "image",
        "ocr_result",
        "structured_result",
        "relation_graph",
        "multi_task",
        "multi_relation_graph",
        "multi_task_structured_result",
    }.issubset(tables)
