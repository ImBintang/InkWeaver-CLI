"""鉴知 HTTP API — 对话发送 + 确认响应 + 上下文管理"""

import time
import threading
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from server.state import state
from server.router.books import _rebuild_jianzhi
from core.events import EventType

router = APIRouter()


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class ChatSendReq(BaseModel):
    text: str


class ConfirmResReq(BaseModel):
    """确认响应请求体（透传给 EventBus.resolve_confirm）

    v6.5.9: 改用 pydantic v2 的 model_config 保留额外字段——
    此前 __pydantic_config__ 在 v2 无效，reason/rejected_indices 被静默丢弃。
    """

    model_config = ConfigDict(extra="allow")
    action: str = "approve"


# ─── 对话 ──────────────────────────────────────────────────────────

@router.post("/api/chat/messages")
async def chat_send(req: ChatSendReq, session_id: str | None = Query(default=None)) -> dict:
    """发送消息，启动鉴知 Agent 线程"""
    if not state.workspace_path:
        raise HTTPException(400, detail="请先打开一个工作区")
    target = session_id or state.current_session_id
    if state.session_manager and target:
        try:
            sess = state.session_manager.get_session(target)
            if sess["message_count"] >= sess["cap"]:
                raise HTTPException(403, detail={"code": "session_full", "session_id": target})
        except HTTPException:
            raise
        except Exception as e:
            # 不静默：会话读取失败（文件损坏等）上报 ERROR 事件（前端 SSE 可见），
            # 但允许继续发送——消息写入路径会再次校验并给出明确错误
            state.bus.emit(EventType.ERROR,
                           {"text": f"会话检查失败（{target}）：{e}"},
                           source="server")
    with state.agent_lock:
        if state.agent_thread and state.agent_thread.is_alive():
            raise HTTPException(409, detail="Agent 正在运行中，请等待完成")
        state.agent_thread = threading.Thread(
            target=_run_jianzhi, args=(req.text, target), daemon=True
        )
        state.agent_thread.start()
        state.current_session_id = target
    return {"ok": True, "session_id": target}


def _run_jianzhi(text: str, session_id: str | None = None):
    """在独立线程中运行鉴知对话（复用持久化实例保持多轮上下文）

    v6.5.3: 任务级 token 隔离 — 任务开始前快照基线并标记会话标签，
    结束后 diff 出本次任务增量持久化到会话 usage 记录（save_usage）。
    """
    # P1-38：用局部变量贯穿任务全程，避免 finally 重读全局被任务中切换的会话串写
    target_session = session_id or state.current_session_id
    try:
        if state.jianzhi is None:
            _rebuild_jianzhi()
        if state.jianzhi is None:
            state.bus.emit(EventType.ERROR, {"text": "无法初始化鉴知 Agent"}, source="jianzhi")
            return
        if target_session:
            state.current_session_id = target_session
        # v6.5.3: 基线快照（实例级 _token_accum 跨会话累计，diff 出本任务增量）
        agent = state.jianzhi
        agent._token_base = dict(getattr(agent, "_token_accum", {}))
        agent._session_tag = target_session
        # 用户消息落盘：此前仅 assistant 回复经 SSE 缓冲落盘，user 消息从未写入，
        # 导致会话恢复时用户发言全部丢失、first_user_message/自动命名失效（user=0 根因）
        if state.session_manager and target_session:
            try:
                state.session_manager.add_message(target_session, {
                    "id": int(time.time() * 1000),
                    "role": "user",
                    "content": text,
                    "timestamp": int(time.time()),
                })
            except Exception as e:
                state.bus.emit(EventType.ERROR,
                               {"text": f"用户消息落盘失败（{target_session}）：{e}"},
                               source="server")
        # 广播任务开始（含会话绑定），供 SSE 后台消费者将输出缓冲到正确会话
        state.bus.emit(EventType.TASK_START, {"session_id": target_session}, source="jianzhi")
        state.jianzhi.chat(text)
        # v6.5.3: 任务结束，diff 出本次任务 token 增量并持久化到会话 usage
        if state.session_manager and target_session:
            try:
                diff_in = agent._token_accum["input"] - agent._token_base["input"]
                diff_out = agent._token_accum["output"] - agent._token_base["output"]
                model_id = getattr(getattr(agent, "llm", None), "model", "") or ""
                state.session_manager.save_usage(target_session, diff_in, diff_out, model_id)
            except Exception as e:
                state.bus.emit(EventType.INFO,
                               {"text": f"会话 token 用量持久化失败（{target_session}）：{e}"},
                               source="server")
        # 复位会话标签，避免下一次无会话任务（如妙笔）被错误归属
        agent._session_tag = None
    except Exception as e:
        state.bus.emit(EventType.ERROR, {"text": str(e)}, source="jianzhi")
    finally:
        state.bus.emit(EventType.TASK_DONE, {"session_id": target_session}, source="jianzhi")


