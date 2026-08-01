"""writing_workflow — 写作 Workflow（纯 chat，无 tools）

由 MuseWorkflow 直接调用，不经过 MuseAgent。
上下文顺序：上一章全文 → 大纲 → 先验知识 → 前情提要 → 审阅意见

v5.4: 新增 run_revise() — 手术刀式修改轮，输出 edits JSON 而非全文。
"""

import json
from pathlib import Path

from api import LLMClient
from core.events import EventType


class WritingWorkflow:
    """写作 Workflow — 纯 chat 调用，组装上下文"""

    def __init__(self, llm: LLMClient, workspace: Path, writer_skill_text: str = "", cli=None, bus=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}
        self.writer_skill_text = writer_skill_text
        # 全局事件总线（GUI 模式注入）：注入后 run() 走流式调用，
        # 正文逐 token 发射 TOKEN 事件 → SSE → 前端实时展示写作过程；
        # CLI/修改轮（run_revise）bus=None 保持原有非流式行为
        self.bus = bus

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self, outline: str, prior_knowledge: str = "", plot_summary: str = "",
            last_chapter: str = "", review_issues: list = None,
            previous_draft: str = "", memory_block: str = "") -> str:
        """执行写作

        Args:
            outline: 大纲/草稿
            prior_knowledge: 先验知识（LLM 压缩重写后的结构化文本）
            plot_summary: 前情提要（LLM 压缩重写后的结构化文本）
            last_chapter: 上一章全文
            review_issues: 上一轮审阅意见列表
            previous_draft: 上一轮被驳回的草稿（重写时传入）
            memory_block: v5.3 记忆注入（style 类）

        Returns:
            生成的正文文本
        """
        # 按顺序组装上下文
        sections = []

        if last_chapter:
            sections.append(f"## 上一章全文\n{last_chapter}")

        sections.append(f"## 大纲/草稿\n{outline}")

        if prior_knowledge:
            sections.append(f"## 先验知识\n{prior_knowledge}")

        if plot_summary:
            sections.append(f"## 前情提要\n{plot_summary}")
        if previous_draft:
            sections.append(f"## 上一轮草稿（需根据审阅意见修改）\n{previous_draft}")
        if review_issues:
            issues_lines = []
            for i in review_issues:
                level = i.get('level', '用户')
                desc = i['description']
                sug = i.get('suggestion', '')
                if sug:
                    issues_lines.append(f"- [{level}] {desc}\n  → 建议：{sug}")
                else:
                    issues_lines.append(f"- [{level}] {desc}")
            sections.append(f"## 上一轮审阅意见\n" + "\n".join(issues_lines))

        # v5.3: 记忆注入（style 类）
        if memory_block:
            sections.append(memory_block)

        context = "\n\n".join(sections)

        # 优先使用外部传入的 skill 文本，否则用内置兜底
        if self.writer_skill_text:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说写作助手。\n"
                "你的风格细腻、克制、充满'活人感'。你拒绝网文化、碎片化表达和AI式陈词滥调。\n"
                "\n"
                "# Skill 指令\n"
                f"{self.writer_skill_text}"
            )
        else:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说写作助手。\n"
                "你的风格细腻、克制、充满'活人感'。你拒绝网文化、碎片化表达和AI式陈词滥调。\n"
                "\n"
                "## 核心规则\n"
                "1. 严格遵循'前因 → 发展 → 高潮 → 结局'叙事架构\n"
                "2. 长句为主，短句占比不超过8%\n"
                "3. 禁用词汇：兜住、接住、稳、守、极其、扭曲、疯狂、空洞、麻木\n"
                "4. 禁用句式：'想说什么…喉咙却发不出声''……就够了''心脏像是被……攥紧'\n"
                "5. 禁用关联词：不是……而是……、既……又……、即使……也……\n"
                "6. 禁止倒叙、禁止括号、禁止代码块\n"
                "7. 心理描写点到为止，多用白描\n"
                "8. 直接输出正文，不要使用任何 Markdown 语法\n"
                "9. 不要输出任何外部解释或上下文说明"
            )

        messages = [{"role": "user", "content": context}]
        if self.bus is not None:
            # 流式模式：正文逐 token 推送前端，让用户实时看到写作过程
            response = None
            for chunk in self.llm.chat_stream(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
            ):
                if chunk["type"] == "token":
                    try:
                        self.bus.emit(EventType.TOKEN, {"text": chunk["text"]}, source="muse")
                    except Exception:
                        pass  # 事件上报失败不阻断写作
                elif chunk["type"] == "done":
                    response = chunk
            if response and response.get("usage"):
                self._last_usage = response["usage"]
            result = ((response.get("content") if response else "") or "").strip()
        else:
            response = self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
            )
            if "usage" in response:
                self._last_usage = response["usage"]
            result = response.get("content", "").strip()

        self._log("WRITING_WF_END", result[:200])
        return result

    def run_revise(self, draft: str, review_issues: list,
                   outline: str = "", last_chapter: str = "",
                   change_log: list[str] | None = None,
                   memory_block: str = "") -> tuple[str, list[str]]:
        """执行手术刀式修改轮（v5.4）

        LLM 输出 edits JSON，后端 apply 到 draft 上。
        如果 LLM 输出纯文本（fallback），则视为全文重写。

        Args:
            draft: 当前草稿全文
            review_issues: 审阅意见列表
            outline: 大纲（供参考）
            last_chapter: 上一章全文（供参考衍接）
            change_log: 上轮变更日志
            memory_block: 记忆注入

        Returns:
            (new_draft, change_log)
        """
        from tools.muse_edits import apply_writer_edits, parse_edits_response

        # 组装上下文
        sections = []
        if last_chapter:
            sections.append(f"## 上一章全文\n{last_chapter}")
        if outline:
            sections.append(f"## 大纲/草稿\n{outline}")
        sections.append(f"## 上一轮草稿（需根据审阅意见修改）\n{draft}")

        # 审阅意见
        if review_issues:
            issues_lines = []
            for i in review_issues:
                level = i.get('level', '用户')
                desc = i['description']
                sug = i.get('suggestion', '')
                if sug:
                    issues_lines.append(f"- [{level}] {desc}\n  → 建议：{sug}")
                else:
                    issues_lines.append(f"- [{level}] {desc}")
            sections.append("## 审阅意见\n" + "\n".join(issues_lines))

        # 上轮变更记录
        if change_log:
            sections.append("## 上轮修改记录\n" + "\n".join(f"- {c}" for c in change_log))

        # 记忆注入
        if memory_block:
            sections.append(memory_block)

        context = "\n\n".join(sections)

        # 使用修改轮 skill
        if self.writer_skill_text:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说修改专家。\n"
                "你的任务是精确修正草稿中的问题，而非重写全文。\n"
                "\n"
                "# Skill 指令\n"
                f"{self.writer_skill_text}"
            )
        else:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说修改专家。\n"
                "输出格式必须是 JSON：{\"edits\": [...], \"notes\": \"...\"}\n"
                "工具：replace_text / delete_text / rewrite_paragraph\n"
                "只改有问题的地方，不要输出全文。"
            )

        messages = [{"role": "user", "content": context}]
        response = self.llm.chat(
            messages=messages,
            system_prompt=system_prompt,
            tools=None,
        )

        if "usage" in response:
            self._last_usage = response["usage"]

        raw_output = response.get("content", "").strip()
        self._log("REVISE_WF_END", raw_output[:200])

        # 解析 LLM 输出
        edits, mode = parse_edits_response(raw_output)

        if mode == "edits" and edits:
            # 手术刀模式：应用编辑指令
            new_draft, changes = apply_writer_edits(draft, edits)
            return new_draft, changes
        else:
            # Fallback：LLM 输出了全文（旧模式兼容）
            # 简单生成 change_log
            changes = ["[全文重写] LLM 未输出 edits JSON，回退到全文模式"]
            return raw_output, changes
