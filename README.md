# 古代地契文书知识图谱 · 后端

Python **3.11+**，框架 **FastAPI**，数据库 **SQLite**（`database/app.db`），异步任务 **Celery + Redis**，可选向量库 **ChromaDB**（问答主流程走 DB 取最近文书；Chroma 用于 `reindex` / `kb-status` 等，见 `app/services/rag_service.py`、`app/routers/chat.py`）。

## 安装依赖

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

> 仓库中**没有** `.env.example`，需自行新建 `.env`。

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

上传图片后的 OCR、结构化、图谱等由 Worker 执行，**须先启动 Redis** 再启动 Worker：

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

若已安装 **slowapi**，`main.py` 会注册默认 `200/minute` 限流；未安装时仅打日志警告，不影响启动。

## 说明：`POST /api/v1/images/upload`

该路由在 `app/routers/images.py` 中在落库成功后调用 **`task_ocr_image.delay(image_id)`**；若 Celery 投递失败则仅记录警告，上传仍成功。**结构化**与**单文书关系图**不会由此路由自动触发，需 `POST /api/v1/structured-results`、`POST /api/v1/relation-graphs`（或 App 内操作）。OpenAPI 文案应与上述行为一致。
