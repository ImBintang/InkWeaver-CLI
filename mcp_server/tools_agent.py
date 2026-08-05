"""MCP 子智能体工具 — 工作流即子智能体调用

鉴知问答 / 知识提取 / 妙笔写作三大工作流以异步任务形式暴露：
外部 Agent 通过 start → status/wait → confirm → result 编排，
InkWeaver 内部用自己的 LLM 配置（config.yaml 角色分配）驱动子 Agent，
对调用方而言就是一个"小说领域子智能体"。
"""

import threading
import time

from mcp_server.context import MCPContext
from mcp_server.tasks import TaskManager, TaskRecord

WAIT_CAP = 1800  # task_wait 单次最长等待秒数


def _ok(**data) -> dict:
    return {"status": "success", **data}


def _err(e: Exception) -> dict:
    return {"status": "error", "message": str(e)}


# ── 任务运行体（在独立线程执行）────────────────────────────


def _extract_last_answer(agent) -> str:
    """从 Agent 历史中提取最后一条 assistant 回复"""
    for msg in reversed(getattr(agent, "messages", [])):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"].strip()
    return getattr(agent, "_last_output", "") or ""


def _extract_tools_called(agent) -> list:
    tools = []
    for msg in getattr(agent, "messages", []):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("function", {}).get("name", "")
                if name and name not in tools:
                    tools.append(name)
    return tools


def _token_data(agent) -> dict | None:
    tokens = getattr(agent, "_token_accum", None)
    if tokens and (tokens.get("input", 0) > 0 or tokens.get("output", 0) > 0):
        return {"input": tokens["input"], "output": tokens["output"], "total": tokens["total"]}
    return None


def _run_ask(task: TaskRecord, config: dict, ws, question: str):
    """鉴知子智能体：单轮问答（可查阅章节/知识库/关联/记忆）"""
    from Jianzhi import JianzhiAgent
    from commands.common import SKILLS_DIR

    agent = JianzhiAgent(config, ws, SKILLS_DIR, task.bus)
    try:
        agent.chat(question)
        task.result = {
            "answer": _extract_last_answer(agent),
            "tools_called": _extract_tools_called(agent),
            "tokens": _token_data(agent),
        }
        task.status = "done"
    finally:
        try:
            agent.close()
        except Exception:
            pass


def _run_extract(task: TaskRecord, config: dict, ws, chapters: str):
    """鉴知子智能体：知识提取（新章节 → wiki/rule/plot 沉淀）"""
    from Jianzhi import JianzhiAgent
    from commands.common import SKILLS_DIR
    from commands.extract import _compute_range

    start_ch, end_ch = _compute_range(ws, chapters)
    if start_ch is None:
        task.error = "没有可提取的章节（全部已处理或工作区无章节）"
        task.status = "error"
        return

    # 预校验范围有效性：超出总章节数直接报错，不浪费 LLM 调用
    try:
        from tools.editor import _get_proxy
        total = _get_proxy(ws)._db.chapter_count()
        if start_ch > total:
            task.error = f"提取范围无效：起始章 {start_ch} 超出总章节数 {total}"
            task.status = "error"
            return
        end_ch = min(end_ch, total)
    except Exception as e:
        task.error = f"章节数校验失败：{e}"
        task.status = "error"
        return

    task.add_progress("info", f"提取范围：第 {start_ch}~{end_ch} 章")
    agent = JianzhiAgent(config, ws, SKILLS_DIR, task.bus)
    try:
        agent.chat(f"请对第{start_ch}~{end_ch}章执行知识提取流程")
        task.result = {
            "answer": _extract_last_answer(agent),
            "scope": f"{start_ch}-{end_ch}",
            "tools_called": _extract_tools_called(agent),
            "tokens": _token_data(agent),
        }
        task.status = "done"
    finally:
        try:
            agent.close()
        except Exception:
            pass


def _run_muse(task: TaskRecord, config: dict, ws, outline: str,
              chapter: int | None, auto_approve: bool, workspaces_dir):
    """妙笔子智能体：四步写作工作流（大纲→知识准备→写作→审阅循环）"""
    from Muse import MuseWorkflow
    from commands.common import SKILLS_DIR
    from muse.workflow import MuseStopped
    from mcp_server.context_cache import (
        KIND_PLOT_SUMMARY,
        KIND_PRIOR_KNOWLEDGE,
        get_default_cache,
    )

    # v7.2.0: 单章快照·用完即丢 —— 新任务启动即清除旧章产物
    get_default_cache().clear_workspace(getattr(ws, "name", ""))

    workflow = MuseWorkflow(
        config=config,
        workspace=ws,
        skills_dir=SKILLS_DIR,
        workspaces_dir=workspaces_dir,
        outline_text=outline,
        auto_approve=auto_approve,
        chapter=chapter,
        bus=task.bus,
        stop_event=task.stop_event,
    )
    try:
        workflow.run()
        tokens = getattr(workflow, "_token_total", {})
        task.result = {
            "final_text": workflow.final_text,
            "final_review": workflow.final_review,
            "task_dir": str(workflow.io.task_dir),
            "target_chapter": workflow.target_chapter,
            "tokens": {"input": tokens.get("input", 0), "output": tokens.get("output", 0),
                       "total": tokens.get("total", 0)} if tokens else None,
        }
        task.status = "done"
        # v7.2.0: 任务结束抓取 LLM 产物（先验知识 + 前情提要）写入上下文缓存
        ws_name = getattr(ws, "name", "")
        cache = get_default_cache()
        meta = {"chapter": workflow.target_chapter, "task_id": task.id}
        cache.put(ws_name, KIND_PRIOR_KNOWLEDGE, workflow.prior_knowledge, meta)
        cache.put(ws_name, KIND_PLOT_SUMMARY, workflow.plot_summary, meta)
    except MuseStopped:
        task.status = "cancelled"
        task.error = "妙笔任务已终止（用户取消）"


