"""TaskManager — Agent 工作流的异步任务化（MCP 长任务模型）

设计：
- 鉴知问答 / 知识提取 / 妙笔写作均为分钟级长任务，不能同步阻塞 MCP 工具调用
- 统一模式：start_xxx() 返回 task_id → task_status/task_wait 查询
  → task_confirm 响应挂起确认 → task_result 获取成果 → task_cancel 终止
- 每个任务有独立 EventBus；消费线程把事件翻译成进度轨迹，
  CONFIRM_REQUEST 按策略处理：auto_approve=True 自动放行，否则挂起等 task_confirm
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from core.events import EventBus, EventType

PROGRESS_MAX = 200  # 进度轨迹环形上限
# v7.0.1: 终态任务保留上限——超出后惰性清理最老的终态任务（防长会话内存增长）
TASK_RETENTION = 200


@dataclass
class TaskRecord:
    """单个异步任务的状态容器"""
    id: str
    kind: str                       # ask | extract | muse_write
    status: str = "running"         # running | awaiting_confirmation | done | error | cancelled
    created_at: float = field(default_factory=time.time)
    params: dict = field(default_factory=dict)
    workspace: str = ""
    progress: list = field(default_factory=list)   # [{t, type, text}]
    step: int = 0                   # 妙笔步骤（1-4）
    pending_confirm: dict | None = None            # {confirm_id, confirm_type, payload}
    result: dict | None = None
    error: str = ""
    # 运行时引用
    bus: EventBus = field(default_factory=EventBus)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    consumer_thread: threading.Thread | None = None
    _consumer_running: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_progress(self, etype: str, text: str):
        with self._lock:
            self.progress.append({"t": round(time.time(), 1), "type": etype, "text": text[:300]})
            if len(self.progress) > PROGRESS_MAX:
                del self.progress[:len(self.progress) - PROGRESS_MAX]

    def snapshot(self, with_progress: bool = True, progress_tail: int = 15) -> dict:
        """任务状态快照（task_status/task_list 返回）"""
        with self._lock:
            snap = {
                "task_id": self.id,
                "kind": self.kind,
                "status": self.status,
                "workspace": self.workspace,
                "created_at": self.created_at,
                "elapsed": round(time.time() - self.created_at, 1),
                "step": self.step,
                "params": self.params,
            }
            if self.pending_confirm:
                snap["pending_confirm"] = {
                    "confirm_id": self.pending_confirm["confirm_id"],
                    "confirm_type": self.pending_confirm["confirm_type"],
                    "payload": self.pending_confirm["payload"],
                }
            if self.error:
                snap["error"] = self.error
            if self.status == "done" and self.result:
                snap["has_result"] = True
            if with_progress:
                snap["progress_tail"] = self.progress[-progress_tail:]
            return snap


class TaskManager:
    """异步任务注册表 — 进程级单例（由 server.py 创建）"""

    def __init__(self):
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    # ---- 任务生命周期 ----

    def create(self, kind: str, params: dict, workspace: str) -> TaskRecord:
        self._prune()  # v7.0.1: 创建时清理历史终态任务
        task = TaskRecord(id=uuid.uuid4().hex[:12], kind=kind, params=params, workspace=workspace)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [t.snapshot(with_progress=False) for t in tasks]

    def _prune(self):
        """v7.0.1: 惰性清理最老的终态任务（done/error/cancelled），保留最近 TASK_RETENTION 个"""
        with self._lock:
            terminal = [t for t in self._tasks.values()
                        if t.status in ("done", "error", "cancelled")]
            if len(terminal) <= TASK_RETENTION:
                return
            # 终态任务按创建时间升序，删除超出上限的最老部分
            terminal.sort(key=lambda t: t.created_at)
            for t in terminal[:len(terminal) - TASK_RETENTION]:
                self._tasks.pop(t.id, None)

    def cancel(self, task_id: str) -> dict:
        task = self.get(task_id)
        if task is None:
            return {"status": "error", "message": f"任务不存在：{task_id}"}
        with task._lock:
            if task.status in ("done", "error", "cancelled"):
                return {"status": "error", "message": f"任务已结束（{task.status}），无需取消"}
            task.stop_event.set()
            # 立即唤醒阻塞在确认上的工作流线程（走终止路径）
            task.bus.cancel_confirms({"action": "reject", "_stopped": True})
        task.add_progress("cancel", "收到取消请求")
        return {"status": "success", "message": f"已请求取消任务 {task_id}（在下一个检查点生效）"}

    def confirm(self, task_id: str, action: str, reason: str = "",
                rejected_indices: list[int] | None = None) -> dict:
        """响应挂起确认 — action: approve | reject | approve_all"""
        task = self.get(task_id)
        if task is None:
            return {"status": "error", "message": f"任务不存在：{task_id}"}
        if not task.pending_confirm:
            return {"status": "error",
                    "message": f"任务 {task_id} 当前无挂起确认（status={task.status}）"}
        pc = task.pending_confirm
        ctype = pc["confirm_type"]

        if action == "approve":
            response = {"action": "approve"}
        elif action == "approve_all":
            response = {"action": "approve_all"} if ctype == "forced_debt" else {"action": "approve"}
        elif action == "reject":
            if not reason.strip():
                return {"status": "error", "message": "驳回必须提供 reason（项目规范：驳回原因必填）"}
            response = {"action": "reject", "reason": reason.strip()}
            if ctype == "forced_debt" and rejected_indices:
                response = {"rejected_indices": rejected_indices, "reason": reason.strip()}
        else:
            return {"status": "error", "message": f"未知 action：{action}（可选 approve/reject/approve_all）"}

        # v7.0.1: 挂起确认的读取/清除加锁——并发 confirm 时防止重复 resolve
        with task._lock:
            if task.pending_confirm is not pc:
                return {"status": "error",
                        "message": f"任务 {task_id} 的挂起确认已被其他请求处理"}
            task.bus.resolve_confirm(pc["confirm_id"], response)
            task.pending_confirm = None
            task.status = "running"
        task.add_progress("confirm", f"确认已响应：{action}")
        return {"status": "success", "message": f"已响应确认（{action}），任务继续执行"}

    # ---- 事件消费线程 ----

    def start_consumer(self, task: TaskRecord, auto_approve: bool):
        """启动任务专属事件消费线程"""
        def _consume():
            while task._consumer_running:
                event = task.bus.get(timeout=0.1)
                if event is None:
                    continue
                try:
                    self._handle_event(task, event, auto_approve)
                except Exception as e:
                    # 兜底：确认事件处理异常时按 fail-safe 拒绝（P1-18 原则），
                    # 避免 Agent 线程永久阻塞；拒绝后由 Agent 降级处理
                    if event.type == EventType.CONFIRM_REQUEST:
                        cid = event.data.get("confirm_id", "")
                        if cid:
                            task.bus.resolve_confirm(cid, {"action": "reject", "_error": True})
                    task.add_progress("warn", f"事件处理异常：{e}")
        task.consumer_thread = threading.Thread(target=_consume, daemon=True)
        task.consumer_thread.start()

    def _handle_event(self, task: TaskRecord, event, auto_approve: bool):
        etype = event.type
        data = event.data

        if etype == EventType.CONFIRM_REQUEST:
            # v7.0.1: 任务已取消后不再挂起新确认——直接拒绝，防止取消后残留挂起
            if task.stop_event.is_set():
                task.bus.resolve_confirm(data["confirm_id"], {"action": "reject", "_stopped": True})
                return
            if auto_approve:
                # 按确认类型给出安全的默认响应
                ctype = data["confirm_type"]
                default = {"action": "approve_all" if ctype == "forced_debt" else "approve"}
                task.bus.resolve_confirm(data["confirm_id"], default)
                task.add_progress("confirm", f"自动确认（auto_approve）：{ctype}")
            else:
                # 挂起等待外部 task_confirm（v7.0.1: 写入加锁，与 confirm() 并发安全）
                with task._lock:
                    task.pending_confirm = {
                        "confirm_id": data["confirm_id"],
                        "confirm_type": data["confirm_type"],
                        "payload": data.get("payload", {}),
                    }
                    task.status = "awaiting_confirmation"
                task.add_progress("confirm_request",
                                  f"等待确认：{data['confirm_type']}")
        elif etype == EventType.OUTPUT:
            task.add_progress("output", data.get("text", ""))
            task._last_output = data.get("text", "")
        elif etype == EventType.STEP_CHANGE:
            task.step = data.get("step", task.step)
            task.add_progress("step", f"步骤{data.get('step')}: {data.get('detail', '')}")
        elif etype == EventType.TOOL_CALL:
            task.add_progress("tool", f"{data.get('name')} {data.get('brief', '')}".strip())
        elif etype == EventType.INFO:
            task.add_progress("info", data.get("text", ""))
        elif etype == EventType.ERROR:
            task.add_progress("error", data.get("text", ""))
        elif etype == EventType.PLAN_READY:
            task.add_progress("plan", "提取计划已生成")
        # TOKEN/REASONING/THINKING 等高频事件不入进度轨迹（降噪）

    def stop_consumer(self, task: TaskRecord):
        task._consumer_running = False
