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

    def chat(self, messages: list, system_prompt: str, tools: list = None) -> dict:
        """单次 LLM 调用

        Args:
            messages: 对话历史
            system_prompt: 系统提示词
            tools: OpenAI 格式的 tool definitions

        Returns:
            {
                "content": [...],      # response.choices[0].message.content 的块列表
                "stop_reason": str,     # "tool_use" | "stop" | "max_tokens"
                "usage": {...}          # token 用量
            }
        """
        # system prompt 作为 system 消息插入到 messages 头部
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs = dict(
            model=self.model,
            messages=full_messages,
            max_tokens=self.max_tokens,
        )
        if tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # 归一化 content 为块列表
        content_blocks = []
        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})
        if msg.tool_calls:
            for tc in msg.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": tc.function.arguments,  # JSON string
                })

        if finish_reason == "length":
            stop_reason = "max_tokens"
        elif msg.tool_calls:
            stop_reason = "tool_use"
        else:
            stop_reason = "stop"

        return {
            "content": content_blocks,
            "stop_reason": stop_reason,
            "usage": dict(response.usage) if response.usage else {},
        }
