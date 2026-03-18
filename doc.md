# API 文档

## 基础信息
- 基地址: `http://localhost:3000`
- API 版本: v1
- 大多数请求和响应为 JSON；图片获取接口返回二进制文件
- 除 `GET /api`、`POST /api/v1/auth/register` 和 `POST /api/v1/auth/login` 外，其他端点都需要 Bearer Token 认证

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
测试接口，返回简单的问候信息。

**请求**
```
GET /api
```

**响应**
```
"Hello, World!"
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
  "message": "logout ok"
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

当前版本上传接口仅保存文件与图片记录，不会自动触发 OCR/结构化/关系图任务。
如需继续处理，请手动调用 `POST /api/v1/images/{image_id}/ocr`。

**请求头**
```
Authorization: Bearer {access_token}
Content-Type: multipart/form-data
```

**请求参数**
- `image` (file) - 图片文件（必需）
- `user_id` (query) - 用户ID，默认为 1

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

**失败响应** (200)
```json
{
  "success": false,
  "message": "文件为空"
}
```

失败响应同样使用 200，`message` 可能为：
- 不支持的文件类型
- 文件过大
- 文件为空
- 读取文件失败
- 保存文件失败
- 保存到数据库失败

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
  "message": "Multi task created successfully",
  "multi_task_id": 1,
  "structured_result_ids": [1, 2, 3],
  "created_at": "2024-01-01T12:00:00"
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
  "message": "Multi task created from images successfully",
  "multi_task_id": 1,
  "image_ids": [1, 2, 3],
  "structured_result_ids": [5, 8, 12],
  "created_at": "2024-01-01T12:00:00"
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

**算法说明**
跨文档分析采用了深化的实体消歧与对齐算法（Deep Entity Resolution）。
- **多维相似度计算**: 综合考量姓名匹配(40%)、角色匹配(20%)、时间相近性(20%)、地点相关性(20%)。
- **动态聚类**: 基于贪心策略将潜在实体归类到实体簇。
- **图谱构建**: 节点代表实体簇，边基于实体在文档中的共现关系。

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
基于知识库的智能问答（RAG）。

**请求头**
```
Authorization: Bearer {access_token}
```

**请求体**
```json
{
  "question": "帮我找出道光年间所有的土地交易"
}
```

**响应** (200)
```json
{
  "success": true,
  "data": {
    "answer": "根据现有文档，道光年间共有以下几笔土地交易：1. 道光十二年，恒忠将田产卖给篋叙堂...",
    "sources": [
      "时间：道光十二年二月初二日，卖方：恒忠，买方：篋叙堂..."
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

目前无速率限制

---

## 使用流程

### 完整的端到端工作流程

#### 1. 初始设置（首次使用）
```
POST /api/v1/auth/register  -> 注册账户，获得用户ID
POST /api/v1/auth/login     -> 登录，获得 access_token
```

#### 2. 图片处理流程
```
POST /api/v1/images/upload              -> 上传图片，获得 imageId（当前版本仅保存图片，不自动触发后续分析）
POST /api/v1/images/{imageId}/ocr       -> 触发 OCR 识别
GET  /api/v1/images/{imageId}/ocr-results  -> 获取 OCR 结果列表
GET  /api/v1/ocr-results/{ocrId}        -> 获取单个 OCR 结果详情
```

#### 3. 结构化分析流程
```
POST /api/v1/structured-results                     -> 创建结构化分析任务
GET  /api/v1/structured-results/{structuredId}      -> 获取结构化结果详情
POST /api/v1/relation-graphs                        -> 创建关系图分析任务
GET  /api/v1/relation-graphs/{relationGraphId}      -> 获取关系图详情
```

#### 4. 跨文档分析流程
```
POST /api/v1/multi-tasks                            -> 创建跨文档任务（直接提供结构化结果ID）
POST /api/v1/multi-tasks/from-images                -> 根据图片ID自动创建跨文档任务
POST /api/v1/multi-relation-graphs                  -> 创建跨文档分析任务
GET  /api/v1/multi-relation-graphs/{multiGraphId}   -> 获取跨文档分析结果
```

#### 5. 智能问答流程
```
POST /api/v1/chat/query                             -> 提问并获取基于文档的回答
```
