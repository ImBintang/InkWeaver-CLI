# InkWeaver MCP 工具使用指南（LLM 编排者手册）

本手册面向接入 inkweaver MCP Server 的编排 LLM。40 个工具分四组，
配合「双模式」使用：**外部编排模式**（你自己推理 + 工具落库，不需要内置 api_key）
与**内部任务模式**（把认知工作交给 InkWeaver 内置子智能体，需要内置 api_key）。

第一步永远是 `server_info`：看 `llm_ready` 与 `llm_hint` 决定走哪种模式。

## 一、工具全景

### 1. 只读查询（同步，无副作用，两种模式都可用）

| 工具 | 用途 |
|---|---|
| `server_info` | 服务器信息 + `llm_ready`（内置 LLM 是否可用） |
| `list_workspaces` | 列出所有书 |
| `chapter_list(n)` / `chapter_status` | 章节清单 / 处理进度（含已提取标记） |
| `chapter_show(num)` / `chapter_read(spec)` | 单章全文 / 按范围批量读原文（spec 如 "1-3"、"1,3,5"） |
| `kb_categories` / `kb_list` / `kb_show` | 类别体系 / 条目浏览 / 条目详情（自动遍历 wiki/plot/rule） |
| `kb_relation(name)` / `kb_memory(category)` | 词条关联 / 作者偏好与文风记忆 |
| `lint_run` / `lint_debt` | 知识库体检 / 未解决问题清单 |
| `token_stats` / `list_skills` / `read_skill` | 用量统计 / 内部技能清单（一般不用） |

### 2. 通用写操作（同步，描述带【写操作】标注，执行前向用户确认）

| 工具 | 用途 |
|---|---|
| `create_workspace(name)` | 新建书 |
| `chapter_import(file_path, append, overwrite)` | 导入 txt（高危：overwrite 会清空重导） |
| `chapter_write(num, content)` | 写入/覆盖章节正文（正文不含标题行） |
| `chapter_export(overwrite)` | 合并导出整本书 |
| `memory_write(content, category)` / `memory_forget(id)` | 作者记忆维护 |

### 3. 知识写工具组（外部编排模式核心，同步）

| 工具 | 用途 |
|---|---|
| `category_create(name, description, writing_guide, has_state)` | 建词条类别（立即生效） |
| `kb_create(category, name, content, description, state, keywords, chapter)` | 新建 wiki 词条 |
| `kb_edit(name, content/description/state/keywords, chapter)` | 编辑词条（未传字段不变） |
| `rule_create(name, content, keywords)` / `rule_edit` | 世界观规则（境界/力量体系等） |
| `plot_create(name, chapters, content, description, keywords)` / `plot_edit` | 剧情卡片/伏笔 |
| `plot_end(name, end_notes)` | 结束剧情卡片（伏笔已收尾） |
| `kb_commit(chapters)` | **统一提交**：校验+版本快照落库+标记章节已处理+lint |

**持久化契约（务必遵守）**：
- `kb_create`/`kb_edit`/`rule_*`/`plot_*`/`plot_end` 只暂存缓存（对 kb_show 等
  读工具已可见），**未 commit 前不算真正入库**。
- 一批写入完成后必须调用一次 `kb_commit(chapters)`。chapters 决定版本快照
  章节号与「章节已处理」标记（如 "11-20"）。
- commit 失败（如「词条不存在」）缓存保留：修复后重新 commit。
- 不要绕过 MCP 直接改工作区文件（wiki.db 是版本化 SQLite 数据库）。

### 4. 子智能体任务（异步，依赖内置 api_key，先查 llm_ready）

| 工具 | 用途 |
|---|---|
| `ask_jianzhi(question)` | 鉴知问答：设定考证/一致性核查（30 秒~3 分钟） |
| `extract_knowledge(chapters, auto_approve)` | 鉴知知识提取（每 10 章 3~10 分钟） |
| `muse_write(outline, chapter, auto_approve)` | 妙笔四步写作（5~20 分钟） |
| `task_wait(task_id, timeout)` | 阻塞等待；状态变化/挂起确认会提前返回 |
| `task_status(task_id)` | 查进度轨迹 progress_tail |
| `task_result(task_id)` | status=done 后取成果 |
| `task_confirm(task_id, action, reason)` | 响应 awaiting_confirmation |
| `task_cancel(task_id)` / `task_list` | 取消 / 列出所有任务 |

**异步任务协议**：
```
启动工具 → task_id
  → task_wait(timeout)          # status: running/done/error/cancelled/awaiting_confirmation
     ├─ awaiting_confirmation → 读 pending_confirm.payload → 转述用户 →
     │     task_confirm(action="approve" | "reject"(必填 reason) | "approve_all")
     │     → 继续 task_wait
     ├─ running 且 wait_timeout=true → 再次 task_wait 续等（任务不会被杀）
     ├─ done → task_result 取成果
     └─ error/cancelled → 读 error/message 字段汇报
```

## 二、双模式选择指引

| 场景 | 推荐模式 |
|---|---|
| `llm_ready=false`（key 缺失/占位符） | 只能外部编排：读原文 → 你推理 → kb 写工具 + kb_commit |
| 少量词条修订、精细把关质量 | 外部编排 |
| 整批知识提取（10 章起）、整章四步写作 | 内部任务（省你的上下文，长流水线稳定） |
| 设定问答 | llm_ready=true 用 ask_jianzhi；否则 kb_*/chapter_* 自行检索 |
| 续写后沉淀新设定 | 混合：muse_write 写完 → 外部编排提取（或再跑一轮 extract_knowledge） |

约束：同一本书同一时刻只跑一个写任务（子智能体任务与手工写操作都算）。

## 三、常见错误排查表

| 现象 | 原因与处理 |
|---|---|
| `api_key 含非 ASCII 字符（疑似占位符）` | config.yaml 里是中文占位符，配置真实 key；或用外部编排模式绕过 |
| `api_key 为空/疑似占位符` | 同上 |
| `API 认证失败，请检查 config.yaml 中的 key` | key 无效/过期，去服务商更换 |
| 工作区不存在 | 先 `list_workspaces`，没有就 `create_workspace` |
| `没有待提交的知识变更（缓存为空）` | kb_commit 前没有调用任何 kb 写工具 |
| commit 报 `wiki 词条「X」不存在` | 写入与声明不一致；补建或改名后重新 kb_commit（缓存仍在） |
| `类别「X」不存在，无法创建词条` | 先 `category_create` 或换已有类别 |
| task 卡在 awaiting_confirmation | 读 pending_confirm.payload 并 task_confirm，任务不会自己往下走 |
| muse_write 报 outline 为空 | 先与用户敲定大纲再启动 |
| 章节导入报「已有 N 章」 | 传 append=true 增量，或 overwrite=true 确认清空重导 |

## 四、工作流速查

- **了解一本书**：`list_workspaces` → `chapter_status` → `kb_categories` → `kb_list`
- **外部编排沉淀知识**：`chapter_read` → 推理抽取（可派子智能体）→ 计划给用户确认 →
  `kb_create`/`rule_create`/`plot_create` 批量写入 → `kb_commit` → `lint_run`
- **外部编排续写**：敲定大纲 → kb_*/chapter_read 知识准备 → 起草（可派子智能体）→
  独立视角审阅修订 → `chapter_write` 落库 → 沉淀新设定
- **内置 LLM 沉淀知识**：`extract_knowledge(auto_approve=false)` → 处理确认 → `task_result`
- **内置 LLM 续写**：`muse_write(outline)` → `task_wait`(长) → `task_result`
