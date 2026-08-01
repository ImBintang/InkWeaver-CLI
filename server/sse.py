"""SSE push — EventBus consume → browser EventSource + session persistence.

P1-34/35/36：唯一后台消费者线程消费 state.bus（无论前端是否连接），
负责消息缓冲落盘与确认持久化；SSE 客户端通过订阅队列接收广播事件，
避免多客户端竞争消费总线导致事件被抢走/丢失，且前端断开时消息不再永久丢失。
"""

import json
import queue
import threading
import time
from collections import defaultdict
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from server.state import state

router = APIRouter()

_msg_buffers: dict[str, list[dict]] = defaultdict(list)
_buf_lock = threading.Lock()
_subscribers: set[queue.Queue] = set()
_subs_lock = threading.Lock()
_stop_consumer = threading.Event()


def _broadcast(evt: dict):
    """将事件广播给所有 SSE 订阅者（拷贝订阅者列表，避免迭代中变更）"""
    with _subs_lock:
        subs = list(_subscribers)
    for sub in subs:
        try:
            sub.put_nowait(evt)
        except queue.Full:
            pass  # 订阅者队列满 → 丢弃（消费端断开时自动注销）


def _flush_buffer(session_id: str):
    """将缓冲的消息落盘；失败不静默，转 error 事件广播供前端感知"""
    with _buf_lock:
        buf = _msg_buffers.pop(session_id, [])
    if not buf or not state.session_manager:
        return
    try:
        for item in buf:
            state.session_manager.add_message(session_id, item)
    except Exception as e:
        _broadcast({
            "type": "error",
            "data": {"text": f"会话「{session_id}」消息落盘失败：{e}"},
            "source": "system",
            "timestamp": time.time(),
        })


def _consumer_loop():
    """后台消费者：唯一从 state.bus 取事件的线程

    职责：
    - output（完整最终回复）→ 追加到活动会话的消息缓冲（task_done 时落盘）；
      TOKEN 是流式碎片仅供前端实时展示，绝不落盘（否则一条回复会被拆成几十条碎片消息）
    - confirm_request → 持久化待确认状态
    - task_done → 刷新缓冲（无论前端是否在线）
    - 全部事件 → 广播给所有 SSE 订阅者
    """
    active_session: str | None = None  # 当前运行任务绑定的会话（防止任务中切换会话导致串写）
    while not _stop_consumer.is_set():
        event = state.bus.get(timeout=0.2)
        if event is None:
            continue

        if event.type.value == "task_start":
            active_session = (event.data or {}).get("session_id") or state.current_session_id

        target = active_session or state.current_session_id

        # 只缓冲鉴知的 output 到会话文件：妙笔（source="muse"）的 OUTPUT 是写作产物，
        # 不属于聊天会话，若一并缓冲会在妙笔任务期间串写进当前鉴知会话
        if target and event.type.value == "output" and event.source == "jianzhi":
            payload = event.data or {}
            text = (payload.get("text") or "").strip()
            if text:
                with _buf_lock:
                    buf = _msg_buffers[target]
                    # 去重：agent_output 工具与 Jianzhi.chat 尾部可能对同一回复发射两次 OUTPUT，
                    # 内容与上一条已缓冲 assistant 消息一致时跳过，避免会话文件出现重复发言
                    if not (buf and buf[-1].get("role") == "assistant"
                            and (buf[-1].get("content") or "").strip() == text):
                        buf.append({
                            "id": payload.get("id") or int(time.time() * 1000),
                            "role": "assistant",
                            "content": text,
                            "timestamp": int(time.time()),
                        })

        if target and event.type.value == "confirm_request":
            try:
                state.session_manager.save_pending_confirm(target, event.data)
            except Exception as e:
                _broadcast({
                    "type": "error",
                    "data": {"text": f"确认状态保存失败：{e}"},
                    "source": "system",
                    "timestamp": time.time(),
                })

        if event.type.value == "task_done":
            done_target = (event.data or {}).get("session_id") or active_session or state.current_session_id
            if done_target:
                _flush_buffer(done_target)
            active_session = None

        _broadcast({
            "type": event.type.value,
            "data": event.data,
            "source": event.source,
            "timestamp": event.timestamp,
        })


def _ensure_consumer():
    """确保后台消费者已启动（幂等；单例守护线程）"""
    if not getattr(state, "_sse_consumer_started", False):
        state._sse_consumer_started = True
        state._sse_consumer_thread = threading.Thread(
            target=_consumer_loop, daemon=True, name="sse-consumer"
        )
        state._sse_consumer_thread.start()


@router.get("/api/events/stream")
async def event_stream():
    _ensure_consumer()
    sub: queue.Queue = queue.Queue(maxsize=2000)
    with _subs_lock:
        _subscribers.add(sub)

    async def generate():
        try:
            yield _sse_event("frontend_ready", {})
            while True:
                try:
                    evt = sub.get_nowait()
                except queue.Empty:
                    await asyncio_sleep(0.05)
                    continue
                payload = {k: v for k, v in evt.items() if k != "type"}
                yield _sse_event(evt["type"], payload)
        finally:
            # 客户端断开：注销订阅者，避免队列堆积
            with _subs_lock:
                _subscribers.discard(sub)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


async def asyncio_sleep(seconds: float):
    """事件循环内休眠（避免每次轮询都经 asyncio.to_thread 起线程）"""
    import asyncio
    await asyncio.sleep(seconds)


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: inkweaver\ndata: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
