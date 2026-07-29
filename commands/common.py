"""子命令公共工具 — 配置加载、工作区解析"""

import sys
import yaml
from pathlib import Path

from core.output import OutputFormatter
from core.io import IOChannel


CONFIG_PATH = Path(__file__).parent.parent / ".env" / "config.yaml"
SKILLS_DIR = Path(__file__).parent.parent / "skills"


def load_config() -> dict:
    """加载配置文件，并解析多模型格式为兼容的 config["api"]"""
    if not CONFIG_PATH.exists():
        print(f"错误：配置文件不存在 - {CONFIG_PATH}", file=sys.stderr)
        raise SystemExit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    # 新格式兼容：从 models + assignments 解析出 config["api"]
    if "models" in config and "api" not in config:
        config["api"] = resolve_api_config(config)
    return config


def resolve_api_config(config: dict, role: str = "chat") -> dict:
    """从新格式（models + assignments）解析出 LLMClient 所需的 api config

    Args:
        config: 完整配置字典
        role: 角色名（chat/extract/write/review）

    Returns:
        {"url": ..., "key": ..., "model": ..., "output_max_tokens": ...}
    """
    assignments = config.get("assignments", {})
    model_id = assignments.get(role, "")
    models = config.get("models", [])

    model = next((m for m in models if m["id"] == model_id), None)
    if model is None and models:
        model = models[0]  # 回退到第一个模型
    if model is None:
        print("错误：配置文件中无可用模型", file=sys.stderr)
        raise SystemExit(1)

    return {
        "url": model.get("base_url", ""),
        "key": model.get("api_key", ""),
        "model": model.get("model", ""),
        "output_max_tokens": model.get("output_max_tokens", 128000),
    }


def save_config(config: dict):
    """保存配置（自动剔除 load_config 派生的 api 字段，避免覆盖 models 配置）"""
    persist = dict(config)
    if "models" in persist:
        persist.pop("api", None)  # api 是 load_config 的派生值，不落盘
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(persist, f, allow_unicode=True, default_flow_style=False)


def get_workspaces_dir(config: dict) -> Path:
    """获取工作区根目录"""
    ws_dir_str = config.get("workspace", {}).get("dir", "../workingArea")
    ws_dir = Path(ws_dir_str).resolve()
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir


def resolve_workspace(config: dict, workspace_name: str = "") -> Path | None:
    """解析目标工作区

    Args:
        config: 配置字典
        workspace_name: 指定工作区名（--workspace flag），空则用 config 中的 last

    Returns:
        工作区路径，不存在则返回 None
    """
    ws_dir = get_workspaces_dir(config)

    if workspace_name:
        target = ws_dir / workspace_name
        if target.exists():
            return target
        return None

    # 用 config 中的 last
    last = config.get("workspace", {}).get("last", "")
    if last:
        target = ws_dir / last
        if target.exists():
            return target

    # 回退到第一个
    workspaces = sorted([d for d in ws_dir.iterdir() if d.is_dir()])
    if workspaces:
        return workspaces[0]
    return None


def make_io(json_mode: bool = False, auto_yes: bool = False,
            workspace: Path | None = None, mode: str = "chat", cmd: str = "") -> IOChannel:
    """创建 IOChannel 实例"""
    fmt = OutputFormatter(json_mode=json_mode)
    io = IOChannel(formatter=fmt, auto_yes=auto_yes)
    if workspace:
        io.init_logger(workspace / "session", mode=mode, cmd=cmd)
    return io


def require_workspace(config: dict, workspace_name: str, json_mode: bool = False) -> Path:
    """获取工作区，失败则退出"""
    ws = resolve_workspace(config, workspace_name)
    if ws is None:
        fmt = OutputFormatter(json_mode=json_mode)
        fmt.error(f"工作区不存在：{workspace_name or '(默认)'}")
        raise SystemExit(1)
    return ws
