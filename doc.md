# 文渊智图 — API 接口文档

> 古代地契文书智能知识图谱系统 · 后端 API 详细说明

本文档完整描述了系统后端提供的所有 RESTful API 接口，包括请求参数、响应格式与使用流程。

启动服务后，也可通过 Swagger UI（`http://<host>:<port>/docs`）或 ReDoc（`http://<host>:<port>/redoc`）查看交互式文档。

## 基础信息

| 项目 | 说明 |
|------|------|
| 基地址 | `http://localhost:3000` |
| API 版本 | v1 |
| 数据格式 | 请求/响应均为 JSON（图片获取接口返回二进制文件） |
| 认证方式 | Bearer Token（JWT） |
| 公开接口 | `GET /api`、`POST /api/v1/auth/register`、`POST /api/v1/auth/login` |

---

## 认证说明

### Token 获取
通过登录接口获得 JWT Token，有效期为 24 小时。

### Token 使用
所有受保护的 API 请求都需在请求头中包含：
```
Authorization: Bearer {access_token}
```

---

## API 端点

### 测试接口

#### GET /api
健康检查。

**请求**
```
GET /api
```

**响应** (200)
```json
{
  "status": "ok",
  "version": "2.0.0"
}
```

---

## 认证路由 (前缀: /api/v1/auth)

### POST /api/v1/auth/register
用户注册端点。

**请求体**
```json
{
  "username": "user@example.com",
  "password": "password123",
  "email": "user@example.com"
}
```

**成功响应** (200)
```json
{
  "success": true,
  "message": "注册成功",
  "userId": 1,
  "username": "user@example.com",
  "email": "user@example.com"
}
```

**失败响应** (400)
```json
{
  "detail": "用户名已存在"
}
```

---

### POST /api/v1/auth/login
用户登录端点，获取 JWT Token。

**请求体**
```json
{
  "username": "user@example.com",
  "password": "password123"
}
```

**成功响应** (200)
```json
{
  "success": true,
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user_id": 1,
  "username": "user@example.com"
}
```

**失败响应** (401)
```json
{
  "detail": "用户名或密码错误"
}
```

---

### POST /api/v1/auth/logout
用户退出登录端点。

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "message": "已成功登出"
}
```

---

### POST /api/v1/auth/refresh
刷新 JWT Token，获取新的有效期。

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "access_token": "new_token...",
  "token_type": "bearer"
}
```

---

## 用户路由 (前缀: /api/v1/users)

### GET /api/v1/users/me
获取当前登录用户的信息。

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "user": {
    "id": 1,
    "username": "user@example.com",
    "email": "user@example.com",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

### PUT /api/v1/users/me
更新当前用户的信息（用户名、密码或邮箱）。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "username": "newusername",
  "password": "newpassword",
  "email": "newemail@example.com"
}
```

**响应** (200)
```json
{
  "success": true,
  "message": "更新成功",
  "user": {
    "id": 1,
    "username": "newusername",
    "email": "newemail@example.com",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

### GET /api/v1/users/images
获取当前用户的图片列表（分页）。

**请求头**
```
Authorization: Bearer {access_token}
```

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 25,
    "skip": 0,
    "limit": 10,
    "ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
  }
}
```

---

### GET /api/v1/users/multi-tasks
获取当前用户的多任务列表（分页）。

**请求头**
```
Authorization: Bearer {access_token}
```

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 5,
    "skip": 0,
    "limit": 10,
    "ids": [1, 2, 3, 4, 5]
  }
}
```

---

## 图片路由 (前缀: /api/v1/images)

### POST /api/v1/images/upload
上传图片文件。

**当前实现**（`app/routers/images.py` + `app/worker/tasks.py`）：

1. 保存文件并写入 `Image` 表后，调用 **`task_ocr_image.delay(db_image.id)`** 将 OCR 提交到 Celery。若 `delay` 抛错（如未启 Redis），异常被捕获并记录日志，**上传仍返回成功**。
2. Worker 执行 **`task_ocr_image`**：OCR 成功后，若最新一条已完成 OCR 的正文非空且不以 `Error:` 开头，会自动投递 **`task_analyze_ocr_result`**（结构化）。
3. **`task_analyze_ocr_result`** 成功后，若对应 **`StructuredResult` 状态为 `done`**，会自动投递 **`task_analyze_structured_result`**（单文书关系图）。

