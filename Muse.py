"""妙笔主流程编排 — ReviewSession + MuseAgent + MuseWorkflow 四步状态机

妙笔（Muse）是与鉴知（Jianzhi）同级且独立的写作工作流。
工具函数复用自 tools/ 各模块，但 prompt 不与鉴知共享。
"""

import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from api import LLMClient
from agent.base import BaseAgent
from agent.skill import SkillRegistry
from agent.todo import TodoManager
from agent.loop import agent_loop
from tools.muse_io import MuseIO
from tools import workspace as workspace_tools
from tools.polish import polish_draft


# ============================================================
# ReviewSession — 审阅会话
# ============================================================

@dataclass
class ReviewSession:
    """一次审阅会话的状态，由 report_issue/review_done 工具共享

    v5.4: 新增 previous_issues 供增量审阅上下文注入。
    """
    issues: list = field(default_factory=list)
    previous_issues: list = field(default_factory=list)  # 上轮审阅意见
    previous_score: int | None = None  # 上轮分数（仅供参考展示）

    def report_issue(self, level: int, quote: str, description: str, suggestion: str) -> str:
        """记录一条审阅问题"""
        self.issues.append({
            "level": level,
            "quote": quote,
            "description": description,
            "suggestion": suggestion,
        })
        return f"已记录问题：{description}"

    def review_done(self) -> dict:
        """审阅结束，计算得分"""
        score = 100
        for issue in self.issues:
            level = issue["level"]
            if level == 0:
                score -= 20
            elif level == 1:
                score -= 10
            elif level == 2:
                score -= 5
            elif level == 3:
                score -= 3
        score = max(0, score)

        return {
            "score": score,
            "pass": score >= 85,
            "issues": self.issues,
        }

    def clear(self):
        """清空 issue 列表（下一轮重写后调用）"""
        self.issues.clear()


# ============================================================
# MuseAgent — 独立于鉴知的妙笔 Agent
# ============================================================

