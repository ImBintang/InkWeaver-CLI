"""鉴知 HTTP API — 对话发送 + 确认响应 + 上下文管理"""

import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.state import state
from server.router.books import _rebuild_jianzhi
from core.events import EventType

router = APIRouter()


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class ChatSendReq(BaseModel):
    text: str


class ConfirmResReq(BaseModel):
    """确认响应请求体（透传给 EventBus.resolve_confirm）"""
    action: str = "approve"
    __pydantic_config__ = {"extra": "allow"}


# ─── 对话 ──────────────────────────────────────────────────────────

@router.post("/api/chat/messages")
async def chat_send(req: ChatSendReq) -> dict:
    """发送消息，启动鉴知 Agent 线程"""
    if not state.workspace_path:
        raise HTTPException(400, detail="请先打开一个工作区")
    with state.agent_lock:
        if state.agent_thread and state.agent_thread.is_alive():
            raise HTTPException(409, detail="Agent 正在运行中，请等待完成")
        state.agent_thread = threading.Thread(
            target=_run_jianzhi, args=(req.text,), daemon=True
        )
        state.agent_thread.start()
    return {"ok": True}


def _run_jianzhi(text: str):
    """在独立线程中运行鉴知对话（复用持久化实例保持多轮上下文）"""
    try:
        if state.jianzhi is None:
            _rebuild_jianzhi()
        if state.jianzhi is None:
            state.bus.emit(EventType.ERROR, {"text": "无法初始化鉴知 Agent"}, source="jianzhi")
            return
        state.jianzhi.chat(text)
    except Exception as e:
        state.bus.emit(EventType.ERROR, {"text": str(e)}, source="jianzhi")
    finally:
        state.bus.emit(EventType.TASK_DONE, {}, source="jianzhi")


# ─── 确认响应 ──────────────────────────────────────────────────────

@router.post("/api/chat/confirm/{confirm_id}")
async def chat_resolve_confirm(confirm_id: str, req: ConfirmResReq) -> dict:
    """响应确认请求"""
    try:
        state.bus.resolve_confirm(confirm_id, req.model_dump())
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ─── 上下文管理 ────────────────────────────────────────────────────

@router.post("/api/chat/compact")
async def chat_compact() -> dict:
    """压缩鉴知对话上下文"""
    try:
        if state.jianzhi is not None:
            state.jianzhi.clear_context()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.get("/api/chat/context")
async def chat_context_report() -> dict:
    """获取当前上下文占用情况"""
    try:
        if state.jianzhi is not None:
            agent = state.jianzhi
            msg_count = len(agent.messages) if hasattr(agent, "messages") else 0
            token_accum = getattr(agent, "_token_accum", {})
            return {
                "ok": True,
                "message_count": msg_count,
                "input_tokens": token_accum.get("input", 0),
                "output_tokens": token_accum.get("output", 0),
                "total_tokens": token_accum.get("total", 0),
            }
        return {
            "ok": True,
            "message_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    except Exception:
        return {"ok": True, "tokens": 0, "percent": 0}


@router.post("/api/chat/clear")
async def chat_clear() -> dict:
    """清空对话历史（context + jianzhi 重建）"""
    try:
        if state.jianzhi is not None:
            state.jianzhi.clear_context()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
