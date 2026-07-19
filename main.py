"""InkWeaver-CLI 入口程序"""

import sys
import shutil
import yaml
from pathlib import Path

from cli import CLI
from Jianzhi import JianzhiAgent
from agent.knowledge import KnowledgeAgent
from tools import workspace as workspace_tools


CONFIG_PATH = Path(".env/config.yaml")
WORKSPACES_DIR = Path("../workingArea")  # 启动后从 config 加载覆盖
SKILLS_DIR = Path("skills")

REQUIRED_CONFIG_KEYS = {
    "api": ["url", "key", "model", "output_max_tokens"],
}

DEFAULT_CONFIG = {
    "api": {
        "url": "https://api.deepseek.com",
        "key": "sk-your-api-key-here",
        "model": "deepseek-chat",
        "input_max_tokens": 384000,
        "output_max_tokens": 128000,
    },
    "workspace": {
        "dir": "../workingArea",
        "last": "",
    },
}


def _ensure_dirs():
    """自动创建缺失的目录和配置文件"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        cfg = dict(DEFAULT_CONFIG)
        # 将默认的工作区路径转为绝对路径
        default_ws_dir = Path(cfg["workspace"]["dir"]).resolve()
        cfg["workspace"]["dir"] = str(default_ws_dir)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"[配置] 已自动创建配置文件：{CONFIG_PATH}")

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # WORKSPACES_DIR 此时可能在 config 中指定，先尝试用默认值
    ws_dir = WORKSPACES_DIR
    try:
        cfg = load_config()
        cfg_dir = cfg.get("workspace", {}).get("dir")
        if cfg_dir:
            ws_dir = Path(cfg_dir)
    except Exception:
        pass
    ws_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def validate_config(config: dict) -> str | None:
    """验证配置完整性，返回错误信息或 None"""
    if not config or not isinstance(config, dict):
        return "配置文件为空或格式错误"

    for section, keys in REQUIRED_CONFIG_KEYS.items():
        sec = config.get(section)
        if not isinstance(sec, dict):
            return f"配置缺少「{section}」字段"
        for key in keys:
            val = sec.get(key)
            if not val:
                return f"配置 {section}.{key} 缺失或为空"

    # 检查 key 是否为默认占位值
    if config["api"]["key"] == "sk-your-api-key-here":
        return "请先在 config.yaml 中配置有效的 api.key"

    return None


def test_api_connection(config: dict) -> str | None:
    """测试 API 连接，返回错误信息或 None"""
    try:
        from api import LLMClient
        client = LLMClient(config["api"])
        resp = client.chat(
            messages=[{"role": "user", "content": "ping"}],
            system_prompt="只回复一个字母 p",
            tools=None,
        )
        if resp["stop_reason"] != "stop":
            return "API 响应异常"
        return None
    except Exception as e:
        return f"API 连接测试失败：{e}"


def resolve_workspace(config: dict) -> Path:
    """确定当前工作区，空 workSpace 时自动创建「未命名」"""
    global WORKSPACES_DIR
    ws_dir_str = config.get("workspace", {}).get("dir", "../workingArea")
    WORKSPACES_DIR = Path(ws_dir_str).resolve()
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    workspaces = sorted([d for d in WORKSPACES_DIR.iterdir() if d.is_dir()])
    if not workspaces:
        target = WORKSPACES_DIR / "未命名"
        if not target.exists():
            workspace_tools.create_workspace(WORKSPACES_DIR, "未命名")
            print(f"[工作区] 检测到无工作区，已自动创建「未命名」，请记得改名。")

    # 再次扫描
    workspaces = sorted([d for d in WORKSPACES_DIR.iterdir() if d.is_dir()])
    if not workspaces:
        return None  # 不应发生

    last = config.get("workspace", {}).get("last")
    if last:
        target = WORKSPACES_DIR / last
        if target.exists():
            return target

    return workspaces[0]


_HELP_TEXT = """可用指令：

  工作区管理：
    /list                 列出所有工作区
    /switch -n <name>     切换到指定工作区
    /create -n <name>     新建工作区并切换
    /update -n <name>     重命名当前工作区
    /delete               删除当前工作区（需确认）
    /move -p <path>       移动工作区目录到新位置（更新配置）

  章节管理：
    /import -p <path>     导入小说文件（按章节拆分）
    /write                手动输入一章内容（qqq 结束）
    /show -n <num>        展示指定章节内容
    /chapters [-N]        列出最新N章的章节号和标题（默认50）
    /export               合并所有章节为 txt（需确认覆盖）

  Agent：
    /clear                清空对话上下文
    /context              查看上下文占用与组成
    /compact              主动压缩上下文
    /token                查询本会话累计 token 用量

  知识管理：
    /knowledge            进入 Knowledge 专家模式
    /exit                 退出 Knowledge 模式（回到普通模式）
    /update               触发知识提取
    /diff                 查看新增/修改的章节
    /memory               查看记忆索引
    /memory -n <name>     查看指定记忆文档
    /list -n <name>       查看指定类别的 wiki 列表
    /wiki -n <name>       查看指定词条的 wiki
    /rule                 查看规则列表
    /rule -n <name>       查看指定规则文档
    /relation -n <name>   查询词条关联
    /link                 从 wiki 文档提取 wikilink，构建关系图（relations.yaml）

  系统：
    /help                 显示本帮助
    /exit                 退出程序（或输入 exit）"""


def handle_command(cmd: str, cli: CLI, jianzhi: JianzhiAgent | None, config: dict):
    """处理 CLI 指令

    Returns:
        tuple[bool, JianzhiAgent | None]:
        - (True, agent) 继续循环
        - (False, agent) 请求退出
        agent 可能被修改（模式切换/工作区切换），调用方需更新引用
    """
    global WORKSPACES_DIR
    parts = cmd.strip().split()
    if not parts:
        return True, jianzhi

    command = parts[0].lower()
    cli.log_cli(cmd)

    # ---- 工作区相关 ----
    if command == "list":
        cli.print_info(workspace_tools.list_workspaces(WORKSPACES_DIR))

    elif command == "switch":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/switch -n <工作区名>")
            return True, jianzhi
        target = workspace_tools.switch_workspace(WORKSPACES_DIR, name)
        if target is None:
            cli.print_info(f"错误：工作区「{name}」不存在或名称非法")
            return True, jianzhi
        _switch_to_workspace(target, cli, config)

    elif command == "create":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/create -n <工作区名>")
            return True, jianzhi
        target = workspace_tools.create_workspace(WORKSPACES_DIR, name)
        if target is None:
            cli.print_info("错误：名称非法或工作区已存在")
            return True, jianzhi
        cli.print_info(f"已创建工作区「{name}」")
        _switch_to_workspace(target, cli, config)

    elif command == "update":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/update -n <新名称>")
            return True, jianzhi
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        # 先关闭日志释放文件锁（Windows rename 需要）
        cli.close_logger()
        old_ws = jianzhi.workspace
        result = workspace_tools.update_workspace(old_ws, name)
        if isinstance(result, str):
            cli.print_info(result)
            # 重命名失败，重新打开旧日志
            cli.init_logger(old_ws / "session")
        else:
            _switch_to_workspace(result, cli, config)

    elif command == "delete":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        cli.print_info(f"确认删除工作区「{jianzhi.workspace.name}」？(y/N)")
        # 先关闭日志释放文件锁
        cli.close_logger()
        confirm = input().strip().lower()
        if confirm != "y":
            # 取消删除，重新打开日志
            cli.init_logger(jianzhi.workspace / "session")
            cli.print_info("已取消。")
            return True, jianzhi
        if confirm == "y":
            ok = workspace_tools.delete_workspace(jianzhi.workspace)
            if ok:
                cli.print_info("已删除。")
            else:
                cli.print_info("删除失败。")
            # 回退到第一个工作区
            workspaces = sorted([d for d in WORKSPACES_DIR.iterdir() if d.is_dir()])
            if workspaces:
                _switch_to_workspace(workspaces[0], cli, config)
            else:
                config.setdefault("workspace", {})["last"] = ""
                save_config(config)
                cli.print_info("已无可用工作区。")

    elif command == "move":
        path = _get_flag_value(parts, "-p")
        if not path:
            cli.print_info("用法：/move -p <目标路径>")
            return True, jianzhi
        target = Path(path).resolve()
        if target == WORKSPACES_DIR.resolve():
            cli.print_info("目标路径与当前工作区目录相同")
            return True, jianzhi
        if target.exists():
            cli.print_info(f"错误：目标路径已存在 - {target}")
            return True, jianzhi
        # 关闭日志，释放文件锁
        cli.close_logger()
        jianzhi = None
        try:
            target.mkdir(parents=True, exist_ok=True)
            for item in WORKSPACES_DIR.iterdir():
                dst = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            # 删除旧目录
            shutil.rmtree(WORKSPACES_DIR)
            # 更新配置
            config.setdefault("workspace", {})["dir"] = str(target)
            save_config(config)
            WORKSPACES_DIR = target
            cli.print_info(f"工作区目录已迁移到：{target}")
            # 重新进入上次的工作区
            last = config.get("workspace", {}).get("last", "")
            if last and (target / last).exists():
                _switch_to_workspace(target / last, cli, config)
        except Exception as e:
            cli.print_info(f"移动失败：{e}")

    # ---- 章节相关 ----
    elif command == "import":
        path = _get_flag_value(parts, "-p")
        if not path:
            cli.print_info("用法：/import -p <文件路径>")
            return True, jianzhi
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        # 检查是否已有章节
        doc_dir = jianzhi.workspace / "document"
        existing = sorted(doc_dir.glob("c*.md")) if doc_dir.exists() else []
        if existing:
            cli.print_info("工作区已有章节，确认删除后重新导入？(y/N)")
            confirm = input().strip().lower()
            if confirm != "y":
                cli.print_info("已取消导入。")
                return True, jianzhi
            for f in existing:
                f.unlink()
        result = workspace_tools.import_novel(jianzhi.workspace, path)
        cli.print_info(result)

    elif command == "write":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        cli.print_info("请输入章节内容，格式：")
        cli.print_info("第x章 标题")
        cli.print_info("正文...")
        cli.print_info("以 qqq 结束：")
        lines = []
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip().lower() == "qqq":
                break
            lines.append(line)
        if not lines:
            cli.print_info("已取消。")
            return True, jianzhi
        raw = "\n".join(lines)
        first_line = lines[0].strip()
        from tools.chapter import parse_chapter_title, write_chapter
        num, title = parse_chapter_title(first_line)
        if num is None:
            cli.print_info("错误：首行格式有误，应为「第x章 xxx」")
            return True, jianzhi
        # 检查是否已存在
        doc_dir = jianzhi.workspace / "document"
        fp = doc_dir / f"c{num:03d}.md"
        if fp.exists():
            cli.print_info(f"第{num}章已存在（{title}），确认覆盖？(y/N)")
            confirm = input().strip().lower()
            if confirm != "y":
                cli.print_info("已取消。")
                return True, jianzhi
        result = write_chapter(jianzhi.workspace, num, raw)
        cli.print_info(result)

    elif command == "show":
        num_str = _get_flag_value(parts, "-n") or (parts[1] if len(parts) > 1 else "")
        try:
            num = int(num_str)
        except (ValueError, IndexError):
            cli.print_info("用法：/show -n <章节号>")
            return True, jianzhi
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        from tools.chapter import show_chapter
        cli.print_info(show_chapter(jianzhi.workspace, num))

    elif command == "chapters":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        n_str = _get_flag_value(parts, "-N")
        if n_str is None:
            n_str = parts[1] if len(parts) > 1 else None
        try:
            n = int(n_str) if n_str else 50
        except ValueError:
            cli.print_info("用法：/chapters [-N]（N 为正整数，默认50）")
            return True, jianzhi
        if n <= 0:
            cli.print_info("错误：N 必须为正整数")
            return True, jianzhi
        cli.print_info(workspace_tools.list_latest_chapters(jianzhi.workspace, n))

    elif command == "export":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        txt_path = jianzhi.workspace / f"{jianzhi.workspace.name}.txt"
        if txt_path.exists():
            cli.print_info(f"文件 {txt_path.name} 已存在，确认覆盖？(y/N)")
            confirm = input().strip().lower()
            if confirm != "y":
                cli.print_info("已取消导出。")
                return True, jianzhi
        result = workspace_tools.export_novel(jianzhi.workspace)
        cli.print_info(result)

    # ---- Agent 相关 ----
    elif command == "clear":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        jianzhi.clear_context()

    elif command == "context":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        cli.print_info(jianzhi.context_report())

    elif command == "compact":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        jianzhi.compact_history()

    elif command == "token":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        cli.print_info(jianzhi.token_report())

    elif command == "help":
        cli.print_info(_HELP_TEXT)

    elif command == "exit":
        # 如果在 Knowledge 模式中，退出到普通模式
        if isinstance(jianzhi, KnowledgeAgent):
            # 自动构建关系图（兜底：确保 wiki 关系是最新的）
            cli.print_info("正在构建关系图...")
            try:
                from auto.relation_extractor import build_relations, save_relations
                relations = build_relations(jianzhi.workspace)
                if relations:
                    save_relations(jianzhi.workspace, relations)
                    total = sum(len(t) for t in relations.values())
                    cli.print_info(f"关系图已构建：共 {len(relations)} 个词条，{total} 条关系")
                else:
                    cli.print_info("（未发现 wikilink 关系）")
            except Exception as e:
                cli.print_info(f"（构建关系图失败：{e}）")

            cli.print_info("退出 Knowledge 模式，回到普通模式。")
            saved_messages = jianzhi.messages
            jianzhi = JianzhiAgent(config, jianzhi.workspace, SKILLS_DIR, cli)
            jianzhi.messages = saved_messages
        else:
            return False, jianzhi

    elif command == "knowledge":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("你已在 Knowledge 模式中。")
            return True, jianzhi
        cli.print_info("进入 Knowledge 专家模式。")
        jianzhi = KnowledgeAgent(config, jianzhi.workspace, SKILLS_DIR, cli, messages=jianzhi.messages)

    elif command == "update":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        cli.print_info("触发知识提取...")
        jianzhi.chat("请执行知识提取流程")

    elif command == "diff":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        from tools.diff import doc_diff
        cli.print_info(doc_diff(jianzhi.workspace))

    elif command == "memory":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        name = _get_flag_value(parts, "-n")
        from tools.memory import read_memory
        cli.print_info(read_memory(jianzhi.workspace, name))

    elif command == "wiki":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/wiki -n <词条名>")
            return True, jianzhi
        from tools.wiki import _wiki_root, _parse_frontmatter
        wiki_root = _wiki_root(jianzhi.workspace)
        if not wiki_root.exists():
            cli.print_info("wiki 目录不存在")
            return True, jianzhi
        found = False
        for cat_dir in sorted(wiki_root.iterdir()):
            if not cat_dir.is_dir():
                continue
            fp = cat_dir / f"{name}.md"
            if fp.exists():
                cli.print_info(fp.read_text(encoding="utf-8"))
                found = True
                break
        if not found:
            cli.print_info(f"词条「{name}」不存在")

    elif command == "rule":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        name = _get_flag_value(parts, "-n")
        from tools.rules import rules_list, read_rule
        if name:
            cli.print_info(read_rule(jianzhi.workspace, name))
        else:
            cli.print_info(rules_list(jianzhi.workspace))

    elif command == "relation":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/relation -n <词条名>")
            return True, jianzhi
        from tools.relation import query_relations
        cli.print_info(query_relations(jianzhi.workspace, name))

    elif command == "link":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return True, jianzhi
        if not isinstance(jianzhi, KnowledgeAgent):
            cli.print_info("请先使用 /knowledge 进入 Knowledge 模式")
            return True, jianzhi
        from auto.relation_extractor import build_relations, save_relations
        relations = build_relations(jianzhi.workspace)
        if not relations:
            cli.print_info("未发现任何 wikilink 关系")
            return True, jianzhi
        save_relations(jianzhi.workspace, relations)
        total_links = sum(len(targets) for targets in relations.values())
        cli.print_info(f"关系图已构建：共 {len(relations)} 个词条，{total_links} 条关系")

    else:
        cli.print_info(f"未知指令：/{command}，输入 /help 查看可用指令")

    return True, jianzhi


def _get_flag_value(parts: list[str], flag: str) -> str | None:
    """从参数列表中提取标志值：/cmd -n value"""
    for i, p in enumerate(parts):
        if p == flag and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _switch_to_workspace(target: Path, cli: CLI, config: dict):
    """切换到指定工作区（仅配置+日志，不重建 Agent）"""
    cli.close_logger()
    cli.init_logger(target / "session")
    ws_cfg = config.setdefault("workspace", {})
    ws_cfg["last"] = target.name
    ws_cfg["dir"] = str(WORKSPACES_DIR)
    save_config(config)
    cli.print_info(f"已切换到工作区「{target.name}」")


def main():
    cli = CLI()

    # ---- 启动前准备：自动创建缺失的目录和配置文件 ----
    _ensure_dirs()

    # ---- 配置校验 ----
    try:
        config = load_config()
    except Exception as e:
        cli.print_info(f"错误：无法读取配置文件 - {e}")
        sys.exit(1)

    err = validate_config(config)
    if err:
        cli.print_info(f"错误：配置无效 - {err}")
        sys.exit(1)

    # ---- 从配置文件加载工作区目录 ----
    global WORKSPACES_DIR
    ws_dir_str = config.get("workspace", {}).get("dir", "../workingArea")
    WORKSPACES_DIR = Path(ws_dir_str).resolve()
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    # ---- API 连接测试 ----
    cli.print_info("正在测试 API 连接...")
    err = test_api_connection(config)
    if err:
        cli.print_info(f"错误：{err}")
        sys.exit(1)
    cli.print_info("API 连接正常。")

    # ---- 工作区初始化 ----
    workspace = resolve_workspace(config)
    if workspace is None:
        cli.print_info("尚无工作区，请先使用 /create -n <名称> 创建。")
        jianzhi = None
    else:
        cli.init_logger(workspace / "session")
        jianzhi = JianzhiAgent(config, workspace, SKILLS_DIR, cli)
        cli.print_info(f"进入工作区：{workspace.name}")
        cli.print_info("输入 /help 查看可用指令。")

    # REPL 主循环
    while True:
        text, is_cmd = cli.read_input()
        if text is None:  # exit
            break

        if is_cmd:
            cont, jianzhi = handle_command(text, cli, jianzhi, config)
            if not cont:
                break
            # 如果指令切换了工作区，重建 Agent
            name = config.get("workspace", {}).get("last", "")
            if name and (jianzhi is None or jianzhi.workspace.name != name):
                ws = WORKSPACES_DIR / name
                if ws.exists():
                    cli.close_logger()
                    cli.init_logger(ws / "session")
                    jianzhi = JianzhiAgent(config, ws, SKILLS_DIR, cli)
                    cli.print_info(f"进入工作区：{ws.name}")
        else:
            if jianzhi is None:
                cli.print_info("请先创建或切换到某个工作区。")
                continue
            needs_handoff = jianzhi.chat(text)
            if needs_handoff and not isinstance(jianzhi, KnowledgeAgent):
                cli.print_info("\n检测到知识提取需求，是否进入 Knowledge 专家模式？(Y/n)")
                confirm = input().strip().lower()
                if confirm in ("", "y", "yes"):
                    jianzhi.permission.confirm_handoff()
                    cli.print_info("进入 Knowledge 专家模式。")
                    jianzhi = KnowledgeAgent(config, jianzhi.workspace, SKILLS_DIR, cli, messages=jianzhi.messages)
                else:
                    cli.print_info("已取消。")

    # 退出
    cli.close_logger()
    cli.print_info("再见！")


if __name__ == "__main__":
    main()
