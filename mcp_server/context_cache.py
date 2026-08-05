"""上下文缓存 — 工作流产物/组装上下文的内存缓存服务（v7.2.0）

定位：宿主 LLM 调度子智能体时，把"InkWeaver 从知识库加载的明确上下文块"
缓存在 CLI 后端，通过工具调用一键调出，宿主不必自己准备上下文塞给子智能体。

设计要点：
- 纯内存，不落盘：进程重启即失，产物可重新生成（用户决策）
- 单章快照 · 用完即丢：muse_write 启动时清空该工作区旧产物，
  任务结束时写入新产物 → 缓存中永远只有"最近一章"的快照
- 线程安全：与 TaskManager 同模式，MCP 工具并发调用安全
- 当前写入路径：tools_agent._run_muse 任务结束（put prior_knowledge / plot_summary）
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any


# 产物 kind 常量
KIND_PRIOR_KNOWLEDGE = "muse_prior_knowledge"   # 先验知识（妙笔知识准备产物）
KIND_PLOT_SUMMARY = "muse_plot_summary"         # 前情提要（妙笔知识准备产物）


# 进程级默认实例（与 tools.editor._proxy_instances 同模式）：
# MCPContext 与 tools_agent._run_muse（异步线程）跨模块共享同一缓存
_DEFAULT_CACHE: "ContextCache | None" = None


def get_default_cache() -> "ContextCache":
    """获取进程级默认缓存实例（懒加载单例）"""
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = ContextCache()
    return _DEFAULT_CACHE


@dataclass
class CachedItem:
    """单条缓存产物"""
    content: str
    meta: dict = field(default_factory=dict)     # {chapter, generated_at, task_id, ...}


class ContextCache:
    """工作区 → 产物类型 → 内容的进程级内存缓存"""

    def __init__(self):
        self._data: dict[str, dict[str, CachedItem]] = {}
        self._lock = threading.Lock()

    # ── 读写 ──────────────────────────────────────────────

    def put(self, workspace: str, kind: str, content: str,
            meta: dict | None = None) -> None:
        """写入/覆盖某工作区的某类产物"""
        if not content:
            return
        with self._lock:
            ws_data = self._data.setdefault(workspace, {})
            ws_data[kind] = CachedItem(
                content=content,
                meta=meta or {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            )

    def get(self, workspace: str, kind: str) -> CachedItem | None:
        """读取某工作区的某类产物（不存在返回 None）"""
        with self._lock:
            return self._data.get(workspace, {}).get(kind)

    def clear_workspace(self, workspace: str) -> None:
        """清空某工作区全部产物（muse_write 新任务启动时调用：旧章产物作废）"""
        with self._lock:
            self._data.pop(workspace, None)

    def clear_kind(self, workspace: str, kind: str) -> None:
        """清空某工作区某类产物"""
        with self._lock:
            ws_data = self._data.get(workspace)
            if ws_data:
                ws_data.pop(kind, None)

    # ── 快照（调试/测试）──────────────────────────────────

    def snapshot(self, workspace: str = "") -> dict[str, Any]:
        """返回某工作区（或全部）的产物概览：kind → {len, meta}"""
        with self._lock:
            if workspace:
                ws = self._data.get(workspace, {})
                return {k: {"chars": len(v.content), "meta": v.meta}
                        for k, v in ws.items()}
            return {
                ws: {k: {"chars": len(v.content), "meta": v.meta}
                     for k, v in ws_data.items()}
                for ws, ws_data in self._data.items()
            }
