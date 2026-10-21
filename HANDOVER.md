# 交接说明（Handover）

> 面向接手开发、部署和联调的同学。建议阅读顺序：本文 → `README.md`（部署与目录）→ `doc.md`（接口字段）→ `experiments/README.md`（离线实验）。
>
> 对应版本：`handover-2026-10-21`（master 分支）

## 1. 项目边界

本仓库包含三块内容，边界要分清：

| 模块 | 路径 | 是否属于线上服务 | 说明 |
|---|---|---|---|
| 后端服务 | `main.py`、`app/`、`database.py`、`migrations/` | 是 | FastAPI + SQLAlchemy(SQLite) + Celery(Redis) + ChromaDB |
| Web 前端 | `web/` | 是 | React + Vite + TypeScript，构建后由 Nginx 托管 |
| 离线课程实验 | `experiments/` | 否 | 契约/族谱：OCR → 实体关系抽取 → ECharts 图谱 |

`scripts/` 是运维与评估脚本（演示数据、模型下载、OCR 准确率评估），不属于请求链路。
`api_test/` 是需要真实服务才能运行的联调测试，默认被 pytest 排除。

## 2. 目录速查

```text
main.py                  FastAPI 入口（路由注册、CORS、限流、/api 健康检查）
database.py              ORM 模型、SQLite 初始化与轻量列补齐
app/routers/             HTTP 路由（auth/users/images/ocr/structured/graphs/multi_tasks/chat/statistics）
app/services/            业务服务（OCR、结构化、图谱、RAG、任务编排、实体消歧）
app/services/ocr/        本地 PaddleOCR 预处理、共识融合、指标
app/worker/tasks.py      Celery 任务（OCR → 结构化 → 单文书图谱；跨文档分析）
migrations/              Alembic 迁移（初始版本 91af0fb8c701）
web/src/                 React 页面（上传、OCR 校对、图谱、问答、统计）
experiments/             离线实验：literature_pipeline / llm_extraction / tiled_ocr
scripts/                 演示与评估脚本
doc.md                   完整 API 文档（字段级）
```

## 3. 启动方式

### 先决条件（缺一不可）

- **必须先创建 `.env`**：`cp .env.example .env`。`SECRET_KEY` 在导入期校验，缺失时服务直接抛 `ValidationError`，不是运行到接口才报错。
- **完整流水线需要 Redis**。没有 Redis 时 API 仍可启动、图片仍可上传，但 OCR 任务不会执行（上传接口会捕获入队异常，只记 warning）。
- **首次 OCR 需要联网下载 PaddleOCR 模型**（镜像默认不预置，`PRELOAD_OCR_MODELS=false`）。离线机器请先执行 `python scripts/download_ocr_models.py`，或构建时加 `--build-arg PRELOAD_OCR_MODELS=true`。
- Python 建议 **3.11 / 3.12**。Windows 的 `paddlepaddle` 轮子已在依赖中声明。

### 最短验证（只跑离线实验，不起服务）

只依赖 Pillow，解压后即可验证数据与图谱链路：

```bash
pip install pillow
python experiments/literature_pipeline.py \
  --data-dir "文献图片数据-6契-5行传" \
  --output-dir experiment_outputs
```

### Docker（推荐）

```bash
cp .env.example .env      # 填入 SECRET_KEY、DASHSCOPE_API_KEY
docker compose up -d --build
```

服务与端口：

| 服务 | 容器 | 端口 |
|---|---|---|
| API | `ancient_doc_backend` | 3000 |
| Web | `ancient_doc_frontend` | 8080 |
| Redis | `ancient_doc_redis` | 宿主机 6380（避免与本机 6379 冲突） |
| OCR Worker | `ancient_doc_celery_worker` | 无对外端口 |

API 容器启动时会先执行 `alembic upgrade head`。OCR Worker 默认 `--concurrency=2`。

### 本地

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # 等价于全量依赖 + pytest
cp .env.example .env
redis-server
alembic upgrade head
uvicorn main:app --host 0.0.0.0 --port 3000
# 另开终端
celery -A app.core.celery_app worker --loglevel=info --concurrency=2
```

前端：

```bash
cd web && npm install && npm run dev   # 5173，代理到 3000
```

### 演示数据

```bash
python scripts/seed_demo_web.py            # 演示账号 demo_web / DemoWeb2026!
docker compose --profile demo run --rm demo_seed
```

## 4. 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `SECRET_KEY` | 是 | JWT 签名密钥，必须替换 |
| `DASHSCOPE_API_KEY` | 线上 AI 功能必填 | 结构化提取、问答、OCR 兜底 |
| `DATABASE_URL` | 否 | 默认 `sqlite:///database/app.db` |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | 否 | 默认 localhost:6379/0 |
| `UPLOAD_DIR` | 否 | 默认 `pic`，图片与 Chroma 持久化都在此目录下 |
| `OCR_ENGINE` | 否 | 默认 `paddle_v6_consensus` |
| `OCR_FALLBACK_ENGINE` | 否 | 默认 `qwen_conservative` |
| `DEEPSEEK_API_KEY` | 仅实验 | `experiments/llm_extraction.py` 使用，不被线上服务读取 |

