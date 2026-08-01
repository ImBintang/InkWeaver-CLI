"""书籍/章节/草稿 HTTP API"""

import copy
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.state import state
from tools.db.service import SQLiteService
from tools import workspace as workspace_tools

router = APIRouter()

# 安全：工作区名只允许中文、字母、数字、下划线、连字符
_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fff]+$")


def _safe_book_path(book: str) -> Path:
    """解析并校验工作区路径，防止路径遍历"""
    if not _NAME_RE.match(book):
        raise HTTPException(400, detail=f"非法工作区名「{book}」")
    ws_path = (state.workspaces_dir / book).resolve()
    # containment 检查：确保路径仍在 workspaces_dir 内
    if not str(ws_path).startswith(str(state.workspaces_dir.resolve())):
        raise HTTPException(400, detail="路径超出工作区范围")
    return ws_path


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class BookOpenReq(BaseModel):
    name: str


class BookCreateReq(BaseModel):
    name: str
    path: str = ""


class BookRenameReq(BaseModel):
    name: str
    new_name: str


class ChapterImportReq(BaseModel):
    file_path: str


class DraftSaveReq(BaseModel):
    chapter_num: int
    content: str
    source: str = "user"
    title: str = ""


# ─── 书籍（工作区）管理 ────────────────────────────────────────────

@router.get("/api/books")
async def list_books() -> list[dict]:
    """列出所有工作区"""
    try:
        if not state.workspaces_dir.exists():
            return []
        entries = sorted([d.name for d in state.workspaces_dir.iterdir() if d.is_dir()])
        result = []
        for name in entries:
            ws_path = state.workspaces_dir / name
            chapters = 0
            chapters_error = None
            db = None
            try:
                db = SQLiteService(ws_path / "wiki.db")
                chapters = db.chapter_count()
            except Exception as e:
                # 不静默：单个工作区读取失败保留错误信息（区分"无数据"与"损坏"）
                chapters_error = str(e)
            finally:
                if db:
                    db.close()
            result.append({"name": name, "path": str(ws_path),
                           "chapters": chapters, "chapters_error": chapters_error})
        return result
    except Exception as e:
        raise HTTPException(500, detail=f"读取工作区列表失败：{e}")


@router.get("/api/books/current")
async def get_current_book() -> dict | None:
    """获取当前打开的工作区"""
    if state.current_book:
        return {"name": state.current_book}
    return None


@router.post("/api/books/open")
async def open_book(req: BookOpenReq) -> dict:
    """打开指定工作区"""
    try:
        target = _safe_book_path(req.name)
    except HTTPException:
        raise
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, detail=f"工作区「{req.name}」不存在")
    state.current_book = req.name
    state.workspace_path = target
    _rebuild_jianzhi()
    try:
        state.bind_session_manager()
        session_data = state.load_or_create_session()
        state.current_session_id = session_data["id"]
    except Exception as e:
        print(f"[books] ⚠ session bind failed: {e}")
        state.current_session_id = None
        session_data = None
    return {"ok": True, "name": req.name, "session": session_data}


@router.post("/api/books")
async def create_book(req: BookCreateReq) -> dict:
    """创建新工作区"""
    try:
        result = workspace_tools.create_workspace(state.workspaces_dir, req.name)
        if result is None:
            raise HTTPException(400, detail=f"工作区「{req.name}」已存在或名称非法")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


def _close_jianzhi_quietly():
    """关闭当前鉴知实例的 DB 连接（重命名/删除工作区前调用，避免 Windows 文件锁）"""
    old = state.jianzhi
    if old is not None:
        try:
            close = getattr(old, "close", None)
            if close:
                close()
        except Exception as e:
            print(f"[books] ⚠ 关闭鉴知连接失败: {e}")
    state.jianzhi = None


@router.post("/api/books/rename")
async def rename_book(req: BookRenameReq) -> dict:
    """重命名工作区（目录改名）"""
    old_name = req.name.strip()
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(400, detail="新名称不能为空")
    if not _NAME_RE.match(new_name):
        raise HTTPException(400, detail=f"非法工作区名「{new_name}」")
    if old_name == new_name:
        return {"ok": True, "name": new_name}
    old_path = _safe_book_path(old_name)
    new_path = _safe_book_path(new_name)
    if not old_path.exists() or not old_path.is_dir():
        raise HTTPException(404, detail=f"工作区「{old_name}」不存在")
    if new_path.exists():
        raise HTTPException(400, detail=f"工作区「{new_name}」已存在")
    is_current = state.current_book == old_name
    if is_current:
        _close_jianzhi_quietly()
    try:
        old_path.rename(new_path)
    except Exception as e:
        raise HTTPException(500, detail=f"重命名失败：{e}")
    if is_current:
        state.current_book = new_name
        state.workspace_path = new_path
        _rebuild_jianzhi()
        try:
            state.bind_session_manager()
        except Exception as e:
            print(f"[books] ⚠ rename 后重绑会话失败: {e}")
    return {"ok": True, "name": new_name}


@router.delete("/api/books/{name}")
async def delete_book(name: str) -> dict:
    """删除工作区（整个目录，危险操作，前端需二次确认）"""
    target = _safe_book_path(name)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, detail=f"工作区「{name}」不存在")
    if state.current_book == name:
        _close_jianzhi_quietly()
        state.current_book = None
        state.workspace_path = None
        state.session_manager = None
        state.current_session_id = None
    try:
        shutil.rmtree(target)
    except Exception as e:
        raise HTTPException(500, detail=f"删除工作区失败：{e}")
    return {"ok": True}


