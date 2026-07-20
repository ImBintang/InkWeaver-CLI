"""审核 Subagent — 知识提取完成后进行自审

检查项：
1. wikilink 悬空检查 — [[wikilink]] 是否指向已存在的词条
2. 信息矛盾检查 — 前后信息是否矛盾
3. 描述/状态混淆检查 — description 和 state 是否混淆
4. 规则混入关系检查 — rules/ 下的规则文档是否包含 [[wikilink]]
5. state 缺失检查 — 人物/势力类词条是否缺少 state 字段
6. 文档篇幅检查 — wiki/规则文档超过 1500 字时需发起压缩请求
"""

import json
import re
from pathlib import Path

from api import LLMClient


# 审核 Subagent 可用工具（不含写工具，审核后通过 knowledge_task 修复）
REVIEW_SUBAGENT_TOOLS = [
    "read_chapters",
    "wiki_list",
    "read_wiki",
    "rules_list",
    "read_rule",
    "read_memory",
    "read_index",
    "query_relations",
    "knowledge_task",
    "agent_output",
    "length_stats",
    "read_plot",
    "plot_list",
    "plot_task",
]


def _build_review_tool_defs() -> list:
    """构建审核 subagent 的 tool definitions（8 个工具）"""
    from tools.chapter import read_chapters
    from tools.wiki import wiki_list, read_wiki
    from tools.memory import read_memory
    from tools.category import read_index
    from tools.relation import query_relations

    return [
        {
            "type": "function",
            "function": {
                "name": "read_chapters",
                "description": "读取指定章节的正文。支持范围表达式如 \"1-3,5,7-9\"。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapters": {"type": "string", "description": "章节范围"},
                    },
                    "required": ["chapters"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wiki_list",
                "description": "查看类别下的 wiki 列表（分页，每页 20 个）。",
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
                "description": "读取指定 wiki 文档（含 frontmatter）。",
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
                "name": "read_memory",
                "description": "读取记忆文档。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "记忆名（None 表示读取索引）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_index",
                "description": "读取总 index 或指定类别 index。",
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
                "name": "query_relations",
                "description": "查询指定词条的所有关联词条。",
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
                "description": "查看规则文档列表（rules/ 目录下的世界观规则，如境界体系）。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_rule",
                "description": "读取指定规则文档的全文。设置 yaml_only=false 可查看完整内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "规则名"},
                        "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true）"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "knowledge_task",
                "description": "创建 knowledge_task 修复词条问题。可指定 review_notes 提供修复建议。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "chapters": {"type": "string", "description": "章节范围"},
                        "entries": {"type": "array", "items": {"type": "string"}, "description": "目标词条列表"},
                        "task_type": {"type": "string", "enum": ["new", "update"], "description": "new 或 update"},
                        "review_notes": {"type": "string", "description": "修复建议/审核意见（可选）"},
                    },
                    "required": ["category", "chapters", "entries", "task_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agent_output",
                "description": "中间轮输出。调用后直接输出文本，不打断流程。",
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
                "name": "length_stats",
                "description": "统计指定 wiki 词条或规则文档除去 YAML frontmatter 后的 Markdown 字数。如果字数超过 1500，需要发起修改请求进行压缩。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["wiki", "rule"],
                            "description": "文档类型：wiki（词条）或 rule（规则文档）",
                        },
                        "category": {
                            "type": "string",
                            "description": "类别名（当 type=wiki 时必填，如「人物」「势力」）",
                        },
                        "name": {
                            "type": "string",
                            "description": "文档名称（不含 .md），如「寒叔（叶寒）」「境界体系」",
                        },
                    },
                    "required": ["type", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_plot",
                "description": "读取指定剧情卡片。",
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
                "description": "列出剧情卡片。ended=\"false\" 只看未结束，\"true\" 只看已结束，\"all\" 看全部。",
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
                "name": "plot_task",
                "description": "创建 plot_task 修复剧情卡片问题。可指定 review_notes 提供修复建议。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapters": {"type": "string", "description": "章节范围"},
                        "active_plots": {"type": "array", "items": {"type": "string"}, "description": "涉及的剧情卡片列表"},
                        "review_notes": {"type": "string", "description": "修复建议/审核意见（可选）"},
                    },
                    "required": ["chapters", "active_plots"],
                },
            },
        },
    ]


