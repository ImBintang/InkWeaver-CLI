"""Agent 主循环 — 检测 stop_reason 调度工具（OpenAI 原生格式）"""

import json
import time

MAX_TURNS = 20


def agent_loop(jianzhi, messages: list) -> list:
    """运行 Agent 循环直到 LLM 返回最终文本或达到轮数上限

    Args:
        jianzhi: JianzhiAgent 实例（提供 llm, system_prompt, tool_defs, dispatch_tool）
        messages: 当前对话历史（OpenAI 格式）

    Returns:
        更新后的 messages（OpenAI 格式）
    """
    for turn in range(MAX_TURNS):
        start = time.time()
        response = jianzhi.llm.chat(
            messages=jianzhi._normalize_messages(messages),
            system_prompt=jianzhi.system_prompt,
            tools=jianzhi.tool_defs,
        )
        elapsed = time.time() - start

        # 记录 usage 并累加 token
        if "usage" in response:
            jianzhi._last_usage = response["usage"]
            jianzhi._accumulate_tokens(response["usage"])

        # 显示思考过程
        reasoning = response.get("reasoning_content")
        if reasoning:
            jianzhi.cli.print_reasoning(reasoning)

        jianzhi.cli.print_thinking_done(elapsed)

        # --- 构建 assistant 消息（OpenAI 原生格式） ---
        assistant_msg = {"role": "assistant", "content": response.get("content")}
        if response.get("tool_calls"):
            assistant_msg["tool_calls"] = response["tool_calls"]
        messages.append(assistant_msg)

        # 检查是否需要继续
        if response["stop_reason"] != "tool_use":
            break

        # --- 处理 tool_calls（OpenAI 原生格式） ---
        tool_results = []
        for tc in response["tool_calls"]:
            tc_id = tc["id"]
            func = tc["function"]
            tool_name = func["name"]

            # 解析 JSON 参数
            try:
                tool_input = json.loads(func["arguments"])
            except (json.JSONDecodeError, TypeError):
                tool_input = {}

            # 特殊处理 agent_output
            if tool_name == "agent_output":
                text = tool_input.get("text", "")
                jianzhi.cli.print_output(text)
                result = "(已输出)"
            else:
                # 打印工具调用（显示简要参数）
                brief = str(tool_input.get("chapters", tool_input.get("name", "")))
                jianzhi.cli.print_tool_call(tool_name, brief)

                # 调度执行
                result = jianzhi.dispatch_tool(tool_name, tool_input)

                # 打印结果摘要
                jianzhi.cli.print_tool_result(result[:60])

            tool_results.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })

        if tool_results:
            messages.extend(tool_results)
            jianzhi.todo.note_round_without_update()
            # 如果 dispatch 设置了停止标记，立即终止循环
            if getattr(jianzhi, "_stop_agent_loop", False):
                break

    return messages
