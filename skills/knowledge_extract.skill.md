---
name: knowledge_extract
description: 知识提取技能 — 从小说章节中提取知识，构建结构化 Wiki 知识库
---

# 知识提取 Skill

## 触发方式

- 指令触发：`/update`
- 自然语言触发：“帮我提取新章节的知识”、“更新一下 wiki”
- 重新提取触发：“重新提取 6-10 章”、“重新更新第 5 章的知识”

## 完整流程

### 范围指定
- 如果用户明确说了提取范围（如“提取第 5-10 章”），直接使用该范围
- 如果用户只说“提取知识”“更新 wiki”但**没指定范围**：
  1. 调用 chapter_list 查看章节列表（含 [已处理]/[未处理] 标记）
  2. 向用户确认计划：“检测到未提取的章节 [1-10]，是否按此范围提取？”
  3. 用户确认后执行

### 执行流程
```
1. chapter_list → 查看章节列表（含 [已处理]/[未处理] 标记）
2. **先查 Wiki，再读章节**：
   a. 先对所有涉及的关键词/实体名使用 wiki_list + read_wiki 查询现有词条
   b. **只有 wiki 无法解答时**（现有词条信息不足/未覆盖），才读取章节原文
   c. 禁止在已有 wiki 词条的情况下跳过 wiki 直接翻原文
3. 查询现有 wiki（wiki_list 获取列表 → read_wiki 按需深入）
4. 分析并制定计划（新增/修改哪些词条，分类规则参见下文）
5. 调用 submit_plan 提交计划（传递 plan_json 字符串）
   → 系统展示计划并等待用户 y/n 确认
   → 用户确认后自动切换至执行阶段（写权限开放）
   → 若被打回，根据理由修改后重新提交
6. 使用 batch_create_wiki / batch_edit_wiki 批量操作（或 new_wiki / edit_wiki 单个操作）
   - 每个词条需包含 category、name、content（正文含 [[wikilink]] 交叉引用）
   - **必须先调用 read_index(类别名) 获取 writing_guide，按规范结构撰写正文**
   - 字段质量要求：
     - description：30-80字，一句话概括词条核心身份（禁止只写名字/职位）
     - state：20-100字，当前状态快照（境界/位置/关系/动态），state_required 类别必填
     - content：≥300字，按 writing_guide 分段撰写，使用 [[wikilink]] 交叉引用
6. **收尾旧剧情卡片**：在创建**新**剧情卡片前，应先调用 `plot_list` 查看已有的未结束卡片，
   对其中章节范围已远落后于最新章节（差值 ≥ 10）的卡片，调用 `end_plot` 结束。
   这样可以避免剧情卡片越积越多永不收尾。
7. **审核**：调用 review_workflow 进入审核模式
   → 系统自动运行 lint 检查，注入债务清单到对话上下文
   → 根据 lint 结果逐项检查 wikilink 悬空、信息矛盾、描述/状态混淆、
     规则混入关系、state 缺失/简洁性、类别归属约束、文档篇幅、剧情卡片等
   → 注意 unended_plots 债务：lint 已自动检测，按提示用 `end_plot` 结束
8. **完成任务记录**：调用 finish_task 记录本次提取的章节区间和所有操作
   （自动校验存在性、写入 log.json、构建关系图）
```

## 规则文档与 Wiki 词条的区分

| 类型 | 存放位置 | 用途 | 示例 |
|------|---------|------|------|
| **规则文档** | `rules/` | 定义世界观的基础规则、体系、设定 | 境界体系、修炼体系、魔法规则、时间线 |
| **Wiki 词条** | `wiki/` | 记录具体的实体、人物、地点、事件 | 叶匀、叶家、赤云城、玄武大会 |

**分类原则**：凡是"定义世界如何运转的底层规则"用 `new_rule` 存入 `rules/`；凡是"故事中具体出现的人、物、地、事"用 `new_wiki` 存入 `wiki/`。

例如：「肉仙十重」是修炼境界体系，属于世界观规则，应写入 `rules/境界体系.md`，而非作为 wiki 词条。

## 初始类别创建

首次知识提取时，wiki 目录为空。流程如下：

