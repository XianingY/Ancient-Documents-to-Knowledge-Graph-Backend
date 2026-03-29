# 古代地契文书知识图谱 · 后端

Python **3.11+**，框架 **FastAPI**，数据库 **SQLite**（`database/app.db`），异步任务 **Celery + Redis**，向量库 **ChromaDB**（智能问答为 **混合 RAG**：Chroma 语义检索 + 数据库按上传时间补充最新文书，合并去重后调用 LLM；未建索引时向量结果为空会自动加大 DB 侧条数补偿，见 `app/services/rag_service.py`。`POST /api/v1/chat/reindex` 将文书写入/更新 Chroma；`GET /api/v1/chat/kb-status` 统计的是 DB 侧「OCR 完成且有正文」的文书数，见 `app/routers/chat.py`）。

## 安装依赖

### 方式一：Docker 容器化部署（推荐）

本项目已全面支持 Docker 部署，一键启动后端与 Redis 服务：

```bash
# 确保已安装 Docker 和 Docker Compose
# 在项目根目录下执行：
docker-compose up -d
```

启动后，可通过 `docker-compose logs -f` 查看运行日志。

### 方式二：本地环境安装

```bash
pip install -r requirements.txt
```

## 环境变量

在项目根目录（与 `main.py` 同级）创建 `.env`。`pydantic-settings` 从该文件加载，定义见 **`app/core/config.py`** 中 `Settings`：

| 变量 | 说明 |
|------|------|
| `SECRET_KEY` | **必填**，JWT 签名 |
| `DASHSCOPE_API_KEY` | 可选；未配置时 OCR/结构化/问答等会降级或返回提示文案 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | 可选，默认 `localhost:6379/0` |
| `UPLOAD_DIR`、`SERVER_PORT` 等 | 可选，有默认值 |

在项目根目录新建 `.env` 即可；**必填**项以 `app/core/config.py` 的 `Settings` 为准（当前至少需 `SECRET_KEY`）。可选示例如下（勿将真实密钥提交版本库）：

```env
SECRET_KEY=请替换为足够长的随机字符串
# DASHSCOPE_API_KEY=
# REDIS_HOST=localhost
# REDIS_PORT=6379
```

## 启动 API

默认端口与 `settings.SERVER_PORT` 一致，当前代码为 **3000**：

```bash
uvicorn main:app --host 0.0.0.0 --port 3000
```

开发热重载：

```bash
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

也可用 `python main.py`，内部使用 `settings.SERVER_PORT`。

## Celery Worker

上传图片后，`POST /api/v1/images/upload` 会投递 **`task_ocr_image`**；Worker 内链路（`app/worker/tasks.py`）为：

1. **`task_ocr_image`**：OCR 成功且正文非空、非 `Error:` 前缀时，投递 **`task_analyze_ocr_result`**（结构化）。
2. **`task_analyze_ocr_result`**：结构化状态为 `done` 时，投递 **`task_analyze_structured_result`**（单文书关系图）。
3. **`task_analyze_structured_result`**：生成并落库单文书关系图。

跨文档图谱：**创建**多任务时由 FastAPI **`BackgroundTasks`** 调用 `analyze_multi_task`（见 `app/routers/multi_tasks.py`）；**`POST /api/v1/multi-relation-graphs`** 则投递 **`task_analyze_multi_task`** 到 Celery（见 `app/routers/graphs.py`）。

须先启动 **Redis** 再启动 Worker：

```bash
celery -A app.core.celery_app worker --loglevel=info --concurrency=2
```

## API 文档

- Swagger：`http://<host>:<port>/docs`
- ReDoc：`http://<host>:<port>/redoc`
- 健康检查：`GET /api`

## 路由前缀（代码中的 `APIRouter`）

| 前缀 | 模块 |
|------|------|
| `/api/v1/auth` | `app/routers/auth.py` |
| `/api/v1/users` | `app/routers/users.py` |
| `/api/v1/images` | `app/routers/images.py` |
| `/api/v1/ocr-results` | `app/routers/ocr.py` |
| `/api/v1/structured-results` | `app/routers/structured.py` |
| `/api/v1/relation-graphs` | `app/routers/graphs.py` |
| `/api/v1/multi-relation-graphs` | `app/routers/graphs.py` |
| `/api/v1/multi-tasks` | `app/routers/multi_tasks.py` |
| `/api/v1/chat` | `app/routers/chat.py` |
| `/api/v1/statistics` | `app/routers/statistics.py` |

## 集成测试

```bash
pip install -r requirements-dev.txt
```

运行前打开 `api_test/test_api.py`，将 **`BASE_URL`** 设为与当前 uvicorn 端口一致，例如：

```text
http://localhost:3000/api/v1
```

（与 `app/core/config.py` 中 `SERVER_PORT = 3000` 对齐。）

```bash
pytest api_test/test_api.py -v -s
```

## 限流

若已安装 **slowapi**（`requirements.txt` 已包含），`app/core/rate_limit.py` 中全局默认为 **`200/minute`（按客户端 IP）**，并设有应用级 **`1000/hour`**；各路由可通过 `@rate_limit("…")` 单独收紧（例如上传 `30/minute`）。`main.py` 注册 `SlowAPIMiddleware`；未安装 slowapi 时装饰器退化为无操作，仅可能影响启动日志。

## 说明：`POST /api/v1/images/upload`

该路由在 `app/routers/images.py` 中落库成功后调用 **`task_ocr_image.delay(image_id)`**；若 Celery 投递失败则仅记录警告，**上传仍返回成功**（响应中含 `pipeline_started: true` 表示已尝试投递，不代表 Worker 已消费）。在 **Redis + Worker 正常运行** 且 **OCR 成功** 的前提下，**结构化分析与单文书关系图**会由上述 Celery 任务链**自动排队**，无需再调 `POST /api/v1/structured-results` / `POST /api/v1/relation-graphs`。若需对**指定** OCR/结构化结果重新跑一遍，仍可手动调用这两个接口（与自动链共用同一批 Celery 任务）。
