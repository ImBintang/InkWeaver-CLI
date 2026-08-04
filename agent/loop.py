"""Agent 主循环 — 流式调用 + 事件总线发射 + 工具调度（OpenAI 原生格式）"""

import json
import time

from agent.compact import micro_compact
from core.events import EventType, StreamBatcher

MAX_TURNS = 50
# v6.5.7: 流式事件批量阈值——逐 token 发射会被 SSE 慢消费者队列丢事件（卡死根因）
TOKEN_BATCH = 16       # 正文 token：16 个合并一次（≈0.3~0.5s 粒度，前端几乎无感知）
REASONING_BATCH = 64   # 思考 token：64 个合并一次（思考流粒度粗，无需逐字）


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
    if messages:
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
        received_reasoning = False
        # v6.5.7: 事件批量发射——逐 token 发射会撑爆 SSE 订阅队列（2000 容量），
        # 长任务思考流上万事件把队列填满后后续事件（含确认请求）被静默丢弃，
        # 前端表现为弹窗不出现/输出不更新（卡死）。批量后事件量降为 1/16、1/64。
        token_batcher = StreamBatcher(bus, EventType.TOKEN, TOKEN_BATCH, source=source)
        reason_batcher = StreamBatcher(bus, EventType.REASONING, REASONING_BATCH, source=source)
        # v6.5.6: 妙笔任务终止时即时打断——宿主注入 stop_event，
        # 每次收到 chunk 都检查，不等当前 LLM 调用自然结束（"直接打断"）
        _stop_event = getattr(agent, "stop_event", None)
        # v7.0.1: 流开始前补一次终止检查——流挂起/迟迟不吐 chunk 时也能即时打断
        if _stop_event is not None and _stop_event.is_set():
            from muse.workflow import MuseStopped
            raise MuseStopped("妙笔任务已终止")
        # v7.0.1: LLM 网络/协议错误不再让整轮直接崩溃传播——
        # 发射错误事件后以无响应结束本轮，由上层决定重试或结束会话
        try:
            for chunk in agent.llm.chat_stream(
                messages=agent._normalize_messages(messages),
                system_prompt=agent.system_prompt,
                tools=agent.tool_defs,
            ):
                if _stop_event is not None and _stop_event.is_set():
                    token_batcher.flush()
                    reason_batcher.flush()
                    from muse.workflow import MuseStopped
                    raise MuseStopped("妙笔任务已终止")
                if chunk["type"] == "token":
                    token_batcher.add(chunk["text"])
                elif chunk["type"] == "reasoning":
                    # v6.5.5: 思考过程实时流式发射，前端实时展示思考过程
                    # （修复审阅/写作长时间无任何界面反馈，误以为卡死）
                    text = chunk.get("text", "")
                    if text:
                        received_reasoning = True
                        reason_batcher.add(text)
                elif chunk["type"] == "done":
                    response = chunk
        except MuseStopped:
            raise  # 控制流异常（用户终止），不得被通用 except 吞掉
        except Exception as e:
            token_batcher.flush()
            reason_batcher.flush()
            print(f"[WARN] LLM 流式调用异常: {e}")
            bus.emit(EventType.ERROR, {"text": f"LLM 调用失败：{e}"}, source=source)
            break
        # 流结束：冲掉剩余缓冲
        token_batcher.flush()
        reason_batcher.flush()

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

        # 发射完整思考过程（仅当流式中未逐块发射过时兜底，避免重复）
        reasoning = response.get("reasoning_content")
        if reasoning and not received_reasoning:
            bus.emit(EventType.REASONING, {"text": reasoning}, source=source)

        # --- 构建 assistant 消息（OpenAI 原生格式） ---
        content = response.get("content")
        # v7.0.1: 无正文且无工具调用的空响应兜底为空串（content=None 会导致下游拼接/解析异常）
        if not content and not response.get("tool_calls"):
            content = ""
        assistant_msg = {"role": "assistant", "content": content}
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
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[WARN] 工具调用参数解析失败 (tool={tool_name}): {e}")
                tool_input = {}

            # 特殊处理 agent_output
            if tool_name == "agent_output":
                text = tool_input.get("text", "")
                bus.emit(EventType.OUTPUT, {"text": text}, source=source)
                # 记录本轮经 agent_output 发射过的文本，供调用方（如 Jianzhi.chat 尾部）
                # 去重，避免同一回复被 agent_output 与尾部兑底各发射一次 OUTPUT 导致重复发言
                agent._last_agent_output = text
                result = "(已输出)"
            else:
                # 发射工具调用事件（v6.5.3: 审阅工具附等级/引用供前端评分卡片渲染）
                brief = str(tool_input.get("name") or tool_input.get("chapters", ""))
                tool_evt = {"name": tool_name, "brief": brief}
                if tool_name == "report_issue":
                    tool_evt["level"] = tool_input.get("level")
                    tool_evt["quote"] = str(tool_input.get("quote", ""))[:80]
                    tool_evt["description"] = str(tool_input.get("description", ""))[:200]
                    tool_evt["suggestion"] = str(tool_input.get("suggestion", ""))[:200]
                bus.emit(EventType.TOOL_CALL, tool_evt, source=source)

                # 调度执行
                try:
                    result = agent.dispatch_tool(tool_name, tool_input)
                except Exception as e:
                    # v6.5.9: MuseStopped 是控制流异常（用户终止），不能被通用 except 吞掉
                    from muse.workflow import MuseStopped
                    if isinstance(e, MuseStopped):
                        raise
                    print(f"[WARN] 工具调度异常 (tool={tool_name}): {e}")
                    result = f"错误：工具调度异常 {e}"

                # 发射工具结果事件（v7.0.1: result 可能非 str，先转字符串再截断）
                bus.emit(EventType.TOOL_RESULT, {"name": tool_name, "msg": str(result)[:60]}, source=source)

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
