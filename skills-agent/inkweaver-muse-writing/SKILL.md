---
name: inkweaver-muse-writing
description: >
  InkWeaver 妙笔写作技能。按大纲续写小说章节：自动检索先验知识与前情提要、
  起草、审阅评分、循环修订直至通过。要求已接入 inkweaver MCP Server。
  Use when: 用户要求"写一章/续写/按大纲创作"，且目标是 InkWeaver 管理的小说。
---

# InkWeaver 妙笔写作流程

妙笔是四步状态机：**大纲输入 → 知识准备（先验知识+前情提要）→ 写作 → 审阅循环**。
全程由内部子智能体驱动（写作 Writer + 审阅 Reviewer 双角色），外部只需编排。

## 标准流程

1. **确认章节锚点**：`chapter_status(workspace)` — 目标章通常是最新章节+1
   （也可显式传 chapter 参数，知识版本会卡控到该章之前，防止剧透未来设定）。
2. **准备大纲**：与用户敲定本章大纲，建议包含：
   - 剧情要点（起因/经过/转折/收尾钩子）
   - 出场人物与各自动机
   - 要回收的伏笔、要埋设的新伏笔
   - 目标字数预期（默认正文 3000~4000 字）
3. **启动写作**：
   ```
   muse_write(outline=大纲文本, chapter=0, auto_approve=true, workspace=...)
   ```
4. **等待**：`task_wait(task_id, timeout=1500)`。长任务（5~20 分钟），
   中途可 `task_status` 看 `step`（1-4）与 progress_tail 向用户汇报进展。
5. **交付**：`task_result(task_id)` —
   - `result.final_text`：定稿正文
   - `result.final_review`：最终审阅意见（issues 列表，等级：严重/重要/一般/可优化）
   - `result.task_dir`：产物目录（大纲/先验知识/前情提要/各轮草稿/审阅记录）
   - `result.target_chapter`：写入的章节号
6. 向用户展示定稿与审阅摘要；产物已自动落盘，无需再写文件。

## 半自动模式（关键节点人工把关）

`auto_approve=false` 时，妙笔在关键节点（如先验知识确认、审阅打回决策）
挂起为 `awaiting_confirmation`：读 `pending_confirm.payload` 转述给用户，
用 `task_confirm` 响应（approve / reject+reason）。

## 失败与重试

- `status=error`：读 `error` 字段汇报；常见为 LLM 配额/网络问题，可重试。
- `status=cancelled`：用户通过 `task_cancel` 终止。
- 审阅多轮未通过也会正常结束（final_review 中带残留 issues），把问题交给用户决策。

## 注意

- 大纲质量决定成稿质量：含糊的大纲会得到平庸的章节，先和用户把大纲聊清楚。
- 不要在 muse_write 运行期间对同一本书发起第二个 muse_write（串行约束）。
- 写作完成后如需沉淀新知识，接 `inkweaver-knowledge-extract` 技能。