class MuseAgent(BaseAgent):
    """妙笔 Agent — 与鉴知同级且独立，拥有完全独立的 system prompt。

    工具函数复用自 tools/ 各模块，但 prompt 不与鉴知共享。
    v5.3: 支持 chapter_ceiling 版本硬切。
    """

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, cli,
                 chapter_ceiling: int | None = None):
        super().__init__(config, workspace, cli)
        self.skills = SkillRegistry(skills_dir)
        self.todo = TodoManager()
        self.review_session = None
        self._last_subagent_output = ""
        self._stop_agent_loop = False
        self.chapter_ceiling = chapter_ceiling  # v5.3: 知识版本卡控上限
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
            wf = KnowledgeWorkflow(llm=self.llm, workspace=self.workspace, cli=self.cli)
            result = wf.validate_and_run(**args)
            if result.startswith("错误"):
                # 校验失败，返回错误信息让 LLM 修正后重试，不终止循环
                return result
            self._last_subagent_output = result
            self.cli.print_output(result)
            self._stop_agent_loop = True
            return "(先验知识已生成)"

        if name == "call_plot_workflow":
            from tools.plot_workflow import PlotWorkflow
            wf = PlotWorkflow(llm=self.llm, workspace=self.workspace, cli=self.cli)
            result = wf.validate_and_run(**args)
            if result.startswith("错误"):
                return result
            self._last_subagent_output = result
            self.cli.print_output(result)
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

    def _wiki_list_at_ceiling(self, args: dict, ceiling: int) -> str:
        """wiki_list 硬切：仅列 created_chapter ≤ ceiling 的词条"""
        db = self._get_db()
        category = args.get("category", "")
        page = args.get("page", 1)
        page_size = args.get("page_size", 20)

        cat = db.get_category_by_name(category)
        if cat is None:
            db.close()
            return f"错误：类别「{category}」不存在"

        mains = db.wiki_list_main_at(cat["id"], ceiling)
        db.close()

        if not mains:
            return f"类别「{category}」在第 {ceiling} 章之前无词条。"

        # 分页
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
        """read_wiki 硬切：读取 ≤ ceiling 的最新版本"""
        from tools.editor import _get_proxy, build_frontmatter
        db = self._get_db()
        name = args.get("name", "")
        yaml_only = args.get("yaml_only", True)

        main = db.wiki_find_main(name)
        if main is None:
            db.close()
            return f"错误：wiki「{name}」不存在"

        # 检查 created_chapter 是否超出 ceiling
        if main["created_chapter"] > ceiling:
            db.close()
            return f"错误：wiki「{name}」不存在（在第 {ceiling} 章时尚未创建）"

        version = db.latest_version_at("wiki", main["id"], ceiling)
        db.close()
        if version is None:
            return f"错误：wiki「{name}」在第 {ceiling} 章之前无版本记录"

        # 构建 frontmatter 输出
        cat = _get_proxy(self.workspace)._db.get_category(main["category_id"])
        cat_name = cat["name"] if cat else ""
        meta = {
            "title": name,
            "type": cat_name,
            "keywords": version.get("keywords", ""),
            "description": version.get("description", ""),
            "state": version.get("state", ""),
            "tags": version.get("tags", []),
        }
        fm = build_frontmatter(meta)
        if yaml_only:
            return fm
        content = version.get("content", "")
        return f"{fm}\n{content}"

    def _plot_list_at_ceiling(self, args: dict, ceiling: int) -> str:
        """plot_list 硬切：仅列 created_chapter ≤ ceiling 的剧情卡片"""
        db = self._get_db()
        mains = db.plot_list_main_at(ceiling)
        db.close()

        if not mains:
            return f"在第 {ceiling} 章之前无剧情卡片。"

        lines = [f"剧情卡片（≤ 第{ceiling}章，共 {len(mains)} 个）："]
        for m in mains:
            ended_mark = "已结束" if m.get("ended") else "未结束"
            lines.append(f"  - {m['name']}（{m.get('chapters', '')}，{ended_mark}）")
        return "\n".join(lines)

    def _read_plot_at_ceiling(self, args: dict, ceiling: int) -> str:
        """read_plot 硬切：读取 ≤ ceiling 的最新版本"""
        from tools.editor import build_frontmatter
        db = self._get_db()
        name = args.get("name", "")
        yaml_only = args.get("yaml_only", True)

        main = db.plot_find_main(name)
        if main is None:
            db.close()
            return f"错误：剧情卡片「{name}」不存在"

        if main["created_chapter"] > ceiling:
            db.close()
            return f"错误：剧情卡片「{name}」不存在（在第 {ceiling} 章时尚未创建）"

        version = db.latest_version_at("plot", main["id"], ceiling)
        db.close()
        if version is None:
            return f"错误：剧情卡片「{name}」在第 {ceiling} 章之前无版本记录"

        meta = {
            "title": name,
            "type": "plot",
            "keywords": version.get("keywords", ""),
            "description": version.get("description", ""),
            "state": version.get("state", ""),
            "tags": version.get("tags", []),
            "chapters": main.get("chapters", ""),
        }
        fm = build_frontmatter(meta)
        if yaml_only:
            return fm
        content = version.get("content", "")
        return f"{fm}\n{content}"

    def _rules_list_at_ceiling(self, ceiling: int) -> str:
        """rules_list 硬切：仅列 created_chapter ≤ ceiling 的规则"""
        db = self._get_db()
        mains = db.rule_list_main_at(ceiling)
        db.close()

        if not mains:
            return f"在第 {ceiling} 章之前无规则文档。"

        lines = [f"规则文档（≤ 第{ceiling}章，共 {len(mains)} 个）："]
        for m in mains:
            lines.append(f"  - {m['name']}")
        return "\n".join(lines)

    def _read_rule_at_ceiling(self, args: dict, ceiling: int) -> str:
        """read_rule 硬切：读取 ≤ ceiling 的最新版本"""
        from tools.editor import build_frontmatter
        db = self._get_db()
        name = args.get("name", "")
        yaml_only = args.get("yaml_only", True)

        main = db.rule_find_main(name)
        if main is None:
            db.close()
            return f"错误：规则「{name}」不存在"

        if main["created_chapter"] > ceiling:
            db.close()
            return f"错误：规则「{name}」不存在（在第 {ceiling} 章时尚未创建）"

        version = db.latest_version_at("rule", main["id"], ceiling)
        db.close()
        if version is None:
            return f"错误：规则「{name}」在第 {ceiling} 章之前无版本记录"

        meta = {
            "title": name,
            "type": "rule",
            "keywords": version.get("keywords", ""),
            "description": version.get("description", ""),
        }
        fm = build_frontmatter(meta)
        if yaml_only:
            return fm
        content = version.get("content", "")
        return f"{fm}\n{content}"

    def _check_wiki_at_ceiling(self, args: dict, ceiling: int) -> str:
        """check_wiki 硬切：仅检查 created_chapter ≤ ceiling 的词条"""
        from tools import wiki as wiki_tools
        # check_wiki 本身不涉及版本读取，直接透传
        return wiki_tools.check_wiki(self.workspace, **args)