完整 OCR 参数见 `.env.example`。

## 5. 请求链路

```text
上传图片
  └─ Celery: task_ocr_image
       └─ 本地 PaddleOCR PP-OCRv6 共识（medium + small + tiny），失败回退 qwen-vl-ocr-latest
            └─ task_analyze_ocr_result（qwen-plus 结构化提取）
                 └─ task_analyze_structured_result（单文书图谱）

多文档分析
  └─ task_analyze_multi_task（NetworkX 图分析 + EntityResolver 实体消歧）
```

问答链路：Chroma 向量检索（`text-embedding-v1`）+ 当前用户数据库补充 → `qwen-turbo` 生成，支持 SSE 流式。

## 6. API 摘要

所有业务接口前缀为 `/api/v1`，除注册/登录外都需要 `Authorization: Bearer <token>`。

| 前缀 | 主要用途 |
|---|---|
| `/api/v1/auth` | 注册、登录、登出、刷新 token |
| `/api/v1/users` | 当前用户信息、我的图片、我的多文档任务 |
| `/api/v1/images` | 上传、详情、缩略图、删除、发起 OCR、OCR 结果列表 |
| `/api/v1/ocr-results` | OCR 详情、分段校对（`segment_edits`）、重分析 |
| `/api/v1/structured-results` | 结构化结果创建与查询 |
| `/api/v1/relation-graphs` | 单文书关系图 |
| `/api/v1/multi-relation-graphs` | 跨文档关系图 |
| `/api/v1/multi-tasks` | 跨文档分析任务 |
| `/api/v1/chat` | RAG 问答（普通/流式） |
| `/api/v1/statistics` | 统计看板 |

健康检查：`GET /api` → `{"status":"ok","version":"2.0.0"}`。
限流：全局 200/分钟、1000/小时；上传与问答更严格（30/分钟）。
字段级说明以 `doc.md` 为准。

## 7. 关键数据契约

- OCR 结果保留原始文本、校正文本、分段坐标与拒绝原因：
  `original_raw_text`、`corrected_text`、`segments_json`、`corrected_segments_json`、`rejection_reasons`。
- 单文书/跨文档图谱统一为 `{ categories, data: [节点], links: [边] }`，可直接喂给 ECharts。
- 离线实验 JSON：`document_id` + `entities` + `relations` + `metadata`，每个实体和关系都带 `evidence`（原文逐字片段），便于人工校验和评分。

## 8. 测试与基线

```bash
pytest                      # 后端单元测试，默认排除 live_api
pytest -m live_api          # 需要服务已启动（API_TEST_BASE_URL）
cd web && npm test          # 前端 Vitest
cd web && npm run build     # TypeScript + Vite 构建
alembic upgrade head && alembic check
```

当前基线：后端 120 个用例通过，前端 20 个用例通过。

## 9. 已知限制（接手需知）

1. **OCR 是 CPU 密集型**：Worker 默认并发 2；批量导入需要队列和错峰，不建议直接压测。
2. **SQLite 单文件**：适合单机/演示；多副本部署需要换 PostgreSQL，并重新验证迁移与并发写入。
3. **Chroma 本地持久化**：位于 `pic/chromadb`，多实例部署需要改为独立向量服务。
4. **图片存本地磁盘**：`pic/`，未接对象存储；容器部署时通过 volume 挂载。
5. **SSE 使用进程内线程池**（4 线程），高并发问答需改为独立异步/队列方案。
6. **`requirements.txt` 是全量兼容入口**：生产镜像分别使用 `requirements-api.txt` / `requirements-worker.txt`。
7. **`experiments/` 不是线上代码**：默认规则抽取，可通过 DeepSeek 切换为 LLM 抽取；结果仅供课程实验。
8. **数据与视频不在 Git 中**：`文献图片数据-6契-5行传/`（约 76 MB）、`experiment_outputs/`、演示视频均需单独交付。

## 10. 交接清单

- [ ] 代码仓库（或 `git archive` 生成的干净压缩包）
- [ ] `.env`（通过安全渠道单独发送，不要提交）
- [ ] 原始数据目录 `文献图片数据-6契-5行传/`（如需复现实验）
- [ ] 演示视频（已从 Git 历史移除，单独提供）
- [ ] DashScope API Key 与配额确认
- [ ] DeepSeek API Key（仅实验需要，建议重新签发）
- [ ] 演示账号初始化或 `database/app.db`（二选一，后者含演示数据）
- [ ] 部署域名、HTTPS 证书、Redis 实例（如走生产环境）

## 11. 接手后的第一批建议

1. 本地跑通 `pytest` 与 `docker compose up`，确认 OCR 能出结果。
2. 用 `doc.md` 对照前端实际调用，补齐 OpenAPI 描述。
3. 若要上生产：优先替换 SQLite、外置 Chroma、图片转对象存储，再谈水平扩容。
4. 若继续课程实验：先确认数据授权与存储位置，再决定是否把 `experiments/` 合并进服务。