# ── 工具注册 ──────────────────────────────────────────────


def register_agent_tools(mcp, ctx: MCPContext, tasks: TaskManager):
    """注册子智能体任务工具与任务管理工具"""

    @mcp.tool()
    def ask_jianzhi(question: str, workspace: str = "") -> dict:
        """【子智能体·鉴知】向小说知识库 Agent 提问（单轮，含工具调用）

        鉴知可查阅章节原文、wiki/rule/plot 知识库、词条关联与作者记忆来回答。
        适合：设定考证、剧情一致性核查、人物状态查询等。
        返回 task_id，属于异步任务（通常 30 秒~3 分钟）。

        Args:
            question: 自然语言问题，如"林婉当前在哪个境界？依据是哪些章节？"
            workspace: 工作区名
        """
        return _start_task_ask(question, workspace)

    def _start_task_ask(question: str, workspace: str) -> dict:
        try:
            ws = ctx.resolve_ws(workspace)
            config = ctx.config()
        except Exception as e:
            return _err(e)
        task = tasks.create("ask", {"question": question}, ws.name)
        tasks.start_consumer(task, auto_approve=True)  # 提问不触发计划确认，自动放行兜底

        def _main():
            try:
                _run_ask(task, config, ws, question)
            except Exception as e:
                task.error = f"{type(e).__name__}: {e}"
                task.status = "error"
                task.add_progress("error", task.error)
            finally:
                tasks.stop_consumer(task)

        task.thread = threading.Thread(target=_main, daemon=True)
        task.thread.start()
        return _ok(task_id=task.id, kind="ask", workspace=ws.name,
                   message="鉴知问答任务已启动，用 task_wait/task_status 查询")

    @mcp.tool()
    def extract_knowledge(chapters: str = "", auto_approve: bool = False,
                          workspace: str = "") -> dict:
        """【子智能体·鉴知】对章节执行知识提取，沉淀为 wiki/rule/plot 知识条目

        提取过程会生成计划并请求确认：
        - auto_approve=true：自动批准计划（全自动模式）
        - auto_approve=false：任务进入 awaiting_confirmation 状态，
          用 task_status 查看计划详情，再用 task_confirm 批准/驳回
        属于异步任务（每 10 章约 3~10 分钟）。

        Args:
            chapters: 章节范围如 "21-30"；空=自动取未处理的下一章起最多 10 章
            auto_approve: 是否自动批准提取计划，默认 false（人工审核模式）
            workspace: 工作区名
        """
        try:
            ws = ctx.resolve_ws(workspace)
            config = ctx.config()
        except Exception as e:
            return _err(e)
        task = tasks.create("extract", {"chapters": chapters, "auto_approve": auto_approve},
                            ws.name)
        tasks.start_consumer(task, auto_approve=auto_approve)

        def _main():
            try:
                _run_extract(task, config, ws, chapters)
            except Exception as e:
                task.error = f"{type(e).__name__}: {e}"
                task.status = "error"
                task.add_progress("error", task.error)
            finally:
                tasks.stop_consumer(task)

        task.thread = threading.Thread(target=_main, daemon=True)
        task.thread.start()
        return _ok(task_id=task.id, kind="extract", workspace=ws.name,
                   auto_approve=auto_approve,
                   message="知识提取任务已启动；若出现 awaiting_confirmation 请用 task_confirm 响应")

    @mcp.tool()
    def muse_write(outline: str, chapter: int = 0, auto_approve: bool = True,
                   workspace: str = "") -> dict:
        """【子智能体·妙笔】四步写作工作流：大纲输入→知识准备→写作→审阅循环

        妙笔会自动检索先验知识与前情提要、按写作技能起草、由审阅子 Agent 评分，
        循环修订直至通过（或达到轮次上限），产出定稿并落盘到工作区 muse 任务目录。
        属于长时异步任务（通常 5~20 分钟）。

        Args:
            outline: 本章大纲文本（剧情要点、出场人物、伏笔等）
            chapter: 目标章节号；0=自动取最新章节+1
            auto_approve: true=全自动（默认，适合 Agent 编排）；
                false=关键节点挂起等待 task_confirm
            workspace: 工作区名
        """
        if not outline.strip():
            return _err(ValueError("outline 不能为空"))
        try:
            ws = ctx.resolve_ws(workspace)
            config = ctx.config()
            workspaces_dir = ctx.workspaces_dir()
        except Exception as e:
            return _err(e)
        task = tasks.create("muse_write",
                            {"outline_chars": len(outline), "chapter": chapter,
                             "auto_approve": auto_approve},
                            ws.name)
        tasks.start_consumer(task, auto_approve=auto_approve)

        def _main():
            try:
                _run_muse(task, config, ws, outline,
                          chapter if chapter > 0 else None, auto_approve, workspaces_dir)
            except Exception as e:
                if task.status != "cancelled":
                    task.error = f"{type(e).__name__}: {e}"
                    task.status = "error"
                task.add_progress("error", task.error)
            finally:
                tasks.stop_consumer(task)

        task.thread = threading.Thread(target=_main, daemon=True)
        task.thread.start()
        return _ok(task_id=task.id, kind="muse_write", workspace=ws.name,
                   message="妙笔写作任务已启动（长任务，建议 task_wait 等待或轮询 task_status）")

    # ── 任务管理 ──────────────────────────────────────────

    @mcp.tool()
    def task_status(task_id: str, progress_tail: int = 15) -> dict:
        """查询异步任务状态与进度轨迹

        Args:
            task_id: 任务 ID
            progress_tail: 返回最近 N 条进度，默认 15
        """
        task = tasks.get(task_id)
        if task is None:
            return _err(KeyError(f"任务不存在：{task_id}"))
        # v7.0.1: progress_tail 限幅到 [1, 100]，防御负值/超大值切片异常
        progress_tail = max(1, min(int(progress_tail), 100))
        snap = task.snapshot(progress_tail=progress_tail)
        if task.status == "awaiting_confirmation" and task.pending_confirm:
            snap["hint"] = ("任务正在等待确认：请查看 pending_confirm.payload，"
                            "然后调用 task_confirm(task_id, action=approve|reject, reason=...)")
        return _ok(**snap)

    @mcp.tool()
    def task_wait(task_id: str, timeout: int = 600) -> dict:
        """阻塞等待任务结束（或状态变化），适合短任务一次拿结果

        等待期间若任务进入 awaiting_confirmation 会立即返回（需要 task_confirm）。
        超时不终止任务，可再次调用 task_wait 续等。

        Args:
            task_id: 任务 ID
            timeout: 最长等待秒数，默认 600，上限 1800
        """
        task = tasks.get(task_id)
        if task is None:
            return _err(KeyError(f"任务不存在：{task_id}"))
        timeout = max(5, min(int(timeout), WAIT_CAP))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.status in ("done", "error", "cancelled", "awaiting_confirmation"):
                break
            time.sleep(1.0)
        snap = task.snapshot()
        if task.status == "running":
            snap["wait_timeout"] = True
            snap["hint"] = "等待超时但任务仍在运行，可再次调用 task_wait 续等"
        return _ok(**snap)

    @mcp.tool()
    def task_result(task_id: str) -> dict:
        """获取已完成任务的最终成果

        - ask: answer（鉴知的回答）
        - extract: answer + scope（提取范围）
        - muse_write: final_text（定稿正文）+ final_review（审阅意见）+ task_dir（产物目录）
        """
        task = tasks.get(task_id)
        if task is None:
            return _err(KeyError(f"任务不存在：{task_id}"))
        if task.status == "done":
            return _ok(task_id=task_id, kind=task.kind, result=task.result)
        if task.status in ("error", "cancelled"):
            return {"status": "error", "message": f"任务未成功（{task.status}）：{task.error}"}
        return {"status": "error",
                "message": f"任务尚未完成（status={task.status}），请先 task_wait 或稍后再查"}

    @mcp.tool()
    def task_confirm(task_id: str, action: str, reason: str = "",
                     rejected_indices: list[int] | None = None) -> dict:
        """响应任务挂起的确认请求（任务处于 awaiting_confirmation 时调用）

        Args:
            task_id: 任务 ID
            action: approve=批准 | reject=驳回（必须附 reason）| approve_all=全部通过（强制债务审核）
            reason: 驳回理由（reject 时必填）
            rejected_indices: forced_debt 审核时拒绝的条目序号列表（1 基），可选
        """
        return tasks.confirm(task_id, action, reason, rejected_indices)

    @mcp.tool()
    def task_cancel(task_id: str) -> dict:
        """取消运行中的任务（在下一个检查点生效；妙笔任务即时打断）"""
        return tasks.cancel(task_id)

    @mcp.tool()
    def task_list() -> dict:
        """列出本 MCP 会话内所有任务及状态"""
        return _ok(tasks=tasks.list_tasks(), count=len(tasks.list_tasks()))