1. LLM 分析章节内容，提出类别建议（如：人物、势力、地图、设定图鉴）
2. **创建类别前必须先调用 `category_design` 技能**，获取 PRD 定义的类别写作规范（index.md 模板、字段要求等）
3. 用户确认后，根据 `category_design` 的规范调用 `new_category` 创建类别和对应的 `index.md`
4. 后续提取复用已有类别

## 计划确认格式

调用 submit_plan 时，plan_json 需遵循以下结构，**每项必须包含对应字段**，否则会被标记警告：

```json
{
  "scope": "1-5",
  "new_category": [
    {"name": "人物", "reason": "故事出现多个关键人物需分类管理"}
  ],
  "new_wiki": [
    {"category": "人物", "name": "叶匀", "chapters": "1", "reason": "首次出场"}
  ],
  "edit_wiki": [
    {"category": "人物", "name": "张三", "chapters": "5", "reason": "境界从筑基→金丹"}
  ],
  "new_rule": [
    {"name": "境界体系", "reason": "第1章描述了完整的修炼境界划分"}
  ],
  "new_plot": [
    {"name": "天才陨落", "chapters": "1", "reason": "开篇核心事件"}
  ],
  "edit_plot": [
    {"name": "天才陨落", "chapters": "1-5", "reason": "补充后续发展"}
  ]
}
```

| 字段 | 所属项 | 说明 |
|------|--------|------|
| `scope` | 顶层 | 提取范围，纯数字格式如 `"1-5"` |
| `category` | new_wiki / edit_wiki | 词条所属类别 |
| `name` | 所有项 | 词条/规则/卡片名称 |
| `chapters` | new_wiki / edit_wiki / new_plot / edit_plot | 关联章节号 |
| `reason` | 所有项 | 为什么新增/修改，让用户理解必要性 |

每个字段的值**不能为空**，缺少必要字段会被系统标记警告。

## Wiki 优先 RAG 原则（重要）

**核心原则**：面对已有 wiki 词条的知识检索，必须先用 wiki 进行 RAG，而不是直接翻原文。

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `wiki_list <类别>` | 先查看该类别下有哪些已有词条 |
| 2 | `read_wiki <类别> <词条名>` | 读取相关词条的完整内容 |
| 3 | `check_wiki <词条名> <章节>` | 检查该词条在指定章节中是否出现 |
| 4 | 只有以上三步无法满足需求时 | 才用 `read_chapters` 读取章节原文 |

**禁止行为**：
- ❌ 跳过 wiki 直接 `read_chapters 1-17` 全文阅读
- ❌ 已有 wiki 词条的情况下，不查 wiki 就去翻原文
- ❌ 把 wiki 能解答的问题（如"叶匀什么修为"）变成章节阅读

## 自审检查项

进入 review_workflow 后，自动 lint 会生成债务清单。在此基础上进行语义审查：

| 检查项 | 说明 |
|--------|------|
| **YAML 结构完整性** | 手动检查 frontmatter 是否存在/重复、正文是否为空 |
| wikilink 悬空检查 | 检查 `[[wikilink]]` 是否指向已存在的词条（lint 已覆盖） |
| 规则文档检查 | 检查 rules/ 下的规则文档是否包含 `[[wikilink]]`，以及是否需要新增规则 |
| state 缺失检查 | 检查人物/势力类词条是否缺少 state 字段（类别 index.md 定义了是否需要 state） |
| **state 简洁性检查** | state 字段应简明扼要（建议 ≤100 字），不应堆叠剧情流水账 |
| **类别归属约束** | **禁止**在审核阶段修改类别归属——类别已在提取计划中经人工确认 |
| 信息矛盾检查 | 检查前后信息是否矛盾（如境界变化是否符合逻辑） |
| 描述/状态混淆检查 | 检查 description 和 state 是否混淆 |
| 关系遗漏检查 | 检查是否遗漏了重要关系（如人物归属势力、持有物品等） |
| 文档篇幅检查 | wiki/规则文档超过 1500 字时使用 `edit_doc` 压缩 |
| 剧情卡片 Wikilink 悬空检查 | 检查剧情卡片中的 `[[wikilink]]` 是否指向存在的词条 |
| 剧情区间越界检查 | 检查剧情卡片的 chapters 是否超出原文实际范围 |
| 未结束卡片收尾遗漏检查 | 对照最新章节判断是否应有收尾操作 |

