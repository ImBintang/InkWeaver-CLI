"""writing_workflow — 写作 Workflow（纯 chat，无 tools）

由 MuseWorkflow 直接调用，不经过 MuseAgent。
上下文顺序：上一章全文 → 大纲 → 先验知识 → 前情提要 → 审阅意见
"""

from pathlib import Path

from api import LLMClient


class WritingWorkflow:
    """写作 Workflow — 纯 chat 调用，组装上下文"""

    def __init__(self, llm: LLMClient, workspace: Path, writer_skill_text: str = "", cli=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}
        self.writer_skill_text = writer_skill_text

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self, outline: str, prior_knowledge: str, plot_summary: str,
            last_chapter: str = "", review_issues: list = None) -> str:
        """执行写作

        Args:
            outline: 大纲/草稿
            prior_knowledge: 先验知识
            plot_summary: 前情提要
            last_chapter: 上一章全文
            review_issues: 上一轮审阅意见列表

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
