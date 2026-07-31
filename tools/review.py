"""review — 知识提取审核 Subagent

由 KnowledgeAgent 通过 review_knowledge 工具派发，
运行 lint 检查 + LLM 语义审核，输出审核报告与修复建议。
"""

import json
from pathlib import Path

from api import LLMClient
from tools.shared_subagent_tools import build_shared_subagent_tools

MAX_REVIEW_TURNS = 20


def _build_review_tools() -> list:
    """审核专属工具"""
    return [
        {
            "type": "function",
            "function": {
                "name": "plot_list",
                "description": "列出剧情卡片。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ended": {"type": "string", "description": "过滤：true/false/all（默认 false）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_plot",
                "description": "读取剧情卡片全文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "剧情卡片名"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "category_list",
                "description": "列出所有类别。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def _build_system_prompt(chapters: str, lint_result: str) -> str:
    """构建审核 subagent 的 system prompt"""
    return f"""你是知识提取审核 Subagent，负责审核知识提取的质量。

## 审核范围
- 章节：{chapters}

## Lint 自动检查结果
{lint_result}

## 审核检查项
在 lint 基础上，进行以下语义级检查：
1. **信息矛盾**：同一词条前后信息是否矛盾（如境界变化不合逻辑）
2. **描述/状态混淆**：description（静态身份）和 state（动态快照）是否混淆
3. **state 简洁性**：state 字段是否过长（建议≤100字），堆叠剧情流水账
4. **规则混入关系**：规则文档（rules/）是否错误包含 [[wikilink]]
5. **类别归属**：词条是否放在了正确的类别下
6. **文档篇幅**：wiki 文档超过 1500 字是否需要压缩
7. **剧情卡片 wikilink**：剧情卡片正文是否包含 [[wikilink]] 引用
8. **剧情区间越界**：剧情卡片 chapters 是否超出实际章节范围

## 工作流程
1. 分析 lint 结果，识别需要修复的问题
2. 对可疑词条调用 read_wiki 深入检查
3. 对剧情卡片调用 read_plot 检查 wikilink 和区间
4. 汇总所有问题，调用 agent_output 输出审核报告

## 输出格式
审核报告格式：
```
## 审核结果

### 需修复问题（按优先级排序）
1. [词条名] 问题描述 → 修复建议
2. ...

### 无问题项
- 已检查 XX 个词条，YY 个剧情卡片

### 修复建议摘要
（供 knowledge_task 使用的 review_notes）
```

## 注意
- 不要自行修改任何文档，只输出审核报告
- 如果 lint 无债务且语义检查无问题，直接输出"审核通过"
- 关注高价值问题（信息矛盾、state缺失），忽略格式微调
"""


def _dispatch(workspace: Path, name: str, args: dict) -> str:
    """审核 Subagent 工具分发"""
    from tools import chapter as chapter_tools
    from tools import wiki as wiki_tools
    from tools import category as category_tools
    from tools import rules as rules_tools
    from tools import relation as relation_tools
    from tools import memory as memory_tools
    from tools import plot as plot_tools

    dispatch_map = {
        # 共享工具（只读为主）
        "read_chapters": lambda **kw: chapter_tools.read_chapters(workspace, **kw),
        "wiki_list": lambda **kw: wiki_tools.wiki_list(workspace, **kw),
        "read_wiki": lambda **kw: wiki_tools.read_wiki(workspace, yaml_only=False, **kw),
        "new_wiki": lambda **kw: "错误：审核模式不允许写操作",
        "edit_wiki": lambda **kw: "错误：审核模式不允许写操作",
        # P1-22：统一文档编辑工具同样拦截，审核只读隔离不被绕过
        "edit_doc_text": lambda **kw: "错误：审核模式不允许写操作",
        "edit_doc_wikilink": lambda **kw: "错误：审核模式不允许写操作",
        "delete_doc": lambda **kw: "错误：审核模式不允许写操作",
        "read_memory": lambda **kw: memory_tools.read_memory(workspace, **kw),
        "check_wiki": lambda **kw: wiki_tools.check_wiki(workspace, **kw),
        "read_index": lambda **kw: category_tools.read_index(workspace, **kw),
        "query_relations": lambda **kw: relation_tools.query_relations(workspace, **kw),
        "rules_list": lambda **kw: rules_tools.rules_list(workspace, **kw),
        "read_rule": lambda **kw: rules_tools.read_rule(workspace, **kw),
        "agent_output": lambda **kw: kw.get("text", ""),
        # 审核专属
        "plot_list": lambda **kw: plot_tools.plot_list(workspace, **kw),
        "read_plot": lambda **kw: plot_tools.read_plot(workspace, yaml_only=False, **kw),
        "category_list": lambda **kw: category_tools.category_list(workspace, **kw),
    }

    handler = dispatch_map.get(name)
    if handler is None:
        return f"错误：未知工具 {name}"
    try:
        return handler(**args)
    except Exception as e:
        return f"错误：{e}"


def run_review(llm: LLMClient, workspace: Path, cli=None,
               token_callback=None, **kwargs) -> str:
    """运行知识提取审核 Subagent

    Args:
        llm: LLM 客户端
        workspace: 工作区路径
        cli: 已废弃，保留兼容
        token_callback: 回调函数 (input_tokens, output_tokens)
        **kwargs: chapters

    Returns:
        审核报告文本
    """
    chapters = kwargs.get("chapters", "")
    if not chapters:
        return "错误：review_knowledge 需要 chapters 参数"

    # 1. 运行 lint
    try:
        from tools.lint import run_lint
        lint_result = run_lint(workspace)
    except Exception as e:
        lint_result = f"（lint 检查异常：{e}）"

    system_prompt = _build_system_prompt(chapters, lint_result)
    tools = build_shared_subagent_tools() + _build_review_tools()

    user_msg = (
        f"请对第 {chapters} 章的知识提取结果进行审核。\n"
        f"lint 自动检查结果已注入 system prompt，请在此基础上进行语义级审核。"
    )
    messages = [{"role": "user", "content": user_msg}]

    # Subagent 循环
    last_output = ""
    for _turn in range(MAX_REVIEW_TURNS):
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
            except (json.JSONDecodeError, TypeError) as e:
                # 不静默：解析失败打印警告（消费端：日志），
                # 参数清空后继续执行，避免单次脏参数中断整个审核
                print(f"[review] 工具参数解析失败 (tool={tool_name}): {e}")
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
        last_output = "审核完成（无详细报告）"
    return last_output
