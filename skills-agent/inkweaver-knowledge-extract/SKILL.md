---
name: inkweaver-knowledge-extract
description: >
  InkWeaver 知识提取技能。把新章节原文沉淀为结构化知识库（wiki 词条/rule 规则/
  plot 剧情卡片）。支持外部编排模式（你亲自推理 + kb 写工具落库，不依赖内置 api_key）
  与内部任务模式（extract_knowledge 子智能体）。要求已接入 inkweaver MCP Server。
  Use when: 用户导入了新章节、续写完成后、或说"提取知识/沉淀设定/更新知识库"。
---

# InkWeaver 知识提取流程（双模式剧本）

目标：读章节原文 → 抽取结构化知识（wiki 词条 / rule 规则 / plot 剧情卡片）→ 落库。

先调 `server_info` 看 `llm_ready` 选模式：
- `llm_ready=false` 或你想亲自把关质量 → **模式一：外部编排**（下文主线）
- `llm_ready=true` 且整批提取想省自己的 token → **模式二：内部任务**（见文末）

## 模式一：外部编排（你是提取引擎）

### 1. 定范围

- `chapter_status(workspace)`：找出「未处理」章节。
- 用户给了明确范围（如"提取 21-30 章"）就用；没给就取未处理章节起最多 10 章
  （质量与上下文成本平衡），并向用户确认该范围。

### 2. 读素材（wiki 优先原则）

1. 先 `kb_categories()` + `kb_list()` 浏览已有知识体系。
2. 与本章相关的已有词条先 `kb_show(name)` 读详情——**已有词条能回答的，
   禁止跳过它去翻原文**。
3. 再 `chapter_read("21-30")` 读章节原文。
4. 首次沉淀（知识库为空）时，先根据原文内容规划类别，用 `category_create`
   建好类别体系（如：人物/势力/地图/物品；人物、势力类 `has_state=true`），
   类别创建立即生效。

### 3. 推理抽取（可调度子智能体）

章节多时调度你的子智能体分工处理（如按章节分段并行抽取），你自己汇总。
抽取时遵守下列领域规范。

### 4. 产出计划 SPEC 并向用户确认

产出提取计划文档（SPEC），结构：

```json
{
  "scope": "21-30",
  "new_wiki":   [{"category": "人物", "name": "叶匀", "chapters": "21", "reason": "首次出场"}],
  "edit_wiki":  [{"category": "人物", "name": "张三", "chapters": "25", "reason": "境界从筑基→金丹"}],
  "new_rule":   [{"name": "境界体系", "reason": "第21章描述了完整的修炼境界划分"}],
  "new_plot":   [{"name": "天才陨落", "chapters": "21-25", "reason": "主线转折事件"}],
  "edit_plot":  [{"name": "旧卡片名", "chapters": "21-30", "reason": "剧情延续"}]
}
```

把计划摘要（各类数量 + 代表性条目）转述给用户确认后再执行；
用户打回则按意见修改计划。

### 5. 执行落库

按计划逐条调用写工具（都只暂存缓存）：

- 新词条：`kb_create(category, name, content, description, state, keywords, chapter)`
- 改词条：`kb_edit(name, content/description/state, chapter)`——先 `kb_show` 读旧全文再合并修改
- 新规则：`rule_create(name, content, keywords)`；改规则：`rule_edit`
- 新卡片：`plot_create(name, chapters, content, description, keywords)`；
  延续：`plot_edit(name, chapters=扩展范围, ...)`
- 收尾：`kb_list` 里已远落后于最新章节（差值 ≥10 章）的未结束剧情卡片，
  用 `plot_end(name, end_notes)` 结束

### 6. 提交与体检

```
kb_commit(chapters="21-30")   # 校验+版本快照落库+标记章节已处理+lint 自动修复
lint_run(workspace)           # 剩余债务用 lint_debt 查看，严重问题（断链/矛盾）汇报用户
```

commit 报「词条不存在」等校验错误：缓存仍保留，修复（补建/改名）后重新 commit。

