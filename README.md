# 文渊智图 — 后端服务

> 古代地契文书智能知识图谱系统 · 后端 API

## 项目简介

本项目是**文渊智图**系统的后端服务，为前端移动应用提供完整的 RESTful API 支持。基于 FastAPI 构建，集成 OCR 文字识别、大模型结构化提取、知识图谱构建、向量检索增强问答（RAG）等核心 AI 能力，实现从古代地契图片到知识图谱的全链路自动化处理。

## 技术架构

```
┌──────────────────────────────────────────────────────────────┐
│                      FastAPI 应用层                           │
│           RESTful API · JWT 鉴权 · CORS · 速率限制            │
├──────────┬──────────┬───────────┬───────────┬───────────────┤
│  认证    │  图片    │  分析     │  问答     │  统计          │
│  auth    │  images  │  OCR/     │  chat     │  statistics   │
│  users   │  upload  │  结构化/  │  RAG      │               │
│          │  delete  │  图谱     │  SSE 流式 │               │
├──────────┴──────────┴───────────┴───────────┴───────────────┤
│                     服务层（Services）                        │
│  ocr_service · analysis_service · graph_service              │
│  multi_task_service · rag_service · llm_client               │
├──────────────────────────────────────────────────────────────┤
│                     异步任务层                                │
│              Celery + Redis（任务队列与消息代理）              │
│     OCR → 结构化 → 关系图谱（自动链式触发）                   │
├──────────────────────────────────────────────────────────────┤
│                     数据存储层                                │
│     SQLite（业务数据）  ·  ChromaDB（向量索引）               │
│     文件系统（图片存储）                                      │
└──────────────────────────────────────────────────────────────┘
```

## 核心能力

### 1. 全链路自动分析流水线

图片上传后，系统通过 Celery 任务链自动完成：

```
图片上传 → OCR 文字识别 → 结构化信息提取 → 知识图谱生成
```

每个阶段独立异步执行，前一阶段成功后自动触发下一阶段，无需人工干预。

### 2. OCR 文字识别

基于阿里云 DashScope 多模态大模型，对古代地契文书中的手写/印刷文字进行识别，支持繁体字、异体字等复杂场景。

### 3. 结构化信息提取

通过大模型从 OCR 文本中智能提取以下关键字段：
- 交易时间（含公元纪年转换）
- 地点、卖方、买方、中人
- 交易标的、价格
- 现代文翻译

### 4. 知识图谱构建

- **单文书图谱**：提取人物、契约、交易信息之间的关系，生成力导向关系图
- **跨文档图谱**：基于实体消歧算法（字符相似度 + 语义向量融合 + 时间/地点加权），跨文书识别同一实体，利用 NetworkX 构建综合知识图谱
- **统计洞察**：自动计算土地流转链、宗族关系、中人网络、年代分布、价格趋势等

### 5. RAG 智能问答

- **混合检索**：ChromaDB 语义向量检索 + 数据库时序补充，确保覆盖最新文书
- **流式输出（SSE）**：支持实时流式生成回答
- **多轮对话**：保留历史上下文，支持连续追问
- **来源溯源**：每条回答附带引用文书来源

### 6. 数据统计

提供全局统计 API，包括文书总量、已分析数量、时间分布、地域分布、高频人物、价格趋势等多维度数据。

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| Web 框架 | FastAPI 0.135 + Uvicorn |
| 数据库 | SQLAlchemy 2.0 + SQLite |
| 异步任务 | Celery 5.6 + Redis 7 |
| AI 服务 | 阿里云 DashScope（OCR / 结构化 / 问答生成 / 文本嵌入） |
| 向量数据库 | ChromaDB 0.5（语义检索） |
| 图分析 | NetworkX 3.6（知识图谱构建与社会网络分析） |
| 鉴权 | JWT（PyJWT + passlib + bcrypt） |
| 速率限制 | slowapi |
| 容器化 | Docker + Docker Compose |

## 项目结构

