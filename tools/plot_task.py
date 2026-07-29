"""plot_task — 剧情提取 Subagent

由 KnowledgeAgent 通过 plot_task 工具派发，
在独立 LLM 会话中完成剧情卡片的提取（读取章节 → 创建/编辑 plot 卡片）。
"""

import json
from pathlib import Path

from api import LLMClient
from tools.shared_subagent_tools import build_shared_subagent_tools

MAX_SUBAGENT_TURNS = 30


def _build_plot_tools() -> list:
    """构建剧情提取专属工具（叠加在共享工具之上）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "plot_list",
                "description": "列出剧情卡片（支持 ended 过滤）。",
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
                "name": "read_plot",
                "description": "读取指定剧情卡片。yaml_only=false 返回全文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "剧情卡片名"},
                        "yaml_only": {"type": "boolean", "description": "是否只读 frontmatter（默认 false）"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "new_plot",
                "description": "新建剧情卡片。正文必须包含 [[wikilink]] 引用相关 wiki 词条。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "剧情卡片标题"},
                        "chapters": {"type": "string", "description": "覆盖章节，如 \"1-5,7-10\""},
                        "content": {"type": "string", "description": "正文内容（含 [[wikilink]]）"},
                        "description": {"type": "string", "description": "剧情概要"},
                        "state": {"type": "string", "description": "当前进展（可选）"},
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
                "description": "将剧情卡片标注为已结束。",
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
    ]


def _build_system_prompt(chapters: str, active_plots: list[str],
                         review_notes: str = "") -> str:
    """构建剧情提取 subagent 的 system prompt"""
    plots_str = "、".join(active_plots) if active_plots else "（无）"
    parts = [
        "你是剧情提取 Subagent，负责从小说章节中提取剧情线并写入剧情卡片。",
        "",
        "## 任务参数",
        f"- 章节范围：{chapters}",
        f"- 涉及的未结束卡片：{plots_str}",
    ]
    if review_notes:
        parts.append(f"\n## 审核修复建议\n{review_notes}")
    parts.extend([
        "",
        "## 工作流程",
        "1. 调用 read_chapters 读取指定章节正文",
        "2. 调用 plot_list 查看现有剧情卡片",
        "3. 对涉及的未结束卡片，调用 read_plot 读取现有内容",
        "4. 分析章节中的剧情发展：",
        "   - 已有卡片有新进展 → edit_plot 更新（扩展 chapters 范围、更新 state）",
        "   - 出现新剧情线 → new_plot 创建",
        "   - 某条线已明确收尾 → end_plot 结束",
        "5. 完成后调用 agent_output 输出操作摘要",
        "",
        "## 剧情卡片内容要求",
        "- 正文**必须**包含 [[wikilink]] 引用相关 wiki 词条",
        "- 先用 wiki_list/read_wiki 查找已有词条名，再在正文中引用",
        "- description：一句话概括剧情线核心冲突",
        "- state：当前进展快照（20-80字）",
        "- chapters：覆盖的章节范围（如 \"1-5,7-10\"）",
        "",
        "## 注意",
        "- 不要创建过于细碎的卡片，一条剧情线应有明确的冲突-发展-高潮结构",
        "- 章节范围已远落后于最新章节的旧卡片（差值≥10），考虑 end_plot 收尾",
        "- 如果章节中没有明显的剧情推进，在摘要中说明即可",
    ])
    return "\n".join(parts)


def _dispatch(workspace: Path, name: str, args: dict) -> str:
    """Subagent 工具分发（含剧情专属工具）"""
    from tools import chapter as chapter_tools
    from tools import wiki as wiki_tools
    from tools import category as category_tools
    from tools import rules as rules_tools
    from tools import relation as relation_tools
    from tools import memory as memory_tools
    from tools import editor as editor_tools
    from tools import plot as plot_tools

    dispatch_map = {
        # 共享工具
        "read_chapters": lambda **kw: chapter_tools.read_chapters(workspace, **kw),
        "wiki_list": lambda **kw: wiki_tools.wiki_list(workspace, **kw),
        "read_wiki": lambda **kw: wiki_tools.read_wiki(workspace, yaml_only=False, **kw),
        "new_wiki": lambda **kw: wiki_tools.new_wiki(workspace, **kw),
        "edit_wiki": lambda **kw: wiki_tools.edit_wiki(workspace, **kw),
        "edit_doc_text": lambda **kw: editor_tools.edit_doc_text(workspace, **kw),
        "edit_doc_wikilink": lambda **kw: editor_tools.edit_doc_wikilink(workspace, **kw),
        "read_memory": lambda **kw: memory_tools.read_memory(workspace, **kw),
        "check_wiki": lambda **kw: wiki_tools.check_wiki(workspace, **kw),
        "read_index": lambda **kw: category_tools.read_index(workspace, **kw),
        "query_relations": lambda **kw: relation_tools.query_relations(workspace, **kw),
        "rules_list": lambda **kw: rules_tools.rules_list(workspace, **kw),
        "read_rule": lambda **kw: rules_tools.read_rule(workspace, **kw),
        "agent_output": lambda **kw: kw.get("text", ""),
        # 剧情专属工具
        "plot_list": lambda **kw: plot_tools.plot_list(workspace, **kw),
        "read_plot": lambda **kw: plot_tools.read_plot(workspace, yaml_only=False, **kw),
        "new_plot": lambda **kw: plot_tools.new_plot(workspace, **kw),
        "edit_plot": lambda **kw: plot_tools.edit_plot(workspace, **kw),
        "end_plot": lambda **kw: plot_tools.end_plot(workspace, **kw),
    }

    handler = dispatch_map.get(name)
    if handler is None:
        return f"错误：未知工具 {name}"
    try:
        return handler(**args)
    except Exception as e:
        return f"错误：{e}"


def run_plot_task(llm: LLMClient, workspace: Path, cli=None,
                  token_callback=None, **kwargs) -> str:
    """运行剧情提取 Subagent

    Args:
        llm: LLM 客户端
        workspace: 工作区路径
        cli: 已废弃，保留兼容
        token_callback: 回调函数 (input_tokens, output_tokens)
        **kwargs: chapters, active_plots, review_notes

    Returns:
        操作摘要文本
    """
    chapters = kwargs.get("chapters", "")
    active_plots = kwargs.get("active_plots", [])
    review_notes = kwargs.get("review_notes", "")

    if not chapters:
        return "错误：plot_task 需要 chapters 参数"

    system_prompt = _build_system_prompt(chapters, active_plots, review_notes)
    tools = build_shared_subagent_tools() + _build_plot_tools()

    # 初始用户消息
    plots_str = "、".join(active_plots) if active_plots else "无"
    user_msg = (
        f"请执行剧情提取任务：\n"
        f"- 章节：{chapters}\n"
        f"- 涉及的未结束卡片：{plots_str}\n"
        f"请按工作流程逐步执行。"
    )
    messages = [{"role": "user", "content": user_msg}]

    # Subagent 循环
    last_output = ""
    for _turn in range(MAX_SUBAGENT_TURNS):
        response = llm.chat(messages=messages, system_prompt=system_prompt, tools=tools)

        # Token 回调
        if token_callback and response.get("usage"):
            usage = response["usage"]
            inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            token_callback(inp, out)

        # 构建 assistant 消息
        assistant_msg = {"role": "assistant", "content": response.get("content")}
        if response.get("tool_calls"):
            assistant_msg["tool_calls"] = response["tool_calls"]
        messages.append(assistant_msg)

        # 无工具调用 → 结束
        if response.get("stop_reason") != "tool_use" or not response.get("tool_calls"):
            last_output = response.get("content") or last_output
            break

        # 处理工具调用
        for tc in response["tool_calls"]:
            func = tc["function"]
            tool_name = func["name"]
            try:
                tool_input = json.loads(func["arguments"])
            except (json.JSONDecodeError, TypeError):
                tool_input = {}

            if tool_name == "agent_output":
                last_output = tool_input.get("text", "")

            result = _dispatch(workspace, tool_name, tool_input)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    if not last_output:
        last_output = f"剧情提取完成：第 {chapters} 章"
    return last_output
