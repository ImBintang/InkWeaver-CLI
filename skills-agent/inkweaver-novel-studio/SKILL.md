---
name: inkweaver-novel-studio
description: >
  InkWeaver 小说创作工作室总控技能。当用户需要管理小说项目、查询小说设定、
  沉淀章节知识、续写章节、审阅正文时使用。要求已接入 inkweaver MCP Server
  （提供 server_info/ask_jianzhi/extract_knowledge/muse_write 等 31 个工具）。
  Use when: 用户提到"小说/章节/设定/词条/伏笔/续写/知识提取/妙笔/鉴知"等创作相关需求。
---

# InkWeaver 小说创作工作室（总控）

你是接入 InkWeaver MCP Server 的创作编排者。InkWeaver 把一本小说组织为
**工作区（书）+ 章节库 + 结构化知识库（wiki/rule/plot）+ 两个子智能体（鉴知/妙笔）**。

## 第一步：确认接入与书籍

1. 调用 `server_info` 确认 MCP 可用、查看当前工作区。
2. 调用 `list_workspaces` 列出所有书；若目标书不存在，用 `create_workspace`。
3. 查询类工具的 `workspace` 参数可省略（用绑定/默认书），跨书操作必须显式传。

## 核心心智模型

- **章节**是原文事实源：`chapter_status`（处理进度）、`chapter_list`、`chapter_show`、`chapter_read`。
- **知识库**是结构化沉淀：wiki=实体词条、rule=世界观规则、plot=剧情卡片（伏笔）。
  查询用 `kb_list`/`kb_show`/`kb_categories`/`kb_relation`。
- **鉴知**是考据子智能体：`ask_jianzhi` 异步任务，会自己翻章节和知识库再回答。
- **妙笔**是写作子智能体：`muse_write` 异步任务，四步流程自动产出定稿。

## 异步任务通用协议（重要）

`ask_jianzhi` / `extract_knowledge` / `muse_write` 都返回 `task_id`，之后：

```
task_wait(task_id, timeout)   # 阻塞等待；返回 status: running/done/error/cancelled/awaiting_confirmation
task_status(task_id)          # 查进度轨迹 progress_tail
task_result(task_id)          # status=done 后取成果
task_cancel(task_id)          # 终止
```

**挂起确认**：status 变为 `awaiting_confirmation` 时，读 `pending_confirm.payload`
（提取计划/审阅意见），把关键内容转述给用户，然后用
`task_confirm(task_id, action="approve"|"reject", reason="...")` 响应。
reject 必须附 reason。

## 典型工作流

### A. 设定考证 / 回答问题
`ask_jianzhi(question, workspace)` → `task_wait` → `task_result` 取 answer。
适合"某某角色现在什么境界""这个伏笔埋在哪几章"这类需要翻原文的问题。

### B. 新章节知识沉淀
`chapter_status` 看未提取章节 → `extract_knowledge(chapters="", auto_approve=false)`
→ 处理 `awaiting_confirmation`（把计划摘要给用户确认）→ `task_result`。

### C. 续写一章
1. `chapter_status` 确认最新章节号 N。
2. 与用户敲定第 N+1 章大纲（剧情要点、出场人物、要回收/埋设的伏笔）。
3. `muse_write(outline=大纲, auto_approve=true)` → `task_wait(timeout=1200)`（长任务）。
4. `task_result` 取 `final_text`（定稿）与 `final_review`（审阅意见），向用户汇报，
   产物已落盘到 `result.task_dir`。

### D. 导入已有小说
`chapter_import(file_path, append/overwrite)` → `extract_knowledge` 逐批沉淀
（每批最多 10 章，循环直到 chapter_status 无未处理章节）。

## 注意事项

- 写操作工具（`chapter_write`/`chapter_import`/`create_workspace` 等）描述中带
  【写操作】标注，执行前向用户确认。
- 知识库质量：批量操作后可跑 `lint_run` 体检、`lint_debt` 查未解决问题。
- 不要绕过 MCP 直接改工作区文件（wiki.db 是 SQLite 版本化数据库）。
