"""knowledge_task — 知识提取 Subagent

由 KnowledgeAgent 通过 knowledge_task 工具派发，
在独立 LLM 会话中完成指定类别的知识提取（读取章节 → 创建/编辑 wiki）。
"""

import json
from pathlib import Path

from api import LLMClient
from tools.shared_subagent_tools import build_shared_subagent_tools

MAX_SUBAGENT_TURNS = 30


def _build_system_prompt(category: str, entries: list[str], task_type: str,
                         chapters: str, review_notes: str = "") -> str:
    """构建知识提取 subagent 的 system prompt"""
    entries_str = "、".join(entries)
    parts = [
        "你是知识提取 Subagent，负责从小说章节中提取知识并写入 Wiki 词条。",
        "",
        f"## 任务参数",
        f"- 类别：{category}",
        f"- 章节范围：{chapters}",
        f"- 目标词条：{entries_str}",
        f"- 任务类型：{'新建' if task_type == 'new' else '更新'}",
    ]
    if review_notes:
        parts.append(f"\n## 审核修复建议\n{review_notes}")
    parts.extend([
        "",
        "## 工作流程",
        "1. 调用 read_index 获取该类别的 writing_guide（写作规范）",
        "2. 调用 read_chapters 读取指定章节正文",
        "3. 如果是更新任务，先调用 read_wiki 读取现有词条内容",
        "4. 根据章节内容，为每个目标词条撰写/更新内容",
        "5. 调用 new_wiki 或 edit_wiki 写入",
        "6. 正文中必须使用 [[wikilink]] 交叉引用相关词条",
        "7. 完成后调用 agent_output 输出操作摘要",
        "",
        "## 质量要求",
        "- description：30-80字，一句话概括核心身份",
        "- state：20-100字，当前状态快照（若类别需要）",
        "- content：≥300字，按 writing_guide 结构撰写",
        "- 使用 [[wikilink]] 交叉引用（至少 2 个）",
        "",
        "## 注意",
        "- 只处理指定类别的词条，不要跨类别操作",
        "- 规则文档（rules/）不要用 wiki 工具处理",
        "- 如果章节中找不到某词条的信息，在摘要中说明即可，不要编造",
    ])
    return "\n".join(parts)


def _dispatch(workspace: Path, name: str, args: dict) -> str:
    """Subagent 工具分发"""
    from tools import chapter as chapter_tools
    from tools import wiki as wiki_tools
    from tools import category as category_tools
    from tools import rules as rules_tools
    from tools import relation as relation_tools
    from tools import memory as memory_tools
    from tools import editor as editor_tools

    dispatch_map = {
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
    }

    handler = dispatch_map.get(name)
    if handler is None:
        return f"错误：未知工具 {name}"
    try:
        return handler(**args)
    except Exception as e:
        return f"错误：{e}"


def run_knowledge_task(llm: LLMClient, workspace: Path, cli=None,
                       token_callback=None, **kwargs) -> str:
    """运行知识提取 Subagent

    Args:
        llm: LLM 客户端
        workspace: 工作区路径
        cli: 已废弃，保留兼容
        token_callback: 回调函数 (input_tokens, output_tokens)
        **kwargs: category, chapters, entries, task_type, review_notes

    Returns:
        操作摘要文本
    """
    category = kwargs.get("category", "")
    chapters = kwargs.get("chapters", "")
    entries = kwargs.get("entries", [])
    task_type = kwargs.get("task_type", "new")
    review_notes = kwargs.get("review_notes", "")

    if not category or not chapters or not entries:
        return "错误：knowledge_task 需要 category、chapters、entries 参数"

    system_prompt = _build_system_prompt(category, entries, task_type, chapters, review_notes)
    tools = build_shared_subagent_tools()

    # 初始用户消息
    user_msg = (
        f"请执行知识提取任务：\n"
        f"- 类别：{category}\n"
        f"- 章节：{chapters}\n"
        f"- 目标词条：{'、'.join(entries)}\n"
        f"- 类型：{'新建' if task_type == 'new' else '更新'}\n"
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
        last_output = f"知识提取完成：{category} 类别，词条 {'、'.join(entries)}"
    return last_output
