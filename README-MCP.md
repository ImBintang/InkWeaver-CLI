# InkWeaver-CLI MCP Server 接入指南（MCP 版 README）

> **版本**: v7.1.0 ｜ 通路：**MCP Server** ｜ 其他通路：[CLI](README-CLI.md) · [HTTP API](README-API.md)

InkWeaver MCP Server 把墨笔的全部能力（知识库查询、章节管理、知识写入、
鉴知问答、知识提取、妙笔写作）以 **Model Context Protocol** 工具的形式暴露，
任何支持 MCP 的 Agent 应用（Qoder、Claude Desktop、Cursor 等）都能即插即用。
支持双模式：**外部编排模式**（宿主编排 LLM 自己推理，经知识写工具落库，
不依赖内置 api_key）与**内部任务模式**（鉴知/妙笔子智能体代劳，需内置 api_key）。

---

## 目录

- [快速接入（Qoder）](#快速接入qoder)
- [其他客户端接入](#其他客户端接入)
- [启动参数](#启动参数)
- [工具清单（44 个）](#工具清单44-个)
- [知识写工具与持久化契约](#知识写工具与持久化契约)
- [异步任务协议](#异步任务协议)
- [确认机制](#确认机制)
- [Agent Skills 技能包](#agent-skills-技能包)
- [返回约定与错误处理](#返回约定与错误处理)
- [测试与验收](#测试与验收)

---

## 快速接入（Qoder）

> v7.2.0 起提供一键安装脚本，自动完成：检测 Python → 创建 .venv → 安装依赖与本包 →
> 写入宿主 MCP 配置（自动注入 .venv 内 Python 绝对路径与项目根 cwd）→ 可选握手验证：
>
> - Windows：`./install.ps1 -Host qoder -Test`
> - Linux/macOS：`./install.sh --host qoder --test`
>
> 手动接入步骤如下（或直接用上面的脚本）：

1. 打开 Qoder 的 MCP 配置文件 `%APPDATA%\QoderCN\SharedClientCache\mcp.json`
   （或在 Qoder 设置页的 MCP 管理中新增），注册：

```json
{
  "mcpServers": {
    "inkweaver": {
      "command": "C:\\ProgramData\\anaconda3\\python.exe",
      "args": ["main.py", "mcp"],
      "cwd": "d:\\Code\\InkWeaver-CLI-workspace\\InkWeaver-CLI"
    }
  }
}
```

   - `command`：本机 Python 解释器绝对路径（需已安装 `mcp>=1.9,<2.0` 与项目依赖）
   - `cwd`：**必填**，InkWeaver-CLI 目录（否则进程找不到 `main.py`/配置文件，
     客户端会报 `transport error: context deadline exceeded`）；
     或将 `args` 中的 `main.py` 改为绝对路径作为双保险

2. 重启 Qoder（或在 MCP 设置页启用 `inkweaver`），工具列表出现 44 个
   `inkweaver` 前缀工具即接入成功。

3. 直接在对话中使用，例如：
   - "列出我的小说工作区" → `list_workspaces`
   - "《补天纪》里一共有多少章？" → `chapter_list` / `ask_jianzhi`
   - "给《补天纪》按这个大纲续写一章" → `muse_write` + 任务轮询

> 绑定默认书（可选）：`args` 改为 `["main.py", "mcp", "-w", "补天纪"]`，
> 之后所有工具缺省作用于该书，无需每次传 `workspace`。

---

## 其他客户端接入

### Claude Desktop

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "inkweaver": {
      "command": "<python 解释器绝对路径>",
      "args": ["main.py", "mcp"],
      "cwd": "<InkWeaver-CLI 目录绝对路径>"
    }
  }
}
```

### Cursor / 其他 MCP 客户端

同上标准 `mcpServers` 格式，stdio 传输。

### HTTP 模式（远程 / 多客户端）

```bash
inkweaver mcp -t streamable-http --host 127.0.0.1 --port 8100
```

客户端连接 `http://127.0.0.1:8100/mcp`（streamable-http 协议）。

---

## 启动参数

```bash
inkweaver mcp [-w 工作区] [-t stdio|streamable-http] [--host H] [--port P]
```

| Flag | 短写 | 默认 | 说明 |
|------|------|------|------|
| `--workspace` | `-w` | 空 | 绑定默认工作区（各工具的 `workspace` 参数可覆盖） |
| `--transport` | `-t` | `stdio` | 传输方式：stdio / streamable-http |
| `--host` | | `127.0.0.1` | HTTP 模式监听地址 |
| `--port` | | `8100` | HTTP 模式监听端口 |

工作区解析优先级：`工具参数 workspace` > `启动绑定 -w` > `config.workspace.last`。

---

## 工具清单（44 个）

### 第 1 层：只读查询（16 个，同步，零 LLM 成本）

| 工具 | 说明 |
|------|------|
| `server_info` | 服务器版本、绑定工作区、**llm_ready**（内置 LLM 可用性，决定子智能体任务能否使用） |
| `list_workspaces` | 列出所有书籍工作区 |
| `chapter_list(n)` | 章节清单（默认最新 50 章） |
| `chapter_status` | 章节处理状态（已导入/已提取进度） |
| `chapter_show(num)` | 查看单章原文 |
| `chapter_read(spec)` | 批量读章（spec 如 `"1-3"`、`"1,3,5"`） |
| `kb_categories` | 类别体系 |
| `kb_list(type_filter, category)` | 列出知识条目（wiki/rule/plot） |
| `kb_show(name)` | 条目详情（wiki → rule → plot 自动回退） |
| `kb_relation(name)` | 词条关联（双链关系） |
| `kb_memory(category)` | 作者偏好/风格记忆 |
| `lint_run` | 知识库体检（断链/状态缺失） |
| `lint_debt` | Lint 债务报告（含重要性评分） |
| `token_stats` | Token 消耗统计 |
| `list_skills` | 内部 skill 清单 |
| `read_skill(name)` | 读取 skill 内容 |

### 第 2 层：写操作（6 个，同步，描述中标注副作用）

| 工具 | 说明 |
|------|------|
| `create_workspace(name)` | 新建书籍工作区 |
| `chapter_import(file_path, append, overwrite)` | 导入小说文件（自动分章）【高危】 |
| `chapter_export` | 合并导出 txt |
| `chapter_write` | 写入/覆盖章节正文（妙笔落章、手工修订） |
| `memory_write` | 写入作者记忆（偏好/风格等） |
| `memory_forget` | 软删除记忆 |

### 第 3 层：知识写工具（9 个，外部编排模式核心，同步）

宿主编排 LLM 自己做提取/写作推理后经这组工具落库，不依赖内置 api_key。

| 工具 | 说明 |
|------|------|
| `category_create(name, description, writing_guide, has_state)` | 建词条类别（立即生效，无需 commit） |
| `kb_create(category, name, content, ...)` | 新建 wiki 词条（暂存缓存） |
| `kb_edit(name, ...)` | 编辑词条（字段级更新，暂存缓存） |
| `rule_create(name, content)` / `rule_edit` | 世界观规则文档（暂存缓存） |
| `plot_create(name, chapters, ...)` / `plot_edit` | 剧情卡片/伏笔（暂存缓存） |
| `plot_end(name, end_notes)` | 结束剧情卡片 |
| `kb_commit(chapters)` | 【关键】统一提交：校验+版本快照落库+标记章节已处理+lint |

### 第 4 层：子智能体任务（3 个启动 + 6 个任务管理，异步，依赖内置 api_key）

| 工具 | 说明 |
|------|------|
| `ask_jianzhi(question)` | 【鉴知】向知识库 Agent 提问（可查章节/词条/关联/记忆） |
| `extract_knowledge(chapters, auto_approve)` | 【鉴知】知识提取：章节 → wiki/rule/plot 沉淀 |
| `muse_write(outline, chapter, auto_approve)` | 【妙笔】四步写作：大纲→知识准备→写作→审阅循环 |
| `task_status(task_id)` | 非阻塞状态快照（step/progress_tail/pending_confirm） |
| `task_wait(task_id, timeout)` | 阻塞等待（带超时） |
| `task_result(task_id)` | 获取最终成果 |
| `task_confirm(task_id, action, reason, rejected_indices)` | 响应挂起确认 |
| `task_cancel(task_id)` | 请求取消 |
| `task_list` | 全部任务总览 |

### 第 5 层：上下文工具（4 个，同步，外部编排增强，v7.2.0 新增）

| 工具 | 说明 |
|------|------|
| `muse_context(workspace, chapter=0)` | 写作 LLM 产物包：先验知识+前情提要（单章快照·用完即丢；传 chapter 校验过期返回 stale） |
| `review_context(workspace, chapter=0)` | 审阅包：规则全文+人物词条（含 state，按 chapter 过滤）+债务清单+审阅检查项 |
| `extract_context(workspace, chapters="")` | 提取包：章节范围（空=自动算下一批）+类别体系+已有词条清单（不含原文） |
| `kb_staging(workspace, name="")` | 暂存区自检：未 kb_commit 的增/改/删清单（纯只读；name 查看单条详情） |

所有需要工作区的工具都带可选 `workspace` 参数，缺省走解析优先级。

---

## 知识写工具与持久化契约

知识写工具采用与内部鉴知一致的两段式持久化：

1. **暂存**：`kb_create`/`kb_edit`/`rule_*`/`plot_*`/`plot_end` 只写 proxy 缓存，
   对 `kb_show` 等读工具已可见，但尚未写入 DB 版本。
2. **提交**：一批写入完成后调 `kb_commit(chapters)`：
   - finish_task 校验条目存在性 + 写 log.json + 标记章节已处理
   - proxy.flush 版本快照 + wikilink 关系解析
   - lint 体检（自动修复）
3. **失败保留**：commit 校验失败时缓存保留，修复后重新 commit 即可。

外部编排模式下，计划审批由宿主 LLM 自己的用户确认能力承担，
不再套内部 permission 审批门。

---

## 异步任务协议

鉴知问答（秒~分钟）、知识提取（分钟级）、妙笔写作（5~20 分钟）均为长任务，
统一为"启动 → 轮询 → 确认 → 取结果"协议：

```
start_xxx() ──► {task_id}
   │
   ├─► task_wait(task_id, timeout)    阻塞等待（推荐主循环）
   ├─► task_status(task_id)           非阻塞快照（中途汇报进展）
   ├─► task_confirm(task_id, ...)     处理 awaiting_confirmation
   ├─► task_result(task_id)           取成果
   ├─► task_cancel(task_id)           终止
   └─► task_list()                    总览
```

**任务状态机**：

```
running ──► awaiting_confirmation ──► running ──► done
   │              │                                   ▲
   ├──► error     └──（reject）─────► cancelled ──────┘（终态）
```

**快照字段**（`task_status` / `task_wait` 返回）：

| 字段 | 说明 |
|------|------|
| `status` | running / awaiting_confirmation / done / error / cancelled |
| `step` | 妙笔当前步骤（1-4：大纲/知识准备/写作/审阅） |
| `progress_tail` | 最近进度轨迹（工具调用/步骤/输出摘要，降噪后） |
| `pending_confirm` | 挂起确认项（confirm_id / confirm_type / payload） |
| `elapsed` | 已运行秒数 |
| `has_result` | done 后是否有成果待取 |

**成果字段**（`task_result`，以 muse_write 为例）：

| 字段 | 说明 |
|------|------|
| `final_text` | 定稿正文 |
| `final_review` | 最终审阅意见（issues 按严重/重要/一般/可优化分级） |
| `task_dir` | 产物目录（大纲/先验知识/前情提要/各轮草稿/审阅记录） |
| `target_chapter` | 写入的章节号 |
| `tokens` | 本次任务 token 用量 |

---

## 确认机制

`auto_approve=false` 时，Agent 工作流在关键节点挂起为
`awaiting_confirmation`，`pending_confirm.payload` 携带决策素材：

| confirm_type | 场景 | payload | 响应方式 |
|--------------|------|---------|----------|
| `plan` | 知识提取计划 | 新增/修改的词条、规则、剧情卡片清单 | `approve` 或 `reject`（**reason 必填**） |
| `forced_debt` | 高频关键实体强制建档审核 | 实体清单（等级/提及数/覆盖章节） | `approve_all` 或 `reject` + `rejected_indices` 部分拒绝 |
| `muse_confirm` | 妙笔关键节点（先验知识确认等） | 节点说明 | `approve` / `reject`+reason |
| `muse_input` | 妙笔需要补充输入 | 提问内容 | `approve`（附决策） / `reject`+reason |

`auto_approve=true` 时按安全默认自动放行（forced_debt → approve_all，其余 → approve），
适合全自动批处理。

> 项目规范：驳回必须给出 reason，`task_confirm` 层强制校验。

---

## Agent Skills 技能包

`skills-agent/` 目录提供 4 个符合开放标准（agentskills.io）的 Agent Skills 包，
把上面的工具编排成开箱即用的工作流知识。每个技能均为**双模式剧本**
（外部编排 + 内部任务），并随包附带 `GUIDE.md` 工具手册：

| 技能 | 定位 |
|------|------|
| `inkweaver-novel-studio` | 总控入口：双模式决策树 + 心智模型 + 持久化契约 |
| `inkweaver-kb-query` | 只读检索：检索路径表 + wiki/rule/plot 语义（零 LLM 成本） |
| `inkweaver-knowledge-extract` | 知识沉淀：外部编排剧本（读→抽→SPEC→确认→落库→commit）+ 领域规范 |
| `inkweaver-muse-writing` | 章节创作：外部编排剧本（知识准备→起草→审阅→chapter_write）+ 内部快捷 |

注意：外部技能（skills-agent/）与内部工作流技能（`skills/*.skill.md`，
供鉴知/妙笔内部 LLM 使用）职责分离，互不混用。

**安装到 Qoder**：把 `skills-agent/<技能名>/`（含 SKILL.md 与 GUIDE.md）复制到项目
`.qoder/skills/<技能名>/`（项目级）或全局技能目录即可，
Qoder 会按 SKILL.md frontmatter 的描述按需加载。

---

## 返回约定与错误处理

所有工具统一返回 JSON 对象：

```json
{ "status": "success", ... }                    // 成功
{ "status": "error", "message": "原因与引导" }   // 失败
```

- 不抛协议级异常：错误信息可读、可自恢复（例如工作区不存在会引导先
  `list_workspaces` / `create_workspace`）。
- api_key 占位符/缺失在 LLM 客户端初始化阶段即拦截，返回明确提示
  （替代 httpx 的 UnicodeEncodeError 崩溃）；`server_info.llm_ready` 可提前探测。
- `extract_knowledge` 启动前预校验章节范围（起始章 > 总章数直接报错），
  避免无效请求消耗 LLM token。
- 取消语义：`task_cancel` 立即唤醒阻塞在确认上的工作流线程，
  流式循环在下一检查点退出，任务最终状态为 `cancelled`。

---

## 测试与验收

**模拟接入**（`test/test_mcp_server.py`，真实 stdio 协议握手）：

```bash
python test/test_mcp_server.py              # 基础全项（握手/枚举/只读/写/错误/任务生命周期）
python test/test_mcp_server.py --with-llm   # 含真实 LLM（鉴知问答/提取校验/妙笔启停）
```

**实际 e2e 接入（Qoder）**：

1. 按 [快速接入](#快速接入qoder) 注册并启用；
2. 会话中依次验证：`list_workspaces` → `kb_list` → `ask_jianzhi`（真实 LLM 回答）
   → `muse_write` + `task_wait` + `task_result`（完整写作链路）；
3. 工具 schema 由 Qoder 缓存至
   `%APPDATA%\QoderCN\SharedClientCache\projects\<项目>\mcps\inkweaver\`。

> 设计细节（架构决策、踩坑记录、线程模型）见
> `docs/design/v7.x/v7.0-MCP服务器与Skills改造设计文档.md`。
