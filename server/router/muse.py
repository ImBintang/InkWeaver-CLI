"""妙笔工作流 HTTP API"""

import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.state import state
from commands.common import load_config, SKILLS_DIR
from core.events import EventType

router = APIRouter()


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class MuseStartReq(BaseModel):
    outline: str
    chapter_num: int | None = None


# ─── 妙笔工作流 ────────────────────────────────────────────────────

@router.post("/api/muse/start")
async def muse_start(req: MuseStartReq) -> dict:
    """启动妙笔工作流"""
    if not state.workspace_path:
        raise HTTPException(400, detail="请先打开一个工作区")
    with state.agent_lock:
        if state.agent_thread and state.agent_thread.is_alive():
            raise HTTPException(409, detail="Agent 正在运行中，请等待完成")
        state.agent_thread = threading.Thread(
            target=_run_muse, args=(req.outline, req.chapter_num), daemon=True
        )
        state.agent_thread.start()
    return {"ok": True}


def _run_muse(outline: str, chapter_num: int | None):
    """在独立线程中运行妙笔工作流"""
    try:
        from Muse import MuseWorkflow
        config = load_config()
        wf = MuseWorkflow(
            config=config,
            workspace=state.workspace_path,
            skills_dir=SKILLS_DIR,
            workspaces_dir=state.workspaces_dir,
            outline_text=outline,
            auto_approve=True,  # API 模式无 stdin，自动通过所有确认
            chapter=chapter_num,
        )
        wf.run()
    except SystemExit as e:
        state.bus.emit(
            EventType.ERROR,
            {"text": f"妙笔工作流异常退出（code={e.code}）"},
            source="muse",
        )
    except Exception as e:
        state.bus.emit(EventType.ERROR, {"text": str(e)}, source="muse")
    finally:
        state.bus.emit(EventType.TASK_DONE, {}, source="muse")


@router.get("/api/muse/status")
async def muse_status() -> dict:
    """获取妙笔运行状态"""
    return {
        "running": state.agent_thread is not None and state.agent_thread.is_alive(),
    }
