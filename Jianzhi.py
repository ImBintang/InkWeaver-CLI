"""鉴知 Agent — 组装 system prompt、tool defs、工具路由"""

import json
from pathlib import Path

from api import LLMClient
from agent.todo import TodoManager
from agent.compact import ContextManager, estimate_tokens
from agent.skill import SkillRegistry
from agent.loop import agent_loop
from tools import chapter as chapter_tools
from tools import workspace as workspace_tools


TOOL_RESULTS_DIR = Path(".task_outputs") / "tool-results"


class JianzhiAgent:
    """写作智能体 — 组装各模块并提供统一入口"""

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, cli):
        self.cli = cli
        self.workspace = workspace
        self.llm = LLMClient(config["api"])
        self.todo = TodoManager()
        self.context = ContextManager()
        self.skills = SkillRegistry(skills_dir)
        self.messages: list = []
        self._last_usage = {}
        # Token 累计统计
        self._token_accum = {"input": 0, "output": 0, "total": 0}

        self.system_prompt = self._build_system_prompt()
        self.tool_defs = self._build_tool_defs()

    def _build_system_prompt(self) -> str:
        """组装 system prompt"""
        parts = [
            f"你是鉴知（Jianzhi），一个专业的写作智能体。",
            f"当前工作区：{self.workspace.name}",
            f"当前目录：{self.workspace}",
            "",
            "# 可用技能",
            self.skills.describe_available(),
            "",
            "# 工具使用指南",
            "- 使用 read_chapters 读取章节正文",
            "- 使用 chapter_list 查看章节列表",
            "- 使用 keywords_stat 统计关键词词频",
            "- 使用 update_todo 管理当前任务计划",
            "- 使用 agent_output 进行中间轮输出",
            "- 使用 tools_log_check 查询被压缩的工具调用记录",
            "- 通过技能名调用对应技能",
            "",
            "# 规则",
            "- 需要多步骤工作时，先用 update_todo 制定计划",
            "- 每次只处理一个 in_progress 步骤",
            "- 完成后标记为 completed 并推进下一步",
        ]
        return "\n".join(parts)

    def _build_tool_defs(self) -> list[dict]:
        """组装 OpenAI 格式 tool definitions"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "update_todo",
                    "description": "更新会话计划列表。多步骤工作前先制定计划。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "计划项列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string", "description": "计划内容"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                            "description": "pending=待办, in_progress=进行中, completed=已完成",
                                        },
                                        "activeForm": {"type": "string", "description": "当前步骤的具体操作描述"},
                                    },
                                    "required": ["content", "status"],
                                },
                            }
                        },
                        "required": ["items"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tools_log_check",
                    "description": "查询被压缩的历史工具调用记录",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_use_id": {
                                "type": "string",
                                "description": "要查询的工具调用 ID",
                            }
                        },
                        "required": ["tool_use_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "agent_output",
                    "description": "中间轮输出。调用后直接输出文本，不打断会话流程。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要输出的文本"},
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_chapters",
                    "description": "读取指定章节的正文。支持范围表达式如 \"1-3,5,7-9\"。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {
                                "type": "string",
                                "description": "章节范围，如 \"1-3,5,7-9\"",
                            }
                        },
                        "required": ["chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "keywords_stat",
                    "description": "分章节统计指定关键词的词频",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "章节范围"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "关键词列表",
                            },
                        },
                        "required": ["chapters", "keywords"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "chapter_list",
                    "description": "获取当前工作区的章节列表（含标题）",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
        ]

        # 为每个 skill 生成一个工具
        for skill_name in self.skills.skill_names():
            tools.append({
                "type": "function",
                "function": {
                    "name": skill_name,
                    "description": f"调用技能「{skill_name}」",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            })

        return tools

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        # skill 调用
        if name in self.skills.skill_names():
            self.context.track_skill(name)
            return self.skills.load_full_text(name)

        dispatch = {
            "update_todo": self._handle_todo,
            "tools_log_check": self._handle_tools_log_check,
            "agent_output": lambda **kw: "(已输出)",
            "read_chapters": self._handle_read_chapters,
            "keywords_stat": self._handle_keywords_stat,
            "chapter_list": self._handle_chapter_list,
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    # ---- 工具 handlers ----
    def _handle_todo(self, items: list) -> str:
        return self.todo.update(items)

    def _handle_tools_log_check(self, tool_use_id: str) -> str:
        path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            return text[:30000]
        return f"未找到工具调用记录：{tool_use_id}"

    def _handle_read_chapters(self, chapters: str) -> str:
        result = chapter_tools.read_chapters(self.workspace, chapters)
        # 追踪已读章节
        nums = chapter_tools.parse_chapter_spec(chapters)
        titles = []
        for n in nums:
            t, _ = chapter_tools._read_chapter_file(self.workspace / "document", n)
            titles.append(t or f"第{n}章")
        self.context.track_chapter(
            [str(n) for n in nums],
            titles,
        )
        return result

    def _handle_keywords_stat(self, chapters: str, keywords: list) -> str:
        return chapter_tools.keywords_stat(self.workspace, chapters, keywords)

    def _handle_chapter_list(self) -> str:
        return chapter_tools.chapter_list(self.workspace)

    # ---- 消息归一化 ----
    def _normalize_messages(self, messages: list) -> list:
        """清理消息列表（OpenAI 格式）"""
        cleaned = []
        for msg in messages:
            clean = {"role": msg["role"]}
            # 复制 content（string 或 None）
            content = msg.get("content")
            if content is not None:
                clean["content"] = content
            # 保留 tool_calls（仅 assistant 消息有）
            if "tool_calls" in msg:
                clean["tool_calls"] = msg["tool_calls"]
            # 保留 tool_call_id（仅 tool 消息有）
            if "tool_call_id" in msg:
                clean["tool_call_id"] = msg["tool_call_id"]
            cleaned.append(clean)

        # 合并同角色连续消息（仅合并 content 文本）
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

    # ---- 公共方法 ----
    def chat(self, user_input: str):
        """处理一条用户输入"""
        self.messages.append({"role": "user", "content": user_input})
        self.messages = agent_loop(self, self.messages)

        # 打印最终输出（取最后一条 assistant 的文本回复）
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"].strip()
                if text:
                    self.cli.print_output(text)
                break

    def _accumulate_tokens(self, usage: dict):
        """累加 token 用量并记录日志

        优先使用 API 返回的 usage，缺失时用 tiktoken 估算。
        """
        if not usage:
            return

        # API 返回的 usage 字段（OpenAI 格式）
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")

        if prompt_tokens is not None and completion_tokens is not None:
            input_tokens = prompt_tokens
            output_tokens = completion_tokens
        else:
            # 无法从 API 获取，用 tiktoken 估算当前消息
            from agent.compact import estimate_tokens
            input_tokens = estimate_tokens(self.messages)
            output_tokens = 0

        total = input_tokens + output_tokens
        self._token_accum["input"] += input_tokens
        self._token_accum["output"] += output_tokens
        self._token_accum["total"] += total

        # 写入日志
        if self.cli.logger:
            self.cli.logger.write(
                "TOKEN",
                f"本次: input={input_tokens}, output={output_tokens}, total={total} | "
                f"累计: input={self._token_accum['input']}, output={self._token_accum['output']}, total={self._token_accum['total']}"
            )

    def token_report(self) -> str:
        """/token 指令 — 返回累计 token 统计"""
        lines = [
            f"Token 用量统计（本次会话）：",
            f"  输入: {self._token_accum['input']}",
            f"  输出: {self._token_accum['output']}",
            f"  总计: {self._token_accum['total']}",
        ]
        # 如果有 API 最近一次返回的 usage，也显示
        if self._last_usage:
            lines.append(f"  （最近一次 API: {self._last_usage}）")
        return "\n".join(lines)

    def clear_context(self):
        """清空上下文（/clear 指令）"""
        self.messages = []
        self.cli.print_info("上下文已清空。")

    def context_report(self) -> str:
        """/context 指令"""
        return self.context.context_report(self.messages)

    def compact_history(self):
        """主动压缩（/compact 指令）"""
        self.context.mark_compacted()
        self.messages = [{
            "role": "user",
            "content": "（上下文已压缩，继续当前工作）"
        }]
        self.cli.print_info("上下文已压缩。")
