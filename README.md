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