### 7. 批量初始化（整本书首次沉淀）

循环直到 `chapter_status` 无未处理章节：定范围 → 读 → 抽 → 计划确认 →
落库 → commit。每批完成后向用户汇报一次增量。

## 领域规范（抽取推理必须遵守）

### rule 与 wiki 的分类原则

| 类型 | 工具 | 判据 |
|---|---|---|
| wiki 词条 | `kb_create` | 故事中具体出现的人、物、地、事 |
| rule 规则 | `rule_create` | 定义世界如何运转的底层规则 |

**规则信号清单**（出现即提取为 rule，不能只建实体词条）：
境界/等级/修炼体系、力量体系、势力组织结构、时间线/纪年、
世界底层设定（天道/灵气）、品阶/货币/资源体系、禁忌/限制规则。

判断口诀：「这是世界的运转规律」→ rule；「这是具体的人、物、地、事」→ wiki。
地点、势力、组织一律 wiki，不建 rule。同一规则分散多章时合并为一个文档，
后续章节用 `rule_edit` 补充。

### 字段质量要求

- `description`：30-80 字，一句话概括核心身份（禁止只写名字/职位）
- `state`：20-100 字当前状态快照（境界/位置/关系），`has_state` 类别必填，
  不堆剧情流水账
- `content`：≥300 字，按类别写作规范分段，用 `[[词条]]` 交叉引用
- `plot_create` 的 `keywords` 必填：涉及的核心人物/地点/事件关键词

### 数量约束

- 单批 `new_wiki` 不设上限：覆盖范围内所有重要实体（30-60 条常见），不要只建几条
- 单批 `new_plot` ≤4 张：只收录主线关键事件，避免堆砌次要事件
- `new_rule` 只建真正的体系性规则

### 命名约定

- `词条名（别名）`：圆括号内为有效同义词，两者都参与关键词匹配（如 `叶寒（寒叔）`）
- `词条名【说明】`：方括号内仅消歧义说明，不作为关键词（如 `叶家【紫玉大陆】`）

### wikilink 约定

- wiki/plot 正文用 `[[词条名]]` 互联（commit 时自动解析为关系）
- rule 文档禁止包含 `[[wikilink]]`（不参与关系系统）
- 链接目标必须是已存在或本批将建的词条，否则 lint 报断链债务

### 自审检查项（commit 前过一遍）

| 检查项 | 说明 |
|---|---|
| 规则遗漏 | 正文反复出现的体系性设定必须有对应 rule 文档 |
| wikilink 悬空 | `[[链接]]` 目标存在（本批新建的也算） |
| state 缺失/简洁 | 有状态实体词条的 state 存在且 ≤100 字 |
| 描述/状态混淆 | description 是身份概括，state 是当前快照 |
| 信息矛盾 | 境界变化等前后逻辑一致 |
| 篇幅 | 单文档正文 >1500 字时考虑压缩 |
| 卡片收尾 | 远落后的旧卡片是否该 plot_end |

## 模式二：内部任务（extract_knowledge）

`llm_ready=true` 时的捷径——认知工作由内置鉴知子智能体完成：

1. `extract_knowledge(chapters="", auto_approve=false, workspace=...)`
   - `chapters` 空=自动从未处理章节起取最多 10 章
2. `task_wait` 返回 `awaiting_confirmation` 时，读 `pending_confirm.payload`
   （new_wiki/edit_wiki/new_rule/new_plot 计划），转述给用户，
   `task_confirm(task_id, action="approve"|"reject", reason=...)` 响应
3. 继续 `task_wait` → `task_result` 看执行摘要 → `lint_run` 体检
4. 用户明确说"全自动/不用确认"时用 `auto_approve=true`
5. `confirm_type=forced_debt`（高频关键实体审核）：展示实体清单，
   `task_confirm(action="approve_all")` 全过或 `reject+rejected_indices` 部分拒绝
6. 批量初始化：循环 `chapter_status` → `extract_knowledge()` → 处理确认，
   直到无未处理章节
