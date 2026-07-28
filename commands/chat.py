"""chat 命令 — 鉴知对话 REPL"""

import typer

from commands.common import (
    load_config, save_config, get_workspaces_dir, resolve_workspace,
    require_workspace, make_io, SKILLS_DIR
)
from core.output import OutputFormatter


_HELP_TEXT = """可用指令：

  会话控制：
    /exit              退出
    /help              显示本帮助
    /clear             清空对话上下文
    /compact           主动压缩上下文
    /context           查看上下文占用与组成
    /token             查询本会话累计 token 用量

  快速查询：
    /chapters [-n]     列出最新N章（默认50）
    /show <num>        展示指定章节内容
    /status            章节处理状态
    /wiki <name>       查看指定词条
    /rule [name]       查看规则（无参数=列表）
    /relation <name>   查询词条关联
    /memory            查看记忆索引

  操作：
    /extract           触发知识提取
"""


def chat(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
):
    """进入鉴知对话 REPL"""
    config = load_config()
    ws = resolve_workspace(config, workspace)

    if ws is None:
        print("尚无工作区，请先使用 inkweaver workspace create <名称> 创建。")
        raise typer.Exit(1)

    # 更新 config 中的 last
    config.setdefault("workspace", {})["last"] = ws.name
    save_config(config)

    # 创建 I/O 通道
    io = make_io(json_mode=False, workspace=ws, mode="chat")
    io.print_info(f"进入工作区：{ws.name}")
    io.print_info("输入 /help 查看可用指令。")

    # 初始化 Agent
    from Jianzhi import JianzhiAgent
    jianzhi = JianzhiAgent(config, ws, SKILLS_DIR, io)

    # REPL 主循环
    while True:
        text = io.read_line()
        if text is None:
            break

        stripped = text.strip()
        if not stripped:
            continue

        # 斜杠指令
        if stripped.startswith("/"):
            cmd_text = stripped[1:]
            io.log_cli(cmd_text)
            if not _handle_slash(cmd_text, io, jianzhi, config, ws):
                break
        else:
            # 普通对话
            jianzhi.chat(text)

    io.close_logger()
    io.print_info("再见！")


def _handle_slash(cmd: str, io, jianzhi, config: dict, ws) -> bool:
    """处理斜杠指令，返回 False 表示退出"""
    parts = cmd.strip().split()
    if not parts:
        return True

    command = parts[0].lower()

    if command == "exit":
        return False

    elif command == "help":
        io.print_info(_HELP_TEXT)

    elif command == "clear":
        jianzhi.clear_context()

    elif command == "compact":
        jianzhi.compact_history()

    elif command == "context":
        io.print_info(jianzhi.context_report())

    elif command == "token":
        io.print_info(jianzhi.token_report())

    elif command == "chapters":
        n = 50
        if len(parts) > 1:
            try:
                n = int(parts[1])
            except ValueError:
                pass
        from tools import workspace as workspace_tools
        io.print_info(workspace_tools.list_latest_chapters(ws, n))

    elif command == "show":
        if len(parts) < 2:
            io.print_info("用法：/show <章节号>")
            return True
        try:
            num = int(parts[1])
        except ValueError:
            io.print_info("错误：章节号必须为整数")
            return True
        from tools.chapter import show_chapter
        io.print_info(show_chapter(ws, num))

    elif command == "status":
        from tools.chapter import chapter_list
        io.print_info(chapter_list(ws))

    elif command == "wiki":
        if len(parts) < 2:
            io.print_info("用法：/wiki <词条名>")
            return True
        name = parts[1]
        from tools.editor import _get_proxy
        proxy = _get_proxy(ws)
        cats = proxy.list_categories()
        found = False
        for cat in cats:
            result = proxy.read_doc("wiki", name, category=cat["name"], yaml_only=False)
            if not result.startswith("错误"):
                io.print_info(result)
                found = True
                break
        if not found:
            io.print_info(f"词条「{name}」不存在")

    elif command == "rule":
        from tools.rules import rules_list, read_rule
        if len(parts) > 1:
            io.print_info(read_rule(ws, parts[1]))
        else:
            io.print_info(rules_list(ws))

    elif command == "relation":
        if len(parts) < 2:
            io.print_info("用法：/relation <词条名>")
            return True
        from tools.relation import query_relations
        io.print_info(query_relations(ws, parts[1]))

    elif command == "memory":
        from tools.memory import read_memory
        io.print_info(read_memory(ws, None))

    elif command == "extract":
        io.print_info("触发知识提取...")
        jianzhi.chat("请执行知识提取流程")

    else:
        io.print_info(f"未知指令：/{command}，输入 /help 查看可用指令")

    return True