def _strip_frontmatter(text: str) -> str:
    """去除 YAML frontmatter，返回纯 Markdown 正文"""
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def length_stats(workspace: Path, doc_type: str, name: str, category: str = "") -> str:
    """统计 wiki 或规则文档除去 YAML frontmatter 后的字数

    Args:
        workspace: 工作区路径
        doc_type: "wiki" 或 "rule"
        name: 文档名（不含 .md）
        category: wiki 类别（doc_type=wiki 时必填）

    Returns:
        字数统计结果
    """
    if doc_type == "wiki":
        if not category:
            return "错误：type=wiki 时必须提供 category 参数"
        fp = workspace / "wiki" / category / f"{name}.md"
    elif doc_type == "rule":
        fp = workspace / "rules" / f"{name}.md"
    else:
        return f"错误：未知文档类型「{doc_type}」"

    if not fp.exists():
        return f"错误：文档不存在 - {fp}"

    text = fp.read_text(encoding="utf-8")
    body = _strip_frontmatter(text)
    char_count = len(body)
    line_count = len(body.splitlines())

    if char_count > 1500:
        suggestion = (
            f"⚠️ 字数 {char_count}，超过 1500 字限制。"
            f"建议压缩：移除过时/重复内容（如早期章节的设定已在后续章节被覆盖），"
            f"保留当前最新状态。"
        )
    else:
        suggestion = f"✓ 字数 {char_count}，在合理范围内。"

    return (
        f"文档：{name}.md\n"
        f"类型：{doc_type}\n"
        f"路径：{fp}\n"
        f"正文行数：{line_count}\n"
        f"正文字数：{char_count}\n"
        f"评估：{suggestion}"
    )


