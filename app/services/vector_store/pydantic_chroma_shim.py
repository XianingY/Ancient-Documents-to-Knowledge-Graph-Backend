"""
在 import chromadb 之前执行。

部分 chromadb / 其依赖仍使用 `from pydantic import BaseSettings`，而 Pydantic 2.12+
已将 BaseSettings 迁至 pydantic-settings，直接导入会抛 ImportError。

将 BaseSettings 挂回 pydantic 模块，供旧版 chromadb 子路径导入成功。
升级 chromadb 至 requirements 中推荐版本后，可弱化对此的依赖，但保留无害。
"""
from __future__ import annotations


def apply() -> None:
    try:
        import pydantic
        import pydantic_settings

        pydantic.BaseSettings = pydantic_settings.BaseSettings  # type: ignore[attr-defined]
    except Exception:
        pass


# 模块加载时执行一次（被 chroma 等先于 chromadb 导入时生效）
apply()
