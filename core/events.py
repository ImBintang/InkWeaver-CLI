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

    def emit(self, event_type: EventType, data: dict, source: str = ""):
        """非阻塞发射事件（Agent 线程调用）"""
        self._queue.put_nowait(Event(type=event_type, data=data, source=source))

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
            # 超时自动放行，避免死锁
            with self._lock:
                self._pending_confirms.pop(confirm_id, None)
            return {"action": "approve", "_timeout": True}
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
