"""MCP 上下文 — 配置加载与工作区解析（复用 commands.common，不重复实现）"""

from pathlib import Path


class MCPContextError(Exception):
    """上下文错误 — 工作区不存在/配置缺失等（工具层捕获后转为 error 结果）"""


class MCPContext:
    """MCP Server 进程级上下文

    - 启动参数 --workspace 可绑定默认工作区（进程级，stdio 下每连接独立进程）
    - 所有工具可传 workspace 参数覆盖；缺省回退到绑定值/配置 last
    """

    def __init__(self, workspace_name: str = ""):
        self.bound_workspace = workspace_name
        self._config_cache: dict | None = None

    # ---- 配置 ----

    def config(self) -> dict:
        """加载配置（每次重读，保证 settings 变更后即时生效）"""
        from commands.common import load_config
        try:
            return load_config()
        except SystemExit as e:
            raise MCPContextError("配置加载失败：请检查 InkWeaver-CLI/.env/config.yaml") from e

    def workspaces_dir(self) -> Path:
        from commands.common import get_workspaces_dir
        return get_workspaces_dir(self.config())

    # ---- 工作区解析 ----

    def resolve_ws(self, workspace: str = "") -> Path:
        """解析目标工作区路径，失败抛 MCPContextError

        优先级：工具参数 workspace > 启动绑定 bound_workspace > config.last/第一个
        """
        from commands.common import resolve_workspace

        config = self.config()
        name = workspace or self.bound_workspace
        ws = resolve_workspace(config, name)
        if ws is None:
            hint = f"「{name}」" if name else "（默认）"
            raise MCPContextError(
                f"工作区不存在：{hint}。请先调用 list_workspaces 查看，"
                f"或 create_workspace 创建。"
            )
        return ws

    def current_workspace_name(self) -> str:
        """当前生效的工作区名（供 server_info 展示）"""
        try:
            return self.resolve_ws().name
        except MCPContextError:
            return ""
