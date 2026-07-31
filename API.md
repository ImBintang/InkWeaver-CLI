# InkWeaver-CLI HTTP API 接口文档

> **版本**: v6.3.0  
> **基础路径**: `http://localhost:8000`  
> **启动方式**: `inkweaver serve`

---

## 目录

- [概述](#概述)
- [通用说明](#通用说明)
- [书籍（工作区）管理](#书籍工作区管理)
- [章节管理](#章节管理)
- [草稿系统](#草稿系统)
- [对话（鉴知）](#对话鉴知)
- [知识库](#知识库)
- [会话管理](#会话管理)
- [妙笔写作](#妙笔写作)
- [设置与模型](#设置与模型)
- [Token 统计](#token-统计)
- [SSE 事件流](#sse-事件流)
- [常见报错](#常见报错)
- [数据模型速查](#数据模型速查)

---

## 概述

InkWeaver HTTP API 基于 FastAPI 构建，为 GUI 前端和第三方客户端提供 RESTful 接口。

核心特性：
- 单用户桌面模式（本地运行）
- 书签式路径参数 `{book}` 标识工作区
- SSE 长连接推送 Agent 实时事件
- 配置和模型管理通过 HTTP API 完成

### Content-Type

所有 JSON 请求/响应均使用 `application/json`，UTF-8 编码。

---

## 通用说明

### 路径参数

| 参数 | 说明 | 约束 |
|------|------|------|
| `book` | 工作区名称 | 仅允许中文、字母、数字、下划线、连字符 |

### HTTP 方法语义

| 方法 | 语义 |
|------|------|
| `GET` | 查询资源 |
| `POST` | 创建资源 / 触发操作 |
| `PUT` | 完整更新 |
| `PATCH` | 部分更新 |
| `DELETE` | 删除资源 |

### 错误响应格式

```json
{
  "detail": "错误描述文本或结构化对象"
}
```

---

## 书籍（工作区）管理

### 列出所有工作区

```http
GET /api/books
```

**响应示例**:

```json
[
  {
    "name": "我的小说",
    "path": "D:\\Code\\InkWeaver-CLI-workspace\\workingArea\\我的小说",
    "chapters": 50
  }
]
```

---

### 获取当前打开的工作区

```http
GET /api/books/current
```

**响应示例**:

```json
{
  "name": "我的小说"
}
```

未打开任何工作区时返回 `null`。

---

### 打开工作区

```http
POST /api/books/open
Content-Type: application/json

{
  "name": "我的小说"
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 工作区名称 |

**响应示例**:

```json
{
  "ok": true,
  "name": "我的小说",
  "session": {
    "id": "sess_20260730_120000_a1b2",
    "name": "新会话",
    "message_count": 0,
    "cap": 500
  }
}
```

打开工作区后，服务器自动绑定 SessionManager 并创建/激活默认会话。

---

### 创建工作区

```http
POST /api/books
Content-Type: application/json

{
  "name": "新书",
  "path": ""
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 工作区名称 |
| `path` | string | 否 | 自定义路径（留空则默认在 workingArea 下创建） |

**响应示例**:

```json
{
  "ok": true
}
```

---

## 章节管理

### 列出章节

```http
GET /api/books/{book}/chapters
```

**响应示例**:

```json
[
  {
    "num": 1,
    "title": "第一章 开局",
    "word_count": 3200,
    "imported_at": "2026-07-28T10:30:00",
    "draft_count": 2
  }
]
```

---

### 获取单章详情

```http
GET /api/books/{book}/chapters/{num}
```

**路径参数**:

| 参数 | 说明 |
|------|------|
| `num` | 章节号 |

**响应示例**:

```json
{
  "chapter_num": 1,
  "title": "第一章 开局",
  "content": "章节正文内容...",
  "word_count": 3200,
  "imported_at": "2026-07-28T10:30:00"
}
```

章节不存在时返回 `{}`。

---

### 导入章节文件

```http
POST /api/books/{book}/chapters/import
Content-Type: application/json

{
  "file_path": "D:\\novels\\output.txt"
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 小说文件绝对路径 |

**响应示例**:

```json
{
  "ok": true,
  "message": "成功导入 50 章"
}
```

**可能的错误**:

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 文件路径无效或文件格式错误 |

---

## 草稿系统

### 列出草稿

```http
GET /api/books/{book}/drafts?chapter_num=1
```

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chapter_num` | int | 否 | 按章节号过滤 |

**响应示例**:

```json
[
  {
    "id": 1,
    "chapter_num": 1,
    "title": "第一章 修改版",
    "content": "草稿内容...",
    "source": "user",
    "created_at": "2026-07-29T15:30:00"
  }
]
```

---

### 获取草稿详情

```http
GET /api/books/{book}/drafts/{draft_id}
```

**响应示例**:

```json
{
  "id": 1,
  "chapter_num": 1,
  "title": "第一章 修改版",
  "content": "草稿内容...",
  "source": "user",
  "created_at": "2026-07-29T15:30:00"
}
```

---

### 保存草稿

```http
POST /api/books/{book}/drafts
Content-Type: application/json

{
  "chapter_num": 1,
  "content": "草稿内容...",
  "source": "user",
  "title": "第一章 修改版"
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chapter_num` | int | 是 | 章节号 |
| `content` | string | 是 | 草稿内容 |
| `source` | string | 否 | 来源标识（默认 `user`） |
| `title` | string | 否 | 草稿标题 |

**响应示例**:

```json
{
  "ok": true,
  "id": 1
}
```

---

### 发布草稿

```http
POST /api/books/{book}/drafts/{draft_id}/publish
```

将草稿内容覆盖写入正式章节。

**响应示例**:

```json
{
  "ok": true,
  "chapter_num": 1
}
```

---

### 删除草稿

```http
DELETE /api/books/{book}/drafts/{draft_id}
```

**响应示例**:

```json
{
  "ok": true
}
```

---

## 对话（鉴知）

### 发送消息

```http
POST /api/chat/messages?session_id=sess_xxx
Content-Type: application/json

{
  "text": "林凡的修为是什么境界？"
}
```

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 否 | 指定会话 ID（默认当前会话） |

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | 是 | 用户消息内容 |

**响应示例**:

```json
{
  "ok": true,
  "session_id": "sess_20260730_120000_a1b2"
}
```

**可能的错误**:

| HTTP 状态码 | `detail` | 说明 |
|-------------|----------|------|
| 400 | `"请先打开一个工作区"` | 未调用 `/api/books/open` |
| 403 | `{"code": "session_full", "session_id": "..."}` | 会话消息数已达上限 |
| 409 | `"Agent 正在运行中，请等待完成"` | 上一次 Agent 任务未完成 |

> **流式响应**: Agent 运行期间通过 [SSE 事件流](#sse-事件流) 推送 token。

---

### 响应确认请求

```http
POST /api/chat/confirm/{confirm_id}
Content-Type: application/json

{
  "action": "approve"
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action` | string | 是 | `approve`（允许）或 `reject`（拒绝） |
| 其他字段 | any | 否 | 透传给 Agent 的额外参数 |

> `confirm_id` 从 SSE 事件的 `confirm_request` 事件中获取。

---

### 压缩上下文

```http
POST /api/chat/compact?session_id=sess_xxx
```

⚠️ 压缩后无法还原，仅在上下文接近上限时使用。

---

### 获取上下文占用

```http
GET /api/chat/context
```

**响应示例**:

```json
{
  "ok": true,
  "message_count": 15,
  "input_tokens": 45000,
  "output_tokens": 12000,
  "total_tokens": 57000
}
```

---

### 清空对话历史

```http
POST /api/chat/clear?session_id=sess_xxx
```

保留会话元数据，重置 `message_count=0`。

**响应示例**:

```json
{
  "ok": true,
  "session_id": "sess_20260730_120000_a1b2"
}
```

---

## 知识库

### 列出类别

```http
GET /api/books/{book}/categories
```

**响应示例**:

```json
[
  {"name": "人物", "type": "wiki"},
  {"name": "势力", "type": "wiki"},
  {"name": "地图", "type": "wiki"}
]
```

---

### 列出 Wiki 词条

```http
GET /api/books/{book}/wiki?category=人物
```

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `category` | string | 否 | 按类别过滤 |

**响应示例**:

```json
[
  {
    "name": "林凡",
    "category": "人物",
    "summary": "主角，出身平凡..."
  }
]
```

---

### 获取 Wiki 词条详情

```http
GET /api/books/{book}/wiki/{name}
```

**响应示例**:

```json
{
  "name": "林凡",
  "category": "人物",
  "created_chapter": 1,
  "updated_chapter": 50,
  "keywords": "主角,穿越,修炼",
  "description": "出身平凡的少年...",
  "state": "活跃",
  "content": "完整词条内容...",
  "relations": "[\"青云宗\", \"玄天功\"]"
}
```

词条不存在时返回 `{}`。

---

### 列出规则

```http
GET /api/books/{book}/rules
```

**响应示例**:

```json
[
  {
    "id": 1,
    "name": "境界体系",
    "content": "炼气→筑基→金丹..."
  }
]
```

---

### 列出剧情卡片

```http
GET /api/books/{book}/plots
```

**响应示例**:

```json
[
  {
    "id": 1,
    "title": "青云宗入门",
    "content": "林凡拜入青云宗...",
    "chapters": "1-20",
    "ended": false
  }
]
```

---

## 会话管理

### 列出会话

```http
GET /api/books/{book}/sessions
```

**响应示例**:

```json
{
  "current_session_id": "sess_20260730_120000_a1b2",
  "sessions": [
    {
      "id": "sess_20260730_120000_a1b2",
      "name": "新会话",
      "archived": false,
      "created_at": "2026-07-30T12:00:00",
      "updated_at": "2026-07-30T14:30:00",
      "message_count": 15,
      "first_user_message": "林凡的修为...",
      "cap": 500
    }
  ]
}
```

---

### 创建会话

```http
POST /api/books/{book}/sessions
Content-Type: application/json

{
  "name": "新会话",
  "cap": 500
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 会话名称（默认"新会话"） |
| `cap` | int | 否 | 消息上限（默认 500） |

**响应示例**:

```json
{
  "session_id": "sess_20260730_150000_c3d4",
  "session": {
    "id": "sess_20260730_150000_c3d4",
    "name": "新会话",
    "created_at": "2026-07-30T15:00:00",
    "updated_at": "2026-07-30T15:00:00",
    "archived": false,
    "message_count": 0,
    "cap": 500,
    "compact_summary": "",
    "pending_confirm": null,
    "first_user_message": ""
  }
}
```

---

### 获取会话详情

```http
GET /api/books/{book}/sessions/{session_id}
```

**响应示例**:

```json
{
  "id": "sess_20260730_120000_a1b2",
  "name": "新会话",
  "created_at": "2026-07-30T12:00:00",
  "updated_at": "2026-07-30T14:30:00",
  "archived": false,
  "message_count": 15,
  "cap": 500,
  "compact_summary": "",
  "pending_confirm": null,
  "messages": [
    {
      "id": 1722312000000,
      "role": "user",
      "content": "林凡的修为...",
      "timestamp": 1722312000
    },
    {
      "id": 1722312001000,
      "role": "assistant",
      "content": "林凡目前处于...",
      "timestamp": 1722312001
    }
  ]
}
```

---

### 更新会话

```http
PATCH /api/books/{book}/sessions/{session_id}
Content-Type: application/json

{
  "name": "修改后的名称",
  "archived": false
}
```

**请求体** (所有字段可选):

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 新名称 |
| `archived` | bool | 是否归档 |

---

### 删除会话

```http
DELETE /api/books/{book}/sessions/{session_id}
```

**响应示例**:

```json
{
  "ok": true,
  "new_current": "sess_20260730_160000_e5f6"
}
```

---

### 激活会话

```http
POST /api/books/{book}/sessions/{session_id}/activate
```

激活后该会话成为当前会话，后续对话消息将写入此会话。

---

### 获取会话统计

```http
GET /api/books/{book}/sessions/{session_id}/stats
```

**响应示例**:

```json
{
  "total_input_tokens": 120000,
  "total_output_tokens": 35000,
  "total_messages": 15,
  "last_active": "2026-07-30T14:30:00",
  "model_usage": {
    "deepseek-v4-flash": {
      "input": 120000,
      "output": 35000
    }
  }
}
```

---

## 妙笔写作

### 启动妙笔工作流

```http
POST /api/muse/start
Content-Type: application/json

{
  "outline": "本章主要剧情：林凡突破筑基期...",
  "chapter_num": 51
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `outline` | string | 是 | 章节大纲内容 |
| `chapter_num` | int | 否 | 指定章节号（默认最新章节+1） |

**响应示例**:

```json
{
  "ok": true
}
```

**可能的错误**:

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 未打开工作区 |
| 409 | Agent 正在运行中 |

> 启动后通过 SSE 事件流推送写作进度。

---

### 获取妙笔运行状态

```http
GET /api/muse/status
```

**响应示例**:

```json
{
  "running": true
}
```

---

## 设置与模型

### 获取配置

```http
GET /api/settings
```

返回脱敏后的配置（`api_key` 显示为 `sk-x***yz` 格式）。

**响应示例**:

```json
{
  "models": [
    {
      "id": "model_001",
      "name": "DeepSeek V4 Flash",
      "provider": "deepseek",
      "api_key": "sk-x***yz",
      "model": "deepseek-v4-flash",
      "base_url": "https://api.deepseek.com",
      "output_max_tokens": 128000
    }
  ],
  "assignments": {
    "chat": "model_001",
    "extract": "model_001",
    "write": "model_001",
    "review": "model_001"
  },
  "workspace": {
    "dir": "../workingArea",
    "last": ""
  }
}
```

---

### 保存配置

```http
PUT /api/settings
Content-Type: application/json

{
  "models": [ ... ],
  "assignments": { ... },
  "workspace": { ... }
}
```

传入完整配置对象进行覆盖保存。

---

### 列出模型

```http
GET /api/settings/models
```

返回模型列表（脱敏）。

---

### 添加模型

```http
POST /api/settings/models
Content-Type: application/json

{
  "name": "DeepSeek V4 Flash",
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "api_key": "sk-your-key",
  "base_url": "https://api.deepseek.com",
  "output_max_tokens": 128000
}
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 显示名称 |
| `provider` | string | 否 | 提供商（默认 `deepseek`） |
| `model` | string | 是 | 模型标识 |
| `api_key` | string | 是 | API 密钥 |
| `base_url` | string | 是 | API 地址 |
| `output_max_tokens` | int | 否 | 最大输出 token（默认 128000） |

**响应示例**:

```json
{
  "ok": true,
  "id": "model_002"
}
```

---

### 更新模型

```http
PUT /api/settings/models/{model_id}
Content-Type: application/json

{
  "name": "新名称",
  "api_key": "sk-new-key"
}
```

支持部分更新（仅传需要修改的字段）。

---

### 删除模型

```http
DELETE /api/settings/models/{model_id}
```

---

### 获取模型分配

```http
GET /api/settings/assignments
```

**响应示例**:

```json
{
  "chat": "model_001",
  "extract": "model_001",
  "write": "model_001",
  "review": "model_001"
}
```

**角色说明**:

| 角色 | 用途 |
|------|------|
| `chat` | 鉴知对话 |
| `extract` | 知识提取 |
| `write` | 妙笔写作 |
| `review` | 审阅 |

---

### 设置模型分配

```http
PUT /api/settings/assignments/{role}
Content-Type: application/json

{
  "model_id": "model_002"
}
```

---

## Token 统计

### 获取 Token 统计汇总

```http
GET /api/stats/tokens?book=我的小说&agent=jianzhi&days=30
```

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `book` | string | 否 | 工作区名（默认当前工作区） |
| `agent` | string | 否 | 按 Agent 过滤（`jianzhi`/`muse`） |
| `days` | int | 否 | 统计天数（默认 30） |

**响应示例**:

```json
{
  "total_input": 1250000,
  "total_output": 380000,
  "total": 1630000,
  "call_count": 45
}
```

---

### 获取 Token 消耗历史

```http
GET /api/stats/tokens/history?limit=50&offset=0
```

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `limit` | int | 否 | 返回条数（默认 50） |
| `offset` | int | 否 | 偏移量（默认 0） |

**响应示例**:

```json
[
  {
    "id": 1,
    "created_at": "2026-07-30T14:30:00",
    "agent": "jianzhi",
    "book": "我的小说",
    "input_tokens": 3200,
    "output_tokens": 850,
    "model_name": "deepseek-v4-flash"
  }
]
```

---

## SSE 事件流

### 订阅事件流

```http
GET /api/events/stream
Accept: text/event-stream
```

建立 SSE 长连接，服务器持续推送 Agent 事件。

**使用方式** (浏览器):

```javascript
const es = new EventSource('/api/events/stream');
es.addEventListener('inkweaver', (e) => {
  const event = JSON.parse(e.data);
  console.log(event.type, event.data);
});
```

### 事件格式

```
event: inkweaver
data: {"type":"token","data":{"data":{"id":123,"text":"林"}},"source":"jianzhi","timestamp":1722312000.123}
```

### 事件类型

| 类型 | 说明 | 典型 `data` |
|------|------|-------------|
| `frontend_ready` | 连接就绪 | `{}` |
| `token` | LLM 流式 token | `{"data": {"id": 123, "text": "林"}}` |
| `output` | 完整输出段落 | `{"data": {"id": 123, "text": "林凡的修为是..."}}` |
| `thinking` | 思考开始 | `{}` |
| `thinking_done` | 思考完成 | `{"elapsed": 3.5}` |
| `reasoning` | 完整思考过程 | `{"text": "让我分析一下..."}` |
| `tool_call` | 工具调用 | `{"name": "read_wiki", "args": {...}}` |
| `tool_result` | 工具结果 | `{"name": "read_wiki", "result": {...}}` |
| `step_change` | 妙笔步骤切换 | `{"step": 2, "name": "知识准备"}` |
| `plan_ready` | 提取计划生成 | `{"plan": [...]}` |
| `confirm_request` | 需要用户确认 | `{"confirm_id": "uuid", "confirm_type": "plan", "payload": {...}}` |
| `confirm_resolved` | 确认已响应 | `{"confirm_id": "uuid"}` |
| `token_stats` | Token 用量更新 | `{"input": 100, "output": 50}` |
| `task_start` | 任务开始 | `{}` |
| `task_done` | 任务完成 | `{"session_id": "sess_xxx"}` |
| `error` | 错误 | `{"text": "错误描述"}` |
| `info` | 信息提示 | `{"text": "提示内容"}` |

### 前端就绪事件

连接建立后，服务器立即推送 `frontend_ready` 事件，表示可以开始接收后续事件。

---

## 常见报错

### HTTP 状态码

| 状态码 | 含义 | 常见场景 |
|--------|------|----------|
| 200 | 成功 | 正常响应 |
| 400 | 请求参数错误 | 未打开工作区、非法工作区名、文件路径无效 |
| 403 | 禁止访问 | 会话消息数已满 |
| 404 | 资源不存在 | 工作区/章节/草稿/模型不存在 |
| 409 | 冲突 | Agent 正在运行中 |
| 500 | 服务器内部错误 | 数据库操作失败、配置保存失败 |

### 错误详情格式

**简单错误**:

```json
{
  "detail": "工作区「xxx」不存在"
}
```

**结构化错误**:

```json
{
  "detail": {
    "code": "session_full",
    "session_id": "sess_20260730_120000_a1b2"
  }
}
```

### 常见错误信息对照

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `"请先打开一个工作区"` | 未调用 `/api/books/open` | 先打开工作区 |
| `"Agent 正在运行中，请等待完成"` | 上一次任务未完成 | 等待 `task_done` 事件或调用 `/api/muse/status` 检查 |
| `"会话消息数已达上限"` | `message_count >= cap` | 调用 `/api/chat/compact` 压缩或创建新会话 |
| `"工作区「xxx」不存在"` | 路径不存在 | 检查工作区名称拼写 |
| `"非法工作区名"` | 名称包含特殊字符 | 仅使用中文、字母、数字、下划线、连字符 |
| `"路径超出工作区范围"` | 路径遍历攻击防护 | 使用合法工作区名 |
| `"模型 xxx 不存在"` | 模型 ID 错误 | 先调用 `/api/settings/models` 获取有效 ID |

### Agent 运行时错误

Agent 运行错误通过 SSE `error` 事件推送，常见原因：

| 错误类型 | 说明 |
|----------|------|
| API 认证失败 | `api_key` 无效或过期 |
| API 请求参数错误 | 模型参数不兼容 |
| 网络连接失败 | 无法访问 API 地址 |
| 上下文溢出 | 对话过长未压缩 |

---

## 数据模型速查

### Model 配置

```json
{
  "id": "model_001",
  "name": "DeepSeek V4 Flash",
  "provider": "deepseek",
  "api_key": "sk-xxx",
  "model": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com",
  "output_max_tokens": 128000
}
```

### Session 结构

```json
{
  "id": "sess_20260730_120000_a1b2",
  "name": "新会话",
  "created_at": "2026-07-30T12:00:00",
  "updated_at": "2026-07-30T14:30:00",
  "archived": false,
  "message_count": 15,
  "cap": 500,
  "compact_summary": "",
  "pending_confirm": null,
  "first_user_message": "林凡的修为..."
}
```

### 完整配置结构

```json
{
  "models": [
    { "id": "model_001", "name": "...", "api_key": "sk-***", ... }
  ],
  "assignments": {
    "chat": "model_001",
    "extract": "model_001",
    "write": "model_001",
    "review": "model_001"
  },
  "workspace": {
    "dir": "../workingArea",
    "last": ""
  }
}
```

---

## 附录：快速调用示例

### cURL 示例

```bash
# 列出工作区
curl http://localhost:8000/api/books

# 打开工作区
curl -X POST http://localhost:8000/api/books/open \
  -H "Content-Type: application/json" \
  -d '{"name":"我的小说"}'

# 发送对话消息
curl -X POST "http://localhost:8000/api/chat/messages" \
  -H "Content-Type: application/json" \
  -d '{"text":"林凡的修为是什么？"}'

# 列出章节
curl http://localhost:8000/api/books/我的小说/chapters

# 列出 wiki 词条
curl http://localhost:8000/api/books/我的小说/wiki

# 启动妙笔
curl -X POST http://localhost:8000/api/muse/start \
  -H "Content-Type: application/json" \
  -d '{"outline":"本章剧情...","chapter_num":51}'
```

### 典型调用流程

```
1. GET  /api/books              → 获取工作区列表
2. POST /api/books/open         → 打开目标工作区
3. GET  /api/books/{book}/sessions → 获取会话列表
4. GET  /api/events/stream      → 订阅 SSE 接收流式响应（先于发消息建立）
5. POST /api/chat/messages      → 发送消息
6. POST /api/chat/confirm/{id}  → 响应确认请求（如有）
```

---

*文档生成时间: 2026-07-30*
