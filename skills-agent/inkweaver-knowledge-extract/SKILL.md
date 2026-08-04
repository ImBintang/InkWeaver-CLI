---
name: inkweaver-knowledge-extract
description: >
  InkWeaver 知识提取技能。把新章节原文沉淀为结构化知识库（wiki 词条/rule 规则/
  plot 剧情卡片），含提取计划的审核确认流程。要求已接入 inkweaver MCP Server。
  Use when: 用户导入了新章节、续写完成后、或说"提取知识/沉淀设定/更新知识库"。
---

# InkWeaver 知识提取流程

由鉴知子智能体执行：读章节 → 生成提取计划（新增/修改哪些词条、规则、剧情卡片）
→ 确认 → 落库并跑 Lint 体检。

## 标准流程

1. **看进度**：`chapter_status(workspace)` — 找出"未提取"章节。
2. **启动提取**：
   ```
   extract_knowledge(chapters="", auto_approve=false, workspace=...)
   ```
   - `chapters` 空 = 自动从未处理章节起取最多 10 章（推荐）
   - 也可显式 `"21-30"`；单次上限建议 10 章（质量与成本平衡）
3. **处理确认**（auto_approve=false 时）：
   - `task_wait` 返回 `status=awaiting_confirmation`
   - 读 `pending_confirm.payload`：包含 new_wiki/edit_wiki/new_rule/new_plot 等计划
   - 向用户转述计划摘要，询问是否执行
   - `task_confirm(task_id, action="approve")` 或 `task_confirm(task_id, action="reject", reason="...")`
4. **等待完成**：继续 `task_wait` → `task_result` 查看执行摘要。
5. **质量体检**：`lint_run(workspace)` 跑一遍，有严重问题（断链/状态缺失）时汇报用户。

## 全自动模式

用户明确说"全自动/不用确认"时用 `auto_approve=true`，计划自动批准，
只需 `task_wait` 到底。

## 强制债务审核（forced_debt）

提取中若出现高频关键实体审核请求（confirm_type=forced_debt），payload 含
实体清单（等级/提及数/覆盖章节）。向用户展示后用
`task_confirm(action="approve_all")` 全过，或
`task_confirm(action="reject", rejected_indices=[2], reason="...")` 部分拒绝。

## 批量初始化（整本书首次沉淀）

循环执行直到无未处理章节：`chapter_status` → `extract_knowledge()`（自动范围）
→ 处理确认 → 下一批。每批完成后可汇报一次增量。
