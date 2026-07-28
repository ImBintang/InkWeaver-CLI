# InkWeaver-CLI

**墨笔（InkWeaver）** — 终端里的写作智能体。基于 LLM Agent 架构，为网文作者提供章节管理、知识提取（Wiki + 剧情卡片 + 规则）、关系图构建与智能写作辅助的一站式 CLI 工具。

当前版本：**v5.2.1**

---

## 快速开始

### 1. 安装

```bash
cd InkWeaver-CLI
pip install -e .
```

或仅安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `.env/config.yaml`：

```yaml
api:
  url: https://api.deepseek.com       # OpenAI 兼容 API 地址
  key: sk-your-api-key-here           # 替换为真实 Key
  model: deepseek-v4-flash            # 模型名
  input_max_tokens: 384000
  output_max_tokens: 128000

workspace:
  dir: ../workingArea                  # 工作区根目录
  last: ""                             # 上次使用的工作区
```

支持任何 OpenAI 兼容格式的 LLM。`.env/` 目录已被 `.gitignore` 排除。

### 3. 使用

```bash
# 查看帮助
inkweaver --help

# 或直接运行（等价于 inkweaver chat）
python main.py
```

---

## 命令体系

```
inkweaver
├── chat                        # 进入鉴知对话 REPL
├── ask <question>              # 单轮提问（完整 Agent loop）
├── extract                     # 单轮知识提取
├── muse                        # 单轮妙笔写作
│
├── workspace                   # 工作区管理
│   ├── list                    # 列出所有工作区
│   ├── switch <name>           # 切换
│   ├── create <name>           # 新建并切换
│   ├── rename <name>           # 重命名
│   ├── delete                  # 删除
│   └── move <path>             # 迁移目录
│
├── chapter                     # 章节管理
│   ├── import <path>           # 导入小说文件
│   ├── list                    # 列出章节号+标题
│   ├── show <num>              # 查看某章内容
│   ├── export                  # 合并导出 txt
│   └── status                  # 章节处理状态
│
└── kb                          # 知识库查询（wiki/rule/plot 三合一）
    ├── list                    # 列出条目
    ├── show <name>             # 查看详情
    ├── categories              # 类别列表
    └── relation <name>         # 关联查询
```

### 通用 Flag

| Flag | 短写 | 适用范围 | 作用 |
|------|------|---------|------|
| `--json` | | 所有命令 | 输出机器可读 JSON |
| `--yes` | `-y` | extract, muse, chapter import/export, workspace delete | 跳过交互确认 |
| `--workspace` | `-w` | 所有命令 | 指定工作区名 |

### 命令专属 Flag

| 命令 | Flag | 说明 |
|------|------|------|
| `extract` | `--chapters <range>` | 手动指定范围如 `21-30`，默认自动计算 |
| `muse` | `--outline-file <path>` | 大纲文件路径（必填） |
| `chapter import` | `--append` | 增量导入（不覆盖已有章节） |
| `chapter list` | `-n <num>` | 显示最新 N 章（默认 50） |
| `kb list` | `--type <wiki\|plot\|rule>` | 按类型过滤 |
| `kb list` | `--category <name>` | 按类别过滤 |

---

## 使用示例

```bash
# 创建工作区并导入小说
inkweaver workspace create 我的小说
inkweaver chapter import novel.txt -w 我的小说 --yes

# 知识提取（自动计算范围，跳过确认）
inkweaver extract -w 我的小说 --yes

# 手动指定提取范围
inkweaver extract -w 我的小说 --chapters 11-20 --yes

# 查询知识库
inkweaver kb list -w 我的小说 --type wiki --json
inkweaver kb show 林凡 -w 我的小说
inkweaver kb relation 林凡 -w 我的小说

# 单轮提问
inkweaver ask "林凡的修为体系是什么？" -w 我的小说 --json

# 妙笔写作（全自动）
inkweaver muse --outline-file draft.txt -w 我的小说 --yes

# 进入对话模式
inkweaver chat -w 我的小说
```

---

## Chat 模式

`inkweaver chat` 进入鉴知对话 REPL，支持自然语言交互和斜杠指令：

```
会话控制：
  /exit              退出
  /help              帮助
  /clear             清空上下文
  /compact           压缩上下文
  /context           上下文占用报告
  /token             token 用量统计

快速查询：
  /chapters [-n]     章节列表
  /show <num>        查看章节
  /status            处理状态
  /wiki <name>       查看词条
  /rule [name]       查看规则
  /relation <name>   查询关联
  /memory            查看记忆

操作：
  /extract           触发知识提取
```

输入规则：单行输入回车发送，支持 `\n` 转义为实际换行。

---

## 核心功能

### 知识提取

Agent 自动分析章节内容，生成提取计划（新增/编辑词条、剧情卡片、规则），确认后执行：