## 注意事项

- 规则文档不参与关系系统，使用 new_rule / edit_rule 管理，不要用 edit_wiki
- **规则文档禁止包含 [[wikilink]]**，规则定义的是世界观底层规则，不与具体词条建立关系
- 设定图鉴类不需要 state 字段
- 所有 wiki 文档使用统一 frontmatter
- new_wiki 的 content 参数为必填，必须提供正文内容
- **new_plot 的 keywords 为必填**，应包含该卡片涉及的核心人物、地点、事件关键词（逗号分隔）
- **end_plot 的 end_notes 为必填**，简述该剧情线如何完结
- **新增统一编辑工具**：`edit_doc_text`（正文精确文本替换）/ `edit_doc_wikilink`（wikilink 定向替换）
  - 只需要改正文中一句话时，用 `edit_doc_text` 比 `edit_doc(content=新全文)` 省大量 token
  - Lint 债务修复时优先使用 `edit_doc_wikilink` 修复断链
  - `edit_doc_wikilink` 支持 `mode="unlink"` 取消链接：`[[肉仙]]` → `肉仙`，`[[肉仙|肉仙六重]]` → `肉仙六重`
    - 适用于被 rules/ 覆盖的概念（境界名、通用物品等不需要建词条的场景）
  - **Unlink 黑名单**：取消链接时加 `remember=true`（如 `edit_doc_wikilink(…, mode="unlink", remember=true)`），会将该目标记入 `unlink-blacklist.json`。后续 lint 运行到此断链自动跳过，不再报债务。适用于跨词条频繁出现的境界名、通用物品等高频误报。
- **知识提取完成后必须调用 review_workflow 进行审核**，审核通过后调用 finish_task 结束
- 审核时 lint 已自动运行并注入上下文，无需手动调用 lint 工具
- 审核阶段只读白名单内的章节/wiki/plot，使用 wiki_list / plot_list 可查看存在性但不可读内容

## 重新提取模式（re-extract）

当用户要求「重新提取 X-Y 章」「重新更新某几个章节的知识」时，使用重新提取模式。

### 与正常提取的差异

| 环节 | 正常提取 | 重新提取 |
|------|---------|----------|
| 规划阶段 | 只读 current_version | 自由读取所有历史版本（read_wiki(version=N)） |
| submit_plan | mode 缺省 "extract" | mode: "re-extract" + scope |
| 执行阶段 | 加载 current_version | 系统自动加载基础版本（≤ scope 最大章节的最近版本） |
| flush | 正常插入新版本 | 同章节覆盖 / 不同章节插入新版本 |

### 重新提取流程

```
1. 用户说“重新提取 6-10 章”
2. 规划阶段：
   a. 调用 read_wiki / read_plot 查看当前版本
   b. 可选：调用 read_wiki(version=N) 查看历史版本，了解变迁
   c. 读取章节原文（read_chapters 6-10）
   d. 制定计划（edit_wiki / edit_plot 为主）
3. submit_plan 时传 mode: "re-extract"，scope: "6-10"
4. 用户确认后，系统自动加载基础版本到缓存
5. 执行阶段：基于基础版本内容修改（而非 current_version）
6. review_workflow → finish_task（flush 时自动处理版本分叉）
```

### 计划 JSON 示例（重新提取）

```json
{
  "mode": "re-extract",
  "scope": "6-10",
  "edit_wiki": [
    {"category": "人物", "name": "叶匀", "chapters": "6-10", "reason": "重新提取后更新境界变化"}
  ],
  "edit_plot": [
    {"name": "天才陨落", "chapters": "6-10", "reason": "补充重新提取的剧情细节"}
  ]
}
```

## 词条命名约定

- **`词条名（别名）`** — 括号 `（）` 内为有效同义词/别名，两个都作为关键词匹配
  - 例：`叶寒（寒叔）`，搜索"叶寒"或"寒叔"均能匹配到该词条
- **`词条名【说明】`** — 括号 `【】` 内仅为消歧义说明，不作为关键词
  - 例：`叶家【紫玉大陆】`，只匹配"叶家"
- **`type` 字段必须用中文**，且与所在文件夹名一致
  - 例：人物类词条 → `type: 人物`（而非 `type: character`）