因此：**无需**再因「自动流水线」单独调 `POST /api/v1/structured-results` / `POST /api/v1/relation-graphs`，**除非**要手动重跑、补跑，或队列曾失败需用 `POST /api/v1/images/{image_id}/ocr` 等补救。流水线依赖 **Redis + Celery Worker** 以及 **`DASHSCOPE_API_KEY`**（未配置时 OCR/后续步骤可能失败或降级，以 `ocr_service` / `analysis_service` 实现为准）。

校验失败时多为 **HTTP 400/413/500**（如类型不支持、文件过大、为空、保存失败等），以路由实现为准。

**请求头**
```
Authorization: Bearer {access_token}
Content-Type: multipart/form-data
```

**请求参数**
- `image` (file) - 图片文件（必需）

**支持格式**
- .jpg, .jpeg, .png, .gif, .bmp, .webp, .tiff

**文件限制**
- 最大大小：10MB

**成功响应** (200)
```json
{
  "success": true,
  "imageId": 1,
  "filename": "photo_a1b2c3d4.jpg",
  "originalName": "photo.jpg",
  "fileSize": 250000,
  "pipeline_started": true
}
```

**错误响应**（常见）
- `400`：`detail` 如不支持的扩展名、文件为空、读取失败等
- `413`：超过 `MAX_FILE_SIZE`（默认 10MB）
- `500`：保存磁盘或数据库失败

---

### GET /api/v1/images/{image_id}
获取指定图片的文件。

**路径参数**
- `image_id` (path, integer) - 图片ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
- 返回图片文件内容（二进制数据）

**错误响应**
- 404: `"detail": "image not found"`
- 404: `"detail": "image file not found"`

---

### GET /api/v1/images/{image_id}/thumbnail
获取指定图片的缩略图。

**路径参数**
- `image_id` (path, integer) - 图片ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
- 返回图片缩略图（二进制数据）

**响应类型**
- `image/jpeg`

**错误响应**
- 404: `"detail": "image not found"`
- 404: `"detail": "image file not found"`

---

### DELETE /api/v1/images/{image_id}
删除指定图片，并级联清理其分析结果。

删除范围包括：
- 图片记录与原图文件
- 该图片下的 OCR 结果
- 该图片下 OCR 对应的结构化结果
- 结构化结果对应的关系图
- 多任务关联表中与上述结构化结果关联的记录
- 缩略图缓存文件（若存在）

**路径参数**
- `image_id` (path, integer) - 图片ID

**请求头**
```
Authorization: Bearer {access_token}
```

**成功响应** (200)
```json
{
  "success": true,
  "message": "图片及关联分析结果已删除",
  "deleted": {
    "image_id": 1,
    "ocr_results": 2,
    "structured_results": 2,
    "relation_graphs": 2,
    "multi_task_associations": 1,
    "removed_files": [
      "pic/photo_a1b2c3d4.jpg",
      "pic/thumbnails/photo_a1b2c3d4_thumb.jpg"
    ]
  }
}
```

**错误响应**
- 403: 无权删除该图片
- 404: 图片不存在
- 500: 删除失败

---

### GET /api/v1/images/{image_id}/info
获取指定图片的基本信息。

**路径参数**
- `image_id` (path, integer) - 图片ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "filename": "photo_a1b2c3d4.jpg",
    "upload_time": "2024-01-01T12:00:00",
    "title": "title_test"
  }
}
```

**错误响应**
- 404: 图片不存在

---

### POST /api/v1/images/{image_id}/ocr
对指定图片执行 OCR 识别（异步处理）。

**路径参数**
- `image_id` (path, integer) - 图片ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "message": "图片 1 的OCR任务已提交到队列"
}
```

---

### GET /api/v1/images/{image_id}/ocr-results
获取指定图片的 OCR 结果列表（分页）。

