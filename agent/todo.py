"""Todo 会话计划管理（参照 s03）"""

from dataclasses import dataclass, field

PLAN_REMINDER_INTERVAL = 3
MAX_ITEMS = 12


@dataclass
class PlanItem:
    content: str
    status: str = "pending"  # pending | in_progress | completed
    active_form: str = ""


class TodoManager:
    """轻量会话计划，最多 12 项，最多 1 个 in_progress"""

    def __init__(self):
        self.items: list[PlanItem] = field(default_factory=list)
        self.rounds_since_update: int = 0

    def update(self, items: list[dict]) -> str:
        """更新 Todo 列表

        Args:
            items: [
                {"content": "...", "status": "pending|in_progress|completed", "activeForm": ""}
            ]

        Returns:
            render() 后的文本展示
        """
        if len(items) > MAX_ITEMS:
            raise ValueError(f"最多 {MAX_ITEMS} 项")

        normalized = []
        in_progress_count = 0
        for idx, raw in enumerate(items):
            content = str(raw.get("content", "")).strip()
            status = str(raw.get("status", "pending")).lower()
            active_form = str(raw.get("activeForm", "")).strip()

            if not content:
                raise ValueError(f"第 {idx + 1} 项缺少 content")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"第 {idx + 1} 项状态无效: {status}")
            if status == "in_progress":
                in_progress_count += 1

            normalized.append(PlanItem(
                content=content,
                status=status,
                active_form=active_form,
            ))

        if in_progress_count > 1:
            raise ValueError("最多 1 项 in_progress")

        self.items = normalized
        self.rounds_since_update = 0
        return self.render()

    def note_round_without_update(self):
        self.rounds_since_update += 1

    def reminder(self) -> str | None:
        """超过 3 轮未更新返回提醒"""
        if not self.items:
            return None
        if self.rounds_since_update < PLAN_REMINDER_INTERVAL:
            return None
        return "<reminder>请刷新当前计划后再继续。</reminder>"

    def render(self) -> str:
        """渲染 Todo 文本"""
        if not self.items:
            return "（暂无计划）"

        lines = []
        for item in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[item.status]
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)

        completed = sum(1 for i in self.items if i.status == "completed")
        lines.append(f"\n({completed}/{len(self.items)} 已完成)")
        return "\n".join(lines)