# ─── 确认响应 ──────────────────────────────────────────────────────

@router.post("/api/chat/confirm/{confirm_id}")
async def chat_resolve_confirm(confirm_id: str, req: ConfirmResReq) -> dict:
    """响应确认请求"""
    try:
        if state.session_manager and state.current_session_id:
            try:
                state.session_manager.save_pending_confirm(state.current_session_id, None)
            except Exception as e:
                # 不静默：清除待确认状态失败（会话文件损坏）须上报，
                # 但不应阻断确认本身（确认是主流程，清除是辅助）
                state.bus.emit(EventType.ERROR,
                               {"text": f"清除待确认状态失败：{e}"},
                               source="server")
        state.bus.resolve_confirm(confirm_id, req.model_dump())
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ─── 上下文管理 ────────────────────────────────────────────────────

@router.post("/api/chat/compact")
async def chat_compact(session_id: str | None = Query(default=None)) -> dict:
    """压缩鉴知对话上下文"""
    target = session_id or state.current_session_id
    try:
        if state.jianzhi is not None:
            state.jianzhi.compact_history()
        if state.session_manager and target:
            summary = getattr(getattr(state.jianzhi, "context", None), "last_summary", "") or ""
            state.session_manager.update_compact_summary(target, summary)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.get("/api/chat/context")
async def chat_context_report(session_id: str | None = Query(default=None)) -> dict:
    """获取上下文占用情况（v6.5.3: 支持按会话隔离的 token 统计）

    - 运行时增量：当前 agent 若正处理该会话，返回其任务增量（实时）
    - 持久化用量：该会话 JSONL 中 usage 记录总和（跨任务累计，已保存）
    """
    try:
        target = session_id or state.current_session_id
        runtime_in = runtime_out = runtime_total = 0
        msg_count = 0
        if state.jianzhi is not None:
            agent = state.jianzhi
            msg_count = len(agent.messages) if hasattr(agent, "messages") else 0
            if getattr(agent, "_session_tag", None) == target:
                diff = {"input": 0, "output": 0, "total": 0}
                base = getattr(agent, "_token_base", {})
                accum = getattr(agent, "_token_accum", {})
                for k in diff:
                    diff[k] = accum.get(k, 0) - base.get(k, 0)
                runtime_in, runtime_out, runtime_total = diff["input"], diff["output"], diff["total"]
        saved = {}
        if state.session_manager and target:
            try:
                saved = state.session_manager.get_stats(target)
            except Exception as e:
                state.bus.emit(EventType.INFO,
                               {"text": f"读取会话 token 统计失败（{target}）：{e}"},
                               source="server")
        return {
            "ok": True,
            "session_id": target,
            "message_count": msg_count,
            # 输入/输出/总计 = 持久化用量 + 运行时增量（实时展示当前任务）
            "input_tokens": saved.get("total_input_tokens", 0) + runtime_in,
            "output_tokens": saved.get("total_output_tokens", 0) + runtime_out,
            "total_tokens": saved.get("total_input_tokens", 0) + saved.get("total_output_tokens", 0) + runtime_total,
            "saved_input_tokens": saved.get("total_input_tokens", 0),
            "saved_output_tokens": saved.get("total_output_tokens", 0),
            "saved_total_tokens": saved.get("total_input_tokens", 0) + saved.get("total_output_tokens", 0),
            "model_usage": saved.get("model_usage", {}),
        }
    except Exception as e:
        # 不静默：返回明确的错误结构（ok=False），前端可展示失败原因
        return {"ok": False, "error": str(e)}


@router.post("/api/chat/clear")
async def chat_clear(session_id: str | None = Query(default=None)) -> dict:
    """清空对话历史（保留 meta，重置 message_count=0）"""
    target = session_id or state.current_session_id
    try:
        if state.jianzhi is not None:
            state.jianzhi.clear_context()
        if state.session_manager and target:
            try:
                state.session_manager.clear_session(target)
            except Exception as e:
                print(f"[chat] [警告] session clear failed: {e}")
        return {"ok": True, "session_id": target}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
