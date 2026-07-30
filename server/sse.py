"""SSE 推送 — EventBus 消费 → 浏览器 EventSource"""

import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from server.state import state

router = APIRouter()


@router.get("/api/events/stream")
async def event_stream():
    """全局 SSE 流 — 唯一的事件接收通道"""
    async def generate():
        # 首条事件：通知前端 SSE 已就绪
        yield _sse_event("frontend_ready", {})

        while True:
            # 在 asyncio 上下文中安全地调用阻塞的 bus.get()
            event = await asyncio.to_thread(state.bus.get, timeout=0.1)
            if event:
                yield _sse_event(event.type.value, {
                    "data": event.data,
                    "source": event.source,
                    "timestamp": event.timestamp,
                })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_event(event_type: str, payload: dict) -> str:
    """格式化 SSE 事件"""
    return f"event: inkweaver\ndata: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
