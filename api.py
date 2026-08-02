"""LLM API 封装 — OpenAI SDK 单次调用 + 流式调用"""

import os
import socket
from urllib.parse import urlparse

import httpx
from openai import OpenAI


def _is_proxy_available(proxy: str) -> bool:
    """测试代理服务是否可用

    返回 False 是接口语义（探测失败=不可用），调用方按"直连"降级；
    但 socket 必须关闭，避免句柄泄漏。
    """
    sock = None
    try:
        # 解析代理地址
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        parsed = urlparse(proxy)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80

        # 尝试连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        return result == 0
    except Exception:
        return False
    finally:
        if sock is not None:
            sock.close()


def _get_system_proxy() -> str | None:
    """获取系统代理设置"""
    # 检查环境变量
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or \
            os.environ.get("http_proxy") or os.environ.get("https_proxy")
    
    if proxy:
        return proxy
    
    # 检查 Windows 注册表代理
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )
        try:
            proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if proxy_enable:
                proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                return proxy_server
        finally:
            winreg.CloseKey(key)
    except Exception:
        # 读取 Windows 注册表代理失败（无权限/平台差异）：按"无系统代理"
        # 处理并继续直连——这是可探测的降级路径，非静默错误；
        # 若此处是权限问题，HTTP 请求会给出明确的连接错误
        pass

    return None


def _create_http_client() -> httpx.Client:
    """创建 HTTP 客户端，自动处理代理问题
    
    当系统代理（如 Clash/V2Ray）配置了但服务未运行时，
    OpenAI SDK 会尝试通过代理连接导致 WinError 10061 错误。
    此时应禁用代理直接连接。
    """
    proxy = _get_system_proxy()
    
    # 如果存在代理，测试代理是否可用
    if proxy:
        if not _is_proxy_available(proxy):
            # 代理不可用，禁用代理
            print(f"[LLMClient] 代理 {proxy} 不可用，已禁用代理直接连接")
            return httpx.Client(trust_env=False, timeout=httpx.Timeout(30, read=300))
    
    return httpx.Client(timeout=httpx.Timeout(30, read=300))


class LLMClient:
    """封装 OpenAI 兼容接口 调用"""

    # 超时设置（DeepSeek thinking 模式可能需要更长时间）
    MAX_RETRIES = 2  # 网络错误时的最大重试次数

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
        # 校验必填配置
        required_keys = ["key", "url", "model"]
        missing = [k for k in required_keys if k not in config or not config[k]]
        if missing:
            raise ValueError(f"LLMClient 配置缺少必填字段：{', '.join(missing)}")

        # 检测是否需要禁用代理（当代理不可用时）
        http_client = _create_http_client()
        
        self.client = OpenAI(
            api_key=config["key"],
            base_url=config["url"],
            http_client=http_client,
            max_retries=0,  # 我们自己处理重试逻辑
        )
        self.model = config["model"]
        self.max_tokens = config.get("output_max_tokens", 128000)
        self.reasoning_effort = config.get("reasoning_effort", "high")

    def chat(self, messages: list, system_prompt: str, tools: list = None,
              max_tokens: int | None = None, thinking: bool = True,
              reasoning_effort: str | None = None) -> dict:
        """单次 LLM 调用 — 原生 OpenAI 格式

        Args:
            messages: 对话历史（OpenAI 格式）
            system_prompt: 系统提示词
            tools: OpenAI 格式的 tool definitions
            max_tokens: v6.5.3 按调用覆盖输出上限（如妙笔写作字数约束），
                None 时回退到实例配置 output_max_tokens
            thinking: v6.5.6 是否开启思考模式。False 时不传 extra_body/reasoning_effort，
                全部输出预算留给正文（写作类任务防思考吃光 max_tokens）
            reasoning_effort: v6.5.6 思考强度覆盖（"low"/"high"/"max"）。
                None 时回退实例配置 reasoning_effort。妙笔写作保留思考但用 "low"
                压低思考 token 消耗（deepseek-v4-flash 的 low 档真实生效），
                配合提高 max_tokens 保证正文预算

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
            max_tokens=max_tokens or self.max_tokens,
        )
        if thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = reasoning_effort or self.reasoning_effort
        if tools:
            kwargs["tools"] = tools

        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                last_error = e
                error_msg = str(e)
                # 认证错误、参数错误等不需要重试
                if "api_key" in error_msg.lower() or "unauthorized" in error_msg.lower():
                    raise ValueError(f"API 认证失败，请检查 config.yaml 中的 key：{e}") from e
                if "invalid" in error_msg.lower() or "bad request" in error_msg.lower():
                    raise ValueError(f"API 请求参数错误：{e}") from e
                if attempt < self.MAX_RETRIES:
                    import time
                    time.sleep(1)
        else:
            raise ConnectionError(f"API 调用失败（已重试 {self.MAX_RETRIES} 次）：{last_error}") from last_error

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

    def chat_stream(self, messages: list, system_prompt: str, tools: list = None,
                     max_tokens: int | None = None, thinking: bool = True,
                     reasoning_effort: str | None = None):
        """流式 LLM 调用 — 生成器，逐 chunk 产出

        用于 GUI 流式渲染。CLI 继续使用 chat()。

        Args:
            messages: 对话历史（OpenAI 格式）
            system_prompt: 系统提示词
            tools: OpenAI 格式的 tool definitions
            max_tokens: v6.5.3 按调用覆盖输出上限，None 时回退实例配置
            thinking: v6.5.6 是否开启思考模式。False 时全部输出预算留给正文
            reasoning_effort: v6.5.6 思考强度覆盖（"low"/"high"/"max"），
                None 时回退实例配置 reasoning_effort

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
            max_tokens=max_tokens or self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = reasoning_effort or self.reasoning_effort
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
