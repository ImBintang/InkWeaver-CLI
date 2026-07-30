"""Agent 抽象基类 — 定义所有 Agent 的通用接口"""

from abc import ABC, abstractmethod
from pathlib import Path

from api import LLMClient
from core.events import EventBus, EventType


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
            from agent.compact import estimate_tokens
            input_tokens = estimate_tokens(self.messages)
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
        """将单次 token 消耗写入全局 token_stats.db（静默失败）"""
        try:
            from tools.db.token_stats import TokenStatsService
            agent_name = getattr(self, "_agent_name", "system")
            model_id = getattr(self.llm, "model", "")
            model_name = getattr(self.llm, "model_name", "") or model_id
            book = getattr(self, "_book_name", "")
            purpose = getattr(self, "_purpose", "") or agent_name

            ts = TokenStatsService()
            ts.record(
                book=book,
                agent=agent_name,
                model_id=model_id,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                purpose=purpose,
            )
            ts.close()
        except Exception:
            pass  # Token 统计是辅助功能，失败不影响主流程

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
