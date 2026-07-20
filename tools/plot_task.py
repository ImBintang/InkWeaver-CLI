"""plot_task 工具 + PlotTaskSubagent 运行逻辑"""

import json
from pathlib import Path

from api import LLMClient
from agent.loop import agent_loop
from tools.shared_subagent_tools import build_shared_subagent_tools


def build_plot_subagent_tools() -> list:
    """构建 plot_task_subagent 的工具定义 = 共享工具 + plot 专属工具"""
    tools = build_shared_subagent_tools()
    tools.extend([
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
                "name": "new_plot",
                "description": "新建剧情卡片。",
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
                "description": "编辑剧情卡片。",
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
                "description": "将指定剧情卡片标注为已结束，写入收尾语。",
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
    ])
    return tools


class PlotTaskSubagent:
    """剧情提取子智能体 — 按任务边界执行剧情卡片操作"""

    MAX_TURNS = 30

    def __init__(self, llm: LLMClient, workspace: Path,
                 chapters: str, active_plots: list,
                 cli=None, review_notes: str = ""):
        """
        Args:
            llm: LLM 客户端实例
            workspace: 工作区路径
            chapters: 章节范围
            active_plots: 涉及到的未结束剧情卡片列表
            cli: CLI 实例
            review_notes: 审核修复建议（可选）
        """
        self.llm = llm
        self.workspace = workspace
        self.chapters = chapters
        self.active_plots = active_plots
        self.cli = cli
        self.review_notes = review_notes
        self.messages: list = []
        self.tool_defs = build_plot_subagent_tools()

    def _build_system_prompt(self) -> str:
        chapters_str = self.chapters
        active_str = "、".join(self.active_plots) if self.active_plots else "（无）"

        review_section = ""
        if self.review_notes:
            review_section = (
                f"\n"
                f"# 审核修复任务\n"
                f"**这不是一次全新的剧情提取，而是根据审核意见修复现有剧情卡片。**\n"
                f"\n"
                f"## 审核意见/修复建议\n"
                f"{self.review_notes}\n"
                f"\n"
                f"请严格按照上述审核意见进行修复。"
            )

        return (
            f"你是剧情提取子智能体，负责从小说章节中提取剧情事件并写入剧情卡片。\n"
            f"当前工作区：{self.workspace.name}\n"
            f"需阅读的章节：第 {chapters_str} 章\n"
            f"涉及到的未结束剧情卡片：{active_str}\n"
            f"\n"
            f"# 操作类型\n"
            f"根据分析结果，你可以执行以下操作：\n"
            f"1. **新增** — 发现新的完整剧情段，创建新的剧情卡片\n"
            f"2. **更新** — 已有卡片需要追加正文内容或扩展 chapters 范围\n"
            f"3. **收尾** — 剧情已有明确的结束事件，调用 end_plot 标记为已结束\n"
            f"\n"
            f"注意：同一张卡片可以在一次任务中先 edit 再 end（先更新正文，再收尾）。\n"
            f"\n"
            f"# 剧情卡片设计要求\n"
            f"- 剧情卡片描述的是「一段完整的故事事件」，通常有固定的人、地点，局限在一段连续的时间中\n"
            f"- 卡片自身的 chapters 字段**可以超出本次提取的章节范围**（穿越边界是正常的）\n"
            f"- 剧情卡片正文必须使用 [[wikilink]] 引用相关 wiki 词条，例如 [[萧炎]]、[[云岚宗]]\n"
            f"- 先查 wiki_list/read_wiki 确认已有词条名，再在剧情卡片中用 [[词条名]] 引用\n"
            f"- description 放剧情概要（静态），state 放当前进展（动态）\n"
            f"\n"
            f"# 工作流程\n"
            f"1. 使用 read_chapters 阅读本次提取范围的章节正文\n"
            f"2. 使用 read_plot 读取涉及到的未结束卡片当前内容\n"
            f"3. 分析并拟定操作\n"
            f"4. 逐个执行操作（new_plot / edit_plot / end_plot）\n"
            f"5. 所有操作完成后，调用 agent_output 输出操作摘要\n"
            f"{review_section}"
        )

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        from tools.chapter import read_chapters
        from tools.wiki import wiki_list, read_wiki, new_wiki, edit_wiki, check_wiki
        from tools.memory import read_memory
        from tools.category import read_index
        from tools.relation import query_relations
        from tools.plot import read_plot, plot_list, new_plot, edit_plot, end_plot

        if name == "agent_output":
            text = args.get("text", "")
            self._log("PLOT_OUTPUT", text)
            return "(已结束)"

        dispatch = {
            "read_chapters": lambda **kw: read_chapters(self.workspace, **kw),
            "wiki_list": lambda **kw: wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: read_wiki(self.workspace, **kw),
            "new_wiki": lambda **kw: new_wiki(self.workspace, **kw),
            "edit_wiki": lambda **kw: edit_wiki(self.workspace, **kw),
            "read_memory": lambda **kw: read_memory(self.workspace, **kw),
            "check_wiki": lambda **kw: check_wiki(self.workspace, **kw),
            "read_index": lambda **kw: read_index(self.workspace, **kw),
            "query_relations": lambda **kw: query_relations(self.workspace, **kw),
            "read_plot": lambda **kw: read_plot(self.workspace, **kw),
            "plot_list": lambda **kw: plot_list(self.workspace, **kw),
            "new_plot": lambda **kw: new_plot(self.workspace, **kw),
            "edit_plot": lambda **kw: edit_plot(self.workspace, **kw),
            "end_plot": lambda **kw: end_plot(self.workspace, **kw),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self) -> str:
        """运行剧情提取 subagent"""
        system_prompt = self._build_system_prompt()

        self._log("PLOT_START", f"章节={self.chapters}, 涉及卡片={self.active_plots}")

        active_summary = "、".join(self.active_plots) if self.active_plots else "暂无未结束剧情卡片"
        initial_msg = (
            f"请对第 {self.chapters} 章的剧情事件进行分析并提取。\n"
            f"涉及到的未结束剧情卡片：{active_summary}\n"
            f"\n"
            f"请先阅读章节内容，再逐一处理。"
        )

        self.messages.append({"role": "user", "content": initial_msg})

        for turn in range(self.MAX_TURNS):
            response = self.llm.chat(
                messages=self.messages,
                system_prompt=system_prompt,
                tools=self.tool_defs,
            )

            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = response["tool_calls"]
            self.messages.append(assistant_msg)

            if "usage" in response:
                usage = response["usage"]
                pt = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
                ct = usage.get("completion_tokens") or usage.get("output_tokens", 0)
                self._log("PLOT_TOKEN",
                          f"轮次={turn+1}, input={pt}, output={ct}, total={pt+ct}")

            if response["stop_reason"] != "tool_use":
                break

            # 处理 tool_calls — 分发工具并收集结果
            tool_results = []
            for tc in response["tool_calls"]:
                tc_id = tc["id"]
                func = tc["function"]
                tool_name = func["name"]

                try:
                    tool_input = json.loads(func["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tool_input = {}

                brief_param = str(list(tool_input.values())[0]) if tool_input else ""
                self._log("PLOT_TOOL", f"{tool_name}({brief_param})")

                result = self.dispatch_tool(tool_name, tool_input)

                result_preview = result[:120].replace("\n", " ")
                self._log("PLOT_RESULT", result_preview)

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result[:5000],
                })

            self.messages.extend(tool_results)

        # 从最终 assistant 消息提取摘要
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                summary = msg["content"].strip()
                self._log("PLOT_END", summary[:300])
                return summary

        self._log("PLOT_END", "（subagent 未返回摘要）")
        return "（subagent 未返回摘要）"


def run_plot_task(llm: LLMClient, workspace: Path,
                  chapters: str, active_plots: list = None,
                  cli=None, review_notes: str = "") -> str:
    """运行 plot_task 的便捷入口
    
    Args:
        llm: LLM 客户端
        workspace: 工作区路径
        chapters: 章节范围
        active_plots: 涉及的未结束剧情卡片列表
        cli: CLI 实例
        review_notes: 审核修复建议
    
    Returns:
        提取结果摘要
    """
    if active_plots is None:
        active_plots = []
    
    subagent = PlotTaskSubagent(
        llm=llm,
        workspace=workspace,
        chapters=chapters,
        active_plots=active_plots,
        cli=cli,
        review_notes=review_notes,
    )
    return subagent.run()
