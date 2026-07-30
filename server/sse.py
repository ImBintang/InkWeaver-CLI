"""SSE push — EventBus consume → browser EventSource + session persistence."""

import asyncio
import json
import time
from collections import defaultdict
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from server.state import state

router = APIRouter()

_msg_buffers: dict[str, list[dict]] = defaultdict(list)


def _flush_buffer(session_id: str):
    buf = _msg_buffers.pop(session_id, [])
    if not buf or not state.session_manager:
        return
    for item in buf:
        state.session_manager.add_message(session_id, item)


@router.get("/api/events/stream")
async def event_stream():
    async def generate():
        yield _sse_event("frontend_ready", {})
        while True:
            event = await asyncio.to_thread(state.bus.get, timeout=0.1)
            if event:
                cur = state.current_session_id
                if cur and event.type.value in ("token", "output"):
                    payload = event.data or {}
                    msg = {
                        "id": payload.get("data", {}).get("id") or int(time.time() * 1000),
                        "role": "assistant",
                        "content": payload.get("data", {}).get("text", ""),
                        "timestamp": int(time.time()),
                    }
                    _msg_buffers[cur].append(msg)
                if cur and event.type.value == "confirm_request":
                    try:
                        state.session_manager.save_pending_confirm(cur, event.data)
                    except Exception:
                        pass
                if event.type.value == "task_done":
                    target = (event.data or {}).get("session_id") or cur
                    if target:
                        _flush_buffer(target)
                yield _sse_event(event.type.value, {
                    "data": event.data, "source": event.source, "timestamp": event.timestamp,
                })
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: inkweaver\ndata: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
