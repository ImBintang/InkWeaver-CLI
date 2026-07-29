"""Agent 主循环 — 流式调用 + 事件总线发射 + 工具调度（OpenAI 原生格式）"""

import json
import time

from agent.compact import micro_compact
from core.events import EventType

MAX_TURNS = 50


def agent_loop(agent, messages: list) -> list:
    """运行 Agent 循环直到 LLM 返回最终文本或达到轮数上限

    Args:
        agent: Agent 实例（提供 llm, bus, system_prompt, tool_defs, dispatch_tool）
        messages: 当前对话历史（OpenAI 格式）

    Returns:
        更新后的 messages（OpenAI 格式）
    """
    bus = agent.bus
    source = getattr(agent, "_agent_name", "system")

    # 在循环开始时检测 /compact 命令
    if messages and len(messages) > 0:
        last_user = None
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                last_user = msg["content"]
                break
        if last_user and "/compact" in last_user.strip():
            from agent.compact import CompactWorkflow
            compact_wf = CompactWorkflow(agent.llm)
            messages = compact_wf.compress(messages)
            messages.append({"role": "user", "content": "上下文已压缩，请继续。"})
            return agent_loop(agent, messages)

    for turn in range(MAX_TURNS):
        # 每轮开始前执行 micro-compact，压缩旧工具结果
        messages = micro_compact(messages)

        # 发射思考状态
        bus.emit(EventType.THINKING, {"turn": turn}, source=source)
        start = time.time()

        # 流式调用 LLM
        response = None
        for chunk in agent.llm.chat_stream(
            messages=agent._normalize_messages(messages),
            system_prompt=agent.system_prompt,
            tools=agent.tool_defs,
        ):
            if chunk["type"] == "token":
                bus.emit(EventType.TOKEN, {"text": chunk["text"]}, source=source)
            elif chunk["type"] == "reasoning":
                pass  # 思考 token 不逐个发射，等完成后一次性发射
            elif chunk["type"] == "done":
                response = chunk

        elapsed = time.time() - start

        if response is None:
            # 安全回退：流未正常结束
            bus.emit(EventType.ERROR, {"text": "LLM 流式调用异常"}, source=source)
            break

        # 记录 usage 并累加 token
        if response.get("usage"):
            agent._last_usage = response["usage"]
            agent._accumulate_tokens(response["usage"])

        # 发射思考完成
        bus.emit(EventType.THINKING_DONE, {"elapsed": elapsed}, source=source)

        # 发射完整思考过程
        reasoning = response.get("reasoning_content")
        if reasoning:
            bus.emit(EventType.REASONING, {"text": reasoning}, source=source)

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
            # 记录 tool_call_id 供 PersistCache 使用
            agent._last_tool_call_id = tc_id
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
                bus.emit(EventType.OUTPUT, {"text": text}, source=source)
                result = "(已输出)"
            else:
                # 发射工具调用事件
                brief = str(tool_input.get("name") or tool_input.get("chapters", ""))
                bus.emit(EventType.TOOL_CALL, {"name": tool_name, "brief": brief}, source=source)

                # 调度执行
                result = agent.dispatch_tool(tool_name, tool_input)

                # 发射工具结果事件
                bus.emit(EventType.TOOL_RESULT, {"name": tool_name, "msg": result[:60]}, source=source)

            tool_results.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
                "tool_name": tool_name,  # 供 micro_compact 判断是否可压缩
            })

        if tool_results:
            messages.extend(tool_results)
            agent.todo.note_round_without_update()
            # 如果 dispatch 设置了停止标记，立即终止循环
            if getattr(agent, "_stop_agent_loop", False):
                break

    return messages
