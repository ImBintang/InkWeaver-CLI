"""终端 I/O — 向后兼容入口

核心实现在 core/ 包中：
- core.io.IOChannel：统一 I/O 通道
- core.session.SessionLogger：会话日志
- core.output.OutputFormatter：输出格式化

本模块保留 CLI 类与 SessionLogger 符号，供旧代码兼容引用。
新代码请直接从 core 包导入。
"""

import datetime
import sys
from pathlib import Path

# 向后兼容：SessionLogger 即 core.session.SessionLogger
from core.session import SessionLogger  # noqa: F401


class CLI:
    """终端输入输出处理 — 向后兼容包装

    功能已迁移至 core.io.IOChannel，本类保留以兼容旧接口。
    """

    def __init__(self):
        self.logger: SessionLogger | None = None
        self._current_tool_line: str = ""

    def init_logger(self, session_dir: Path):
        self.logger = SessionLogger(session_dir)

    def close_logger(self):
        if self.logger:
            self.logger.close()
            self.logger = None

    # ---- 输入 ----

    def read_input(self) -> tuple[str | None, bool | None]:
        """读取用户输入

        Returns:
            (text, is_cmd): 文本内容 + 是否指令
            (None, None): 用户输入 exit
        """
        lines = []
        is_first_line = True

        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                return (None, None)

            stripped = line.strip()

            # exit 检测（大小写不敏感）
            if stripped.lower() == "exit":
                return (None, None)

            # 第一行指令检测
            if is_first_line and stripped.startswith("/"):
                cmd_text = stripped[1:]  # 去掉 /
                self._log_user(f"/{cmd_text}")
                return (cmd_text, True)

            # qqq 结束监听
            if stripped.lower() == "qqq":
                result = "\n".join(lines)
                self._log_user(result)
                return (result, False)

            lines.append(line)
            is_first_line = False

    # ---- 输出 ----

    def print_thinking(self, text: str):
        """思考输出 — 同一行覆盖写"""
        sys.stdout.write("\r\033[K" + text)
        sys.stdout.flush()

    def print_thinking_done(self, elapsed: float):
        """思考结束提示"""
        if elapsed < 60:
            msg = f"已思考（耗时 {int(elapsed)} 秒）"
        else:
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            msg = f"已思考（耗时 {minutes} 分 {seconds} 秒）"
        sys.stdout.write("\r\033[K" + msg + "\n")
        sys.stdout.flush()
        self._log("THINK", msg)

    def print_reasoning(self, text: str):
        """打印模型的完整思考过程"""
        if not text or not text.strip():
            return
        sys.stdout.write("\033[2m")  # 暗淡色
        print(f"\n{'─' * 40}")
        print(" 思考过程")
        print(f"{'─' * 40}")
        for line in text.strip().split("\n"):
            print(f" {line}")
        print(f"{'─' * 40}")
        sys.stdout.write("\033[22m")  # 恢复正常
        sys.stdout.flush()
        self._log("REASON", text[:2000])

    def print_tool_call(self, tool_name: str, brief: str):
        """工具调用 — 当前行输出工具名+说明，不换行"""
        self._current_tool_line = f"{tool_name} {brief}"
        sys.stdout.write(self._current_tool_line)
        sys.stdout.flush()

    def print_tool_result(self, msg: str):
        """工具调用结果 — 追加到当前工具调用行尾"""
        sys.stdout.write(f" -> {msg}\n")
        sys.stdout.flush()
        self._log("TOOL", f"{self._current_tool_line} -> {msg}")

    def print_output(self, text: str):
        """普通输出 — 直接打印，正常换行"""
        print(text)
        self._log("AGENT", text)

    def print_info(self, text: str):
        """信息提示（非 Agent 输出，如 CLI 指令结果）"""
        print(text)

    def print_plan(self, plan_summary: dict):
        """格式化展示提取计划"""
        stats = plan_summary.get("stats", {})
        plan = plan_summary.get("plan", {})

        # 显示缺失字段警告
        warnings = plan_summary.get("warnings", [])
        if warnings:
            lines = ["[警告] 计划字段缺失警告："]
            for w in warnings:
                lines.append(f"  • {w}")
            lines.append("")
        else:
            lines = []

        lines += [
            "=" * 50,
            f"知识提取计划 — 范围：第 {plan_summary.get('scope', '?')} 章",
            "=" * 50,
        ]

        if plan.get("new_category"):
            lines.append(f"\n新增类别 ({len(plan['new_category'])} 个)：")
            for item in plan["new_category"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("new_wiki"):
            lines.append(f"\n新增 Wiki ({len(plan['new_wiki'])} 个)：")
            for item in plan["new_wiki"]:
                lines.append(f"  • [{item['category']}] {item['name']}")
                lines.append(f"    章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("edit_wiki"):
            lines.append(f"\n修改 Wiki ({len(plan['edit_wiki'])} 个)：")
            for item in plan["edit_wiki"]:
                lines.append(f"  • [{item['category']}] {item['name']}")
                lines.append(f"    章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("new_rule"):
            lines.append(f"\n新增规则 ({len(plan['new_rule'])} 个)：")
            for item in plan["new_rule"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("edit_rule"):
            lines.append(f"\n修改规则 ({len(plan['edit_rule'])} 个)：")
            for item in plan["edit_rule"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("new_plot"):
            lines.append(f"\n新增剧情卡片 ({len(plan['new_plot'])} 个)：")
            for item in plan["new_plot"]:
                lines.append(f"  • {item['name']} — 章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("edit_plot"):
            lines.append(f"\n修改剧情卡片 ({len(plan['edit_plot'])} 个)：")
            for item in plan["edit_plot"]:
                lines.append(f"  • {item['name']} — 章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        lines.extend([
            "",
            "-" * 50,
            "是否执行此计划？(y/n)",
            "  y  — 确认执行",
            "  n  — 打回，输入理由",
        ])

        for line in lines:
            self.print_info(line)

    # ---- 日志 ----

    def _log(self, tag: str, text: str):
        if self.logger:
            self.logger.write(tag, text[:2000])

    def _log_user(self, text: str):
        self._log("USER", text)

    def log_cli(self, cmd: str):
        self._log("CLI", cmd)
