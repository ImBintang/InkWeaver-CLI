"""LLM API 封装 — OpenAI SDK 单次调用"""

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
