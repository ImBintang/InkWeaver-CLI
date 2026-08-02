"""服务器全局状态 — 单用户模式"""

import threading
from pathlib import Path

from core.events import EventBus
from tools.session_manager import SessionManager


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
        # v6.5.3: 妙笔任务终止信号（/api/muse/stop 设置，步骤边界生效）
        self.muse_stop_event: threading.Event | None = None
        self.jianzhi = None  # 持久化鉴知 Agent 实例（多轮上下文）
        # ─── Session management (v6.2) ───
        self.current_session_id: str | None = None
        self.session_manager: SessionManager | None = None
        # P1-34：SSE 后台消费者（单例守护线程，幂等启动）
        self._sse_consumer_started: bool = False
        self._sse_consumer_thread: threading.Thread | None = None


    def bind_session_manager(self) -> SessionManager:
        if not self.workspace_path:
            raise RuntimeError("workspace_path not set")
        self.session_manager = SessionManager(self.workspace_path, self.workspace_path / "chat_sessions")
        return self.session_manager

    def load_or_create_session(self) -> dict:
        mgr = self.session_manager or self.bind_session_manager()
        idx = mgr.load_index()
        cur = idx.get("current_session_id")
        if cur and not any(s["id"] == cur for s in idx["sessions"]):
            cur = None
        if cur is None:
            active = [s for s in idx["sessions"] if not s.get("archived")]
            if active:
                cur = active[0]["id"]
                mgr.set_current_in_index(cur)
            else:
                cur = mgr.create_session("新会话")
        return mgr.activate(cur, clear_pending_confirm=True)


state = ServerState()
