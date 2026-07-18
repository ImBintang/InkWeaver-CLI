"""上下文管理：Token 估算、压缩追踪、/context 报告（参照 s06）"""

import json
from pathlib import Path

import tiktoken


# 使用 cl100k_base 编码（OpenAI GPT-4 标准，中文估算相对准确）
_ENCODER = tiktoken.get_encoding("cl100k_base")


def estimate_tokens(messages: list) -> int:
    """使用 tiktoken 估算 token 数"""
    text = json.dumps(messages, default=str, ensure_ascii=False)
    return len(_ENCODER.encode(text))


class ContextManager:
    """追踪对话上下文中的章节和技能，提供 /context 报告"""

    def __init__(self):
        self.has_compacted = False
        self.tracked_chapters: list[tuple[str, str]] = []  # [(id, title)]
        self.tracked_skills: list[str] = []

    def track_chapter(self, chapter_ids: list[str], titles: list[str]):
        """记录已读章节"""
        for cid, title in zip(chapter_ids, titles):
            pair = (cid, title)
            if pair not in self.tracked_chapters:
                self.tracked_chapters.append(pair)

    def track_skill(self, skill_name: str):
        """记录已加载技能"""
        if skill_name not in self.tracked_skills:
            self.tracked_skills.append(skill_name)

    def context_report(self, messages: list) -> str:
        """/context 输出"""
        tokens = estimate_tokens(messages)

        lines = [
            f"Token 总量：约 {tokens}",
        ]

        if self.tracked_chapters:
            chapters_str = "，".join(t for _, t in self.tracked_chapters)
            lines.append(f"已有章节：{chapters_str}")
        else:
            lines.append("已有章节：无")

        if self.tracked_skills:
            lines.append(f"已有技能：{', '.join(self.tracked_skills)}")
        else:
            lines.append("已有技能：无")

        if self.has_compacted:
            lines.append("上下文状态：已压缩过")

        return "\n".join(lines)

    def mark_compacted(self):
        self.has_compacted = True
