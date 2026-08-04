# InkWeaver-CLI 使用手册（CLI 版 README）

> **版本**: v7.0.0 ｜ 通路：**命令行** ｜ 其他通路：[HTTP API](README-API.md) · [MCP Server](README-MCP.md)

InkWeaver（墨笔）的命令行形态：终端即工作台，覆盖工作区管理、章节导入导出、
知识库查询、鉴知对话、知识提取与妙笔写作的全部操作。

---

## 目录

- [安装与配置](#安装与配置)
- [命令体系总览](#命令体系总览)
- [顶级命令](#顶级命令)
- [workspace：工作区管理](#workspace工作区管理)
- [chapter：章节管理](#chapter章节管理)
- [kb：知识库查询](#kb知识库查询)
- [settings：配置管理](#settings配置管理)
- [mcp：启动 MCP Server](#mcp启动-mcp-server)
- [chat 模式：自然语言工作台](#chat-模式自然语言工作台)
- [通用约定](#通用约定)

---

## 安装与配置

```bash
cd InkWeaver-CLI
pip install -e .        # 完整安装（推荐，注册 inkweaver 命令）
# 或仅安装依赖后以 python main.py 方式运行：
pip install -r requirements.txt
```

配置文件 `.env/config.yaml`（多模型 + 四角色分配，兼容任意 OpenAI 格式 LLM）：

```yaml
models:
  - id: "model_001"
    name: "DeepSeek V4 Flash"
    provider: "deepseek"
    api_key: sk-your-api-key-here
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com
    output_max_tokens: 128000

assignments:
  chat: "model_001"        # 鉴知对话
  extract: "model_001"     # 知识提取
  write: "model_001"       # 妙笔写作
  review: "model_001"      # 审阅

workspace:
  dir: ../workingArea
  last: ""
```

`chat / extract / write / review` 四个角色可分别绑定不同模型（例如写作用强模型、对话用快模型）。`.env/` 已被 `.gitignore` 排除，密钥不会入库。

---

## 命令体系总览

```
inkweaver
├── chat                        # 进入鉴知对话 REPL（自然语言 + 斜杠指令）
├── ask <question>              # 单轮提问（完整 Agent loop）
├── extract                     # 单轮知识提取（规划 → 确认 → 执行 → 审阅）
├── muse                        # 单轮妙笔写作（大纲 → 知识准备 → 写作 → 审阅）
├── serve                       # 启动 FastAPI HTTP 后端（端口 8000）
├── mcp                         # 启动 MCP Server（stdio / streamable-http）
│
├── workspace                   # 工作区管理
│   ├── list / switch / create / rename / delete / move
├── chapter                     # 章节管理
│   ├── import / list / show / export / status
├── kb                          # 知识库查询（wiki / rule / plot / memory）
│   ├── list / show / categories / relation / memory
└── settings                    # 配置管理
    ├── show / edit-model / assign ...
```

### 通用 Flag

| Flag | 短写 | 适用范围 | 作用 |
|------|------|---------|------|
| `--json` | | 所有命令 | 输出机器可读 JSON（脚本/管道友好） |
| `--yes` | `-y` | extract, muse, chapter import/export, workspace delete | 跳过交互确认（全自动模式） |
| `--workspace` | `-w` | 所有命令 | 指定工作区名（缺省用 config.workspace.last） |

---

## 顶级命令

### `inkweaver chat`

进入鉴知对话 REPL，详见 [chat 模式](#chat-模式自然语言工作台)。

```bash
inkweaver chat -w 我的小说
```

### `inkweaver ask <question>`

单轮提问（完整 Agent loop：自动查章节/词条/关联/记忆后回答）。

```bash
inkweaver ask "林凡的修炼体系是什么？" -w 我的小说
```

### `inkweaver extract`

单轮知识提取：规划 → 确认 → 执行 → 审阅。

```bash
inkweaver extract -w 我的小说                    # 自动计算章节范围，关键步骤确认
inkweaver extract --chapters 21-30 --yes        # 指定范围 + 全自动
```

| Flag | 说明 |
|------|------|
| `--chapters <range>` | 手动指定范围如 `21-30`，默认自动从未处理章节计算 |
| `--yes` | 跳过提取计划确认 |

### `inkweaver muse`

单轮妙笔写作：大纲 → 知识准备 → 写作 → 审阅循环。

```bash
inkweaver muse --outline-file 大纲.txt -w 我的小说          # 关键节点确认
inkweaver muse --outline-file 大纲.txt --yes               # 全自动（< 85 分自动打回，最多 3 轮）
inkweaver muse --outline-file 大纲.txt -c 51               # 锚定写第 51 章（知识硬切 ≤ 50 章）
```

| Flag | 说明 |
|------|------|
| `--outline-file <path>` | 大纲文件路径（必填） |
| `--chapter / -c <num>` | 指定创作章节号，默认最新章节 + 1 |
| `--yes` | 全自动模式 |

产物落在 `{workspace}/muse/YYYY-MM-DD_NNN/`，成稿可发布为正式章节。

### `inkweaver serve`

启动 FastAPI HTTP 后端（端口 8000），详见 [README-API.md](README-API.md)。

### `inkweaver mcp`

启动 MCP Server，供 Qoder / Claude Desktop / Cursor 等 Agent 接入，详见 [README-MCP.md](README-MCP.md)。

---

## workspace：工作区管理

```bash
inkweaver workspace list                    # 列出所有工作区
inkweaver workspace create 我的小说          # 新建并切换
inkweaver workspace switch 我的小说          # 切换（写入 config.last）
inkweaver workspace rename 新名称            # 重命名当前工作区
inkweaver workspace delete --yes            # 删除当前工作区（高危，需确认）
inkweaver workspace move D:/backup          # 迁移工作区目录
```

工作区名约束：仅允许中文、字母、数字、下划线、连字符。

---

## chapter：章节管理

```bash
inkweaver chapter import novel.txt -w 我的小说 --yes   # 导入（自动按章节标题拆分）
inkweaver chapter import novel.txt --append            # 增量导入（不覆盖已有章节）
inkweaver chapter list -n 50                            # 列出最新 50 章（章节号 + 标题）
inkweaver chapter show 3                                # 查看第 3 章内容
inkweaver chapter export -o out.txt                     # 合并导出 txt
inkweaver chapter status                                # 章节处理状态（已导入/已提取）
```

| Flag | 说明 |
|------|------|
| `import --append` | 增量导入 |
| `import --overwrite` | 覆盖导入 |
| `list -n <num>` | 显示最新 N 章（默认 50） |

---

## kb：知识库查询

```bash
inkweaver kb list --type wiki --category 人物    # 按类型/类别过滤列出条目
inkweaver kb show 林凡                           # 查看详情（wiki → rule → plot 自动回退）
inkweaver kb categories                          # 类别体系
inkweaver kb relation 林凡                       # 词条关联（双链关系图）
inkweaver kb memory --category preference        # 查看记忆（四类：preference/observation/correction/style）
```

三类知识的语义：

| 类型 | 说明 |
|------|------|
| **wiki** | 按类别组织的实体词条（人物/势力/地图/功法等），含状态字段与版本时间线 |
| **rule** | 世界观硬规则（力量体系、势力格局），设定考据的最高权威 |
| **plot** | 剧情卡片，绑定章节区间，记录伏笔（埋设/回收章节与状态） |

---

## settings：配置管理

```bash
inkweaver settings show          # 查看当前配置（api_key 脱敏）
# 其余模型增删改、角色分配见 inkweaver settings --help
```

也可直接编辑 `.env/config.yaml`；配置文件每次调用重读，修改即时生效。

---

## mcp：启动 MCP Server

```bash
inkweaver mcp                                        # stdio 模式（Qoder/Claude Desktop 默认）
inkweaver mcp -w 补天纪                              # 绑定默认工作区
inkweaver mcp -t streamable-http --host 127.0.0.1 --port 8100   # HTTP 模式
```

| Flag | 短写 | 默认 | 说明 |
|------|------|------|------|
| `--workspace` | `-w` | 空 | 绑定默认工作区（工具参数可覆盖） |
| `--transport` | `-t` | stdio | 传输方式：stdio / streamable-http |
| `--host` | | 127.0.0.1 | HTTP 模式监听地址 |
| `--port` | | 8100 | HTTP 模式监听端口 |

工具清单与 Agent 接入配置见 [README-MCP.md](README-MCP.md)。

---

## chat 模式：自然语言工作台

`inkweaver chat` 进入鉴知对话 REPL。直接问自然语言即可——它会自动调用工具查章节、查词条、查关系；同时支持斜杠指令：

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
  /remember <text>   写入偏好记忆
  /forget <id>       软删除记忆
```

---

## 通用约定

- **确认机制**：未加 `--yes` 时，提取计划、强制债务审核、妙笔关键节点都会先征求确认；驳回必须给出原因。
- **JSON 输出**：所有查询命令支持 `--json`，便于脚本管道处理。
- **工作区缺省**：`-w` 缺省时使用 `config.workspace.last`（最近打开的工作区）。
- **错误码约定**：命令异常时以非零退出码结束，stderr 输出人类可读原因。