**路径参数**
- `image_id` (path, integer) - 图片ID

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 5,
    "skip": 0,
    "limit": 10,
    "ids": [1, 2, 3, 4, 5]
  }
}
```

---

## OCR 结果路由 (前缀: /api/v1/ocr-results)

### GET /api/v1/ocr-results/{ocr_id}
获取指定 OCR 结果的详细信息。

**路径参数**
- `ocr_id` (path, integer) - OCR 结果ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "image_id": 1,
    "raw_text": "识别出的文本内容...",
    "status": "done",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

**状态值**
- `pending` - 待处理
- `processing` - 处理中
- `done` - 完成
- `failed` - 失败

---

### GET /api/v1/ocr-results/{ocr_result_id}/structured-results
获取指定 OCR 结果的结构化结果列表（分页）。

**路径参数**
- `ocr_result_id` (path, integer) - OCR 结果ID

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 3,
    "skip": 0,
    "limit": 10,
    "ids": [1, 2, 3]
  }
}
```

---

## 结构化结果路由 (前缀: /api/v1/structured-results)

### POST /api/v1/structured-results
对指定 OCR 结果进行结构化分析。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "ocr_result_id": 1
}
```

**响应** (200)
```json
{
  "success": true,
  "message": "OCR结果 1 的结构化分析任务已提交到队列"
}
```

---

### GET /api/v1/structured-results/{structured_result_id}
获取指定结构化结果的详细信息。

**路径参数**
- `structured_result_id` (path, integer) - 结构化结果ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "ocr_result_id": 1,
    "content": {
      "Time": "道光十二年二月初二日",
      "Time_AD": 1832,
      "Location": "小堤團虎禪垹輝訟",
      "Seller": "恆忠",
      "Buyer": "篋叙堂",
      "Middleman": "叔如玉",
      "Price": "感式捨榮串曹整",
      "Subject": "田肆點捌分玖厘",
      "Translation": "永久业产..."
    },
    "status": "done",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

### GET /api/v1/structured-results/{structured_result_id}/relation-graphs
获取指定结构化结果的关系图列表（分页）。

**路径参数**
- `structured_result_id` (path, integer) - 结构化结果ID

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 2,
    "skip": 0,
    "limit": 10,
    "ids": [1, 2]
  }
}
```

---

## 关系图路由 (前缀: /api/v1/relation-graphs)

### POST /api/v1/relation-graphs
对指定结构化结果进行关系图分析。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "structured_result_id": 1
}
```

**响应** (200)
```json
{
  "success": true,
  "message": "StructuredResult 1 的关系图生成任务已提交到队列"
}
```

---

### GET /api/v1/relation-graphs/{relation_graph_id}
获取指定关系图的详细信息。

**路径参数**
- `relation_graph_id` (path, integer) - 关系图ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "structured_result_id": 1,
    "content": {},
    "status": "done",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

## 多任务路由 (前缀: /api/v1/multi-tasks)

### POST /api/v1/multi-tasks
创建跨文档分析任务。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "structured_result_ids": [1, 2, 3]
}
```

**响应** (200)
```json
{
  "success": true,
  "message": "Multi task created successfully, analysis started automatically",
  "multi_task_id": 1,
  "structured_result_ids": [1, 2, 3],
  "created_at": "2024-01-01T12:00:00+08:00"
}
```

---

### POST /api/v1/multi-tasks/from-images
根据多个图片ID创建跨文档分析任务。

