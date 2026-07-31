"""Agent 抽象基类 — 定义所有 Agent 的通用接口"""

import sys
from abc import ABC, abstractmethod
from pathlib import Path

from api import LLMClient
from core.events import EventBus, EventType

# 模块级 Token 统计服务单例（避免每次 LLM 调用都开关 DB 连接）
_token_stats_svc = None
_token_stats_lock = __import__("threading").Lock()


def _get_token_stats():
    global _token_stats_svc
    if _token_stats_svc is None:
        with _token_stats_lock:
            if _token_stats_svc is None:
                from tools.db.token_stats import TokenStatsService
                _token_stats_svc = TokenStatsService()
    return _token_stats_svc


class BaseAgent(ABC):
    """Agent 抽象基类 — 所有 Agent 继承此类

    v6.0: I/O 交互通过 EventBus 解耦，不再直接引用 cli 对象。
    """

    def __init__(self, config: dict, workspace: Path, bus: EventBus):
        self.config = config
        self.workspace = workspace
        self.bus = bus
        self.llm = LLMClient(config["api"])
        self.messages: list = []
        self._last_usage = {}
        self._token_accum = {"input": 0, "output": 0, "total": 0}

    @abstractmethod
    def build_system_prompt(self) -> str:
        """构建 system prompt"""
        ...

    @abstractmethod
    def build_tool_defs(self) -> list:
        """构建 OpenAI 格式的 tool definitions"""
        ...

    @abstractmethod
    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        ...

    def _normalize_messages(self, messages: list) -> list:
        """清理消息列表（OpenAI 格式）"""
        cleaned = []
        for msg in messages:
            clean = {"role": msg["role"]}
            content = msg.get("content")
            if content is not None:
                clean["content"] = content
            if "tool_calls" in msg:
                clean["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                clean["tool_call_id"] = msg["tool_call_id"]
            cleaned.append(clean)

        if not cleaned:
            return cleaned
        merged = [cleaned[0]]
        for msg in cleaned[1:]:
            if msg["role"] == merged[-1]["role"] and "tool_calls" not in msg and "tool_call_id" not in msg:
                prev = merged[-1]
                prev_c = prev.get("content") or ""
                curr_c = msg.get("content") or ""
                if prev_c and curr_c:
                    prev["content"] = prev_c + "\n" + curr_c
                elif curr_c:
                    prev["content"] = curr_c
            else:
                merged.append(msg)
        return merged

    def _accumulate_tokens(self, usage: dict):
        """累加 token 用量"""
        if not usage:
            return
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")

        if prompt_tokens is not None and completion_tokens is not None:
            input_tokens = prompt_tokens
            output_tokens = completion_tokens
        else:
            # P1-11：tiktoken 环境故障（离线加载编码失败等）时降级为字符粗估，
            # 不允许异常沿对话循环传播中断整个会话；估算仅用于统计，不影响正确性
            try:
                from agent.compact import estimate_tokens
                input_tokens = estimate_tokens(self.messages)
            except Exception as e:
                # 不静默：降级为字符粗估前提示一次（事件总线消费端可见），
                # 避免统计口径变化被当作正常
                if not getattr(self, "_tiktoken_warned", False):
                    self._tiktoken_warned = True
                    try:
                        self.bus.emit(EventType.INFO,
                                      {"text": f"tiktoken 不可用，Token 估算降级为字符粗估：{e}"},
                                      source=getattr(self, "_agent_name", "system"))
                    except Exception as e2:
                        # 最外层兜底：上报通道故障打印 stderr（消费端：日志）
                        print(f"[base] tiktoken 降级提示上报失败：{e2}", file=sys.stderr)
                input_tokens = sum(
                    len(str(m.get("content", ""))) for m in self.messages) // 2
            output_tokens = 0

        total = input_tokens + output_tokens
        self._token_accum["input"] += input_tokens
        self._token_accum["output"] += output_tokens
        self._token_accum["total"] += total

        # 通过事件总线广播 token 统计
        self.bus.emit(EventType.TOKEN_STATS, {
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
            "accum": dict(self._token_accum),
        }, source=getattr(self, "_agent_name", "system"))

        # 持久化到全局 token_stats.db
        self._persist_token_record(input_tokens, output_tokens)

    def _persist_token_record(self, input_tokens: int, output_tokens: int):
        """将单次 token 消耗写入全局 token_stats.db（失败不阻断主流程，但必须上报）"""
        try:
            agent_name = getattr(self, "_agent_name", "system")
            model_id = getattr(self.llm, "model", "")
            model_name = getattr(self.llm, "model_name", "") or model_id
            book = getattr(self, "_book_name", "")
            purpose = getattr(self, "_purpose", "") or agent_name

            ts = _get_token_stats()
            ts.record(
                book=book,
                agent=agent_name,
                model_id=model_id,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                purpose=purpose,
            )
        except Exception as e:
            # 不静默：Token 统计失败通过事件总线上报（用户可感知统计缺失），
            # 但不阻断主流程（统计是辅助功能）
            try:
                self.bus.emit(EventType.INFO,
                              {"text": f"Token 统计写入失败：{e}"},
                              source=getattr(self, "_agent_name", "system"))
            except Exception as e2:
                # 最外层兜底：事件上报通道也故障时打印 stderr（消费端：日志），
                # 保证统计失败不石沉大海
                print(f"[base] Token 统计写入失败且上报通道故障：{e}（{e2}）",
                      file=sys.stderr)

    def token_report(self) -> str:
        """返回累计 token 统计"""
        lines = [
            f"Token 用量统计（本次会话）：",
            f"  输入: {self._token_accum['input']}",
            f"  输出: {self._token_accum['output']}",
            f"  总计: {self._token_accum['total']}",
        ]
        if self._last_usage:
            lines.append(f"  （最近一次 API: {self._last_usage}）")
        return "\n".join(lines)

    def clear_context(self):
        """清空上下文"""
        self.messages = []
        self.bus.emit(EventType.INFO, {"text": "上下文已清空。"}, source="system")