```
├── main.py                    # FastAPI 应用入口
├── database.py                # ORM 模型与数据库初始化
├── app/
│   ├── core/
│   │   ├── config.py          # 配置管理（pydantic-settings）
│   │   ├── security.py        # JWT 生成与验证
│   │   ├── deps.py            # 依赖注入（当前用户、数据库会话）
│   │   ├── celery_app.py      # Celery 实例
│   │   ├── rate_limit.py      # 速率限制配置
│   │   └── logger.py          # 日志配置
│   ├── routers/               # API 路由
│   │   ├── auth.py            # 认证（注册/登录/刷新/登出）
│   │   ├── users.py           # 用户信息与资源列表
│   │   ├── images.py          # 图片上传/获取/删除
│   │   ├── ocr.py             # OCR 结果查询
│   │   ├── structured.py      # 结构化结果查询
│   │   ├── graphs.py          # 关系图（单文书 + 跨文档）
│   │   ├── multi_tasks.py     # 跨文档分析任务管理
│   │   ├── chat.py            # 智能问答（RAG + SSE）
│   │   └── statistics.py      # 数据统计
│   ├── services/              # 业务服务层
│   │   ├── ocr_service.py     # OCR 处理
│   │   ├── analysis_service.py# 结构化分析与跨文档分析
│   │   ├── graph_service.py   # 单文书图谱生成
│   │   ├── multi_task_service.py # 跨文档任务编排
│   │   ├── rag_service.py     # RAG 检索与问答
│   │   ├── llm_client.py      # DashScope 大模型调用
│   │   ├── analysis_components/
│   │   │   └── entity_resolver.py  # 实体消歧算法
│   │   └── vector_store/
│   │       └── chroma.py      # ChromaDB 操作封装
│   └── worker/
│       └── tasks.py           # Celery 异步任务定义
├── docker-compose.yml         # Docker 编排配置
├── Dockerfile                 # 容器镜像构建
├── nginx.conf                 # Nginx 反向代理配置（生产环境）
├── requirements.txt           # Python 依赖
├── requirements-dev.txt       # 开发/测试依赖
├── doc.md                     # 详细 API 接口文档
└── api_test/
    └── test_api.py            # 集成测试
```

## 快速开始

### 环境要求

- Python 3.11+
- Redis（Celery 消息代理）
- 阿里云 DashScope API Key（用于 OCR/AI 分析/问答）

### 方式一：Docker 部署（推荐）

```bash
# 1. 创建环境变量文件
cp .env.example .env
# 编辑 .env，填入 SECRET_KEY 和 DASHSCOPE_API_KEY

# 2. 一键启动（API 服务 + Redis + Celery Worker）
docker-compose up -d

# 3. 查看运行日志
docker-compose logs -f
```

### 方式二：本地部署

```bash
# 1. 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 创建环境变量文件
cp .env.example .env
# 编辑 .env，填入必要配置

# 4. 启动 Redis（需提前安装）
redis-server

# 5. 启动 Celery Worker（新终端）
celery -A app.core.celery_app worker --loglevel=info --concurrency=2

# 6. 启动 API 服务
uvicorn main:app --host 0.0.0.0 --port 3000
```

### 验证部署

```bash
# 健康检查
curl http://localhost:3000/api
# 预期返回: {"status":"ok","version":"2.0.0"}
```

### API 文档

启动服务后，可通过以下地址访问交互式 API 文档：
- Swagger UI：`http://localhost:3000/docs`
- ReDoc：`http://localhost:3000/redoc`

详细的 API 接口说明请参阅 [doc.md](./doc.md)。

## 环境变量

| 变量 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `SECRET_KEY` | 是 | JWT 签名密钥 | — |
| `DASHSCOPE_API_KEY` | 否 | 阿里云 DashScope API 密钥（AI 功能所需） | — |
| `REDIS_HOST` | 否 | Redis 主机地址 | `localhost` |
| `REDIS_PORT` | 否 | Redis 端口 | `6379` |
| `REDIS_DB` | 否 | Redis 数据库编号 | `0` |
| `SERVER_PORT` | 否 | API 服务端口 | `3000` |
| `UPLOAD_DIR` | 否 | 图片存储目录 | `pic` |
| `DATABASE_URL` | 否 | 数据库连接字符串 | SQLite（`database/app.db`） |

## 自动分析流水线

```
用户上传图片
     │
     ▼
┌─────────────┐    成功     ┌──────────────┐    成功     ┌──────────────┐
│  OCR 识别   │ ────────→  │  结构化提取  │ ────────→  │  图谱生成    │
│ (Celery)    │            │  (Celery)    │            │  (Celery)    │
└─────────────┘            └──────────────┘            └──────────────┘
                                                              │
                                                              ▼
                                                      单文书知识图谱
                                                              │
                                                     选择多份文书 ──→ 跨文档分析
                                                              │
                                                              ▼
                                                      综合知识图谱
                                                    + 统计洞察报告
```

## API 路由总览

| 前缀 | 功能 |
|------|------|
| `/api/v1/auth` | 用户认证（注册、登录、Token 刷新、登出） |
| `/api/v1/users` | 用户信息与资源列表 |
| `/api/v1/images` | 图片上传、获取、删除、触发 OCR |
| `/api/v1/ocr-results` | OCR 识别结果查询 |
| `/api/v1/structured-results` | 结构化提取结果查询 |
| `/api/v1/relation-graphs` | 单文书关系图查询 |
| `/api/v1/multi-tasks` | 跨文档分析任务管理 |
| `/api/v1/multi-relation-graphs` | 跨文档关系图查询 |
| `/api/v1/chat` | 智能问答（RAG + SSE 流式） |
| `/api/v1/statistics` | 全局数据统计 |

## 应用信息

| 项目 | 内容 |
|------|------|
| 应用名称 | 文渊智图 · 后端 |
| API 版本 | 2.0.0 |
| Python 版本 | 3.11+ |
| 默认端口 | 3000 |
