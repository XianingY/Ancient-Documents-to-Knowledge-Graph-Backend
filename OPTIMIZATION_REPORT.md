# 项目优化验收报告

**项目名称**: Ancient-Documents-to-Knowledge-Graph-Backend  
**验收日期**: 2026-03-17  
**说明**: 下文描述与**当前仓库代码**一致；路径为相对仓库根目录。

---

## 1. 概述

后端为 FastAPI + SQLAlchemy（SQLite）+ Celery（Redis）+ ChromaDB，OCR/结构化/部分生成能力通过 **阿里云 DashScope** 调用；跨文档图谱在 `analysis_service` 中结合 **NetworkX** 与 `EntityResolver` 构建。

---

## 2. 架构与规范

### 2.1 配置

- **文件**: `app/core/config.py`
- **内容**: 使用 `pydantic-settings` 从环境变量 / `.env` 加载；`SECRET_KEY` 无默认值（必填）；集中定义 `REDIS_*`、`UPLOAD_DIR`、`SERVER_PORT` 等。

### 2.2 分层

- **路由**: `app/routers/*` — 参数校验、鉴权、调用 Service 或投递 Celery / `BackgroundTasks`。
- **业务**: `app/services/*` — OCR、结构化、图谱、RAG、跨文档编排等。
- **数据**: `database.py` — ORM 模型与 `get_db` 会话。

### 2.3 多任务路由

- **文件**: `app/routers/multi_tasks.py`
- **内容**: 使用 Pydantic 请求体；创建 MultiTask 后通过 `BackgroundTasks` 调用异步 `analyze_multi_task`（非 Celery 默认路径，见该文件 `_auto_analyze`）。

---

## 3. 性能与并发

### 3.1 线程池包装阻塞调用

- **文件**: `app/services/rag_service.py`、`app/services/llm_client.py`、`app/services/analysis_service.py` 等
- **内容**: 对 DashScope 同步 SDK、`run_in_threadpool` 等在 async 路由中避免阻塞事件循环。OCR 主路径在 **Celery Worker** 中以同步方式执行（`ocr_service.ocr_image_by_id`），而非在 HTTP 进程内跑 PaddleOCR。

### 3.2 数据库访问

- **文件**: `app/services/analysis_service.py`（跨文档分析等）
- **内容**: 对结构化结果等使用 `filter(..., id.in_(ids))` 批量查询，减少循环内单条查询。

---

## 4. 功能要点（与实现一致）

### 4.1 Celery + Redis

- **文件**: `app/core/celery_app.py`、`app/worker/tasks.py`
- **内容**: OCR、单文书结构化、单文书关系图、以及 `POST /api/v1/multi-relation-graphs` 触发的跨文档任务等通过 Celery 异步执行；Broker/Backend 使用 `settings.REDIS_URL`。

### 4.2 ChromaDB

- **文件**: `app/services/vector_store/chroma.py`、`app/services/ocr_service.py`、`app/services/analysis_service.py`、`app/routers/chat.py`
- **内容**: OCR 完成与结构化完成后对文书做 **upsert**；`POST /api/v1/chat/reindex`、`GET /api/v1/chat/kb-status` 维护/查询索引。智能问答主流程在 `rag_service.rag_pipeline` 中从 **数据库取当前用户最近 8 条**已完成 OCR 的文书作上下文，**不**以 Chroma 向量检索为主路径（`retrieve_context` 仍保留在代码中）。

### 4.3 实体消歧

- **文件**: `app/services/analysis_components/entity_resolver.py`
- **内容**: 字符相似与 DashScope **text-embedding-v1** 向量融合，再结合时间、地点加权；阈值与公式以源码为准。

### 4.4 限流

- **文件**: `main.py`
- **内容**: 若安装 `slowapi`，则注册默认限流；未安装时记录警告，应用仍可启动。

---

## 5. 总结

当前后端具备清晰分层、异步任务解耦、向量索引与可选 API 限流。后续可加强单元测试与 CI。

**验收结果**: ✅ **通过**（表述已与当前代码对齐）
