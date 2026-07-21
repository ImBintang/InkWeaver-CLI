"""妙笔工作区管理"""

import json
from pathlib import Path
from datetime import datetime


class MuseIO:
    """管理 muse/ 目录的创建和文件写入"""

    def __init__(self, workspace: Path):
        self.muse_dir = workspace / "muse"
        self.muse_dir.mkdir(parents=True, exist_ok=True)
        self.task_dir = self._create_task_dir()
        self.round = 1

    def _create_task_dir(self) -> Path:
        """创建 YYYY-MM-DD_NNN 目录"""
        today = datetime.now().strftime("%Y-%m-%d")
        index = 1
        while (self.muse_dir / f"{today}_{index:03d}").exists():
            index += 1
        task_dir = self.muse_dir / f"{today}_{index:03d}"
        task_dir.mkdir(parents=True)
        return task_dir

    def save_outline(self, text: str):
        (self.task_dir / "outline.txt").write_text(text, encoding="utf-8")

    def save_prior_knowledge(self, text: str):
        (self.task_dir / "prior_knowledge.md").write_text(text, encoding="utf-8")

    def save_plot_summary(self, text: str):
        (self.task_dir / "plot_summary.md").write_text(text, encoding="utf-8")

    def save_draft(self, text: str):
        round_dir = self.task_dir / f"review_round_{self.round}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "draft.txt").write_text(text, encoding="utf-8")

    def save_review(self, text: str):
        round_dir = self.task_dir / f"review_round_{self.round}"
        (round_dir / "review.md").write_text(text, encoding="utf-8")

    def next_round(self):
        self.round += 1

    def save_final(self, text: str):
        (self.task_dir / "final.txt").write_text(text, encoding="utf-8")

    def save_session_log(self, messages: list):
        """向 session.log 追加一次 LLM 对话的全量记录（可读格式）"""
        path = self.task_dir / "session.log"
        with open(path, "a", encoding="utf-8") as f:
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                tool_call_id = msg.get("tool_call_id", "")

                if role == "user":
                    f.write(f"[USER] {content}\n\n")
                elif role == "assistant":
                    if tool_calls:
                        f.write(f"[ASSISTANT] {content}\n")
                        for tc in tool_calls:
                            name = tc["function"]["name"]
                            args = tc["function"]["arguments"]
                            f.write(f"  └─ TOOL_CALL: {name}({args})\n")
                        f.write("\n")
                    else:
                        f.write(f"[ASSISTANT] {content}\n\n")
                elif role == "tool":
                    preview = content[:200].replace("\n", "\\n")
                    if len(content) > 200:
                        preview += "..."
                    f.write(f"[TOOL_RESULT] ({tool_call_id}) {preview}\n\n")

    @property
    def prior_knowledge_path(self) -> Path:
        return self.task_dir / "prior_knowledge.md"

    @property
    def plot_summary_path(self) -> Path:
        return self.task_dir / "plot_summary.md"
