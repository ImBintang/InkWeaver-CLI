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


def _map_opinions(issues: list) -> list:
    """将审阅 issue（level/quote/description/suggestion）映射为前端 MuseOpinion 结构"""
    level_sev = {0: "error", 1: "error", 2: "warning", 3: "info"}
    opinions = []
    for i, issue in enumerate(issues, 1):
        level = issue.get("level")
        sev = level_sev.get(level, "info") if isinstance(level, int) else "info"
        quote = (issue.get("quote") or "").strip()
        desc = issue.get("description", "")
        sug = issue.get("suggestion", "")
        text = f"「{quote}」{desc}" if quote else desc
        if sug:
            text += f"（建议：{sug}）"
        opinions.append({"id": i, "paragraph": 0, "text": text, "severity": sev})
    return opinions


def _run_muse(outline: str, chapter_num: int | None):
    """在独立线程中运行妙笔工作流"""
    wf = None
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
            bus=state.bus,  # 注入全局事件总线：准备/写作/审阅过程实时推送前端
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
        # TASK_DONE 携带审阅结果回传前端（此前为空 {} 导致前端拿不到意见/分数/定稿）
        payload: dict = {}
        if wf is not None:
            review = getattr(wf, "final_review", None)
            if review:
                payload["score"] = review.get("score")
                payload["pass"] = review.get("pass")
                payload["opinions"] = _map_opinions(review.get("issues", []))
            if getattr(wf, "final_text", ""):
                payload["final_text"] = wf.final_text
            try:
                payload["task_dir"] = str(wf.io.task_dir)
            except Exception:
                pass
        state.bus.emit(EventType.TASK_DONE, payload, source="muse")


@router.get("/api/muse/status")
async def muse_status() -> dict:
    """获取妙笔运行状态"""
    return {
        "running": state.agent_thread is not None and state.agent_thread.is_alive(),
    }
