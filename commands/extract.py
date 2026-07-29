"""extract 命令 — 单轮知识提取"""

import time
import json
import threading
import typer
from pathlib import Path

from commands.common import load_config, resolve_workspace, make_io, SKILLS_DIR
from core.output import OutputFormatter
from core.events import EventBus, EventType


def extract(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    chapters: str = typer.Option("", "--chapters", help="章节范围，如 21-30"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过计划确认"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """单轮知识提取"""
    config = load_config()
    ws = resolve_workspace(config, workspace)

    fmt = OutputFormatter(json_mode=json_mode)
    if ws is None:
        fmt.error("请先指定一个有效工作区")
        raise typer.Exit(1)

    # 计算提取范围
    start_ch, end_ch = _compute_range(ws, chapters)
    if start_ch is None:
        fmt.error("没有可提取的章节（全部已处理或无章节）")
        raise typer.Exit(1)

    # 创建 I/O 通道
    io = make_io(json_mode=json_mode, auto_yes=yes, workspace=ws,
                 mode="single-turn", cmd="extract")

    # 初始化 Agent（事件总线模式）
    from Jianzhi import JianzhiAgent
    from commands.chat import _CLIConsumer
    bus = EventBus()
    jianzhi = JianzhiAgent(config, ws, SKILLS_DIR, bus)

    # 启动事件消费线程
    consumer = _CLIConsumer(io, bus)
    consumer_thread = threading.Thread(target=consumer.run, daemon=True)
    consumer_thread.start()

    # 构造提取指令
    prompt = f"请对第{start_ch}~{end_ch}章执行知识提取流程"
    if not fmt.json_mode:
        fmt.info(f"提取范围：第 {start_ch}~{end_ch} 章")

    # 执行（Agent 在独立线程，主线程处理确认）
    elapsed_start = time.time()
    agent_done = threading.Event()

    def _run():
        try:
            jianzhi.chat(prompt)
        except Exception as e:
            bus.emit(EventType.ERROR, {"text": f"Agent 异常：{e}"}, source="jianzhi")
        finally:
            bus.emit(EventType.TASK_DONE, {}, source="jianzhi")
            agent_done.set()

    agent_thread = threading.Thread(target=_run, daemon=True)
    agent_thread.start()
    consumer.wait_for_done(agent_done)
    elapsed = time.time() - elapsed_start

    # 统计输出
    if json_mode:
        tokens = getattr(jianzhi, '_token_accum', None)
        token_data = None
        if tokens and (tokens.get("input", 0) > 0 or tokens.get("output", 0) > 0):
            token_data = {"input": tokens["input"], "output": tokens["output"], "total": tokens["total"]}
        # 从消息历史提取工具调用
        tools = []
        for msg in getattr(jianzhi, 'messages', []):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name = tc.get("function", {}).get("name", "")
                    if name and name not in tools:
                        tools.append(name)
        fmt.summary(
            answer=f"知识提取完成：第{start_ch}~{end_ch}章",
            tools_called=tools,
            tokens=token_data,
            elapsed=elapsed,
        )

    consumer.stop()
    io.close_logger()


def _compute_range(ws: Path, chapters_flag: str) -> tuple[int | None, int | None]:
    """计算提取范围

    Returns:
        (start, end) 或 (None, None) 表示无可提取章节
    """
    if chapters_flag:
        # 手动指定：解析 "21-30" 格式
        parts = chapters_flag.split("-")
        try:
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
            elif len(parts) == 1:
                n = int(parts[0])
                return n, n
        except ValueError:
            return None, None
        return None, None

    # 自动计算
    processed_max = _get_processed_max(ws)
    total = _get_total_chapters(ws)

    if total == 0:
        return None, None

    start = processed_max + 1
    if start > total:
        return None, None

    end = min(start + 9, total)  # 最多 10 章
    return start, end


def _get_processed_max(ws: Path) -> int:
    """获取已提取的最大章节号"""
    log_path = ws / "log.json"
    if not log_path.exists():
        return 0
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        processed = data.get("processed", {})
        # 兼容新格式（dict: {"chapter_ranges": [...], ...}）和旧格式（list）
        if isinstance(processed, dict):
            ranges = processed.get("chapter_ranges", [])
        elif isinstance(processed, list):
            ranges = processed
        else:
            return 0
        if not ranges:
            return 0
        from tools.chapter import parse_chapter_spec
        max_ch = 0
        for item in ranges:
            if isinstance(item, str):
                nums = parse_chapter_spec(item)
                if nums:
                    max_ch = max(max_ch, max(nums))
            elif isinstance(item, list) and len(item) == 2:
                max_ch = max(max_ch, item[1])
            elif isinstance(item, int):
                max_ch = max(max_ch, item)
        return max_ch
    except Exception:
        return 0


def _get_total_chapters(ws: Path) -> int:
    """获取总章节数"""
    try:
        from tools.editor import _get_proxy
        proxy = _get_proxy(ws)
        return proxy._db.chapter_count()
    except Exception:
        return 0