根据每个图片最后一个（id最大）OCR结果的最后一个结构化结果，自动关联并创建跨文档分析任务。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "image_ids": [1, 2, 3]
}
```

**成功响应** (200)
```json
{
  "success": true,
  "message": "Multi task created from images successfully, analysis started automatically",
  "multi_task_id": 1,
  "image_ids": [1, 2, 3],
  "structured_result_ids": [5, 8, 12],
  "created_at": "2024-01-01T12:00:00+08:00"
}
```

**失败响应** (400)
```json
{
  "detail": "Image 1 has no OCR results"
}
```

典型失败信息还包括：
- `Image {image_id} not found`
- `Image {image_id} has no structured results`
- `Image {image_id} does not belong to the current user`
- `StructuredResult {id} not found`
- `StructuredResult {id} does not belong to the current user`
- `Failed to create multi task: ...`

---

### GET /api/v1/multi-tasks/{multi_task_id}
获取指定多任务的详细信息。

**路径参数**
- `multi_task_id` (path, integer) - 多任务ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "user_id": 1,
    "status": "done",
    "structured_result_ids": [1, 2, 3],
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

### DELETE /api/v1/multi-tasks/{multi_task_id}
删除指定多任务，并级联清理其分析结果。

删除范围包括：
- 多任务记录
- 多任务关联的结构化结果关联表中的记录
- 多任务下的所有跨文档关系图

**路径参数**
- `multi_task_id` (path, integer) - 多任务ID

**请求头**
```
Authorization: Bearer {access_token}
```

**成功响应** (200)
```json
{
  "success": true,
  "message": "Multi task deleted",
  "deleted": {
    "multi_task_id": 1,
    "multi_relation_graphs": 2,
    "multi_task_associations": 3
  }
}
```

**错误响应**
- 403: `"detail": "Permission denied"`
- 404: `"detail": "MultiTask not found"`
- 400: `"detail": "Failed to delete multi task: ..."`

---

### GET /api/v1/multi-tasks/{multi_task_id}/multi-relation-graphs
获取指定多任务的跨文档关系图列表（分页）。

**路径参数**
- `multi_task_id` (path, integer) - 多任务ID

**查询参数**
- `skip` (query, integer) - 分页偏移量，默认为 0
- `limit` (query, integer) - 每页数量，默认为 10，范围 1-100

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "total": 1,
    "skip": 0,
    "limit": 10,
    "ids": [1]
  }
}
```

---

## 跨文档关系图路由 (前缀: /api/v1/multi-relation-graphs)

### POST /api/v1/multi-relation-graphs
对指定多任务进行跨文档分析。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "multi_task_id": 1
}
```

**响应** (200)
```json
{
  "success": true,
  "message": "MultiTask 1 的跨文档分析任务已提交到队列"
}
```

**算法说明**（与 `app/services/analysis_components/entity_resolver.py` 一致，细节以源码为准）

- **姓名**：单层融合为 **0.4×字符相似度 + 0.6×语义相似度**（语义为 `text-embedding-v1` 余弦，缺向量时回退字符层）；`calculate_similarity` 中 **0.6×姓名分 + 时间阶梯加分 + 地点匹配加分**；**阈值 0.45** 合并为同一实体。
- **图谱**：在 NetworkX 上构建跨文书关系，含买卖、见证、地块流转等边类型，并导出 ECharts 力导向数据。

---

### GET /api/v1/multi-relation-graphs/{multi_relation_graph_id}
获取指定跨文档关系图的详细信息。

**路径参数**
- `multi_relation_graph_id` (path, integer) - 跨文档关系图ID

**请求头**
```
Authorization: Bearer {access_token}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "id": 1,
    "multi_task_id": 1,
    "content": {},
    "status": "done",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

---

## 智能问答路由 (前缀: /api/v1/chat)

### POST /api/v1/chat/query
智能问答（**混合 RAG**，实现见 `app/services/rag_service.py` 的 `rag_pipeline` / `hybrid_retrieve`）：

1. **向量检索（ChromaDB）**：对问题做嵌入（DashScope `text-embedding-v1`；未配置 `DASHSCOPE_API_KEY` 时使用占位向量），默认取语义最相关 **`_VECTOR_TOP_K = 15`** 条；`query_documents` 按 `user_id` 过滤，无结果时放宽过滤重试。
2. **数据库时序补充**：`_fetch_latest_docs_sync` 从 DB 取当前用户最新 **`_DB_RECENT_N = 5`** 份已完成 OCR 的文书（每张图只保留最新一条 OCR，再按图片上传时间倒序截断），并附加最新已完成 `StructuredResult` 中的 Time/Location/Seller/Buyer/Price/Subject 等到元数据。
3. **合并**：`_hybrid_retrieve_sync` 按 `image_id` 去重，**向量结果在前**，再拼接 DB 补充；最终列表不超过 **`_MAX_CONTEXT = 20`**。若向量侧无结果，DB 侧取 **`max_context`（20）** 条作为补偿（`fallback_n` 逻辑）。
4. **上下文格式化**：`_format_context` 按文书条数动态限制每条最大字数（**400 / 250 / 150**），非固定 250。
5. **生成**：DashScope **`qwen-turbo`**；未配置 `DASHSCOPE_API_KEY` 时返回固定提示、不调用模型。
6. **多轮**：`history` 在 `_build_messages` 中**最多最近 6 轮**。