# ─── 章节管理 ──────────────────────────────────────────────────────

def _get_db(book: str) -> SQLiteService:
    """获取指定工作区的 SQLiteService 实例（调用方负责 close）"""
    ws_path = _safe_book_path(book)
    return SQLiteService(ws_path / "wiki.db")


@router.get("/api/books/{book}/chapters")
async def list_chapters(book: str) -> list[dict]:
    """列出某工作区下的所有章节"""
    db = None
    try:
        db = _get_db(book)
        rows = db.chapter_list_all_with_count()
        return [
            {
                "num": r["chapter_num"],
                "title": r["title"],
                "word_count": r["word_count"],
                "imported_at": r.get("imported_at"),
                "draft_count": r.get("draft_count", 0),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, detail=f"列出章节失败：{e}")
    finally:
        if db:
            db.close()


@router.get("/api/books/{book}/chapters/{num}")
async def get_chapter(book: str, num: int) -> dict:
    """获取单章详情"""
    db = None
    try:
        db = _get_db(book)
        row = db.chapter_get(num)
        return row if row else {}
    except Exception as e:
        raise HTTPException(500, detail=f"读取章节 {num} 失败：{e}")
    finally:
        if db:
            db.close()


@router.post("/api/books/{book}/chapters/import")
async def import_chapter(book: str, req: ChapterImportReq) -> dict:
    """导入章节文件"""
    try:
        ws_path = _safe_book_path(book)
        result = workspace_tools.import_novel(ws_path, req.file_path)
        if result.startswith("错误"):
            raise HTTPException(400, detail=result)
        return {"ok": True, "message": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ─── 草稿系统 ──────────────────────────────────────────────────────

@router.get("/api/books/{book}/drafts")
async def list_drafts(book: str, chapter_num: int | None = None) -> list[dict]:
    """列出草稿（可按章节过滤）"""
    db = None
    try:
        db = _get_db(book)
        return db.draft_list(chapter_num=chapter_num)
    except Exception as e:
        raise HTTPException(500, detail=f"列出草稿失败：{e}")
    finally:
        if db:
            db.close()


@router.get("/api/books/{book}/drafts/{draft_id}")
async def get_draft(book: str, draft_id: int) -> dict:
    """获取草稿详情"""
    db = None
    try:
        db = _get_db(book)
        row = db.draft_get(draft_id)
        return row if row else {}
    except Exception as e:
        raise HTTPException(500, detail=f"读取草稿 #{draft_id} 失败：{e}")
    finally:
        if db:
            db.close()


@router.post("/api/books/{book}/drafts")
async def save_draft(book: str, req: DraftSaveReq) -> dict:
    """保存草稿"""
    db = None
    try:
        db = _get_db(book)
        draft_id = db.draft_create(req.chapter_num, req.content, source=req.source, title=req.title)
        return {"ok": True, "id": draft_id}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
    finally:
        if db:
            db.close()


@router.post("/api/books/{book}/drafts/{draft_id}/publish")
async def publish_draft(book: str, draft_id: int) -> dict:
    """发布草稿为正式章节"""
    try:
        if not state.workspace_path:
            raise HTTPException(400, detail="请先打开一个工作区")
        db = None
        try:
            db = _get_db(book)
            draft = db.draft_get(draft_id)
            if not draft:
                raise HTTPException(404, detail=f"草稿 #{draft_id} 不存在")
            chapter_num = draft["chapter_num"]
            content = draft["content"]
            title = draft.get("title", "")
            db.chapter_upsert(chapter_num, title, content)
            return {"ok": True, "chapter_num": chapter_num}
        finally:
            if db:
                db.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.delete("/api/books/{book}/drafts/{draft_id}")
async def delete_draft(book: str, draft_id: int) -> dict:
    """删除草稿"""
    db = None
    try:
        db = _get_db(book)
        ok = db.draft_delete(draft_id)
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
    finally:
        if db:
            db.close()


# ─── 内部工具 ──────────────────────────────────────────────────────

def _rebuild_jianzhi():
    """重建鉴知 Agent 实例（open_book 时调用）

    P1-37：重建前先关闭旧实例的 SQLite 连接，避免句柄累积
    导致 Windows 下 wiki.db 被锁、删除/移动失败。
    """
    from commands.common import load_config, SKILLS_DIR

    if not state.workspace_path:
        return
    try:
        # 关闭旧实例连接（幂等）
        old = state.jianzhi
        if old is not None:
            try:
                close = getattr(old, "close", None)
                if close:
                    close()
            except Exception as e:
                print(f"[books] ⚠ 关闭旧鉴知实例失败: {e}")
        from Jianzhi import JianzhiAgent
        config = load_config()
        state.jianzhi = JianzhiAgent(
            config=config,
            workspace=state.workspace_path,
            skills_dir=SKILLS_DIR,
            bus=state.bus,
        )
    except Exception as e:
        # 不静默：重建失败原因打印到服务端日志（GUI 会在下次调用时感知 jianzhi 为空）
        print(f"[books] ⚠ 重建鉴知失败: {e}")
        state.jianzhi = None
