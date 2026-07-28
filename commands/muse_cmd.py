"""muse 命令 — 妙笔写作工作流"""

import time
import typer
from pathlib import Path

from commands.common import load_config, resolve_workspace, make_io, SKILLS_DIR, get_workspaces_dir
from core.output import OutputFormatter


def muse(
    outline_file: str = typer.Option(..., "--outline-file", help="大纲文件路径"),
    chapter: int = typer.Option(0, "--chapter", "-c", help="目标章节号（不传则默认最新章节+1）"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过所有确认（全自动）"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """妙笔写作工作流"""
    config = load_config()
    ws = resolve_workspace(config, workspace)

    fmt = OutputFormatter(json_mode=json_mode)
    if ws is None:
        fmt.error("请先指定一个有效工作区")
        raise typer.Exit(1)

    # 读取大纲文件
    outline_path = Path(outline_file)
    if not outline_path.exists():
        fmt.error(f"大纲文件不存在：{outline_file}")
        raise typer.Exit(1)
    outline_text = outline_path.read_text(encoding="utf-8").strip()
    if not outline_text:
        fmt.error("大纲文件为空")
        raise typer.Exit(1)

    # 创建 I/O 通道
    io = make_io(json_mode=json_mode, auto_yes=yes, workspace=ws,
                 mode="single-turn", cmd="muse")

    # 初始化 MuseWorkflow
    from Muse import MuseWorkflow
    ws_dir = get_workspaces_dir(config)

    start = time.time()
    workflow = MuseWorkflow(
        config=config,
        workspace=ws,
        skills_dir=SKILLS_DIR,
        workspaces_dir=ws_dir,
        io=io,
        outline_text=outline_text,
        auto_approve=yes,
        chapter=chapter if chapter > 0 else None,
    )
    workflow.run()
    elapsed = time.time() - start

    # 统计输出
    if json_mode:
        tokens = getattr(workflow, '_token_total', {})
        token_data = None
        if tokens and tokens.get("total", 0) > 0:
            token_data = {"input": tokens["input"], "output": tokens["output"], "total": tokens["total"]}
        fmt.summary(
            answer=f"妙笔写作完成，输出目录：{workflow.io.task_dir}",
            tokens=token_data,
            elapsed=elapsed,
        )

    io.close_logger()
