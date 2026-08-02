"""muse 命令 — 妙笔写作工作流"""

import datetime
import sys
import time
import typer
from pathlib import Path

from commands.common import load_config, resolve_workspace, make_io, SKILLS_DIR, get_workspaces_dir
from core.output import OutputFormatter


class _StdoutMirror:
    """v6.5.6: stdout 镜像——同时写终端与 UTF-8 日志文件

    背景：PowerShell 重定向/管道（Tee-Object、> file）默认按系统编码（GBK）写盘，
    而程序内部统一 UTF-8，导致日志文件中文乱码。
    方案：muse 命令启动时用本类替换 sys.stdout，程序自身以 UTF-8 落盘，
    终端显示与日志文件互不影响（跑 `python main.py muse ...` 即自动留档，无需管道）。
    """

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log_file = log_file

    def write(self, s: str):
        try:
            self._stream.write(s)
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log_file.write(s)
            self._log_file.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log_file.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _install_stdout_log(ws: Path) -> tuple:
    """安装 stdout/stderr 镜像日志；失败时静默降级（不影响主流程）

    Returns:
        (orig_stdout, orig_stderr, log_file | None)
    """
    try:
        log_dir = ws / "session"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = (log_dir / f"muse_stdout_{stamp}.log").open("w", encoding="utf-8")
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout = _StdoutMirror(orig_out, log_file)
        sys.stderr = _StdoutMirror(orig_err, log_file)
        return orig_out, orig_err, log_file
    except Exception:
        return None, None, None


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

    # v6.5.6: 程序自身保存 stdout/stderr 日志（UTF-8），绕过 PowerShell 重定向的 GBK 乱码
    orig_stdout, orig_stderr, stdout_log = (None, None, None)
    if ws is not None:
        orig_stdout, orig_stderr, stdout_log = _install_stdout_log(ws)

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

    # 恢复 stdout/stderr 并关闭镜像日志（异常退出时由解释器自动关闭，已逐次 flush 不丢数据）
    if orig_stdout is not None:
        sys.stdout = orig_stdout
    if orig_stderr is not None:
        sys.stderr = orig_stderr
    if stdout_log is not None:
        try:
            stdout_log.close()
        except Exception:
            pass