1. **规划阶段**：分析章节 → 对比现有知识库 → 生成计划 → 用户确认（`--yes` 跳过）
2. **执行阶段**：写入 SQLite DB → 构建关系图 → 记录 log.json → 运行 lint 检查

### 妙笔写作

四步状态机工作流：

```
① 大纲输入（从文件读取）→ ② 知识准备 → ③ 润色写作 → ④ 审阅循环
```

- `--yes` 全自动模式：审阅分数 < 85 自动打回重写（最多 3 轮）
- 输出保存在 `{workspace}/muse/YYYY-MM-DD_NNN/` 目录

### 知识系统

三类结构化知识，统一存储于 SQLite（`wiki.db`）：

| 类型 | 说明 |
|------|------|
| **Wiki** | 按类别组织的实体词条（人物/势力/地图/功法等），含状态字段和版本时间线 |
| **Plot** | 剧情卡片，绑定章节区间，有「未结束/已结束」生命周期 |
| **Rule** | 世界观规则文档（如境界体系） |

所有类型通过 `[[wikilink]]` 交叉引用，自动构建关系图。

---

## 工作区结构

```
workingArea/
├── 我的小说/
│   ├── wiki.db              # SQLite 数据库（章节 + 知识库）
│   ├── session/             # 对话日志归档
│   ├── muse/                # 妙笔输出目录
│   ├── log.json             # 提取记录 & 文档哈希
│   ├── relations.yaml       # 关系图
│   └── lint-debt.json       # Lint 债务报告
└── ...
```

---

## 项目结构

```
InkWeaver-CLI/
├── main.py                 # typer app 入口
├── commands/               # 子命令实现
│   ├── common.py           # 公共工具（配置加载、工作区解析）
│   ├── workspace.py        # workspace 子命令组
│   ├── chapter.py          # chapter 子命令组
│   ├── kb.py               # kb 子命令组
│   ├── chat.py             # chat REPL
│   ├── ask.py              # 单轮提问
│   ├── extract.py          # 知识提取
│   └── muse_cmd.py         # 妙笔写作
├── core/                   # 核心 I/O 层
│   ├── io.py               # IOChannel 统一 I/O 通道
│   ├── output.py           # OutputFormatter（人类/JSON 双模式）
│   └── session.py          # SessionLogger 会话日志
├── agent/                  # Agent 核心
│   ├── base.py             # BaseAgent 基类
│   ├── loop.py             # agent_loop 主循环
│   ├── compact.py          # 上下文压缩
│   ├── permission.py       # 权限管理
│   ├── skill.py            # Skill 注册
│   └── todo.py             # TODO 管理
├── tools/                  # 工具函数
│   ├── db/                 # SQLite 数据层
│   │   ├── service.py      # SQL 操作
│   │   ├── proxy.py        # 缓存代理
│   │   └── version_manager.py  # 版本管理
│   ├── workspace.py        # 工作区操作
│   ├── chapter.py          # 章节工具
│   ├── wiki.py             # Wiki 工具
│   ├── plot.py             # 剧情卡片
│   ├── rules.py            # 规则文档
│   ├── relation.py         # 关系查询
│   ├── editor.py           # 统一编辑器
│   └── lint.py             # Lint 检查
├── skills/                 # Skill 文件（注入 system prompt）
├── auto/                   # 自动化（关系提取）
├── api.py                  # LLMClient
├── Jianzhi.py              # 鉴知 Agent
├── Muse.py                 # 妙笔工作流
├── cli.py                  # Legacy CLI（仅 print_plan 复用）
└── pyproject.toml          # 包定义 + entry_points
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.10+ |
| CLI 框架 | typer（基于 rich） |
| LLM 接口 | OpenAI SDK（兼容任意 OpenAI 格式推理模型） |
| 数据存储 | SQLite（章节 + 知识库 + 版本时间线） |
| 配置 | YAML |
| Token 估算 | tiktoken（cl100k_base） |

---

## Session 日志

每次命令执行自动写入 `{workspace}/session/session_YYYYMMDD_HHMMSS.log`：

```
[META] mode=single-turn cmd=ask
[USER] 林凡的修为体系是什么？
[THINK] 已思考（耗时 3 秒）
[TOOL] read_wiki 林凡 -> 成功
[AGENT] 林凡的修为体系为...
```

META 首行标注调用模式：`chat` / `single-turn cmd=ask|extract|muse`。
# InkWeaver-CLI

**墨笔（InkWeaver）** — 终端里的写作智能体。基于 LLM Agent 架构，为网文作者提供章节管理、知识提取（Wiki + 剧情卡片）、结构化审核、关系图构建与智能写作辅助的一站式 CLI 工具。

当前版本：**v3.1.0**

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `.env/config.yaml`，填入你的 API Key：

```yaml
api:
  url: https://api.deepseek.com       # OpenAI 兼容 API 地址
  key: sk-your-api-key-here           # 替换为真实 Key
  model: deepseek-v4-flash            # 模型名
  input_max_tokens: 384000            # 输入上限
  output_max_tokens: 128000           # 输出上限
