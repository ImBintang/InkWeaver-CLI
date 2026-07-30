"""审阅会话 — 管理一次审阅的状态"""

from dataclasses import dataclass, field


@dataclass
class ReviewSession:
    """一次审阅会话的状态，由 report_issue/review_done 工具共享

    v5.4: 新增 previous_issues 供增量审阅上下文注入。
    """
    issues: list = field(default_factory=list)
    previous_issues: list = field(default_factory=list)  # 上轮审阅意见
    previous_score: int | None = None  # 上轮分数（仅供参考展示）

    def report_issue(self, level: int, quote: str, description: str, suggestion: str) -> str:
        """记录一条审阅问题"""
        self.issues.append({
            "level": level,
            "quote": quote,
            "description": description,
            "suggestion": suggestion,
        })
        return f"已记录问题：{description}"

    def review_done(self) -> dict:
        """审阅结束，计算得分"""
        score = 100
        for issue in self.issues:
            level = issue["level"]
            if level == 0:
                score -= 20
            elif level == 1:
                score -= 10
            elif level == 2:
                score -= 5
            elif level == 3:
                score -= 3
        score = max(0, score)

        return {
            "score": score,
            "pass": score >= 85,
            "issues": self.issues,
        }

    def clear(self):
        """清空 issue 列表（下一轮重写后调用）"""
        self.issues.clear()
