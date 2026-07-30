"""服务器全局状态 — 单用户模式"""

import threading
from pathlib import Path

from core.events import EventBus


class ServerState:
    """服务器全局状态（单用户桌面应用）"""

    def __init__(self):
        self.bus = EventBus()
        self.current_book: str | None = None
        self.workspace_path: Path | None = None
        # 工作区根目录默认指向 ../workingArea（相对于 CLI 目录）
        self.workspaces_dir: Path = (Path(__file__).parent.parent / ".." / "workingArea").resolve()
        if not self.workspaces_dir.exists():
            self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.agent_thread: threading.Thread | None = None
        self.agent_lock = threading.Lock()
        self.jianzhi = None  # 持久化鉴知 Agent 实例（多轮上下文）


state = ServerState()
