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


