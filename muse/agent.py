"""妙笔 Agent — 独立于鉴知的写作 Agent"""

import json
from pathlib import Path

from api import LLMClient
from agent.base import BaseAgent
from agent.skill import SkillRegistry
from agent.todo import TodoManager
from core.events import EventType
from muse.review_session import ReviewSession


class MuseAgent(BaseAgent):
    """妙笔 Agent — 与鉴知同级且独立，拥有完全独立的 system prompt。

    工具函数复用自 tools/ 各模块，但 prompt 不与鉴知共享。
    v5.3: 支持 chapter_ceiling 版本硬切。
    """

    _agent_name = "muse"

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, bus,
                 chapter_ceiling: int | None = None, stop_event=None):
        super().__init__(config, workspace, bus, stop_event=stop_event)
        self.skills = SkillRegistry(skills_dir)
        self.todo = TodoManager()
        self.review_session = None
        self._last_subagent_output = ""
        self._stop_agent_loop = False
        self.chapter_ceiling = chapter_ceiling  # v5.3: 知识版本卡控上限
        # 创建 LLM 客户端（供 workflow 调用使用，修复之前未定义导致的 AttributeError）
        self.llm = LLMClient(config["api"]) if "api" in config else None
        self.system_prompt = self.build_system_prompt()
        self.tool_defs = self.build_tool_defs()
        self.system_prompt += "\n\n" + self._build_tool_guide()

    def build_system_prompt(self) -> str:
        """构建妙笔独立 system prompt（不含任何鉴知要素）"""
        parts = [
            "你是妙笔（Muse），一个专业的长篇小说写作辅助助手。",
            f"当前工作区：{self.workspace.name}",
            "",
            "# Skill 指令已加载",
            "你的 system prompt 中已经包含了完整的 skill 工作流指令（<skill> 标签）。",
            "请直接按照 system prompt 中 skill 定义的步骤顺序执行，不要调用任何额外的技能工具。",
            "",
            "# 规则",
            "- 需要多步骤工作时，先制定计划再执行",
            "- 严格遵循 skill 文件中定义的步骤顺序，不要自行推断",
        ]
        return "\n".join(parts)

    def _build_tool_guide(self) -> str:
        """从当前 tool_defs 动态生成工具使用指南"""
        if not self.tool_defs:
            return ""
        names = [t["function"]["name"] for t in self.tool_defs]
        lines = ["# 可用工具"]
        for name in sorted(names):
            lines.append(f"- {name}")
        return "\n".join(lines)

    def build_tool_defs(self) -> list:
        """组装工具定义（复用 tools/ 模块的函数，不依赖鉴知）"""
        tools = [
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
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            # Wiki 只读工具
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
                    "description": "查看指定类别下的 wiki 词条列表（分页，每页 20 个）。注意：必须翻完所有页才能确认某个词条不存在！不要只看第一页就下结论！",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "page": {"type": "integer", "description": "页码，默认 1。如需查看更多请传入 page=2、page=3 等继续翻页"},
                        },
                        "required": ["category"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_wiki",
                    "description": "读取指定 wiki 词条。默认只返回 frontmatter（yaml_only=true），设置 yaml_only=false 可查看全文。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true，false 返回全文）"},
                        },
                        "required": ["category", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_wiki",
                    "description": "检查 wiki 词条在指定章节或文本中是否出现。传入 name+chapters 查章节匹配，传入 text 查文本匹配实体。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "词条名（与 chapters 配合使用）"},
                            "chapters": {"type": "string", "description": "章节范围（与 name 配合使用）"},
                            "text": {"type": "string", "description": "任意文本，自动匹配其中包含的实体名"},
                        },
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
            # 剧情卡片工具
            {
                "type": "function",
                "function": {
                    "name": "read_plot",
                    "description": "读取指定剧情卡片。yaml_only=true 只返回 frontmatter。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "yaml_only": {"type": "boolean", "description": "是否只读 frontmatter（默认 true）"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plot_list",
                    "description": "列出剧情卡片（支持 ended 过滤）。ended=\"false\" 只看未结束，\"true\" 只看已结束，\"all\" 看全部。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ended": {"type": "string", "description": "过滤：true/false/all（默认 false）"},
                            "page": {"type": "integer", "description": "页码（默认 1）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_plot_by_chapters",
                    "description": "查询指定章节区间覆盖的剧情卡片列表（含标题、区间、ended 状态）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "章节范围，如 \"1-5,7-10\""},
                        },
                        "required": ["chapters"],
                    },
                },
            },
            # 规则文档工具
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
                    "description": "读取指定规则文档。默认只返回 frontmatter（yaml_only=true），设置 yaml_only=false 可查看全文。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true，false 返回全文）"},
                        },
                        "required": ["name"],
                    },
                },
            },
            # 记忆工具
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
            # 差异工具（已废弃，重定向到 chapter_list）
            {
                "type": "function",
                "function": {
                    "name": "doc_diff",
                    "description": "[DEPRECATED] 已废弃，请使用 chapter_list 替代。查看章节列表（含处理状态）。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            # 索引工具
            {
                "type": "function",
                "function": {
                    "name": "read_index",
                    "description": "读取总 index 或指定类别 index。category=None 查看总索引。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名（None 表示总索引）"},
                        },
                    },
                },
            },
            # 审阅工具
            {
                "type": "function",
                "function": {
                    "name": "report_issue",
                    "description": "【妙笔审阅专用】报告一条审阅问题。每发现一条问题调用一次。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "level": {
                                "type": "integer",
                                "description": "问题等级：0-严重(-20分) / 1-重要(-10分) / 2-一般(-5分) / 3-可优化(-3分)",
                                "enum": [0, 1, 2, 3],
                            },
                            "quote": {"type": "string", "description": "原文引用"},
                            "description": {"type": "string", "description": "问题描述"},
                            "suggestion": {"type": "string", "description": "优化建议"},
                        },
                        "required": ["level", "quote", "description", "suggestion"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "review_done",
                    "description": "【妙笔审阅专用】所有问题报告完毕，结束审阅，触发后端自动算分。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            # Workflow 调用工具
            {
                "type": "function",
                "function": {
                    "name": "call_knowledge_workflow",
                    "description": "提交先验知识编写任务。根据词条名列表自动查找 Wiki 内容汇编参考材料，然后调用 LLM 压缩重写为结构化的先验知识文档。如有名称不存在或数量超限则拒绝调用并返回错误提示。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "wiki_only_yaml": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "只需展示 YAML frontmatter 的 wiki 词条名，至多36个",
                            },
                            "wiki_full": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "需展示完整内容（含正文）的 wiki 词条名，至多18个",
                            },
                            "rules": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "需读取的规则文档名",
                            },
                        },
                        "required": ["wiki_only_yaml", "wiki_full", "rules"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "call_plot_workflow",
                    "description": "提交前情提要编写任务。根据剧情卡片名自动查找内容并生成前情提要。如有名称不存在或数量超限则拒绝调用并返回错误提示。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "plot_only_yaml": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "只需展示 YAML frontmatter 的剧情卡片名，至多24个",
                            },
                            "plot_full": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "需展示完整内容（含正文）的剧情卡片名，至多12个",
                            },
                        },
                        "required": ["plot_only_yaml", "plot_full"],
                    },
                },
            },
        ]
        return tools

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由（独立于鉴知）"""
        # 妙笔审阅工具
        if name == "report_issue":
            if self.review_session is None:
                return "错误：report_issue 仅在妙笔审阅模式下可用"
            return self.review_session.report_issue(**args)
        if name == "review_done":
            if self.review_session is None:
                return "错误：review_done 仅在妙笔审阅模式下可用"
            result = self.review_session.review_done()
            self._stop_agent_loop = True
            return json.dumps(result, ensure_ascii=False)

        # Workflow 调用 — 输出存入 _last_subagent_output，供 Workflow 取用
        if name == "call_knowledge_workflow":
            from tools.knowledge_workflow import KnowledgeWorkflow
            # v6.5.3: 先发射计划事件（前端渲染可浏览的写作计划卡片），再流式执行
            self.bus.emit(EventType.PLAN_READY, {
                "kind": "knowledge",
                "items": {
                    "wiki_only_yaml": args.get("wiki_only_yaml", []),
                    "wiki_full": args.get("wiki_full", []),
                    "rules": args.get("rules", []),
                },
            }, source="muse")
            # 注入私有总线：撰写过程经 drain 线程转发到全局总线 → SSE → 前端实时展示
            # v6.5.6: 注入 stop_event——用户终止时 workflow 的 LLM 流式循环内即时打断
            wf = KnowledgeWorkflow(llm=self.llm, workspace=self.workspace, bus=self.bus,
                                   stop_event=self.stop_event)
            result = wf.validate_and_run(**args)
            if result.startswith("错误"):
                # 校验失败，返回错误信息让 LLM 修正后重试，不终止循环
                return result
            self._last_subagent_output = result
            self.bus.emit(EventType.OUTPUT, {"text": result, "kind": "prior_knowledge"}, source="muse")
            self._stop_agent_loop = True
            return "(先验知识已生成)"

        if name == "call_plot_workflow":
            from tools.plot_workflow import PlotWorkflow
            # v6.5.3: 先发射计划事件（前端渲染可浏览的写作计划卡片），再流式执行
            self.bus.emit(EventType.PLAN_READY, {
                "kind": "plot",
                "items": {
                    "plot_only_yaml": args.get("plot_only_yaml", []),
                    "plot_full": args.get("plot_full", []),
                },
            }, source="muse")
            # 注入私有总线：撰写过程经 drain 线程转发到全局总线 → SSE → 前端实时展示
            # v6.5.6: 注入 stop_event——用户终止时 workflow 的 LLM 流式循环内即时打断
            wf = PlotWorkflow(llm=self.llm, workspace=self.workspace, bus=self.bus,
                              stop_event=self.stop_event)
            result = wf.validate_and_run(**args)
            if result.startswith("错误"):
                return result
            self._last_subagent_output = result
            self.bus.emit(EventType.OUTPUT, {"text": result, "kind": "plot_summary"}, source="muse")
            self._stop_agent_loop = True
            return "(前情提要已生成)"

        # 通用工具路由
        from tools import chapter as chapter_tools
        from tools import wiki as wiki_tools
        from tools import category as category_tools
        from tools import relation as relation_tools
        from tools import rules as rules_tools
        from tools import memory as memory_tools
        from tools import diff as diff_tools
        from tools import plot as plot_tools

        # v5.3: 版本硬切拦截
        if self.chapter_ceiling is not None:
            ceiling = self.chapter_ceiling

            if name == "wiki_list":
                return self._wiki_list_at_ceiling(args, ceiling)
            if name == "read_wiki":
                return self._read_wiki_at_ceiling(args, ceiling)
            if name == "plot_list":
                return self._plot_list_at_ceiling(args, ceiling)
            if name == "read_plot":
                return self._read_plot_at_ceiling(args, ceiling)
            if name == "rules_list":
                return self._rules_list_at_ceiling(ceiling)
            if name == "read_rule":
                return self._read_rule_at_ceiling(args, ceiling)
            if name == "check_wiki":
                return self._check_wiki_at_ceiling(args, ceiling)

        dispatch = {
            "agent_output": lambda **kw: "(已输出)",

            "keywords_stat": lambda **kw: chapter_tools.keywords_stat(self.workspace, **kw),
            "chapter_list": lambda **kw: chapter_tools.chapter_list(self.workspace),
            "category_list": lambda **kw: category_tools.category_list(self.workspace),
            "wiki_list": lambda **kw: wiki_tools.wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: wiki_tools.read_wiki(self.workspace, **kw),
            "check_wiki": lambda **kw: wiki_tools.check_wiki(self.workspace, **kw),
            "query_relations": lambda **kw: relation_tools.query_relations(self.workspace, **kw),
            "read_plot": lambda **kw: plot_tools.read_plot(self.workspace, **kw),
            "plot_list": lambda **kw: plot_tools.plot_list(self.workspace, **kw),
            "query_plot_by_chapters": lambda **kw: plot_tools.query_plot_by_chapters(self.workspace, **kw),
            "rules_list": lambda **kw: rules_tools.rules_list(self.workspace),
            "read_rule": lambda **kw: rules_tools.read_rule(self.workspace, **kw),
            "read_memory": lambda **kw: memory_tools.read_memory(self.workspace, **kw),
            "read_index": lambda **kw: category_tools.read_index(self.workspace, **kw),
            "doc_diff": lambda **kw: diff_tools.doc_diff(self.workspace),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    # ---- v5.3 版本硬切辅助方法 ----

    def _get_db(self):
        """获取 DB 服务实例"""
        from tools.db.service import SQLiteService
        return SQLiteService(self.workspace / "wiki.db")

    def _tool_read_at_ceiling(self, doc_type: str, name: str, ceiling: int, yaml_only: bool = True) -> str:
        """通用方法：读取指定文档类型 ≤ ceiling 的最新版本（消除6个 _at_ceiling 方法的重复）"""
        from tools.editor import build_frontmatter
        db = self._get_db()

        # 根据 doc_type 选择对应的查找方法
        finder_map = {
            "wiki": "wiki_find_main",
            "plot": "plot_find_main",
            "rule": "rule_find_main",
        }
        finder = getattr(db, finder_map[doc_type])
        main = finder(name)

        type_labels = {"wiki": "wiki", "plot": "剧情卡片", "rule": "规则"}
        type_label = type_labels[doc_type]

        if main is None:
            db.close()
            return f"错误：{type_label}「{name}」不存在"

        if main["created_chapter"] > ceiling:
            db.close()
            return f"错误：{type_label}「{name}」不存在（在第 {ceiling} 章时尚未创建）"

        version = db.latest_version_at(doc_type, main["id"], ceiling)
        db.close()
        if version is None:
            return f"错误：{type_label}「{name}」在第 {ceiling} 章之前无版本记录"

        meta = {
            "title": name,
            "type": doc_type,
            "keywords": version.get("keywords", ""),
            "description": version.get("description", ""),
            "state": version.get("state", ""),
        }
        if doc_type == "wiki":
            # wiki 需要类别名
            from tools.editor import _get_proxy
            cat = _get_proxy(self.workspace)._db.get_category(main["category_id"])
            meta["type"] = cat["name"] if cat else ""
            meta["tags"] = version.get("tags", [])
        elif doc_type == "plot":
            meta["tags"] = version.get("tags", [])
            meta["chapters"] = main.get("chapters", "")

        fm = build_frontmatter(meta)
        if yaml_only:
            return fm
        content = version.get("content", "")
        return f"{fm}\n{content}"

    def _tool_list_at_ceiling(self, doc_type: str, ceiling: int, category: str = None) -> str:
        """通用方法：列出指定文档类型 ≤ ceiling 的所有条目"""
        db = self._get_db()

        if doc_type == "wiki":
            if not category:
                return "错误：wiki_list 需要提供 category 参数"
            cat = db.get_category_by_name(category)
            if cat is None:
                db.close()
                return f"错误：类别「{category}」不存在"
            mains = db.wiki_list_main_at(cat["id"], ceiling)
            db.close()
            if not mains:
                return f"类别「{category}」在第 {ceiling} 章之前无词条。"
            lines = [f"类别「{category}」词条（≤ 第{ceiling}章，共 {len(mains)} 个）："]
            for m in mains:
                lines.append(f"  - {m['name']}")
        elif doc_type == "plot":
            mains = db.plot_list_main_at(ceiling)
            db.close()
            if not mains:
                return f"在第 {ceiling} 章之前无剧情卡片。"
            lines = [f"剧情卡片（≤ 第{ceiling}章，共 {len(mains)} 个）："]
            for m in mains:
                ended_mark = "已结束" if m.get("ended") else "未结束"
                lines.append(f"  - {m['name']}（{m.get('chapters', '')}，{ended_mark}）")
        elif doc_type == "rule":
            mains = db.rule_list_main_at(ceiling)
            db.close()
            if not mains:
                return f"在第 {ceiling} 章之前无规则文档。"
            lines = [f"规则文档（≤ 第{ceiling}章，共 {len(mains)} 个）："]
            for m in mains:
                lines.append(f"  - {m['name']}")
        else:
            db.close()
            return f"错误：不支持的文档类型 {doc_type}"

        return "\n".join(lines)

    def _wiki_list_at_ceiling(self, args: dict, ceiling: int) -> str:
        """wiki_list 硬切：仅列 created_chapter ≤ ceiling 的词条"""
        category = args.get("category", "")
        page = args.get("page", 1)
        page_size = args.get("page_size", 20)

        result = self._tool_list_at_ceiling("wiki", ceiling, category)
        if result.startswith("错误") or "无词条" in result:
            return result

        # 需要分页，重新获取完整列表
        db = self._get_db()
        cat = db.get_category_by_name(category)
        mains = db.wiki_list_main_at(cat["id"], ceiling)
        db.close()

        total = len(mains)
        total_pages = (total + page_size - 1) // page_size
        start = (page - 1) * page_size
        end = start + page_size
        page_items = mains[start:end]

        lines = [f"类别「{category}」词条（≤ 第{ceiling}章，第 {page}/{total_pages} 页，共 {total} 个）："]
        for m in page_items:
            lines.append(f"  - {m['name']}")
        return "\n".join(lines)

    def _read_wiki_at_ceiling(self, args: dict, ceiling: int) -> str:
        """read_wiki 硬切"""
        return self._tool_read_at_ceiling("wiki", args.get("name", ""), ceiling, args.get("yaml_only", True))

    def _plot_list_at_ceiling(self, args: dict, ceiling: int) -> str:
        """plot_list 硬切"""
        return self._tool_list_at_ceiling("plot", ceiling)

    def _read_plot_at_ceiling(self, args: dict, ceiling: int) -> str:
        """read_plot 硬切"""
        return self._tool_read_at_ceiling("plot", args.get("name", ""), ceiling, args.get("yaml_only", True))

    def _rules_list_at_ceiling(self, ceiling: int) -> str:
        """rules_list 硬切"""
        return self._tool_list_at_ceiling("rule", ceiling)

    def _read_rule_at_ceiling(self, args: dict, ceiling: int) -> str:
        """read_rule 硬切"""
        return self._tool_read_at_ceiling("rule", args.get("name", ""), ceiling, args.get("yaml_only", True))

    def _check_wiki_at_ceiling(self, args: dict, ceiling: int) -> str:
        """check_wiki 硬切：仅检查 created_chapter ≤ ceiling 的词条"""
        from tools import wiki as wiki_tools
        # check_wiki 本身不涉及版本读取，直接透传
        return wiki_tools.check_wiki(self.workspace, **args)
