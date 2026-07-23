"""审核 Subagent — 知识提取完成后进行自审

检查项（代码 lint 已处理的项不再重复审查，通过 check_debt 查看结果）：
- 信息矛盾检查 — 前后信息是否矛盾
- 描述/状态混淆检查 — description 和 state 是否混淆
"""

import json
from pathlib import Path

from api import LLMClient
from tools.lint import read_debt


# 审核 Subagent 可用工具（不含写工具，审核后通过 knowledge_task 修复）
# 先调用 check_debt 查看 lint 债务清单
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
    "check_debt",
    "read_plot",
    "plot_list",
    "plot_task",
]


def _build_review_tool_defs() -> list:
    """构建审核 subagent 的 tool definitions"""
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
                "name": "check_debt",
                "description": "读取代码 lint 产生的债务清单。必须先调用此工具了解当前有哪些待处理问题。",
                "parameters": {
                    "type": "object",
                    "properties": {},
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
        self._input_tokens = 0
        self._output_tokens = 0

    def _build_system_prompt(self) -> str:
        """构建审核专用 system prompt"""
        return (
            f"你是知识审核子智能体，负责审核 Wiki 知识库的质量。\n"
            f"当前工作区：{self.workspace.name}\n"
            f"涉及章节：第 {self.chapters} 章\n"
            f"\n"
            f"# 审核流程\n"
            f"1. **先调用 check_debt** — 查看代码 lint 已发现的债务清单\n"
            f"2. **语义审查** — 检查信息矛盾、描述/状态混淆（代码无法自动判断的问题）\n"
            f"3. **汇总分析** — 结合债务清单和语义发现，判断：\n"
            f"   - 断链是\"真缺失\"还是\"被规则覆盖\"\n"
            f"   - state 缺失需要从原文补充\n"
            f"   - 篇幅/状态过长需要压缩\n"
            f"4. **委派修复** — 调用 knowledge_task / plot_task 执行修复\n"
            f"5. 输出审查报告\n"
            f"\n"
            f"# 审核检查项\n"
            f"\n"
            f"## 信息矛盾检查\n"
            f"- 检查前后信息是否矛盾（如境界变化是否符合逻辑）\n"
            f"- 对照原文核实关键信息\n"
            f"\n"
            f"## 描述/状态混淆检查\n"
            f"- description 应放客观介绍（静态信息）\n"
            f"- state 应放近期事件带来的变化（动态信息）\n"
            f"- 两者不应混淆\n"
            f"\n"
            f"代码 lint 已处理的检查项（YAML 结构、断链、规则 wikilink、state 缺失/冗长/冗余、类别归属、文档篇幅、剧情区间越界、出现章节）不再重复审查，通过 check_debt 查看结果。\n"
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
            "check_debt": lambda **kw: read_debt(self.workspace),
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
            f"请先调用 check_debt 查看 lint 债务清单，再进行语义审查。"
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
                self._input_tokens += pt
                self._output_tokens += ct
                self._log("REVIEW_TOKEN",
                          f"轮次={turn+1}, input={pt}, output={ct}, total={pt+ct} | "
                          f"累计: input={self._input_tokens}, output={self._output_tokens}")

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
               chapters: str, cli=None,
               token_callback=None) -> str:
    """代码 lint + 审查 subagent

    先运行纯代码 lint（自动修复 + 债务清单），再启动 ReviewSubagent 进行语义审查。

    Args:
        llm: LLM 客户端实例
        workspace: 工作区路径
        chapters: 涉及的章节范围
        cli: CLI 实例

    Returns:
        审核结果摘要
    """
    # 1. 代码 lint
    from tools.lint import run_lint
    lint_result = run_lint(workspace)
    if cli:
        cli.print_output(lint_result)

    # 2. 审查 subagent
    subagent = ReviewSubagent(
        llm=llm,
        workspace=workspace,
        chapters=chapters,
        cli=cli,
    )
    result = subagent.run()

    # 3. 将 subagent 的 token 用量回调给主 agent
    if token_callback and (subagent._input_tokens or subagent._output_tokens):
        token_callback(subagent._input_tokens, subagent._output_tokens)

    return result
