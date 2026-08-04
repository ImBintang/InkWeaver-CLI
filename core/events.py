"""事件总线 — Agent 与 I/O 层解耦的核心抽象

Agent 线程通过 emit() 发射事件，消费线程（CLI/GUI）通过 get() 轮询消费。
确认类事件通过 threading.Event 阻塞 Agent 线程，外部 resolve 后唤醒。
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class EventType(str, Enum):
    """事件类型枚举"""

    # 流式输出
    TOKEN = "token"                 # LLM 流式 token（逐片段）
    OUTPUT = "output"              # 完整输出段落
    THINKING = "thinking"          # 思考状态开始
    THINKING_DONE = "thinking_done"  # 思考完成（含耗时）
    REASONING = "reasoning"        # 完整思考过程文本

    # 工具链
    TOOL_CALL = "tool_call"        # 工具调用开始
    TOOL_RESULT = "tool_result"    # 工具调用结果

    # 工作流
    STEP_CHANGE = "step_change"    # 妙笔步骤切换
    PLAN_READY = "plan_ready"      # 提取计划生成
    MUSE_EDITS = "muse_edits"      # v6.5.8 妙笔修改轮编辑标注（前端高亮被 edit 的字段）

    # 确认请求（需要用户响应）
    CONFIRM_REQUEST = "confirm_request"
    CONFIRM_RESOLVED = "confirm_resolved"

    # 统计
    TOKEN_STATS = "token_stats"    # token 用量更新

    # 生命周期
    TASK_START = "task_start"
    TASK_DONE = "task_done"
    ERROR = "error"
    INFO = "info"                  # 信息提示


@dataclass
class Event:
    """事件数据结构"""
    type: EventType
    data: dict                     # 确认类事件包含 confirm_id、confirm_type、payload
    timestamp: float = field(default_factory=time.time)
    source: str = ""               # "jianzhi" | "muse" | "system"


class StreamBatcher:
    """流式事件批量发射器——把高频逐 chunk 事件聚合成低频批量事件

    v6.5.7: SSE 订阅者队列容量有限（2000），妙笔流程思考+正文逐 token 发射时
    事件速率可达每秒数十个，长任务（多轮写作/审阅）累计上万事件，慢消费者
    （SSE 每 50ms 取一个）队列会被填满 → 后续事件（含确认请求）被静默丢弃，
    前端表现为“弹窗不出现 / 输出不更新”（卡死）。
    批量发射把 N 个 chunk 合并为 1 个事件，事件总量降为 1/N。
    用法：with StreamBatcher(bus, EventType.TOKEN, 16) as b: b.add(text, kind)
    """

    def __init__(self, bus, event_type, batch_size=32, source="muse"):
        self.bus = bus
        self.event_type = event_type
        self.batch_size = max(1, batch_size)
        self.source = source
        self._buf: list[str] = []
        self._kind: str | None = None

    def add(self, text: str, kind: str | None = None):
        """累积一个 chunk；达到批量阈值即发射一次"""
        if not text:
            return
        self._buf.append(text)
        if kind:
            self._kind = kind
        if len(self._buf) >= self.batch_size:
            self.flush()

    def flush(self):
        """发射当前缓冲（流结束/批量满时调用）"""
        if not self._buf:
            return
        text = "".join(self._buf)
        kind = self._kind
        self._buf = []
        self._kind = None
        try:
            data = {"text": text}
            if kind:
                data["kind"] = kind
            self.bus.emit(self.event_type, data, source=self.source)
        except Exception:
            pass  # 事件上报失败不阻断主流程

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.flush()


class EventBus:
    """线程安全的事件总线 — Agent 线程 emit，消费线程 get

    设计要点：
    - emit() 非阻塞，Agent 线程调用无感知
    - get() 带超时轮询，消费者不会死等
    - request_confirm() 阻塞 Agent 线程直到 resolve_confirm() 被调用
    - 所有共享状态通过 _lock 保护
    """

    def __init__(self):
        self._queue: queue.Queue[Event] = queue.Queue()
        self._pending_confirms: dict[str, threading.Event] = {}
        self._confirm_results: dict[str, dict] = {}
        self._lock = threading.Lock()

    # v7.0.1: 无界队列是设计决策（Agent 线程不可被阻塞）；当积压超过阈值时
    # 丢弃普通流事件、只保留关键事件，防止消费者崩溃后内存无限增长
    _MAX_BACKLOG = 50000
    _CRITICAL_TYPES = (EventType.CONFIRM_REQUEST, EventType.TASK_DONE, EventType.ERROR)

    def emit(self, event_type: EventType, data: dict, source: str = ""):
        """非阻塞发射事件（Agent 线程调用）"""
        if event_type not in self._CRITICAL_TYPES and self._queue.qsize() > self._MAX_BACKLOG:
            return  # 消费者积压过深：丢弃普通事件，保住关键事件通路
        try:
            self._queue.put_nowait(Event(type=event_type, data=data, source=source))
        except queue.Full:
            pass  # 无界队列理论上不会 Full；防御性兜底，不阻塞主流程

    def request_confirm(self, confirm_type: str, data: dict, source: str = "",
                         timeout: float = 300.0) -> dict:
        """发射确认请求并阻塞 Agent 线程，等待外部 resolve

        Args:
            confirm_type: 确认类型，如 "plan"、"forced_debt"、"muse_confirm"
            data: 确认载荷（展示给用户的内容）
            source: 事件来源
            timeout: 超时秒数，默认 300s（避免消费者崩溃后 Agent 永久阻塞）

        Returns:
            用户响应 dict，如 {"action": "approve"}；超时返回 {"action": "approve"}（自动放行）
        """
        confirm_id = str(uuid4())
        event = threading.Event()
        with self._lock:
            self._pending_confirms[confirm_id] = event
        self.emit(EventType.CONFIRM_REQUEST, {
            "confirm_id": confirm_id,
            "confirm_type": confirm_type,
            "payload": data,
        }, source=source)
        # 阻塞 Agent 线程，直到 resolve_confirm 被调用或超时
        resolved = event.wait(timeout=timeout)
        if not resolved:
            # P1-18：超时不自动放行，默认拒绝（fail-safe）——用户不在场时
            # 高重要性实体/计划不能被默认批准；拒绝后由 Agent 自行降级处理
            print(f"[WARN] 确认请求超时（{timeout}s），默认拒绝 (confirm_type={confirm_type})")
            with self._lock:
                self._pending_confirms.pop(confirm_id, None)
            return {"action": "reject", "_timeout": True}
        with self._lock:
            return self._confirm_results.pop(confirm_id, {})

    def resolve_confirm(self, confirm_id: str, response: dict):
        """外部（CLI/GUI）调用，唤醒阻塞的 Agent 线程

        Args:
            confirm_id: 确认事件 ID（从 CONFIRM_REQUEST 事件的 data 中获取）
            response: 用户响应
        """
        with self._lock:
            evt = self._pending_confirms.pop(confirm_id, None)
            if evt:
                self._confirm_results[confirm_id] = response
                evt.set()

    def cancel_confirms(self, response: dict):
        """取消所有挂起的确认请求（终止任务时调用，立即唤醒阻塞线程）

        v6.5.9: /api/muse/stop 设置 stop_event 后调用此方法，
        使阻塞在 request_confirm 上的工作流线程立即醒来并走终止路径。
        """
        with self._lock:
            ids = list(self._pending_confirms.keys())
        for cid in ids:
            self.resolve_confirm(cid, response)

    def get(self, timeout: float = 0.1) -> Event | None:
        """消费者获取事件（带超时，避免死等）

        Args:
            timeout: 超时秒数，默认 100ms

        Returns:
            Event 或 None（超时）
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self):
        """清空队列中未消费的事件（用于重置状态）"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