class ReviewSubagent:
    """审核子智能体 — 检查知识提取质量，委托 knowledge_task 修复"""

    MAX_TURNS = 40

    def __init__(self, llm: LLMClient, workspace: Path,
                 chapters: str, cli=None):
        """
        Args:
            llm: LLM 客户端实例
            workspace: 工作区路径
            chapters: 涉及的章节范围
            cli: CLI 实例
        """
        self.llm = llm
        self.workspace = workspace
        self.chapters = chapters
        self.cli = cli
        self.messages: list = []
        self.tool_defs = _build_review_tool_defs()

    def _build_system_prompt(self) -> str:
        """构建审核专用 system prompt"""
        return (
            f"你是知识审核子智能体，负责审核 Wiki 知识库的质量。\n"
            f"当前工作区：{self.workspace.name}\n"
            f"涉及章节：第 {self.chapters} 章\n"
            f"\n"
            f"# 审核流程\n"
            f"1. 使用 wiki_list 获取所有类别的词条列表（遍历所有类别）\n"
            f"2. 使用 read_wiki 抽样或全量读取词条内容\n"
            f"3. 使用 read_index 查看类别 index.md 了解写作规范（特别是是否需要 state 字段）\n"
            f"4. 使用 rules_list 查看规则文档列表，使用 read_rule 读取全量检查\n"
            f"5. 使用 read_chapters 在必要时对照原文\n"
            f"6. 使用 query_relations 查询关联关系\n"
            f"\n"
            f"# 审核检查项\n"
            f"\n"
            f"## 1. Wikilink 悬空检查\n"
            f"- 检查 [[wikilink]] 是否指向已存在的词条\n"
            f"- 如果指向的词条不存在，需要记录\n"
            f"\n"
            f"## 2. 规则文档检查\n"
            f"- 使用 rules_list 和 read_rule 检查 rules/ 目录下的规则文档\n"
            f"- 检查本次涉及章节的知识提取是否新增了需要更新规则的内容\n"
            f"  （如新的修炼境界、新的妖兽等级体系等世界观规则）\n"
            f"- 规则混入关系检查：rules/ 下的规则文档**禁止**包含 [[wikilink]]\n"
            f"  - 规则定义的是世界观底层规则，不应与具体词条建立关系\n"
            f"  - 发现后需移除规则文档中的 wikilink（改为纯文本引用）\n"
            f"\n"
            f"## 3. state 缺失检查\n"
            f"- 查看每个类别的 index.md，确认该类别的「是否需要 state 字段」\n"
            f"- 人物、势力类通常需要 state 字段\n"
            f"- 设定图鉴类不需要 state 字段\n"
            f"- 如果类别需要 state 但词条缺失，需要记录并修复\n"
            f"\n"
            f"## 4. 信息矛盾检查\n"
            f"- 检查前后信息是否矛盾（如境界变化是否符合逻辑）\n"
            f"- 对照原文核实关键信息\n"
            f"\n"
            f"## 5. 描述/状态混淆检查\n"
            f"- description 应放客观介绍（静态信息）\n"
            f"- state 应放近期事件带来的变化（动态信息）\n"
            f"- 两者不应混淆\n"
            f"\n"
            f"## 6. 文档篇幅检查\n"
            f"- 使用 length_stats 逐个检查 wiki 词条和规则文档的字数\n"
            f"- 对**超过 1500 字**的文档，必须发起修改请求进行压缩\n"
            f"- 压缩目标：移除过时、重复、被后续章节覆盖的内容\n"
            f"- 例如：小说已写到 100 章，词条中却还在详细描述第 20 章的修为境界——"
            f"这类已被覆盖的早期细节应删除，只保留当前最新状态\n"
            f"- 压缩通过 knowledge_task 的 review_notes 传递压缩意见来实现\n"
            f"\n"
            f"## 7. 剧情卡片 Wikilink 悬空检查\n"
            f"- 检查剧情卡片中的 [[wikilink]] 是否指向已存在的 wiki 词条\n"
            f"- 如果指向的词条不存在，需要记录\n"
            f"\n"
            f"## 8. 剧情区间越界检查\n"
            f"- 检查剧情卡片的 chapters 字段是否超出原文实际范围\n"
            f"- 例如：原文只有 20 章，卡片写的 chapters: 1-25 则越界\n"
            f"\n"
            f"## 9. 未结束卡片收尾遗漏检查\n"
            f"- 对于标记为未结束的剧情卡片，对照最新章节判断是否应有收尾操作\n"
            f"- 如果本应收尾但未被处理，需要记录\n"
            f"\n"
            f"# 输出要求\n"
            f"1. 先调用 agent_output 输出审核报告，列出所有发现的问题\n"
            f"2. 然后针对每个问题，调用 knowledge_task 进行修复\n"
            f"   - 使用 knowledge_task 的 review_notes 参数传递具体的修复建议\n"
            f"   - 一个 knowledge_task 可以修复同一类别的多个词条\n"
            f"3. 所有修复完成后，调用 agent_output 输出修复结果摘要\n"
        )

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        from tools.chapter import read_chapters
        from tools.wiki import wiki_list, read_wiki
        from tools.memory import read_memory
        from tools.category import read_index
        from tools.relation import query_relations
        from tools import rules as rules_tools
        from tools.knowledge_task import run_knowledge_task
        from tools.plot import read_plot, plot_list
        from tools.plot_task import run_plot_task

        # agent_output 特殊处理
        if name == "agent_output":
            text = args.get("text", "")
            self._log("REVIEW_OUTPUT", text)
            return "(已结束)"

        dispatch = {
            "read_chapters": lambda **kw: read_chapters(self.workspace, **kw),
            "wiki_list": lambda **kw: wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: read_wiki(self.workspace, **kw),
            "rules_list": lambda **kw: rules_tools.rules_list(self.workspace),
            "read_rule": lambda **kw: rules_tools.read_rule(self.workspace, **kw),
            "read_memory": lambda **kw: read_memory(self.workspace, **kw),
            "read_index": lambda **kw: read_index(self.workspace, **kw),
            "query_relations": lambda **kw: query_relations(self.workspace, **kw),
            "knowledge_task": lambda **kw: run_knowledge_task(
                self.llm, self.workspace, cli=self.cli, **kw
            ),
            "length_stats": lambda **kw: length_stats(self.workspace, **kw),
            "read_plot": lambda **kw: read_plot(self.workspace, **kw),
            "plot_list": lambda **kw: plot_list(self.workspace, **kw),
            "plot_task": lambda **kw: run_plot_task(
                self.llm, self.workspace, cli=self.cli, **kw
            ),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    def _log(self, tag: str, text: str):
        """记录日志"""
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self) -> str:
        """运行审核 subagent，返回审核摘要"""
        system_prompt = self._build_system_prompt()

        self._log("REVIEW_START", f"章节={self.chapters}")

        initial_msg = (
            f"请对工作区「{self.workspace.name}」的知识库进行全面审核。\n"
            f"涉及章节：第 {self.chapters} 章\n"
            f"\n"
            f"请先从 wiki_list 获取所有类别的词条列表，逐项检查。\n"
            f"同时使用 length_stats 检查各文档字数，对超过 1500 字的发起压缩。"
        )

        self.messages.append({"role": "user", "content": initial_msg})

        for turn in range(self.MAX_TURNS):
            response = self.llm.chat(
                messages=self.messages,
                system_prompt=system_prompt,
                tools=self.tool_defs,
            )

            # 构建 assistant 消息
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = response["tool_calls"]
            self.messages.append(assistant_msg)

            # 记录 token
            if "usage" in response:
                usage = response["usage"]
                pt = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
                ct = usage.get("completion_tokens") or usage.get("output_tokens", 0)
                self._log("REVIEW_TOKEN",
                          f"轮次={turn+1}, input={pt}, output={ct}, total={pt+ct}")

            # 检查是否完成
            if response["stop_reason"] != "tool_use":
                final_content = response.get("content", "")
                if final_content:
                    self._log("REVIEW_OUTPUT", final_content)
                break

            # 处理 tool_calls
            tool_results = []
            for tc in response["tool_calls"]:
                tc_id = tc["id"]
                func = tc["function"]
                tool_name = func["name"]

                try:
                    tool_input = json.loads(func["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tool_input = {}

                if tool_name != "agent_output":
                    brief = str(list(tool_input.values())[0]) if tool_input else ""
                    self._log("REVIEW_TOOL", f"{tool_name}({brief})")

                result = self.dispatch_tool(tool_name, tool_input)

                if tool_name == "agent_output":
                    pass
                else:
                    preview = result[:120].replace("\n", " ")
                    self._log("REVIEW_RESULT", preview)

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result[:5000],
                })

            self.messages.extend(tool_results)

        # 提取最终摘要
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                summary = msg["content"].strip()
                self._log("REVIEW_END", summary[:300])
                return summary

        self._log("REVIEW_END", "（审核 subagent 未返回摘要）")
        return "（审核 subagent 未返回摘要）"


def run_review(llm: LLMClient, workspace: Path,
               chapters: str, cli=None) -> str:
    """便捷函数：创建并运行 ReviewSubagent

    Args:
        llm: LLM 客户端实例
        workspace: 工作区路径
        chapters: 涉及的章节范围
        cli: CLI 实例

    Returns:
        审核结果摘要
    """
    subagent = ReviewSubagent(
        llm=llm,
        workspace=workspace,
        chapters=chapters,
        cli=cli,
    )
    return subagent.run()
