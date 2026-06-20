"""
在 import chromadb 之前执行。

修复 pydantic 2.12+ 将 BaseSettings 迁至 pydantic-settings 的兼容性问题。
"""
from __future__ import annotations


def apply() -> None:
    try:
        import pydantic
        import pydantic_settings

        pydantic.BaseSettings = pydantic_settings.BaseSettings  # type: ignore[attr-defined]
    except Exception:
        pass


apply()
