"""workspace 子命令组 — 工作区管理"""

import json
import shutil
import typer
from pathlib import Path

from commands.common import (
    load_config, save_config, get_workspaces_dir, resolve_workspace, require_workspace
)
from core.output import OutputFormatter
from tools import workspace as workspace_tools

app = typer.Typer(help="工作区管理")


@app.command("list")
def ws_list(
    workspace: str = typer.Option("", "--workspace", "-w", help="（此命令无需指定）"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出所有工作区"""
    config = load_config()
    ws_dir = get_workspaces_dir(config)
    fmt = OutputFormatter(json_mode=json_mode)
    result = workspace_tools.list_workspaces(ws_dir)
    if json_mode:
        import json
        entries = sorted([d.name for d in ws_dir.iterdir() if d.is_dir()])
        last = config.get("workspace", {}).get("last", "")
        print(json.dumps({"status": "success", "workspaces": entries, "current": last},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(result)


@app.command("switch")
def ws_switch(
    name: str = typer.Argument(..., help="工作区名称"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """切换到指定工作区"""
    config = load_config()
    ws_dir = get_workspaces_dir(config)
    fmt = OutputFormatter(json_mode=json_mode)

    target = workspace_tools.switch_workspace(ws_dir, name)
    if target is None:
        fmt.error(f"工作区「{name}」不存在")
        raise typer.Exit(1)

    config.setdefault("workspace", {})["last"] = name
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "message": f"已切换到工作区「{name}」"},
                         ensure_ascii=False))
    else:
        fmt.result(f"已切换到工作区「{name}」")


@app.command("create")
def ws_create(
    name: str = typer.Argument(..., help="新工作区名称"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """新建工作区并切换"""
    config = load_config()
    ws_dir = get_workspaces_dir(config)
    fmt = OutputFormatter(json_mode=json_mode)

    target = workspace_tools.create_workspace(ws_dir, name)
    if target is None:
        fmt.error("名称非法或工作区已存在")
        raise typer.Exit(1)

    config.setdefault("workspace", {})["last"] = name
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "message": f"已创建工作区「{name}」"},
                         ensure_ascii=False))
    else:
        fmt.result(f"已创建工作区「{name}」")


@app.command("rename")
def ws_rename(
    name: str = typer.Argument(..., help="新名称"),
    workspace: str = typer.Option("", "--workspace", "-w", help="当前工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """重命名当前工作区"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = resolve_workspace(config, workspace)
    if ws is None:
        fmt.error("请先指定一个有效工作区")
        raise typer.Exit(1)

    result = workspace_tools.update_workspace(ws, name)
    if isinstance(result, str):
        fmt.error(result)
        raise typer.Exit(1)

    config.setdefault("workspace", {})["last"] = name
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "message": f"已重命名为「{name}」"},
                         ensure_ascii=False))
    else:
        fmt.result(f"已重命名为「{name}」")


@app.command("delete")
def ws_delete(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """删除工作区"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = resolve_workspace(config, workspace)
    if ws is None:
        fmt.error("请先指定一个有效工作区")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(f"确认删除工作区「{ws.name}」？")
        if not confirm:
            fmt.info("已取消。")
            return

    ok = workspace_tools.delete_workspace(ws)
    if ok:
        # 回退到第一个工作区
        ws_dir = get_workspaces_dir(config)
        remaining = sorted([d for d in ws_dir.iterdir() if d.is_dir()])
        config.setdefault("workspace", {})["last"] = remaining[0].name if remaining else ""
        save_config(config)
        if json_mode:
            print(json.dumps({"status": "success", "message": "已删除"},
                             ensure_ascii=False))
        else:
            fmt.result("已删除。")
    else:
        fmt.error("删除失败（文件可能被占用）")
        raise typer.Exit(1)


@app.command("move")
def ws_move(
    path: str = typer.Argument(..., help="目标路径"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """迁移工作区目录到新位置"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws_dir = get_workspaces_dir(config)
    target = Path(path).resolve()

    if target == ws_dir:
        fmt.error("目标路径与当前相同")
        raise typer.Exit(1)
    if target.exists():
        fmt.error(f"目标路径已存在：{target}")
        raise typer.Exit(1)

    try:
        target.mkdir(parents=True, exist_ok=True)
        for item in ws_dir.iterdir():
            dst = target / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        shutil.rmtree(ws_dir)
        config.setdefault("workspace", {})["dir"] = str(target)
        save_config(config)
        fmt.result(f"工作区目录已迁移到：{target}")
    except Exception as e:
        fmt.error(f"迁移失败：{e}")
        raise typer.Exit(1)
