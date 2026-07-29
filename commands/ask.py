"""ask 命令 — 单轮提问"""

import time
import threading
import typer

from commands.common import load_config, resolve_workspace, make_io, SKILLS_DIR
from core.output import OutputFormatter
from core.events import EventBus, EventType


def ask(
    question: str = typer.Argument(..., help="提问内容"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """单轮提问 — 完整 Agent loop 后退出"""
    config = load_config()
    ws = resolve_workspace(config, workspace)

    fmt = OutputFormatter(json_mode=json_mode)
    if ws is None:
        fmt.error("请先指定一个有效工作区")
        raise typer.Exit(1)

    # 创建 I/O 通道（单轮模式）
    io = make_io(json_mode=json_mode, workspace=ws, mode="single-turn", cmd="ask")

    # 初始化 Agent（事件总线模式）
    from Jianzhi import JianzhiAgent
    from commands.chat import _CLIConsumer
    bus = EventBus()
    jianzhi = JianzhiAgent(config, ws, SKILLS_DIR, bus)

    # 启动事件消费线程
    consumer = _CLIConsumer(io, bus)
    consumer_thread = threading.Thread(target=consumer.run, daemon=True)
    consumer_thread.start()

    # 执行单轮提问
    start = time.time()
    agent_done = threading.Event()

    def _run():
        try:
            jianzhi.chat(question)
        except Exception as e:
            bus.emit(EventType.ERROR, {"text": f"Agent 异常：{e}"}, source="jianzhi")
        finally:
            bus.emit(EventType.TASK_DONE, {}, source="jianzhi")
            agent_done.set()

    agent_thread = threading.Thread(target=_run, daemon=True)
    agent_thread.start()
    consumer.wait_for_done(agent_done)
    elapsed = time.time() - start

    # 收集统计
    tokens = getattr(jianzhi, '_token_accum', None)
    if tokens and (tokens.get("input", 0) > 0 or tokens.get("output", 0) > 0):
        token_data = {"input": tokens["input"], "output": tokens["output"], "total": tokens["total"]}
    else:
        token_data = None

    # 输出统计摘要（json 模式）
    if json_mode:
        # 从 Agent 的最后输出中提取答案
        answer = _extract_last_answer(jianzhi)
        tools = _extract_tools_called(jianzhi)
        fmt.summary(
            answer=answer,
            tools_called=tools,
            tokens=token_data,
            elapsed=elapsed,
        )

    consumer.stop()
    io.close_logger()


def _extract_last_answer(jianzhi) -> str:
    """从 Agent 历史中提取最后一条 assistant 回复"""
    history = getattr(jianzhi, 'messages', [])
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"].strip()
    return ""


def _extract_tools_called(jianzhi) -> list:
    """从 Agent 消息历史中提取调用过的工具名"""
    tools = []
    history = getattr(jianzhi, 'messages', [])
    for msg in history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("function", {}).get("name", "")
                if name and name not in tools:
                    tools.append(name)
    return tools
