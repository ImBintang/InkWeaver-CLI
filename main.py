"""InkWeaver-CLI 入口程序"""

import sys
import yaml
from pathlib import Path

from cli import CLI
from Jianzhi import JianzhiAgent
from tools import workspace as workspace_tools


CONFIG_PATH = Path("../config.yaml")
WORKSPACES_DIR = Path("../workingArea")
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
}


def _ensure_dirs():
    """自动创建缺失的目录和配置文件"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)
        print(f"[配置] 已自动创建配置文件：{CONFIG_PATH}")

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)


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

  章节管理：
    /import -p <path>     导入小说文件（按章节拆分）
    /show -n <num>        展示指定章节内容

  Agent：
    /clear                清空对话上下文
    /context              查看上下文占用与组成
    /compact              主动压缩上下文

  系统：
    /help                 显示本帮助
    /exit                 退出程序（或输入 exit）"""


def handle_command(cmd: str, cli: CLI, jianzhi: JianzhiAgent | None, config: dict):
    """处理 CLI 指令"""
    parts = cmd.strip().split()
    if not parts:
        return

    command = parts[0].lower()
    cli.log_cli(cmd)

    # ---- 工作区相关 ----
    if command == "list":
        cli.print_info(workspace_tools.list_workspaces(WORKSPACES_DIR))

    elif command == "switch":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/switch -n <工作区名>")
            return
        target = workspace_tools.switch_workspace(WORKSPACES_DIR, name)
        if target is None:
            cli.print_info(f"错误：工作区「{name}」不存在或名称非法")
            return
        _switch_to_workspace(target, cli, config)

    elif command == "create":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/create -n <工作区名>")
            return
        target = workspace_tools.create_workspace(WORKSPACES_DIR, name)
        if target is None:
            cli.print_info("错误：名称非法或工作区已存在")
            return
        cli.print_info(f"已创建工作区「{name}」")
        _switch_to_workspace(target, cli, config)

    elif command == "update":
        name = _get_flag_value(parts, "-n")
        if not name:
            cli.print_info("用法：/update -n <新名称>")
            return
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
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
            return
        cli.print_info(f"确认删除工作区「{jianzhi.workspace.name}」？(y/N)")
        # 先关闭日志释放文件锁
        cli.close_logger()
        confirm = input().strip().lower()
        if confirm != "y":
            # 取消删除，重新打开日志
            cli.init_logger(jianzhi.workspace / "session")
            cli.print_info("已取消。")
            return
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

    # ---- 章节相关 ----
    elif command == "import":
        path = _get_flag_value(parts, "-p")
        if not path:
            cli.print_info("用法：/import -p <文件路径>")
            return
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
        # 检查是否已有章节
        doc_dir = jianzhi.workspace / "document"
        existing = sorted(doc_dir.glob("c*.md")) if doc_dir.exists() else []
        if existing:
            cli.print_info("工作区已有章节，确认删除后重新导入？(y/N)")
            confirm = input().strip().lower()
            if confirm != "y":
                cli.print_info("已取消导入。")
                return
            for f in existing:
                f.unlink()
        result = workspace_tools.import_novel(jianzhi.workspace, path)
        cli.print_info(result)

    elif command == "show":
        num_str = _get_flag_value(parts, "-n") or (parts[1] if len(parts) > 1 else "")
        try:
            num = int(num_str)
        except (ValueError, IndexError):
            cli.print_info("用法：/show -n <章节号>")
            return
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
        from tools.chapter import show_chapter
        cli.print_info(show_chapter(jianzhi.workspace, num))

    # ---- Agent 相关 ----
    elif command == "clear":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
        jianzhi.clear_context()

    elif command == "context":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
        cli.print_info(jianzhi.context_report())

    elif command == "compact":
        if jianzhi is None:
            cli.print_info("请先进入一个工作区")
            return
        jianzhi.compact_history()

    elif command == "help":
        cli.print_info(_HELP_TEXT)

    else:
        cli.print_info(f"未知指令：/{command}，输入 /help 查看可用指令")


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
    config.setdefault("workspace", {})["last"] = target.name
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
            handle_command(text, cli, jianzhi, config)
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
            jianzhi.chat(text)

    # 退出
    cli.close_logger()
    cli.print_info("再见！")


if __name__ == "__main__":
    main()
