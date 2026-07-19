"""鉴知 Agent — 组装 system prompt、tool defs、工具路由"""

from pathlib import Path

from agent.base import BaseAgent
from agent.todo import TodoManager
from agent.compact import ContextManager
from agent.skill import SkillRegistry
from agent.loop import agent_loop
from agent.permission import PermissionManager
from tools import chapter as chapter_tools
from tools import wiki as wiki_tools
from tools import rules as rules_tools
from tools import relation as relation_tools
from tools import category as category_tools
from tools import memory as memory_tools
from tools import diff as diff_tools


TOOL_RESULTS_DIR = Path(".task_outputs") / "tool-results"


class JianzhiAgent(BaseAgent):
    """写作智能体 — 组装各模块并提供统一入口"""

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, cli):
        super().__init__(config, workspace, cli)
        self.todo = TodoManager()
        self.context = ContextManager()
        self.skills = SkillRegistry(skills_dir)
        self.permission = PermissionManager()

        self.system_prompt = self.build_system_prompt()
        self.tool_defs = self.build_tool_defs()

    def build_system_prompt(self) -> str:
        """组装 system prompt"""
        parts = [
            f"你是鉴知（Jianzhi），一个专业的写作智能体。",
            f"当前工作区：{self.workspace.name}",
            f"当前目录：{self.workspace}",
            "",
            "# 可用技能",
            self.skills.describe_available(),
            "",
            "# 工具使用指南",
            "- 使用 read_chapters 读取章节正文",
            "- 使用 chapter_list 查看章节列表",
            "- 使用 keywords_stat 统计关键词词频",
            "- 使用 update_todo 管理当前任务计划",
            "- 使用 agent_output 进行中间轮输出",
            "- 使用 tools_log_check 查询被压缩的工具调用记录",
            "- 使用 handoff_knowledge 进入 Knowledge 专家模式（知识提取、Wiki管理）",
            "- 通过技能名调用对应技能",
            "",
            "# Wiki 知识库（只读）",
            "工作区可能包含 Wiki 知识库（wiki/目录），其中存储了已提取的结构化知识。",
            "当用户询问故事相关的问题时，**优先使用 Wiki 进行检索**，而不是直接翻原文。",
            "",
            "### Wiki 查询工具",
            "- 使用 category_list 查看有哪些类别（人物/势力/地点/功法/宝物）",
            "- 使用 wiki_list <类别> 查看某类别下的所有词条",
            "- 使用 read_wiki <类别> <词条名> 读取词条完整内容（含 frontmatter）",
            "- 使用 check_wiki <词条名> <章节> 检查词条在章节中是否出现",
            "- 使用 query_relations <词条名> 查看词条关联关系",
            "- 使用 rules_list 查看规则文档列表",
            "- 使用 read_rule <规则名> 读取规则文档",
            "- 使用 read_memory 读取记忆索引/<name> 读取指定记忆",
            "- 使用 doc_diff 查看新增/修改的章节",
            "",
            "### Wiki 优先 RAG 原则（重要）",
            "**核心原则**：面对已有 wiki 词条的知识检索，必须先用 wiki 进行 RAG，而不是直接翻原文。",
            "",
            "正确的检索顺序：",
            "1. category_list → 查看有哪些类别",
            "2. wiki_list <类别> → 查看该类别下有哪些已有词条",
            "3. read_wiki <类别> <词条名> → 读取相关词条内容",
            "4. check_wiki <词条名> <章节> → 检查词条在章节中是否出现",
            "5. 只有以上无法满足需求时，才用 read_chapters 读取章节原文",
            "",
            "**禁止行为**：",
            "- ❌ 跳过 wiki 直接 read_chapters 全文阅读",
            "- ❌ 已有 wiki 词条的情况下，不查 wiki 就去翻原文",
            "- ❌ 把 wiki 能解答的问题变成大段章节阅读",
            "",
            "# 模式切换",
            "- 当用户要求「提取知识」「更新 wiki」「管理知识库」等知识相关任务时，",
            "  调用 handoff_knowledge 进入 Knowledge 专家模式",
            "- Knowledge 模式拥有完整的 Wiki 管理工具集（new_wiki / knowledge_task 等）",
            "- 切换后会询问用户确认，确认后方可执行",
            "",
            "# 规则",
            "- 需要多步骤工作时，先用 update_todo 制定计划",
            "- 每次只处理一个 in_progress 步骤",
            "- 完成后标记为 completed 并推进下一步",
        ]
        return "\n".join(parts)

    def build_tool_defs(self) -> list:
        """组装 OpenAI 格式 tool definitions"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "update_todo",
                    "description": "更新会话计划列表。多步骤工作前先制定计划。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "计划项列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string", "description": "计划内容"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                            "description": "pending=待办, in_progress=进行中, completed=已完成",
                                        },
                                        "activeForm": {"type": "string", "description": "当前步骤的具体操作描述"},
                                    },
                                    "required": ["content", "status"],
                                },
                            }
                        },
                        "required": ["items"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tools_log_check",
                    "description": "查询被压缩的历史工具调用记录",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_use_id": {
                                "type": "string",
                                "description": "要查询的工具调用 ID",
                            }
                        },
                        "required": ["tool_use_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "agent_output",
                    "description": "中间轮输出。调用后直接输出文本，不打断会话流程。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要输出的文本"},
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_chapters",
                    "description": "读取指定章节的正文。支持范围表达式如 \"1-3,5,7-9\"。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {
                                "type": "string",
                                "description": "章节范围，如 \"1-3,5,7-9\"",
                            }
                        },
                        "required": ["chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "keywords_stat",
                    "description": "分章节统计指定关键词的词频",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "章节范围"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "关键词列表",
                            },
                        },
                        "required": ["chapters", "keywords"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "chapter_list",
                    "description": "获取当前工作区的章节列表（含标题）",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "handoff_knowledge",
                    "description": "切换到 Knowledge 专家模式（用于知识提取、Wiki 管理）。当用户需要提取章节知识、管理 wiki、更新知识库时调用此工具。调用后系统会询问用户确认。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            # Wiki 只读工具（通用模式可用）
            {
                "type": "function",
                "function": {
                    "name": "category_list",
                    "description": "查看 wiki 类别列表（如人物、势力、地点、功法、宝物等）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "wiki_list",
                    "description": "查看指定类别下的 wiki 词条列表（分页，每页 20 个）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "page": {"type": "integer", "description": "页码（默认 1）"},
                        },
                        "required": ["category"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_wiki",
                    "description": "读取指定 wiki 词条的完整内容（含 frontmatter）。先查 category_list 得到类别名，再调用此工具。",
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
                    "name": "check_wiki",
                    "description": "检查 wiki 词条在指定章节中是否出现",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "词条名"},
                            "chapters": {"type": "string", "description": "章节范围"},
                        },
                        "required": ["name", "chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_relations",
                    "description": "查询指定词条的所有关联词条",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "词条名"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rules_list",
                    "description": "查看规则文档列表（rules/ 目录下的世界观规则，如境界体系）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_rule",
                    "description": "读取指定规则文档",
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
                    "name": "read_memory",
                    "description": "读取记忆文档（name=None 时读取索引 MEMORY.md）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "记忆名（None 表示索引）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "doc_diff",
                    "description": "对比文档哈希，查看新增/修改的章节",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

        # 为每个 skill 生成一个工具
        for skill_name in self.skills.skill_names():
            tools.append({
                "type": "function",
                "function": {
                    "name": skill_name,
                    "description": f"调用技能「{skill_name}」",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            })

        return tools

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        # 统一权限检查
        if name in ("handoff_knowledge", "new_wiki", "edit_wiki", "delete_wiki",
                     "new_category", "edit_category", "new_rule", "edit_rule",
                     "delete_rule", "knowledge_task", "edit_index"):
            result = self.permission.check(name)
            if result is not None:
                return result

        # skill 调用
        if name in self.skills.skill_names():
            self.context.track_skill(name)
            return self.skills.load_full_text(name)

        dispatch = {
            "update_todo": self._handle_todo,
            "tools_log_check": self._handle_tools_log_check,
            "agent_output": lambda **kw: "(已输出)",
            "read_chapters": self._handle_read_chapters,
            "keywords_stat": self._handle_keywords_stat,
            "chapter_list": self._handle_chapter_list,
            # Wiki 只读工具
            "category_list": lambda **kw: category_tools.category_list(self.workspace),
            "wiki_list": lambda **kw: wiki_tools.wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: wiki_tools.read_wiki(self.workspace, **kw),
            "check_wiki": lambda **kw: wiki_tools.check_wiki(self.workspace, **kw),
            "query_relations": lambda **kw: relation_tools.query_relations(self.workspace, **kw),
            "rules_list": lambda **kw: rules_tools.rules_list(self.workspace),
            "read_rule": lambda **kw: rules_tools.read_rule(self.workspace, **kw),
            "read_memory": lambda **kw: memory_tools.read_memory(self.workspace, **kw),
            "doc_diff": lambda **kw: diff_tools.doc_diff(self.workspace),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    # ---- 工具 handlers ----
    def _handle_todo(self, items: list) -> str:
        return self.todo.update(items)

    def _handle_tools_log_check(self, tool_use_id: str) -> str:
        path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            return text[:30000]
        return f"未找到工具调用记录：{tool_use_id}"

    def _handle_read_chapters(self, chapters: str) -> str:
        result = chapter_tools.read_chapters(self.workspace, chapters)
        # 追踪已读章节
        nums = chapter_tools.parse_chapter_spec(chapters)
        titles = []
        for n in nums:
            t, _ = chapter_tools._read_chapter_file(self.workspace / "document", n)
            titles.append(t or f"第{n}章")
        self.context.track_chapter(
            [str(n) for n in nums],
            titles,
        )
        return result

    def _handle_keywords_stat(self, chapters: str, keywords: list) -> str:
        return chapter_tools.keywords_stat(self.workspace, chapters, keywords)

    def _handle_chapter_list(self) -> str:
        return chapter_tools.chapter_list(self.workspace)

    def chat(self, user_input: str):
        """处理一条用户输入

        Returns:
            True 如果请求了切换到 Knowledge 模式，否则 False
        """
        self.messages.append({"role": "user", "content": user_input})
        self.messages = agent_loop(self, self.messages)

        # 打印最终输出（取最后一条 assistant 的文本回复）
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"].strip()
                if text:
                    self.cli.print_output(text)
                break

        return self.permission.handoff_requested

    def context_report(self) -> str:
        """/context 指令"""
        return self.context.context_report(self.messages)

    def compact_history(self):
        """主动压缩（/compact 指令）"""
        self.context.mark_compacted()
        self.messages = [{
            "role": "user",
            "content": "（上下文已压缩，继续当前工作）"
        }]
        self.cli.print_info("上下文已压缩。")
