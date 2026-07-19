# InkWeaver-CLI

**墨笔** — 终端里的写作智能体。基于 LLM Agent 架构，为网文作者提供章节管理、知识提取、Wiki 构建与审核的一站式 CLI 工具。

---

## 项目依赖

### 运行环境

- Python >= 3.10
- pip（Python 包管理器）

### Python 依赖

| 包名 | 版本要求 | 用途 |
|------|---------|------|
| `openai` | >= 1.0.0 | 调用 LLM API（DeepSeek / OpenAI 兼容接口） |
| `pyyaml` | >= 6.0 | 解析 YAML 配置文件与 Wiki frontmatter |
| `tiktoken` | >= 0.5.0 | Token 用量统计 |

安装方式：

```bash
pip install -r requirements.txt
```

### LLM 模型

默认接入 [DeepSeek API](https://platform.deepseek.com/)，兼容任何 OpenAI 格式的推理模型（如 DeepSeek R1/V4、OpenAI o1/o3 等）。

---

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd InkWeaver-CLI
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API Key

编辑 `.env/config.yaml`，填入你的 API Key：

```yaml
api:
  url: https://api.deepseek.com
  key: sk-your-api-key-here        # ← 替换为真实 Key
  model: deepseek-v4-flash
  output_max_tokens: 128000
```

> `.env/` 目录已被 `.gitignore` 排除，不会提交到版本库。

### 4. 启动

```bash
python main.py
```

首次启动会自动创建配置文件和工作区目录。

---

## 配置文件

位置：`.env/config.yaml`

```yaml
api:
  url: https://api.deepseek.com       # API 地址
  key: sk-xxx                          # API Key
  model: deepseek-v4-flash             # 模型名
  input_max_tokens: 384000             # 输入上限
  output_max_tokens: 128000            # 输出上限

workspace:
  dir: D:/path/to/workingArea          # 工作区根目录（绝对路径）
  last: 项目名                          # 上次使用的工作区
```

---

## 工作区结构

```
workingArea/
├── 项目1/
│   ├── document/          # 章节文件（c001.md, c002.md, ...）
│   ├── session/           # 对话日志
│   ├── wiki/              # Wiki 知识库
│   │   ├── index.md
│   │   ├── 人物/
│   │   │   ├── index.md
│   │   │   ├── 张三.md
│   │   │   └── 李四.md
│   │   ├── 势力/
│   │   └── 地点/
│   ├── rules/             # 世界观规则文档
│   │   ├── 境界体系.md
│   │   └── 法宝等级.md
│   ├── relations.yaml     # Wikilink 关系图
│   ├── memory.md          # 跨会话记忆
│   └── log.json           # 文档变更日志
├── 项目2/
└── ...
```

---

## 指令列表

### 工作区管理

| 指令 | 说明 |
|------|------|
| `/list` | 列出所有工作区 |
| `/switch -n <name>` | 切换到指定工作区 |
| `/create -n <name>` | 新建工作区并切换 |
| `/update -n <name>` | 重命名当前工作区 |
| `/delete` | 删除当前工作区（需确认） |
| `/move -p <path>` | 移动工作区目录到新位置 |

### 章节管理

| 指令 | 说明 |
|------|------|
| `/import -p <path>` | 导入小说文件（自动按章节拆分） |
| `/write` | 手动输入一章（`qqq` 结束） |
| `/show -n <num>` | 展示指定章节内容 |
| `/chapters [-N]` | 列出最新 N 章（默认 50） |
| `/export` | 合并所有章节为 txt 文件 |

### Agent 控制

| 指令 | 说明 |
|------|------|
| `/clear` | 清空对话上下文 |
| `/context` | 查看上下文占用与组成 |
| `/compact` | 主动压缩上下文 |
| `/token` | 查询本次会话累计 Token 用量 |

### 知识管理（需先进入 Knowledge 模式）

| 指令 | 说明 |
|------|------|
| `/knowledge` | 进入 Knowledge 专家模式 |
| `/exit` | 退出 Knowledge 模式 |
| `/update` | 触发知识提取流程 |
| `/diff` | 查看新增/修改的章节 |
| `/memory` | 查看记忆索引 |
| `/memory -n <name>` | 查看指定记忆文档 |
| `/list -n <name>` | 查看指定类别的 Wiki 列表 |
| `/wiki -n <name>` | 查看指定词条的 Wiki |
| `/rule` | 查看规则列表 |
| `/rule -n <name>` | 查看指定规则文档 |
| `/relation -n <name>` | 查询词条关联关系 |
| `/link` | 从 Wiki 提取 wikilink，构建关系图 |

### 系统

| 指令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/exit` | 退出程序（或输入 `exit`） |

---

## 功能详解

### 1. 双模式 Agent

**普通模式（JianzhiAgent）**：日常对话、章节问答、Wiki 检索。拥有 17 个通用工具，包括章节阅读、Wiki 查询、关键词统计等。

**Knowledge 专家模式（KnowledgeAgent）**：继承普通模式全部能力，额外叠加知识提取工具包。通过 `/knowledge` 进入，`/exit` 返回，上下文不丢失。

### 2. 知识提取流水线

```
导入章节 → doc_diff 检测变更 → knowledge_task 派发子智能体
  → 按类别提取知识 → 生成/更新 Wiki 词条 → Review 审核
  → 构建 Wikilink 关系图
```

每个 `knowledge_task` 会创建一个子智能体（KnowledgeSubagent），按类别独立提取知识，提取完成后自动调用审核子智能体（ReviewSubagent）进行质量检查。

### 3. 审核机制

审核子智能体（ReviewSubagent）自动检查以下项目：

- **Wikilink 悬空**：`[[wikilink]]` 是否指向已存在的词条
- **信息矛盾**：前后信息是否矛盾
- **描述/状态混淆**：`description` vs `state` 字段是否正确
- **规则混入关系**：规则文档是否包含 `[[wikilink]]`
- **State 缺失**：人物/势力类词条是否缺少 `state` 字段
- **篇幅检查**：使用 `length_stats` 检查文档字数，超过 1500 字自动发起压缩

### 4. Wiki 知识库

Wiki 采用 **YAML frontmatter + Markdown 正文** 格式：

```markdown
---
title: 张三
category: 人物
description: 青云宗内门弟子
state: 筑基中期（第100章）
tags: [青云宗, 主角团]
---

张三自幼入青云宗，天资聪颖……
```

支持 `[[wikilink]]` 语法建立词条间关联关系，通过 `/link` 指令构建关系图。

### 5. 权限系统

两阶段权限控制：

- **Planning 阶段**：只读权限，Agent 分析章节、查阅 Wiki、制定提取计划
- **Executing 阶段**：读写权限，允许创建/修改/删除 Wiki 词条

### 6. 上下文管理

- 自动追踪已读章节和已用技能
- `/compact` 指令主动压缩历史，将早期消息摘要化
- `/context` 查看上下文各组成部分的 Token 占比
- `/token` 查看累计 Token 用量

### 7. 关系图

通过 `[[wikilink]]` 语法在 Wiki 词条中建立关联，`/link` 指令自动扫描所有词条提取链接，生成 `relations.yaml`。支持 `query_relations` 工具查询词条关联网络。

---

## 项目结构

```
InkWeaver-CLI/
├── main.py                  # 入口：配置加载、REPL 主循环
├── cli.py                   # 终端 I/O：多行输入、输出格式化、Session 日志
├── Jianzhi.py               # 鉴知 Agent：System Prompt、Tool 定义、工具路由
├── api.py                   # LLM API 封装（OpenAI SDK）
├── requirements.txt         # Python 依赖
├── skills/                  # 技能定义文件（Markdown）
│   └── knowledge_extract.skill.md
├── agent/                   # Agent 核心组件
│   ├── base.py              # BaseAgent 基类
│   ├── loop.py              # Agent 主循环（tool_calls 调度）
│   ├── todo.py              # Todo 任务管理
│   ├── compact.py           # 上下文压缩
│   ├── skill.py             # 技能注册与加载
│   ├── knowledge.py         # Knowledge 专家模式
│   ├── permission.py        # 两阶段权限系统
│   └── ...
├── tools/                   # 工具实现
│   ├── chapter.py           # 章节 CRUD
│   ├── workspace.py         # 工作区 CRUD + 小说导入
│   ├── wiki.py              # Wiki 词条 CRUD
│   ├── category.py          # 类别管理
│   ├── rules.py             # 规则文档管理
│   ├── memory.py            # 记忆管理
│   ├── relation.py          # 关系查询
│   ├── diff.py              # 文档差异对比
│   ├── review.py            # 审核 Subagent
│   └── knowledge_task.py    # 知识提取 Subagent
├── auto/                    # 自动化脚本
│   └── relation_extractor.py # Wikilink 关系提取
└── .env/                    # 配置文件（已 gitignore）
    └── config.yaml
```

---

## 技术栈

| 层面 | 技术 |
|------|------|
| 语言 | Python >= 3.10 |
| LLM API | OpenAI SDK（兼容 DeepSeek / OpenAI） |
| 配置 | YAML |
| 文档格式 | Markdown + YAML frontmatter |
| 知识链接 | Wikilink 语法 + 关系图 |
| Token 计数 | tiktoken |
| Agent 架构 | ReAct 模式（工具调用循环） |

---

## 设计理念

- **不兜底、不兼容**：仅实现当前明确需求，不添加防御性代码
- **Wiki 优先 RAG**：知识检索优先使用 Wiki，不到万不得已不翻原文
- **两阶段权限**：先计划再执行，避免 Agent 在分析阶段误修改数据
- **自动审核**：每次知识提取后自动审核，确保 Wiki 质量
