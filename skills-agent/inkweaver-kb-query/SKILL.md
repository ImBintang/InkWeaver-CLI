---
name: inkweaver-kb-query
description: >
  InkWeaver 小说知识库查询技能。查询小说设定、人物词条、世界观规则、剧情伏笔卡片、
  词条关联与章节原文。要求已接入 inkweaver MCP Server。
  Use when: 用户询问小说设定/人物/规则/伏笔/某章内容，且只需检索不需推理时优先用本技能
  （比 ask_jianzhi 更快、零内置 LLM 成本，也不要求 server_info.llm_ready）。
---

# InkWeaver 知识库查询

通过 inkweaver MCP Server 的只读工具直接检索，全部同步返回、无 LLM 开销。
属于外部编排模式的一部分：检索后如需推理回答，由你自己完成；
如需写入/修改知识，改用 kb 写工具组（见文末）。

## 检索路径选择

| 需求 | 工具 |
|---|---|
| 知道条目名，看详情 | `kb_show(name)` — 自动遍历 wiki/plot/rule |
| 浏览有哪些条目 | `kb_list(type_filter, category)` |
| 看类别体系 | `kb_categories()` |
| 词条之间的关系 | `kb_relation(name)` |
| 作者偏好/文风记忆 | `kb_memory(category)` |
| 某章原文 | `chapter_show(num)` |
| 多章原文（范围） | `chapter_read(spec)` — spec 如 "1-3"、"1,3,5" |
| 章节清单/提取进度 | `chapter_list(n)` / `chapter_status()` |
| 知识库健康问题 | `lint_debt()` |

## 三类知识的语义

- **wiki**：实体词条（人物/地点/物品/功法…），按类别组织，带 frontmatter
  （首次出场章、重要性等级等）+ wikilink 互联。
- **rule**：世界观硬规则（力量体系、势力格局），设定考据的最高权威。
- **plot**：剧情卡片，记录关键事件与**伏笔**（埋设章节/回收章节/状态）。

## 注意

- 所有工具可传 `workspace` 指定书；缺省用 server 绑定书
  （`server_info` 可查当前绑定）。
- 查不到 ≠ 不存在：条目名可能是别名，改用 `kb_list` 浏览或 `ask_jianzhi` 让
  鉴知子智能体翻原文考证（后者需 server_info.llm_ready=true；不可用时
  自己用 chapter_read 翻原文）。
- 条目内容含 wikilink `[[词条]]`，需要展开时对被链接词条再次 `kb_show`。

## 检索之后要写入？

本技能只覆盖只读检索。新建/修改词条、规则、剧情卡片用 kb 写工具组：
`kb_create`/`kb_edit`/`rule_create`/`rule_edit`/`plot_create`/`plot_edit`/`plot_end`，
写完必须 `kb_commit(chapters)` 提交落库（完整剧本见 inkweaver-knowledge-extract 技能）。
