"""LLM API 封装 — OpenAI SDK 单次调用 + 流式调用"""

from openai import OpenAI


class LLMClient:
    """封装 OpenAI 兼容接口的 chat 调用"""

    def __init__(self, config: dict):
        """
        config 结构:
        {
            "url": "https://api.deepseek.com",
            "key": "sk-xxx",
            "model": "deepseek-v4-flash",
            "output_max_tokens": 128000,
        }
        """
        self.client = OpenAI(
            api_key=config["key"],
            base_url=config["url"],
        )
        self.model = config["model"]
        self.max_tokens = config.get("output_max_tokens", 128000)
        self.reasoning_effort = config.get("reasoning_effort", "high")

    def chat(self, messages: list, system_prompt: str, tools: list = None) -> dict:
        """单次 LLM 调用 — 原生 OpenAI 格式

        Args:
            messages: 对话历史（OpenAI 格式）
            system_prompt: 系统提示词
            tools: OpenAI 格式的 tool definitions

        Returns:
            {
                "content": str | None,  # 文本回复
                "reasoning_content": str | None,  # 思考过程
                "tool_calls": list | None,  # OpenAI 原生 tool_calls 对象
                "stop_reason": str,     # "tool_use" | "stop" | "max_tokens"
                "usage": {...}          # token 用量
            }
        """
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs = dict(
            model=self.model,
            messages=full_messages,
            max_tokens=self.max_tokens,
            extra_body={"thinking": {"type": "enabled"}},
            reasoning_effort=self.reasoning_effort,
        )
        if tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # 提取 reasoning_content（DeepSeek 思考过程）
        reasoning_content = getattr(msg, "reasoning_content", None)

        # 提取原生 tool_calls（OpenAI 格式）
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,  # JSON string
                    },
                }
                for tc in msg.tool_calls
            ]

        if finish_reason == "length":
            stop_reason = "max_tokens"
        elif msg.tool_calls:
            stop_reason = "tool_use"
        else:
            stop_reason = "stop"

        return {
            "content": msg.content,
            "reasoning_content": reasoning_content,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": dict(response.usage) if response.usage else {},
        }

    def chat_stream(self, messages: list, system_prompt: str, tools: list = None):
        """流式 LLM 调用 — 生成器，逐 chunk 产出

        用于 GUI 流式渲染。CLI 继续使用 chat()。

        Args:
            messages: 对话历史（OpenAI 格式）
            system_prompt: 系统提示词
            tools: OpenAI 格式的 tool definitions

        Yields:
            {"type": "token", "text": "..."}             # 文本 token
            {"type": "reasoning", "text": "..."}         # 思考 token
            {"type": "done", "content": str|None, "reasoning_content": str|None,
             "tool_calls": list|None, "stop_reason": str, "usage": dict}
        """
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs = dict(
            model=self.model,
            messages=full_messages,
            max_tokens=self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "enabled"}},
            reasoning_effort=self.reasoning_effort,
        )
        if tools:
            kwargs["tools"] = tools

        stream = self.client.chat.completions.create(**kwargs)

        # 累积缓冲区
        content_buf = []
        reasoning_buf = []
        tool_calls_buf: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
        finish_reason = None
        usage = {}

        for chunk in stream:
            # usage 在最后一个 chunk 中返回（stream_options.include_usage）
            if chunk.usage:
                usage = dict(chunk.usage)

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason or finish_reason

            # 文本 token
            if delta.content:
                content_buf.append(delta.content)
                yield {"type": "token", "text": delta.content}

            # 思考 token（DeepSeek reasoning_content）
            reasoning_piece = getattr(delta, "reasoning_content", None)
            if reasoning_piece:
                reasoning_buf.append(reasoning_piece)
                yield {"type": "reasoning", "text": reasoning_piece}

            # 工具调用（流式中分片到达，需累积）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": tc_delta.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_calls_buf[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["function"]["arguments"] += tc_delta.function.arguments

        # 流结束，产出汇总结果
        tool_calls = None
        if tool_calls_buf:
            tool_calls = [tool_calls_buf[i] for i in sorted(tool_calls_buf.keys())]

        if finish_reason == "length":
            stop_reason = "max_tokens"
        elif tool_calls:
            stop_reason = "tool_use"
        else:
            stop_reason = "stop"

        yield {
            "type": "done",
            "content": "".join(content_buf) or None,
            "reasoning_content": "".join(reasoning_buf) or None,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": usage,
        }
