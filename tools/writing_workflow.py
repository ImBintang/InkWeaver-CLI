"""writing_workflow — 写作 Workflow（纯 chat，无 tools）

由 MuseWorkflow 直接调用，不经过 MuseAgent。
上下文顺序：大纲 → 先验知识（材料清单）→ 前情提要（材料清单）→ 审阅意见
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from api import LLMClient


@dataclass
class WikiItem:
    category: str
    name: str
    content: str


@dataclass
class KnowledgeMaterial:
    rules: list[str] = field(default_factory=list)
    wiki_important: list[WikiItem] = field(default_factory=list)
    wiki_supplement: list[WikiItem] = field(default_factory=list)


@dataclass
class PlotItem:
    name: str
    content: str
    chapters: str = ""


@dataclass
class ChapterItem:
    num: int
    content: str


@dataclass
class PlotMaterial:
    plots: list[PlotItem] = field(default_factory=list)
    chapters: list[ChapterItem] = field(default_factory=list)


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

    @staticmethod
    def _dict_to_knowledge_material(d: dict) -> KnowledgeMaterial:
        """将 workflow 产出的 raw dict 转换为 KnowledgeMaterial"""
        import re
        km = KnowledgeMaterial()
        km.rules = d.get("rules", [])

        def _parse_wiki_items(items: list, full: bool) -> list[WikiItem]:
            result = []
            for item in items:
                # 格式: "## 名称（类别）\n内容"
                m = re.match(r"## (.+?)（(.+?)）\n", item)
                if m:
                    name, cat = m.group(1), m.group(2)
                    result.append(WikiItem(category=cat, name=name, content=item))
                else:
                    result.append(WikiItem(category="", name=item[:20], content=item))
            return result

        km.wiki_important = _parse_wiki_items(d.get("wiki_important", []), full=True)
        km.wiki_supplement = _parse_wiki_items(d.get("wiki_supplement", []), full=False)
        return km

    @staticmethod
    def _dict_to_plot_material(d: dict) -> PlotMaterial:
        """将 workflow 产出的 raw dict 转换为 PlotMaterial"""
        pm = PlotMaterial()
        for item in d.get("plots", []):
            m = __import__("re").match(r"## (.+?)\n", item)
            name = m.group(1) if m else ""
            pm.plots.append(PlotItem(name=name, content=item))
        for item in d.get("chapters", []):
            m = __import__("re").match(r"## 第(\d+)章\n", item)
            num = int(m.group(1)) if m else 0
            pm.chapters.append(ChapterItem(num=num, content=item))
        return pm

    def _build_prior_knowledge_context(self, material: KnowledgeMaterial) -> str:
        """按排版规则组装先验知识上下文"""
        sections = ["## 先验知识"]
        if material.rules:
            rules_text = "\n\n".join(material.rules)
            sections.append(f"### 世界观规则\n{rules_text}")
        if material.wiki_important:
            by_cat = {}
            for item in material.wiki_important:
                by_cat.setdefault(item.category, []).append(item.content)
            cat_order = ["人物", "势力", "地图", "设定图鉴"]
            for cat in cat_order:
                if cat in by_cat:
                    sections.append(f"### {cat}\n" + "\n\n".join(by_cat[cat]))
        if material.wiki_supplement:
            by_cat = {}
            for item in material.wiki_supplement:
                by_cat.setdefault(item.category, []).append(item.content)
            for cat, items in by_cat.items():
                sections.append(f"### {cat}（概要）\n" + "\n\n".join(items))
        return "\n\n".join(sections)

    def _build_plot_context(self, material: PlotMaterial) -> str:
        """按排版规则组装前情提要上下文"""
        sections = ["## 前情提要"]
        if material.plots:
            import re

            def _sort_key(p: PlotItem) -> int:
                m = re.match(r"\d+", p.chapters)
                return int(m.group(0)) if m else 0

            sorted_plots = sorted(material.plots, key=_sort_key)
            plot_texts = [p.content for p in sorted_plots]
            sections.append("### 剧情脉络\n" + "\n\n".join(plot_texts))
        if material.chapters:
            sorted_chs = sorted(material.chapters, key=lambda c: c.num)
            ch_texts = [c.content for c in sorted_chs]
            sections.append("### 章节参考\n" + "\n\n".join(ch_texts))
        return "\n\n".join(sections)

    def run(self, outline: str,
            knowledge_material: Optional[KnowledgeMaterial | dict] = None,
            plot_material: Optional[PlotMaterial | dict] = None,
            review_issues: list = None,
            previous_draft: str = "") -> str:
        """执行写作

        Args:
            outline: 大纲/草稿
            knowledge_material: 先验知识材料（KnowledgeMaterial 实例或 dict）
            plot_material: 前情提要材料（PlotMaterial 实例或 dict）
            review_issues: 上一轮审阅意见列表
            previous_draft: 上一轮被驳回的草稿（重写时传入）

        Returns:
            生成的正文文本
        """
        # 自动转换 dict → dataclass
        if isinstance(knowledge_material, dict):
            knowledge_material = self._dict_to_knowledge_material(knowledge_material)
        if isinstance(plot_material, dict):
            plot_material = self._dict_to_plot_material(plot_material)
        # 按顺序组装上下文
        sections = []
        sections.append(f"## 大纲/草稿\n{outline}")
        if knowledge_material:
            sections.append(self._build_prior_knowledge_context(knowledge_material))
        if plot_material:
            sections.append(self._build_plot_context(plot_material))
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
