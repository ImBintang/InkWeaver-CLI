"""kb 子命令组 — 知识库查询（wiki/rule/plot 三合一）"""

import json
import typer
from pathlib import Path

from commands.common import load_config, require_workspace
from core.output import OutputFormatter

app = typer.Typer(help="知识库查询（wiki/rule/plot）")


def _get_proxy_for(workspace: Path):
    """获取工作区的 proxy 实例"""
    from tools.editor import _get_proxy
    return _get_proxy(workspace)


@app.command("list")
def kb_list(
    type_filter: str = typer.Option("", "--type", help="按类型过滤：wiki/plot/rule"),
    category: str = typer.Option("", "--category", help="按类别过滤"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出知识库条目"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)
    proxy = _get_proxy_for(ws)

    lines = []

    # wiki 词条
    if not type_filter or type_filter == "wiki":
        cats = proxy.list_categories()
        for cat in cats:
            if category and cat["name"] != category:
                continue
            docs = proxy.list_docs("wiki", category=cat["name"])
            for doc in docs:
                lines.append(f"[wiki/{cat['name']}] {doc}")

    # plot 剧情卡片
    if not type_filter or type_filter == "plot":
        plots = proxy.list_docs("plot")
        for p in plots:
            lines.append(f"[plot] {p}")

    # rule 规则
    if not type_filter or type_filter == "rule":
        from tools.rules import rules_list
        result = rules_list(ws)
        if result and not result.startswith("（"):
            for line in result.strip().splitlines():
                lines.append(f"[rule] {line.strip()}")

    if not lines:
        output = "（知识库为空）"
    else:
        output = "\n".join(lines)

    if json_mode:
        print(json.dumps({"status": "success", "answer": output, "count": len(lines)},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(output)


@app.command("show")
def kb_show(
    name: str = typer.Argument(..., help="条目名称"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看条目详情（自动遍历 wiki/rule/plot）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)
    proxy = _get_proxy_for(ws)

    # 先搜 wiki
    cats = proxy.list_categories()
    for cat in cats:
        result = proxy.read_doc("wiki", name, category=cat["name"], yaml_only=False)
        if not result.startswith("错误"):
            if json_mode:
                print(json.dumps({"status": "success", "answer": result, "type": "wiki"},
                                 ensure_ascii=False, indent=2))
            else:
                fmt.result(result)
            return

    # 再搜 plot
    result = proxy.read_doc("plot", name, yaml_only=False)
    if not result.startswith("错误"):
        if json_mode:
            print(json.dumps({"status": "success", "answer": result, "type": "plot"},
                             ensure_ascii=False, indent=2))
        else:
            fmt.result(result)
        return

    # 最后搜 rule
    from tools.rules import read_rule
    result = read_rule(ws, name)
    if not result.startswith("错误"):
        if json_mode:
            print(json.dumps({"status": "success", "answer": result, "type": "rule"},
                             ensure_ascii=False, indent=2))
        else:
            fmt.result(result)
        return

    fmt.error(f"条目「{name}」不存在")
    raise typer.Exit(1)


@app.command("categories")
def kb_categories(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出所有类别"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)
    proxy = _get_proxy_for(ws)

    cats = proxy.list_categories()

    if json_mode:
        print(json.dumps({"status": "success", "categories": cats},
                         ensure_ascii=False, indent=2))
    else:
        if not cats:
            fmt.result("（尚无类别）")
        else:
            lines = []
            for cat in cats:
                spec = cat.get("spec", {})
                desc = spec.get("description", "") if isinstance(spec, dict) else ""
                lines.append(f"  • {cat['name']} — {desc}")
            fmt.result("\n".join(lines))


@app.command("relation")
def kb_relation(
    name: str = typer.Argument(..., help="词条名"),
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查询词条关联"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    ws = require_workspace(config, workspace, json_mode)

    from tools.relation import query_relations
    result = query_relations(ws, name)

    if json_mode:
        print(json.dumps({"status": "success", "answer": result},
                         ensure_ascii=False, indent=2))
    else:
        fmt.result(result)