**流式**：`POST /api/v1/chat/query-stream`（SSE）使用相同的 **`hybrid_retrieve`** 与流式生成（见 `app/routers/chat.py`）。

**知识库状态**：`GET /api/v1/chat/kb-status` 的 `indexed_count` 为 DB 中当前用户 **不同 `image_id`**、OCR 完成且 `raw_text` 非空的数量（**不是** Chroma 条数）。

**重建索引**：`POST /api/v1/chat/reindex` 通过 `BackgroundTasks` 执行 `_reindex_all_sync`，将文书嵌入并 **upsert** 到 Chroma（`doc_id = image_{image_id}`）。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "question": "帮我找出道光年间所有的土地交易",
  "history": null
}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "answer": "根据参考文书……",
    "sources": [
      {
        "index": 1,
        "doc_id": 1,
        "image_id": 1,
        "filename": "#1 示例",
        "time": "",
        "location": "",
        "seller": "",
        "buyer": "",
        "price": "",
        "subject": "",
        "excerpt": "……"
      }
    ]
  }
}
```

---

## 错误处理

所有 API 错误响应遵循以下格式：

**错误响应示例**
```json
{
  "detail": "错误信息描述"
}
```

**HTTP 状态码**
- `200` - 请求成功
- `400` - 请求参数错误或业务逻辑失败
- `401` - 认证失败或 Token 无效/过期
- `403` - 无权限访问资源
- `404` - 资源不存在
- `500` - 服务器错误

---

## 速率限制

安装 **`slowapi`** 时（`requirements.txt` 已包含），`app/core/rate_limit.py` 中 Limiter 配置为：默认 **`200/minute`**（按客户端 IP），应用级 **`1000/hour`**。各路由可叠加更严的 `@rate_limit`（例如图片上传 `30/minute`、问答 `30/minute`）。未安装 `slowapi` 时 `@rate_limit` 退化为无操作。

---

## 使用流程

### 完整的端到端工作流程

#### 1. 初始设置（首次使用）
```
POST /api/v1/auth/register  -> 注册账户，获得用户ID
POST /api/v1/auth/login     -> 登录，获得 access_token
```

#### 2. 图片与自动流水线（Celery）
```
POST /api/v1/images/upload                 -> 上传并落库，并尝试 Celery 投递 task_ocr_image
  └─（Worker 内）OCR 成功 → task_analyze_ocr_result → 结构化 done → task_analyze_structured_result（单文书关系图）
POST /api/v1/images/{imageId}/ocr          -> 手动补投/重试 OCR（Celery，同上可触发后续链）
GET  /api/v1/images/{imageId}/ocr-results  -> 获取 OCR 结果 ID 列表
GET  /api/v1/ocr-results/{ocrId}           -> 获取单个 OCR 详情
```

#### 3. 结构化与单文书关系图（自动 + 可选手动）
```
（通常无需）POST /api/v1/structured-results          -> 对指定 ocr_result_id 再投 Celery 结构化（与自动链同一任务）
GET  /api/v1/structured-results/{structuredId}       -> 获取结构化结果详情
（通常无需）POST /api/v1/relation-graphs             -> 对指定 structured_result_id 再投 Celery 单文书关系图
GET  /api/v1/relation-graphs/{relationGraphId}      -> 获取关系图详情
```

#### 4. 跨文档分析流程
```
POST /api/v1/multi-tasks                            -> 创建跨文档任务（structured_result_ids）；创建后 FastAPI BackgroundTasks 调用异步 analyze_multi_task（非 Celery）
POST /api/v1/multi-tasks/from-images                -> 按图片 ID 取最新结构化结果并创建任务；同上 BackgroundTasks + analyze_multi_task
POST /api/v1/multi-relation-graphs                  -> 投递 Celery task_analyze_multi_task（与上条二选一或用于重算/补跑）
GET  /api/v1/multi-relation-graphs/{multiGraphId}   -> 获取跨文档分析结果
```

#### 5. 智能问答流程
```
POST /api/v1/chat/query                             -> 提问并获取基于文档的回答
```