# ============================================================
# MuseWorkflow — 妙笔主流程编排
# ============================================================

class MuseWorkflow:
    """妙笔工作流——四步状态机

    步骤：
    ① 大纲输入
    ② 知识准备（先验知识 + 前情提要）
    ③ 润色写作
    ④ 写作审阅（可循环③④直至通过）
    """

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, workspaces_dir: Optional[Path] = None,
                 io=None, outline_text: str = "", auto_approve: bool = False,
                 chapter: int | None = None):
        self.workspace = workspace
        self.skills_dir = skills_dir
        self.workspaces_dir = workspaces_dir
        self.llm_config = config["api"]
        self.io = MuseIO(workspace)
        self.outline: str = outline_text
        self.prior_knowledge: str = ""
        self.plot_summary: str = ""
        self.current_draft: str = ""
        self.issues: list = []  # 携带到下一轮的 issue 列表
        self.change_log: list[str] = []  # v5.4: Writer 修改轮的变更日志
        self._token_stats = {}  # step_name -> {input, output, total}
        self._token_total = {"input": 0, "output": 0, "total": 0}
        self._io_channel = io  # v5.2: IOChannel 实例
        self._auto_approve = auto_approve  # v5.2: 自动确认模式
        # v5.3: 章节锚定
        self._chapter_arg = chapter  # 用户传入的目标章节号（None 表示自动）
        self.target_chapter: int | None = None  # run() 时解析
        self.chapter_ceiling: int | None = None  # target_chapter - 1，知识版本卡控上限

    def run(self):
        """运行妙笔工作流"""
        self._resolve_chapter_anchor()
        if not self.outline:
            self._step_input_outline()
        else:
            self.io.save_outline(self.outline)
        self._step_knowledge_prep()
        self._step_writing_loop()
        self._finish()

    def _resolve_chapter_anchor(self):
        """解析目标章节号和版本卡控上限"""
        from tools.db.service import SQLiteService
        db = SQLiteService(self.workspace / "wiki.db")
        max_ch = db.chapter_max_num()
        db.close()

        if self._chapter_arg:
            self.target_chapter = self._chapter_arg
        else:
            self.target_chapter = max_ch + 1 if max_ch > 0 else 1

        # ceiling = N-1，用于知识版本卡控
        self.chapter_ceiling = self.target_chapter - 1 if self.target_chapter > 1 else None

        # 校验上一章是否存在（target=1 时无需上一章）
        if self.target_chapter > 1:
            prev_num = self.target_chapter - 1
            from tools.chapter import read_chapters
            prev_text = read_chapters(self.workspace, str(prev_num))
            if prev_text.startswith("错误"):
                print(f"错误：第 {prev_num} 章不存在，请先导入后再写第 {self.target_chapter} 章。")
                import sys
                sys.exit(1)

        print(f"目标章节：第 {self.target_chapter} 章")
        if self.chapter_ceiling:
            print(f"知识版本卡控：≤ 第 {self.chapter_ceiling} 章")

    # ---- v5.2 I/O 辅助 ----

    def _confirm(self, prompt: str) -> bool:
        """y/n 确认，尊重 _auto_approve"""
        if self._auto_approve:
            return True
        print(prompt)
        try:
            return input().strip().lower() == "y"
        except (EOFError, KeyboardInterrupt):
            return True

    def _input(self, prompt: str = "") -> str:
        """读取输入，_auto_approve 时返回空"""
        if self._auto_approve:
            return ""
        if prompt:
            print(prompt)
        try:
            return input().strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    # ---- 工作区切换 ----

    def _switch_workspace_interactive(self):
        """交互式切换工作区"""
        if not self.workspaces_dir:
            print("（未配置工作区目录，无法切换）")
            return
        ws_list = workspace_tools.list_workspaces(self.workspaces_dir)
        print("\n" + ws_list)
        print("请输入编号切换工作区（直接回车取消）：")
        try:
            choice = input().strip()
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if not choice:
            print("已取消切换。")
            return
        try:
            idx = int(choice) - 1
            entries = sorted([d for d in self.workspaces_dir.iterdir() if d.is_dir()])
            if idx < 0 or idx >= len(entries):
                print(f"错误：编号 {choice} 超出范围")
                return
            new_workspace = entries[idx]
        except ValueError:
            print(f"错误：无效编号「{choice}」")
            return

        old_name = self.workspace.name
        self.workspace = new_workspace
        self.io = MuseIO(self.workspace)
        print(f"已切换到工作区「{self.workspace.name}」")

    # ---- 步骤①：大纲输入 ----

    def _step_input_outline(self):
        """① 大纲输入"""
        print("=" * 40)
        print("妙笔写作工作流 - 第一步：大纲输入")

        # 确认/切换工作区
        print(f"当前工作区：{self.workspace.name}")
        print("确认使用当前工作区？[y/n]")
        try:
            choice = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "y"
        if choice == "n":
            self._switch_workspace_interactive()

        print("请输入大纲或章节草稿（多行输入，输入 qqq 结束）：")
        print("-" * 40)

        lines = []
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == "qqq":
                break
            lines.append(line)

        self.outline = "\n".join(lines).strip()
        self.io.save_outline(self.outline)
        print(f"\n已保存大纲（{len(self.outline)} 字）")

    # ---- Token 统计 ----

    def _update_token_stats(self, step_name: str, usage: dict):
        """记录一步的 token 用量"""
        input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        total = input_tokens + output_tokens
        self._token_stats[step_name] = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
        }
        self._token_total["input"] += input_tokens
        self._token_total["output"] += output_tokens
        self._token_total["total"] += total

    def _update_token_stats_from_agent(self, step_name: str, agent):
        """从 agent._token_accum 记录 token 用量"""
        accum = getattr(agent, "_token_accum", None)
        if accum and (accum["input"] > 0 or accum["output"] > 0):
            self._token_stats[step_name] = {
                "input": accum["input"],
                "output": accum["output"],
                "total": accum["total"],
            }
            self._token_total["input"] += accum["input"]
            self._token_total["output"] += accum["output"]
            self._token_total["total"] += accum["total"]

    def _update_token_stats_from_wf(self, step_name: str, wf):
        """从 Workflow._last_usage 记录 token 用量"""
        usage = getattr(wf, "_last_usage", {})
        if usage:
            self._update_token_stats(step_name, usage)

    def _save_token_stats(self):
        """将 token 统计写入任务目录下的 token.json"""
        data = {
            "steps": dict(self._token_stats),
            "total": dict(self._token_total),
        }
        path = self.io.task_dir / "token.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 步骤②：知识准备 ----

    def _step_knowledge_prep(self):
        """② 知识准备

        顺序：先生成先验知识 → 用户确认 → 再生成前情提要 → 用户确认
        """
        print("=" * 40)
        print("第二步：知识准备")

        # ---- 先验知识 ----
        while True:
            print("正在生成先验知识...")
            self.prior_knowledge = self._run_researcher()
            self.io.save_prior_knowledge(self.prior_knowledge)
            print("\n" + "=" * 40)
            print("先验知识：")
            print(self.prior_knowledge)
            print("\n确认知识准备通过？[y/n]")
            if self._confirm(""):
                break
            else:
                reason = self._input("请输入打回理由：")
                print("正在根据反馈增量修正...")
                self._revise_knowledge(reason)

        # ---- 前情提要 ----
        while True:
            print("正在生成前情提要...")
            self.plot_summary = self._run_plot_summary()
            self.io.save_plot_summary(self.plot_summary)
            print("\n" + "=" * 40)
            print("前情提要：")
            self.plot_summary = textwrap.dedent(self.plot_summary)
            print(self.plot_summary)
            print("\n确认前情提要通过？[y/n]")
            if self._confirm(""):
                break
            else:
                reason = self._input("请输入打回理由：")
                print("正在根据反馈增量修正...")
                self._revise_knowledge(reason)

    @staticmethod
    def _check_word_count(text: str) -> list[dict]:
        """自动字数检查：统计正文中文字数，返回审阅问题列表

        阈值（对齐 skill 要求 3000～4000 字）：
          <2200 或 >5000 → level 0（严重）
          <2600 或 >4500 → level 1（重要）
          <2800 或 >4200 → level 2（一般）
          <3000 或 >4000 → level 3（可优化）
        """
        cn_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')

        if cn_count < 2200 or cn_count > 5000:
            level = 0
            if cn_count < 2200:
                quote = f"正文共 {cn_count} 字（不足 2200 字）"
                desc = f"正文字数不足 2200 字（当前 {cn_count} 字），篇幅过短，内容展开不足。"
                sug = f"建议扩充至 3000 字以上（需补充约 {3000 - cn_count} 字），增加细节描写或情节铺垫。"
            else:
                quote = f"正文共 {cn_count} 字（超过 5000 字）"
                desc = f"正文字数超过 5000 字（当前 {cn_count} 字），篇幅过长，可能拖慢叙事节奏。"
                sug = f"建议精简至 4000 字以内（需精简约 {cn_count - 4000} 字），删减冗余描写或拆分段落。"
        elif cn_count < 2600 or cn_count > 4500:
            level = 1
            if cn_count < 2600:
                quote = f"正文共 {cn_count} 字（不足 2600 字）"
                desc = f"正文字数不足 2600 字（当前 {cn_count} 字），篇幅偏短。"
                sug = f"建议适当扩充至 3000 字以上，使情节更加丰满。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4500 字）"
                desc = f"正文字数超过 4500 字（当前 {cn_count} 字），篇幅偏长。"
                sug = f"建议适当精简至 4000 字以内，避免节奏拖沓。"
        elif cn_count < 2800 or cn_count > 4200:
            level = 2
            if cn_count < 2800:
                quote = f"正文共 {cn_count} 字（不足 2800 字）"
                desc = f"正文字数不足 2800 字（当前 {cn_count} 字），篇幅略短。"
                sug = f"可考虑补充部分细节，使内容更加充实，目标 3000 字以上。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4200 字）"
                desc = f"正文字数超过 4200 字（当前 {cn_count} 字），篇幅略长。"
                sug = f"可考虑适当精简至 4000 字以内，保持节奏紧凑。"
        elif cn_count < 3000 or cn_count > 4000:
            level = 3
            if cn_count < 3000:
                quote = f"正文共 {cn_count} 字（不足 3000 字）"
                desc = f"正文字数不足 3000 字（当前 {cn_count} 字），篇幅稍短。"
                sug = f"若感觉内容偏少，可适当增加描写，最佳 3500 字左右。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4000 字）"
                desc = f"正文字数超过 4000 字（当前 {cn_count} 字），篇幅稍长。"
                sug = f"若感觉内容偏多，可适当精简，最佳 3500 字左右。"
        else:
            return []  # 字数在合理范围内，不生成问题

        return [{
            "level": level,
            "quote": quote,
            "description": desc,
            "suggestion": sug,
        }]

    def _run_researcher(self) -> str:
        """运行 Researcher Agent 生成先验知识"""
        agent = self._create_agent(["muse_knowledge.skill.md"])
        messages = [{"role": "user", "content": f"以下是大纲/草稿：\n\n{self.outline}"}]
        messages = agent_loop(agent, messages)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("knowledge_prep", agent)
        self._save_token_stats()
        # 优先取 workflow 输出（call_knowledge_workflow 存入了 _last_subagent_output）
        if agent._last_subagent_output:
            return agent._last_subagent_output
        return self._extract_last_text(messages)

    def _run_plot_summary(self) -> str:
        """运行 Plot Summary Agent 生成前情提要"""
        agent = self._create_agent(["muse_plot.skill.md"])
        messages = [{"role": "user", "content": f"以下是大纲/草稿：\n\n{self.outline}"}]
        messages = agent_loop(agent, messages)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("plot_summary", agent)
        self._save_token_stats()
        # 优先取 workflow 输出
        if agent._last_subagent_output:
            return agent._last_subagent_output
        return self._extract_last_text(messages)

    def _revise_knowledge(self, reason: str):
        """增量修正知识准备"""
        context = (
            f"## 当前先验知识\n{self.prior_knowledge}\n\n"
            f"## 当前前情提要\n{self.plot_summary}\n\n"
            f"## 打回理由\n{reason}"
        )
        agent = self._create_agent(["muse_knowledge.skill.md", "muse_plot.skill.md"])
        messages = [{"role": "user", "content": context + "\n\n请根据打回理由修正上述知识文档。"}]
        messages = agent_loop(agent, messages)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("knowledge_revise", agent)
        self._save_token_stats()
        result = self._extract_last_text(messages)
        # 简单分成先验知识和前情提要（按段落分割）
        parts = result.split("\n## ")
        if len(parts) >= 2:
            self.prior_knowledge = parts[0]
            self.plot_summary = "\n## ".join(parts[1:])
        else:
            self.prior_knowledge = result
        self.io.save_prior_knowledge(self.prior_knowledge)
        self.io.save_plot_summary(self.plot_summary)

    # ---- 步骤③→④：写作与审阅循环 ----

    MAX_WRITING_ROUNDS = 3  # 最大写作-审阅轮次

    def _step_writing_loop(self):
        """③→④ 写作与审阅循环

        v5.4: R2+ Writer 使用手术刀编辑，审阅者收到 change_log + 上轮 issues。
        """
        round_count = 0
        previous_review_result: dict | None = None  # v5.4: 保存上轮审阅结果
        while True:
            round_count += 1
            # ③ 润色写作
            print("=" * 40)
            print(f"第三步：润色写作（第 {round_count} 轮）")
            self.current_draft = self._run_writer()
            polished = polish_draft(self.current_draft)
            self.io.save_draft(polished)
            # 更新 current_draft 为润色版，供下一轮重写时传入
            self.current_draft = polished

            # ④ 写作审阅
            print("正在进行写作审阅...")
            review_session = ReviewSession()

            # v5.4: R2+ 注入上轮 issues 和分数（供分数保底逻辑）
            if previous_review_result:
                review_session.previous_issues = previous_review_result["issues"]
                review_session.previous_score = previous_review_result["score"]

            # 自动字数检查：将字数问题注入审阅会话
            word_count_issues = self._check_word_count(polished)
            cn_count = sum(1 for ch in polished if '\u4e00' <= ch <= '\u9fff')
            for issue in word_count_issues:
                review_session.report_issue(**issue)
            level_map = {0: "严重", 1: "重要", 2: "一般", 3: "可优化"}
            lv = level_map.get(word_count_issues[0]["level"], "正常") if word_count_issues else "正常"
            print(f"  [自动字数检查] {lv}：正文共 {cn_count} 字")

            review_result = self._run_reviewer(polished, review_session)
            previous_review_result = review_result  # v5.4: 保存本轮结果

            # 保存审阅意见到 review.md
            review_md_lines = [
                f"# 审阅报告",
                f"**分数**：{review_result['score']} / 100",
                f"**判定**：{'✅ 通过' if review_result['pass'] else '❌ 未通过（< 85）'}",
                f"**问题数量**：{len(review_result['issues'])}",
                "",
            ]
            if review_result["issues"]:
                for i, issue in enumerate(review_result["issues"], 1):
                    level_map = {0: "严重", 1: "重要", 2: "一般", 3: "可优化"}
                    lv = level_map.get(issue.get("level"), "用户")
                    review_md_lines.append(f"### 问题 {i}（{lv}）")
                    review_md_lines.append(f"- **原文**：{issue['quote']}")
                    review_md_lines.append(f"- **描述**：{issue['description']}")
                    review_md_lines.append(f"- **建议**：{issue['suggestion']}")
                    review_md_lines.append("")
            self.io.save_review("\n".join(review_md_lines))

            # 展示审阅意见
            print(f"\n审阅分数：{review_result['score']}")
            print(f"问题数量：{len(review_result['issues'])}")

            if review_result["pass"]:
                # 展示给用户确认
                print("\n" + "=" * 40)
                print("最终正文：")
                print(polished)
                print("\n审阅意见已保存至 muse/ 目录。")
                print("\n确认通过？[y/n]（输入 n 可写自定义意见打回重写）")
                if self._confirm(""):
                    self.io.save_final(polished)
                    break
                else:
                    user_feedback = self._input("请输入修改意见（可选，直接回车跳过）：")
                    self.issues = review_result["issues"]
                    if user_feedback:
                        self.issues.append({
                            "level": None,
                            "quote": "",
                            "description": user_feedback,
                            "suggestion": "",
                        })
                    self.io.next_round()
                    continue
            else:
                # 自动打回重写
                if round_count >= self.MAX_WRITING_ROUNDS:
                    print(f"已达最大轮次（{self.MAX_WRITING_ROUNDS}），强制通过。")
                    self.io.save_final(polished)
                    break
                print(f"分数 {review_result['score']} < 85，自动打回重写。")
                self.issues = review_result["issues"]
                self.io.next_round()

    def _run_writer(self) -> str:
        """运行 Writer 创作正文

        v5.4: R1 走 wf.run()（全文创作）；R2+ 走 wf.run_revise()（手术刀编辑）。
        修改轮输出 edits JSON，后端 apply 到 draft 上，大幅减少 output token。
        """
        from tools.writing_workflow import WritingWorkflow
        from agent.skill import SkillRegistry

        skill_reg = SkillRegistry(self.skills_dir)

        # 有审阅意见 → 修改轮，使用专门的修改 skill
        is_revise = bool(self.issues)
        skill_name = "muse_writer_revise" if is_revise else "muse_writer"
        writer_skill_text = skill_reg.load_full_text(skill_name)

        wf = WritingWorkflow(
            llm=LLMClient(self.llm_config),
            workspace=self.workspace,
            writer_skill_text=writer_skill_text,
            cli=None,
        )

        last_chapter = self._get_last_chapter_full()

        # v5.3: 注入 style 记忆
        from tools.memory import get_memories_for_prompt
        memory_block = get_memories_for_prompt(self.workspace, ["style"], limit=5)

        if is_revise and self.current_draft:
            # v5.4: 手术刀修改轮 — 输出 edits JSON
            new_draft, changes = wf.run_revise(
                draft=self.current_draft,
                review_issues=self.issues,
                outline=self.outline,
                last_chapter=last_chapter,
                change_log=self.change_log if self.change_log else None,
                memory_block=memory_block,
            )
            self.change_log = changes  # 保存变更日志供审阅者参考
            self.io.save_session_log([{"role": "assistant", "content": new_draft}])
            step_name = f"writing_round_{self.io.round}"
            self._update_token_stats_from_wf(step_name, wf)
            self._save_token_stats()
            return new_draft
        else:
            # R1 或无草稿：全文创作模式
            result = wf.run(
                outline=self.outline,
                prior_knowledge=self.prior_knowledge,
                plot_summary=self.plot_summary,
                last_chapter=last_chapter,
                review_issues=self.issues if self.issues else None,
                previous_draft="",
                memory_block=memory_block,
            )
            self.change_log = []  # R1 无变更日志
            self.io.save_session_log([{"role": "assistant", "content": result}])
            step_name = f"writing_round_{self.io.round}"
            self._update_token_stats_from_wf(step_name, wf)
            self._save_token_stats()
            return result

    def _run_reviewer(self, draft: str, review_session: ReviewSession) -> dict:
        """运行 Reviewer 审阅正文（仅 report_issue / review_done / agent_output 工具）

        上下文：上一章全文 → 大纲 → 正文
        v5.4 R2+：额外注入修改记录 + 上轮审阅意见（增量审阅模式）。
        """
        agent = self._create_restricted_agent(
            skill_names=["muse_reviewer.skill.md"],
            allowed_tools=["agent_output", "report_issue", "review_done"],
        )
        # 覆盖角色定义：审阅Agent绝不能去写作，否则会跑去写下一章
        agent.system_prompt = agent.system_prompt.replace(
            "你是妙笔（Muse），一个专业的长篇小说写作辅助助手。",
            "你是妙笔审阅官（Muse Reviewer），你的唯一职责是审阅正文并报告问题。你绝不创作、绝不续写。",
        )
        agent.review_session = review_session
        last_chapter = self._get_last_chapter_full()
        context_parts = []
        if last_chapter:
            context_parts.append(f"## 上一章全文\n{last_chapter}")
        context_parts.append(f"## 大纲/草稿\n{self.outline}")
        context_parts.append(f"## 正文\n{draft}")

        # v5.4: R2+ 注入修改记录和上轮审阅意见（触发增量审阅模式）
        if review_session.previous_issues:
            # 修改记录（本轮 Writer 变更）
            if self.change_log:
                change_lines = "\n".join(f"- {c}" for c in self.change_log)
                context_parts.append(f"## 修改记录（本轮 Writer 变更）\n{change_lines}")

            # 上轮审阅意见
            prev_lines = []
            for idx, issue in enumerate(review_session.previous_issues, 1):
                level = issue.get("level", "?")
                desc = issue.get("description", "")
                sug = issue.get("suggestion", "")
                prev_lines.append(f"①②③④⑤⑥⑦⑧⑨⑩"[idx] if idx <= 10 else f"{idx}.")
                prev_lines[-1] = f"{'①②③④⑤⑥⑦⑧⑨⑩'[idx-1] if idx <= 10 else str(idx)} [{level}] {desc} → {sug}"
            context_parts.append(
                f"## 上轮审阅意见（共{len(review_session.previous_issues)}条）\n"
                + "\n".join(prev_lines)
            )

        # v5.3: 审阅注入 style + correction 记忆
        from tools.memory import get_memories_for_prompt
        review_memory = get_memories_for_prompt(
            self.workspace, ["style", "correction"], limit=5)
        if review_memory:
            context_parts.append(review_memory)

        context = "\n\n".join(context_parts)
        messages = [{"role": "user", "content": context}]
        messages = agent_loop(agent, messages)
        self.io.save_session_log(messages)
        step_name = f"review_round_{self.io.round}"
        self._update_token_stats_from_agent(step_name, agent)
        self._save_token_stats()
        return review_session.review_done()

    # ---- 辅助方法 ----

    def _get_last_chapter_full(self) -> str:
        """读取上一章全文（v5.3：使用锚定的 target_chapter - 1）"""
        from tools.chapter import read_chapters
    
        if self.target_chapter and self.target_chapter > 1:
            prev_num = self.target_chapter - 1
            text = read_chapters(self.workspace, str(prev_num))
            if text.startswith("错误"):
                return ""
            return text
    
        # 兼容旧行为：无锚定时取 DB 最后一章
        from tools.chapter import chapter_list
        import re
        raw = chapter_list(self.workspace)
        if raw in ("（尚无章节）", ""):
            return ""
        lines = raw.strip().splitlines()
        last_line = lines[-1]
        m = re.match(r"第(\d+)章", last_line)
        if not m:
            return ""
        last_num = int(m.group(1))
        text = read_chapters(self.workspace, str(last_num))
        if text.startswith("错误"):
            return ""
        return text

    def _create_agent(self, skill_names: list[str]) -> MuseAgent:
        """创建妙笔 Agent 实例，将 skill 文件内容注入 system prompt"""
        from core.io import IOChannel
        from core.output import OutputFormatter
        cli = IOChannel(formatter=OutputFormatter(json_mode=False))
        agent = MuseAgent(
            config={"api": self.llm_config},
            workspace=self.workspace,
            skills_dir=self.skills_dir,
            cli=cli,
            chapter_ceiling=self.chapter_ceiling,
        )
        # 将 skill 全文注入 system prompt（LLM 才能看到工作流指引）
        # SkillRegistry 使用 frontmatter 中的 name 字段做 key，不是文件名
        for name in skill_names:
            skill_key = name.replace(".skill.md", "")
            skill_text = agent.skills.load_full_text(skill_key)
            agent.system_prompt += "\n\n" + skill_text
        return agent

    def _create_restricted_agent(self, skill_names: list[str], allowed_tools: list[str]) -> MuseAgent:
        """创建工具受限的妙笔 Agent（写作/审阅阶段使用，防止 LLM 回去翻 wiki/章节）"""
        agent = self._create_agent(skill_names)
        # 先保存旧工具指南文本（在过滤 tool_defs 之前）
        old_tool_guide_text = "\n\n" + agent._build_tool_guide()
        # 只保留 allowed_tools 中的工具
        agent.tool_defs = [
            t for t in agent.tool_defs
            if t["function"]["name"] in allowed_tools
        ]
        # 重写 system prompt 末尾的工具指南，与实际 tool_defs 一致
        # 注意：不能简单 rsplit，因为 skill 文本在 tool guide 之后追加
        new_tool_guide_text = "\n\n" + agent._build_tool_guide()
        if old_tool_guide_text in agent.system_prompt:
            agent.system_prompt = agent.system_prompt.replace(old_tool_guide_text, new_tool_guide_text, 1)
        else:
            agent.system_prompt = agent.system_prompt.rstrip() + new_tool_guide_text
        return agent

    def _extract_last_text(self, messages: list) -> str:
        """从消息列表中提取最后一条 assistant 的文本回复"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"].strip()
                if text:
                    return text
        return ""

    def _finish(self):
        print(f"\n妙笔任务完成！")
        print(f"输出目录：{self.io.task_dir}")
        # 显示 token 统计
        if self._token_stats:
            sep = "-" * 40
            print(f"\nToken 用量统计：")
            print(sep)
            for step_name, stats in self._token_stats.items():
                label = {
                    "knowledge_prep": "知识准备（先验知识）",
                    "plot_summary": "知识准备（前情提要）",
                }.get(step_name, step_name)
                print(f"  {label}: 输入={stats['input']}, 输出={stats['output']}, 总计={stats['total']}")
            print(sep)
            print(f"  总计: 输入={self._token_total['input']}, 输出={self._token_total['output']}, 总计={self._token_total['total']}")
