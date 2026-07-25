"""Knowledge 专家模式 — 继承 JianzhiAgent，叠加 Knowledge 专属工具包"""

from pathlib import Path

from Jianzhi import JianzhiAgent
from tools import wiki as wiki_tools
from tools import category as category_tools
from tools import rules as rules_tools
from tools import plot as plot_tools
from tools import editor as editor_tools
from tools.knowledge_task import run_knowledge_task
from tools.plot_task import run_plot_task
from tools.review import run_review as _run_review
from tools import diff as diff_tools


class KnowledgeAgent(JianzhiAgent):
    """Knowledge 专家模式 Agent

    - 继承 JianzhiAgent 的所有功能（通用工具 + 上下文 + todo + skills）
    - 叠加 Knowledge 专属工具包（8 个工具）
    - 注入 Knowledge 模式 system prompt
    """

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, cli,
                 messages: list = None):
        """
        Args:
            messages: 从父 Agent 传入的对话历史（模式切换时保留上下文）
        """
        super().__init__(config, workspace, skills_dir, cli)
        if messages is not None:
            self.messages = messages
        # 权限管家由父类 JianzhiAgent 初始化

    def build_system_prompt(self) -> str:
        """构建 Knowledge 模式 system prompt = 基础 prompt + Knowledge 指令"""
        base_prompt = super().build_system_prompt()
        knowledge_extension = (
            "\n"
            "# Knowledge 专家模式\n"
            "你当前处于 Knowledge 专家模式，负责知识提取与 Wiki 管理。\n"
            "\n"
            "## 技能调用\n"
            "本模式下技能仍然可用。需要提取知识时，调用 `knowledge_extract` 技能获取完整流程步骤；需要类别设计指导时，调用 `category_design` 技能获取 PRD 规范。\n"
            "\n"
            "## 可用操作\n"
            "- 使用 doc_diff 查看新增/修改的章节\n"
            "- 使用 wiki_list 查看类别下的词条列表\n"
            "- 使用 read_wiki 读取指定词条\n"
            "- 使用 new_wiki / edit_wiki / delete_wiki 管理 wiki 词条\n"
            "- 使用 create_doc / edit_doc / delete_doc（统一工具，通过 doc_type 参数指定类型）\n"
            "- 使用 edit_doc_text 在正文中精确替换文本（手术刀式，省 token）\n"
            "- 使用 edit_doc_wikilink 替换 [[wikilink]] 目标（支持 mode=\"unlink\" 取消链接，用于不需要建词条的断链）\n"
            "- edit_doc_wikilink(mode=\"unlink\", remember=true) 取消链接的同时记入 unlink 黑名单，后续 lint 自动跳过该断链的债务报告\n"
            "- 使用 new_rule / edit_rule / delete_rule 管理规则文档（rules/ 目录）\n"
            "- 使用 new_category / edit_category 管理类别\n"
            "- 使用 rules_list / read_rule 查看规则文档\n"
            "- 使用 query_relations 查询词条关联\n"
            "- 使用 read_memory 读取记忆\n"
            "- 使用 knowledge_task 派发 subagent 执行知识提取\n"
            "- 使用 review_knowledge 启动代码 lint + 审核 subagent（知识提取完成后必须执行，自动修复 + 构建关系图）\n"            "- 使用 finish_task 完成知识提取任务（自动校验、记录日志）\n"            "\n"
            "## Wiki 优先 RAG 原则（重要）\n"
            "**核心原则**：面对已有 wiki 词条的知识检索，必须先用 wiki 进行 RAG，而不是直接翻原文。\n"
            "\n"
            "### 正确的检索顺序\n"
            "1. `wiki_list <类别>` → 查看该类别下有哪些已有词条\n"
            "2. `read_wiki <类别> <词条名>` → 读取相关词条内容\n"
            "3. `check_wiki <词条名> <章节>` → 检查词条在章节中是否出现\n"
            "4. 只有以上三步无法满足需求时，才用 `read_chapters` 读取章节原文\n"
            "\n"
            "### 禁止行为\n"
            "- ❌ 跳过 wiki 直接 `read_chapters 1-17` 全文阅读\n"
            "- ❌ 已有 wiki 词条的情况下，不查 wiki 就去翻原文\n"
            "- ❌ 把 wiki 能解答的问题变成大段章节阅读\n"
            "\n"
            "## 权限系统（两阶段）\n"
            "本模式设有权限管家，分为两个阶段：\n"
            "\n"
            "### 阶段一：规划阶段（planning）— 默认状态\n"
            "- ✅ 允许：read_chapters / wiki_list / read_wiki / rules_list / read_rule /\n"
            "  doc_diff / category_list / check_wiki / query_relations / read_memory /\n"
            "  agent_output / update_todo / confirm_plan / review_knowledge\n"
            "- ❌ 禁止（工具会返回拦截提示）：new_wiki / edit_wiki / delete_wiki /\n"
            "  new_category / edit_category / new_rule / edit_rule / delete_rule /\n"
            "  knowledge_task / edit_index\n"
            "\n"
            "### 阶段二：执行阶段（executing）— 用户确认后\n"
            "- 所有工具放行\n"
            "\n"
            "### 切换方式\n"
            "1. 在规划阶段分析章节内容，制定计划\n"
            "2. 调用 agent_output 输出完整计划给用户\n"
            "3. **等待用户明确确认**（用户会说「确认」「可以」「行」等）\n"
            "4. 用户确认后，调用 confirm_plan 切换到执行阶段\n"
            "5. 切换到执行阶段后即可执行写入操作\n"
            "\n"
            "## 知识提取\n"
            "执行知识提取时，先调用 `knowledge_extract` 技能获取完整的流程步骤说明（范围确定 → wiki 查询 → 计划制定 → subagent 派发 → 审核 → 完成任务）。技能会指导你每一步的具体操作。\n"
            "\n"
            "## 规则文档与 Wiki 词条的区分\n"
            "| 类型 | 位置 | 用途 | 示例 |\n"
            "|------|------|------|------|\n"
            "| 规则文档 | rules/ | 定义世界观的基础规则 | 境界体系、修炼体系、魔法规则 |\n"
            "| Wiki 词条 | wiki/ | 记录具体的人/物/地/事 | 叶匀、叶家、赤云城、玄武大会 |\n"
            "\n"
            "**分类原则**：「定义世界如何运转的底层规则」用 new_rule → rules/；「故事中具体出现的人/物/地/事」用 new_wiki → wiki/。\n"
            "例如「肉仙十重」是修炼体系规则，应写入 rules/境界体系.md，而非作为 wiki 词条。\n"
            "\n"
            "## 规则\n"
            "- 规则文档使用 new_rule / edit_rule 管理（不参与关系系统），不要用 edit_wiki 编辑规则\n"
            "- **规则文档禁止包含 [[wikilink]]**，规则定义的是世界观底层规则，不与具体词条建立关系\n"
            "- new_wiki 的 content 为必填参数，必须提供正文内容\n"
            "- 设定图鉴类不需要 state 字段\n"
            "- 人物/势力类词条必须包含 state 字段（动态信息），见类别 index.md 定义\n"
            "- 所有 wiki 文档使用统一 frontmatter\n"
            f"- 知识提取完成后**必须**调用 review_knowledge 进行审核\n"
            f"\n"
            f"## 剧情卡片操作\n"
            f"- 使用 plot_list(ended=\"false\") 查看未结束的剧情卡片\n"
            f"- 使用 read_plot 读取剧情卡片内容\n"
            f"- 使用 plot_task 派发子智能体执行剧情提取\n"
            f"- plot_task 的 active_plots 参数传入涉及到的未结束卡片列表\n"
            f"\n"
            f"## 剧情卡片内容要求\n"
            f"- 剧情卡片正文**必须**包含 [[wikilink]] 引用相关 wiki 词条\n"
            f"- 先用 wiki_list/read_wiki 查找已有词条名，再在卡片正文中引用\n"
            f"- 例如：[[叶匀]]在[[银沙河]]修炼[[铁打功]]，获得[[寒叔（叶寒）]]传承\n"
            f"- 卡片正文的 wikilink 能让关系图关联 plot 和 wiki，至关重要\n"
        )
        return base_prompt + knowledge_extension

    def build_tool_defs(self) -> list:
        """构建 Knowledge 模式 tool defs = 通用工具 + Knowledge 专属工具"""
        tools = super().build_tool_defs()

        # Knowledge 专家模式专属工具（叠加在通用工具之上）
        knowledge_tools = [
            # Knowledge 专属管理工具
            {
                "type": "function",
                "function": {
                    "name": "confirm_plan",
                    "description": "权限系统：用户确认计划后，调用此工具从「规划阶段」切换到「执行阶段」。切换后写工具（new_wiki/edit_wiki 等）才可执行。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_category",
                    "description": "创建新类别（规划阶段被权限系统拦截，需用户确认后才可用）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "类别名"},
                            "description": {"type": "string", "description": "类别描述"},
                            "writing_guide": {"type": "string", "description": "写作规范"},
                            "has_state": {"type": "boolean", "description": "是否需要 state 字段"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_category",
                    "description": "编辑类别（规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "类别名"},
                            "description": {"type": "string", "description": "新描述"},
                            "writing_guide": {"type": "string", "description": "新写作规范"},
                            "has_state": {"type": "boolean", "description": "新 state 配置"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_wiki",
                    "description": "新建 wiki 文档（规则文档请用 new_rule；规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "content": {"type": "string", "description": "正文内容（必填，否则只有frontmatter无正文）"},
                            "description": {"type": "string", "description": "描述（静态）"},
                            "state": {"type": "string", "description": "状态（动态）"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                        },
                        "required": ["category", "name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_wiki",
                    "description": "编辑 wiki 文档（规则文档请用 edit_rule；规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "content": {"type": "string", "description": "新正文"},
                            "description": {"type": "string", "description": "新描述"},
                            "state": {"type": "string", "description": "新状态"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签"},
                        },
                        "required": ["category", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_rule",
                    "description": "新建规则文档（规则存储在 rules/ 目录，不参与关系系统；规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "content": {"type": "string", "description": "文档全文（含frontmatter）"},
                        },
                        "required": ["name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_rule",
                    "description": "编辑规则文档（规则存储在 rules/ 目录，不参与关系系统；规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "content": {"type": "string", "description": "新全文（含frontmatter）"},
                        },
                        "required": ["name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_rule",
                    "description": "删除规则文档（规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_wiki",
                    "description": "删除 wiki 文档（规则文档请用 delete_rule；规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                        },
                        "required": ["category", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "knowledge_task",
                    "description": "创建 subagent 完成知识提取（按类别；规划阶段被权限系统拦截）。支持 review_notes 参数传递审核修复建议。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "chapters": {"type": "string", "description": "章节范围"},
                            "entries": {"type": "array", "items": {"type": "string"}, "description": "目标词条列表"},
                            "task_type": {"type": "string", "enum": ["new", "update"], "description": "new 或 update"},
                            "review_notes": {"type": "string", "description": "审核修复建议（由审核 subagent 提供，可选）"},
                        },
                        "required": ["category", "chapters", "entries", "task_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_plot",
                    "description": "新建剧情卡片（规划阶段被权限系统拦截）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片标题"},
                            "chapters": {"type": "string", "description": "覆盖章节，如 \"1-5,7-10\""},
                            "content": {"type": "string", "description": "正文内容"},
                            "description": {"type": "string", "description": "剧情概要（静态）"},
                            "state": {"type": "string", "description": "当前进展（动态，可选）"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                        },
                        "required": ["name", "chapters", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_plot",
                    "description": "编辑剧情卡片（规划阶段被权限系统拦截）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片标题"},
                            "chapters": {"type": "string", "description": "新章节范围"},
                            "content": {"type": "string", "description": "新正文"},
                            "description": {"type": "string", "description": "新描述"},
                            "state": {"type": "string", "description": "新状态"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_plot",
                    "description": "将指定剧情卡片标注为已结束，写入收尾语（规划阶段被权限系统拦截）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片标题"},
                            "end_notes": {"type": "string", "description": "收尾语"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plot_task",
                    "description": "创建 subagent 完成剧情提取（按任务边界；规划阶段被权限系统拦截）。支持 review_notes 参数传递审核修复建议。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "章节范围"},
                            "active_plots": {"type": "array", "items": {"type": "string"}, "description": "涉及的未结束剧情卡片列表"},
                            "review_notes": {"type": "string", "description": "审核修复建议（可选）"},
                        },
                        "required": ["chapters", "active_plots"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_index",
                    "description": "编辑 index 文档（规划阶段被权限系统拦截）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "新内容"},
                            "category": {"type": "string", "description": "类别名（None 表示总索引）"},
                        },
                        "required": ["content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_index",
                    "description": "读取总 index 或指定类别 index",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名（None 表示总索引）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_unprocessed_chapters",
                    "description": "获取未处理的章节号列表（从小到大排序），至多 limit 个。返回纯数字列表，如 [1,2,3]。用于知识提取前确定待提取范围。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "description": "最大返回数量（默认 10）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "review_knowledge",
                    "description": "审核知识提取结果。检查wikilink悬空、信息矛盾、描述/状态混淆、规则混入关系、state缺失等问题。审核后会委托knowledge_task修复问题。知识提取完成后必须调用此工具进行审核。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "本次涉及的章节范围"},
                        },
                        "required": ["chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish_task",
                    "description": "完成知识提取任务。声明本次提取的章节区间和所有涉及的新增/修改词条、规则、剧情卡片。后端校验存在性后记录到 log.json 并自动构建关系图。提取完成后必须调用此工具。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "本次提取的章节区间，如 \"1-10,15-20\""},
                            "new_wiki": {"type": "array", "items": {"type": "string"}, "description": "新增的 wiki 词条名列表"},
                            "updated_wiki": {"type": "array", "items": {"type": "string"}, "description": "修改的 wiki 词条名列表"},
                            "new_rules": {"type": "array", "items": {"type": "string"}, "description": "新增的规则名列表"},
                            "updated_rules": {"type": "array", "items": {"type": "string"}, "description": "修改的规则名列表"},
                            "new_plots": {"type": "array", "items": {"type": "string"}, "description": "新增的剧情卡片名列表"},
                            "updated_plots": {"type": "array", "items": {"type": "string"}, "description": "修改的剧情卡片名列表"}
                        },
                        "required": ["chapters"],
                    },
                },
            },
        ]

        tools.extend(knowledge_tools)
        return tools

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由 — Knowledge 专属工具优先，否则交给父类"""
        # 统一权限检查（confirm_plan 也由 check 处理）
        if name in ("confirm_plan", "handoff_knowledge", "new_wiki", "edit_wiki", "delete_wiki",
                     "new_category", "edit_category", "new_rule", "edit_rule",
                     "delete_rule", "knowledge_task", "edit_index",
                     "new_plot", "edit_plot", "end_plot", "delete_plot", "plot_task",
                     "create_doc", "edit_doc", "edit_doc_text", "edit_doc_wikilink",
                     "delete_doc"):
            result = self.permission.check(name)
            if result == "__HANDOFF_KNOWLEDGE__":
                return "你已在 Knowledge 专家模式中。"
            if result is not None:
                return result

        # confirm_plan 已由 check 处理并返回结果
        if name == "confirm_plan":
            self.permission.confirm_plan()
            msg = "✅ 已切换至「执行阶段」，写操作已放行。"
            if self.cli and self.cli.logger:
                self.cli.logger.write("PERMISSION", msg)
            return msg

        # Knowledge 专家模式专属工具（通用工具由父类 JianzhiAgent 处理）
        knowledge_dispatch = {
            "handoff_knowledge": lambda **kw: "你已在 Knowledge 专家模式中。",
            "new_category": lambda **kw: category_tools.new_category(self.workspace, **kw),
            "edit_category": lambda **kw: category_tools.edit_category(self.workspace, **kw),
            "new_wiki": lambda **kw: wiki_tools.new_wiki(self.workspace, **kw),
            "edit_wiki": lambda **kw: wiki_tools.edit_wiki(self.workspace, **kw),
            "delete_wiki": lambda **kw: wiki_tools.delete_wiki(self.workspace, **kw),
            "new_rule": lambda **kw: rules_tools.new_rule(self.workspace, **kw),
            "edit_rule": lambda **kw: rules_tools.edit_rule(self.workspace, **kw),
            "delete_rule": lambda **kw: rules_tools.delete_rule(self.workspace, **kw),
            "knowledge_task": lambda **kw: run_knowledge_task(
                self.llm, self.workspace, cli=self.cli,
                token_callback=lambda i, o: self._accumulate_tokens(
                    {"input_tokens": i, "output_tokens": o}
                ), **kw
            ),
            "plot_task": lambda **kw: run_plot_task(
                self.llm, self.workspace, cli=self.cli,
                token_callback=lambda i, o: self._accumulate_tokens(
                    {"input_tokens": i, "output_tokens": o}
                ), **kw
            ),
            "read_plot": lambda **kw: plot_tools.read_plot(self.workspace, **kw),
            "plot_list": lambda **kw: plot_tools.plot_list(self.workspace, **kw),
            "new_plot": lambda **kw: plot_tools.new_plot(self.workspace, **kw),
            "edit_plot": lambda **kw: plot_tools.edit_plot(self.workspace, **kw),
            "end_plot": lambda **kw: plot_tools.end_plot(self.workspace, **kw),
            "delete_plot": lambda **kw: plot_tools.delete_plot(self.workspace, **kw),
            "edit_index": lambda **kw: category_tools.edit_index(self.workspace, **kw),
            "read_index": lambda **kw: category_tools.read_index(self.workspace, **kw),
            "review_knowledge": lambda **kw: _run_review(
                self.llm, self.workspace, cli=self.cli,
                token_callback=lambda i, o: self._accumulate_tokens(
                    {"input_tokens": i, "output_tokens": o}
                ), **kw
            ),
            "finish_task": lambda **kw: diff_tools.finish_task(self.workspace, **kw),
            "get_unprocessed_chapters": lambda **kw: str(diff_tools.get_unprocessed_chapters(self.workspace, **kw)),
            # 统一文档管理工具
            "create_doc": lambda **kw: editor_tools.create_doc(self.workspace, **kw),
            "edit_doc": lambda **kw: editor_tools.edit_doc(self.workspace, **kw),
            "edit_doc_text": lambda **kw: editor_tools.edit_doc_text(self.workspace, **kw),
            "edit_doc_wikilink": lambda **kw: editor_tools.edit_doc_wikilink(self.workspace, **kw),
            "delete_doc": lambda **kw: editor_tools.delete_doc(self.workspace, **kw),
        }

        handler = knowledge_dispatch.get(name)
        if handler is not None:
            try:
                return handler(**args)
            except Exception as e:
                return f"错误：{e}"

        # 交给父类处理通用工具
        return super().dispatch_tool(name, args)
