"""仓库体积与二进制文件管理约定。"""
from pathlib import Path


def test_demo_video_files_are_ignored():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8"
    )

    assert "*.mp4" in gitignore
