---
name: inkweaver-muse-writing
description: >
  InkWeaver 妙笔写作技能。按大纲续写小说章节。支持外部编排模式（你亲自组织
  知识准备→起草→审阅→修订，用 chapter_write 落库，不依赖内置 api_key）与
  内部任务模式（muse_write 四步写作子智能体）。要求已接入 inkweaver MCP Server。
  Use when: 用户要求"写一章/续写/按大纲创作"，且目标是 InkWeaver 管理的小说。
---

# InkWeaver 章节写作流程（双模式剧本）

目标：按大纲产出一章定稿（正文 3000~4000 字）并落库为目标章节。

先调 `server_info` 看 `llm_ready` 选模式：
- `llm_ready=false` 或你想亲自把控文风质量 → **模式一：外部编排**（下文主线）
- `llm_ready=true` 且想一键跑完四步流水线 → **模式二：内部任务**（见文末）

## 模式一：外部编排（你是主笔）

### 1. 确认章节锚点与大纲

- `chapter_status(workspace)`：最新章节号 N，目标章通常 N+1。
- 与用户敲定第 N+1 章大纲（大纲质量决定成稿质量，先聊清楚再动笔）：
  - 剧情要点（起因/经过/转折/收尾钩子）
  - 出场人物与各自动机
  - 要回收的伏笔、要埋设的新伏笔
  - 目标字数（默认 3000~4000 字）

### 2. 知识准备（防剧透、防设定矛盾）

- `kb_show`/`kb_list` 查出场人物词条与相关规则（境界/能力边界必须合规）
- `kb_memory` 读作者偏好/文风记忆
- `chapter_read` 读最近 1~3 章前情，衔接语感与悬念
- 涉及伏笔：`kb_list`（plot 类）查埋设状态，明确本章回收哪些、埋哪些

### 3. 起草（可调度子智能体）

可以自己写，也可调度子智能体起草（把大纲 + 知识准备材料 + 前情片段交给它）。
写作要求：
- 正文不含章节标题行（标题由系统维护）
- 人物言行符合词条设定；境界/能力不越规则文档
- 章末留钩子；伏笔按大纲回收/埋设

### 4. 审阅与修订循环

调度一个独立子智能体（或切换审稿视角）对草稿评分，检查：
设定矛盾、人物 OOC、伏笔遗漏、节奏、字数达标。
按意见修订，通常 1~3 轮；把关键审阅意见与用户同步。

### 5. 落库与收尾

```
chapter_write(num=N+1, content=定稿正文)   # 【写操作】执行前向用户确认
```

落库后如需沉淀新设定：接 inkweaver-knowledge-extract 技能
（外部编排模式提取本章新知识 → kb_commit）。

## 模式二：内部任务（muse_write）

妙笔是四步状态机：**大纲输入 → 知识准备（先验知识+前情提要）→ 写作 → 审阅循环**，
由内置 Writer/Reviewer 双角色驱动，你只做编排：

1. `muse_write(outline=大纲文本, chapter=0, auto_approve=true, workspace=...)`
   - chapter=0 自动取最新章节+1；显式传章号时知识版本卡控到该章之前防剧透
2. `task_wait(task_id, timeout=1500)`——长任务（5~20 分钟），
   中途 `task_status` 看 `step`（1-4）与 progress_tail 向用户汇报进展
3. `task_result(task_id)`：
   - `result.final_text` 定稿正文
   - `result.final_review` 最终审阅意见（issues 按严重/重要/一般/可优化分级）
   - `result.task_dir` 产物目录（大纲/先验知识/前情提要/各轮草稿/审阅记录）
   - `result.target_chapter` 写入章节号
4. 向用户展示定稿与审阅摘要；产物已自动落盘

**半自动模式**：`auto_approve=false` 时关键节点挂起 `awaiting_confirmation`，
读 `pending_confirm.payload` 转述用户，`task_confirm` 响应（approve / reject+reason）。

**失败与重试**：
- `status=error`：读 `error` 字段汇报；常见为 LLM 配额/网络问题，可重试
- `status=cancelled`：用户 `task_cancel` 终止
- 审阅多轮未通过也会正常结束（final_review 带残留 issues），交给用户决策

## 通用注意

- 同一本书同一时刻只跑一个写作任务（串行约束）
- 含糊的大纲会得到平庸的章节：先把大纲聊清楚
- 模式一草稿未落库前可随时推翻重写；`chapter_write` 会覆盖目标章节正文
