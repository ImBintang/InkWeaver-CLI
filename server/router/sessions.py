"""Chat sessions HTTP API — CRUD + activate + stats per book."""

from fastapi import APIRouter
from pydantic import BaseModel

from server.state import state
from server.router.books import _safe_book_path

router = APIRouter()


class SessionCreate(BaseModel):
    name: str = "新会话"
    cap: int | None = None


class SessionPatch(BaseModel):
    name: str | None = None
    archived: bool | None = None


def _mgr(book: str):
    if state.current_book and state.current_book != book:
        from fastapi import HTTPException
        raise HTTPException(409, detail=f"当前工作区为「{state.current_book}」，请先切换")
    if state.session_manager is None:
        if state.workspace_path:
            state.bind_session_manager()
        else:
            from fastapi import HTTPException
            raise HTTPException(400, detail="请先打开一个工作区")
    return state.session_manager


@router.get("/api/books/{book}/sessions")
async def list_sessions(book: str) -> dict:
    ws = _safe_book_path(book)
    from tools.session_manager import SessionManager
    return SessionManager(ws, ws / "chat_sessions").load_index()


@router.post("/api/books/{book}/sessions")
async def create_session(book: str, req: SessionCreate) -> dict:
    mgr = _mgr(book)
    # v7.0.1: cap 限幅（1~10000），防负数/超大值破坏会话计数
    cap = req.cap
    if cap is not None:
        cap = max(1, min(int(cap), 10000))
    sid = mgr.create_session(req.name, cap)
    sess = mgr.activate(sid, clear_pending_confirm=True)
    state.current_session_id = sid
    return {"session_id": sid, "session": sess}


@router.get("/api/books/{book}/sessions/{session_id}")
async def get_session(book: str, session_id: str) -> dict:
    return _mgr(book).get_session(session_id)


@router.patch("/api/books/{book}/sessions/{session_id}")
async def patch_session(book: str, session_id: str, req: SessionPatch) -> dict:
    mgr = _mgr(book)
    if req.name is not None: mgr.rename(session_id, req.name)
    if req.archived is not None: mgr.archive(session_id, req.archived)
    return mgr.get_session(session_id)


@router.delete("/api/books/{book}/sessions/{session_id}")
async def delete_session(book: str, session_id: str) -> dict:
    mgr = _mgr(book)
    mgr.delete_session(session_id)
    state.current_session_id = mgr.load_index().get("current_session_id")
    return {"ok": True, "new_current": state.current_session_id}


@router.post("/api/books/{book}/sessions/{session_id}/activate")
async def activate_session(book: str, session_id: str) -> dict:
    sess = _mgr(book).activate(session_id, clear_pending_confirm=True)
    state.current_session_id = session_id
    return sess


@router.get("/api/books/{book}/sessions/{session_id}/stats")
async def session_stats(book: str, session_id: str) -> dict:
    return _mgr(book).get_stats(session_id)