```

支持任何 OpenAI 兼容格式的 LLM。`.env/` 目录已被 `.gitignore` 排除，不会提交到版本库。

### 3. 启动

```bash
# 普通模式 — 章节管理、知识提取、知识库维护
python main.py

# 妙笔写作模式 — 大纲 → 先验知识 → 前情提要 → 写作 → 审阅
python main.py --muse
```

首次启动会自动创建配置文件和工作区目录。

---

## 功能概览

### 双运行模式

| 模式 | 启动方式 | 用途 |
|------|---------|------|
| **普通模式** | `python main.py` | 章节管理、知识库维护（Wiki / 剧情卡片 / 规则）、关系图构建 |
| **妙笔模式** | `python main.py --muse` | 四步写作工作流：大纲输入 → 知识准备 → 自动写作 → 审阅循环 |

### 普通模式功能

#### 工作区管理

支持多项目隔离，每个工作区拥有独立的章节、Wiki 知识库、剧情卡片、规则文档和日志。

| 指令 | 说明 |
|------|------|
| `/list` | 列出所有工作区 |
| `/switch -n <name>` | 切换到指定工作区 |
| `/create -n <name>` | 新建工作区并切换 |
| `/update -n <name>` | 重命名当前工作区 |
| `/delete` | 删除当前工作区 |
| `/move -p <path>` | 移动工作区目录到新位置 |

#### 章节管理

支持小说导入、手动录入、查看、导出。

| 指令 | 说明 |
|------|------|
| `/import -p <path>` | 导入小说文件（自动按章节拆分，支持 `第X章/回/节`、`Chapter X`、`序章/楔子` 等格式） |
| `/write` | 手动输入一章（`qqq` 结束） |
| `/show -n <num>` | 展示指定章节内容 |
| `/chapters [-N]` | 列出最新 N 章（默认 50） |
| `/export` | 合并所有章节为 txt 文件 |

#### 知识系统

构建结构化的故事知识库，包含三个正交维度：

**Wiki 知识库** — 按类别组织实体词条（人物/势力/地点/功法/神通/法宝等），每篇含 YAML frontmatter（元数据）和 Markdown 正文。人物/势力类词条含 `state` 动态状态字段。

**剧情卡片** — 记录故事事件/情节段，绑定章节区间，有明确的「未结束/已结束」生命周期，通过 `[[wikilink]]` 与 Wiki 词条交叉引用。

**规则文档** — 世界观底层规则（如境界体系），不参与关系系统。

#### 指令

| 类别 | 指令 | 说明 |
|------|------|------|
| 模式 | `/knowledge` | 进入 Knowledge 专家模式（知识提取、Wiki 管理） |
| | `/exit` | 退出 Knowledge 模式 |
| 提取 | `/update` | 触发知识提取流程 |
| | `/diff` | 查看新增/修改的章节 |
| 查询 | `/memory` | 查看记忆索引 |
| | `/memory -n <name>` | 查看指定记忆文档 |
| | `/list -n <name>` | 查看指定类别的 Wiki 列表 |
| | `/wiki -n <name>` | 查看指定词条的 Wiki |
| | `/rule` | 查看规则列表 |
| | `/rule -n <name>` | 查看指定规则文档 |
| | `/relation -n <name>` | 查询词条关联关系 |
| | `/link` | 提取 wikilink，构建关系图 |
| Agent | `/clear` | 清空对话上下文 |
| | `/context` | 查看上下文占用与组成 |
| | `/compact` | 主动压缩上下文 |
| | `/token` | 查询累计 Token 用量 |
| 系统 | `/help` | 显示帮助信息 |
| | `/exit` | 退出程序 |

#### 知识提取流水线

Knowledge 模式下的核心能力，分为两阶段：

1. **规划阶段**（只读）：分析新章节 → 对比现有知识库 → 生成提取计划 → 用户确认
2. **执行阶段**（读写）：按类别提取 Wiki 词条 → 提取剧情卡片 → 审核 Subagent 系统性检查 → 构建关系图

所有写操作在规划阶段被权限系统**物理拦截**，只有用户明确确认后才放行。

#### 输入机制

- **多行文本**：输入多行后，以 `qqq` 单独一行结束，内容一次性发送
- **指令**：第一行以 `/` 开头则立即执行，无需 `qqq`
- **退出**：输入 `exit`

---

### 妙笔模式功能

四步写作工作流，专为长篇小说创作设计：

```
① 大纲输入 → ② 知识准备 → ③ 自动写作 → ④ 审阅循环
```

#### ① 大纲输入

多行输入大纲或草稿内容，保存至工作区。

#### ② 知识准备

自动执行两个步骤，每步用户可打回重写：

- **先验知识提取**：LLM 通过工具检索 Wiki 词条（支持只读 YAML / 读全文两种粒度）、规则文档，生成创作所需的知识背景
- **前情提要提取**：LLM 检索剧情卡片（支持 YAML / 全文）、最新章节正文，梳理故事当前进展

参数由 LLM 自动规划提交，后端做严格校验（存在检查、数量上限检查），校验失败返回错误提示供修正重试。

#### ③→④ 写作与审阅循环

- **写作**：按 `上一章全文 → 大纲 → 先验知识 → 前情提要 → 审阅意见` 顺序组装上下文，纯 chat 模式生成正文
- **后处理**：自动去除 Markdown 标题行、全角标点置换
- **审阅**：独立 Agent 逐条报问题（使用 `report_issue` / `review_done` 工具），后端自动算分。评分维度涵盖叙事结构、段落句式、语言修辞、与上一章衔接、人物感官
  - ≥ 85 分：通过，用户确认后保存
  - < 85 分：自动打回重写，携带审阅意见进入下一轮
  - 用户也可手动打回并补充自定义意见

审阅上下文中包含 `上一章全文 + 大纲 + 正文`，不包含先验知识和前情提要，避免总结性内容干扰评审判断。

#### Skill 系统

妙笔各步骤由独立的 Skill 文件驱动，通过 `skills/` 目录加载：

| Skill 文件 | 用途 |
|-----------|------|
| `muse_knowledge.skill.md` | 先验知识提取工作流指引 |
| `muse_plot.skill.md` | 前情提要提取工作流指引 |
| `muse_writer.skill.md` | 写实主义创作规范（叙事架构、段落铁律、禁用清单、白描主义） |
| `muse_reviewer.skill.md` | 审阅审计清单及评分标准 |

Skill 内容直接注入 system prompt，LLM 严格按照定义的步骤执行。

---

## 工作区结构

```
workingArea/
├── 项目1/
│   ├── document/          # 章节文件（c001.md, c002.md, ...）
│   ├── session/           # 对话日志归档
│   ├── wiki/              # Wiki 知识库
│   │   ├── index.md       # 总索引
│   │   ├── relations.yaml # Wikilink 关系图
│   │   ├── 人物/          # 类别目录
│   │   │   ├── index.md
│   │   │   ├── 张三.md
│   │   │   └── ...
│   │   ├── 势力/
│   │   ├── 地点/
│   │   └── ...            # 自定义类别
│   ├── plot/              # 剧情卡片
│   │   ├── index.md
│   │   └── 剧情事件.md
│   ├── rules/             # 规则文档
│   │   ├── 境界体系.md
│   │   └── ...
│   ├── memory/            # 跨会话记忆
│   │   ├── MEMORY.md
│   │   └── *.md
│   ├── muse/              # 妙笔输出目录
│   │   ├── YYYY-MM-DD_NNN/
│   │   │   ├── outline.txt
│   │   │   ├── prior_knowledge.md
│   │   │   ├── plot_summary.md
│   │   │   ├── draft.txt
│   │   │   ├── review_round_1/
│   │   │   │   ├── review.md
│   │   │   │   └── final.txt
│   │   │   ├── session.log
│   │   │   └── ...
│   │   └── ...
│   └── log.json           # 文档变更日志 & 提取记录
├── 项目2/
└── ...
```

---

## 配置文件

位置：`.env/config.yaml`

```yaml
api:
  url: https://api.deepseek.com       # OpenAI 兼容 API 地址
  key: sk-xxx                          # API Key
  model: deepseek-v4-flash             # 模型名
  input_max_tokens: 384000             # 输入上限
  output_max_tokens: 128000            # 输出上限

workspace:
  dir: D:/path/to/workingArea          # 工作区根目录
  last: 项目名                          # 上次使用的工作区
```

---

## 项目依赖

### 运行环境

- Python >= 3.10
- pip（Python 包管理器）

### Python 依赖

| 包名 | 版本要求 | 用途 |
|------|---------|------|
| `openai` | >= 1.0.0 | LLM API 调用（OpenAI 兼容接口） |
| `pyyaml` | >= 6.0 | 解析 YAML 配置与 Wiki / Plot frontmatter |
| `tiktoken` | >= 0.5.0 | Token 用量统计（cl100k_base 编码） |

### 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.10+ |
| LLM 接口 | OpenAI SDK（兼容任意 OpenAI 格式推理模型） |
| 数据格式 | YAML（配置 + frontmatter）+ Markdown（正文） |
| Token 估算 | tiktoken（cl100k_base） |
| 关系图 | YAML 文件 + wikilink 正则提取 |


