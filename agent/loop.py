"""Agent 主循环 — 检测 stop_reason 调度工具（参照 s01/s02）"""

import json

MAX_TURNS = 20


def agent_loop(jianzhi, messages: list) -> list:
    """运行 Agent 循环直到 LLM 返回最终文本或达到轮数上限

    Args:
        jianzhi: JianzhiAgent 实例（提供 llm, system_prompt, tool_defs, dispatch_tool）
        messages: 当前对话历史

    Returns:
        更新后的 messages
    """
    for turn in range(MAX_TURNS):
        response = jianzhi.llm.chat(
            messages=jianzhi._normalize_messages(messages),
            system_prompt=jianzhi.system_prompt,
            tools=jianzhi.tool_defs,
        )

        # 记录 usage
        if "usage" in response:
            jianzhi._last_usage = response["usage"]

        # 写入 assistant 响应
        messages.append({"role": "assistant", "content": response["content"]})

        # 检查是否需要继续
        if response["stop_reason"] != "tool_use":
            break

        # 处理 tool_use 块
        tool_results = []
        for block in response["content"]:
            if block["type"] != "tool_use":
                continue

            tool_name = block["name"]
            tool_input = block["input"]

            # 解析 JSON 参数（OpenAI 返回的是 JSON string）
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except json.JSONDecodeError:
                    tool_input = {}

            # 特殊处理 agent_output
            if tool_name == "agent_output":
                text = tool_input.get("text", "")
                jianzhi.cli.print_output(text)
                result = "(已输出)"
            else:
                # 打印工具调用
                brief = str(tool_input.get("chapters", tool_input.get("name", "")))
                jianzhi.cli.print_tool_call(tool_name, brief)

                # 调度执行
                result = jianzhi.dispatch_tool(tool_name, tool_input)

                # 打印结果
                jianzhi.cli.print_tool_result(result[:60])

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": result,
            })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
            jianzhi.todo.note_round_without_update()

    return messages
