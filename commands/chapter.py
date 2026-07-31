"""chapter 子命令组 — 章节管理"""

import json
import typer
from pathlib import Path

from commands.common import load_config, require_workspace
from core.output import OutputFormatter
from tools import workspace as workspace_tools
from tools.chapter import chapter_list, show_chapter

app = typer.Typer(help="章节管理")


@app.command("import")
def ch_import(
    path: str = typer.Argument(..., help="小说文件路径"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    append: bool = typer.Option(False, "--append", help="增量导入（不覆盖已有章节）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """导入小说文件（按章节拆分）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    # P1-01：先预校验文件可解析（存在性+编码+章节标题），再动旧数据，
    # 避免"先删后导"在文件损坏/无法解析时旧章节已清空不可恢复
    err = workspace_tools.check_novel_file(path)
    if err:
        msg = f"无法导入：{path}（{err}）"
        if json_mode:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            fmt.error(msg)
        raise typer.Exit(1)

    # 检查是否已有章节
    from tools.editor import _get_proxy
    proxy = _get_proxy(ws)
    db = proxy._db
    existing_count = db.chapter_count()

    if existing_count > 0 and not append:
        if not yes:
            confirm = typer.confirm(f"工作区已有 {existing_count} 章，确认删除后重新导入？")
            if not confirm:
                fmt.info("已取消导入。")
                return
        # P1-01：删除旧章节与导入在同一事务内，任一步失败自动回滚
        with db.transaction():
            db.chapter_delete_all()
            result = workspace_tools.import_novel(ws, path)
    else:
        result = workspace_tools.import_novel(ws, path)

    if result.startswith("错误"):
        if json_mode:
            print(json.dumps({"status": "error", "message": result}, ensure_ascii=False))
        else:
            fmt.error(result)
        raise typer.Exit(1)
    if json_mode:
        print(json.dumps({"status": "success", "message": result}, ensure_ascii=False))
    else:
        fmt.result(result)


@app.command("list")
def ch_list(
    n: int = typer.Option(50, "-n", help="显示最新 N 章"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出章节号+标题"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    result = workspace_tools.list_latest_chapters(ws, n)

    if json_mode:
        # 首行为表头（"最新 N 章（共 M 章）："），不计入章节数
        lines = [l for l in result.strip().splitlines() if l.strip()]
        chapters = [l.strip() for l in lines[1:]]
        print(json.dumps({"status": "success", "answer": result, "count": len(chapters)},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(result)


@app.command("show")
def ch_show(
    num: int = typer.Argument(..., help="章节号"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看某章内容"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    result = show_chapter(ws, num)

    if json_mode:
        if result.startswith("错误"):
            print(json.dumps({"status": "error", "message": result},
                             ensure_ascii=False, indent=2))
            raise typer.Exit(1)
        print(json.dumps({"status": "success", "answer": result},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(result)


@app.command("export")
def ch_export(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """合并所有章节为 txt"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    txt_path = ws / f"{ws.name}.txt"
    if txt_path.exists() and not yes:
        confirm = typer.confirm(f"文件 {txt_path.name} 已存在，确认覆盖？")
        if not confirm:
            fmt.info("已取消导出。")
            return

    result = workspace_tools.export_novel(ws)
    if result.startswith("错误"):
        if json_mode:
            print(json.dumps({"status": "error", "message": result}, ensure_ascii=False))
        else:
            fmt.error(result)
        raise typer.Exit(1)
    if json_mode:
        print(json.dumps({"status": "success", "message": result}, ensure_ascii=False))
    else:
        fmt.result(result)


@app.command("status")
def ch_status(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """章节处理状态（含已提取/未提取标记）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    result = chapter_list(ws)

    if json_mode:
        print(json.dumps({"status": "success", "answer": result},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(result)
