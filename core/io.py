"""统一 I/O 通道"""

import sys

from core.output import OutputFormatter
from core.session import SessionLogger


class IOChannel:
    """统一所有用户交互

    兼容旧 CLI 接口（print_output, print_info, print_tool_call 等），
    使 JianzhiAgent / MuseWorkflow 可无缝切换。
    """

    def __init__(self, formatter: OutputFormatter, session: SessionLogger | None = None,
                 auto_yes: bool = False):
        self.fmt = formatter
        self.session = session
        self.auto_yes = auto_yes
        self._current_tool_line: str = ""

    @property
    def logger(self):
        """兼容旧 CLI 接口：self.cli.logger"""
        return self.session

    # ---- 输入 ----

    def read_line(self) -> str | None:
        """读取单行输入，解析 \\n 转义。EOF/Ctrl+C 返回 None"""
        try:
            raw = input()
        except (EOFError, KeyboardInterrupt):
            return None
        # 解析 \n 转义为实际换行（GUI 兼容）— 仅转换显式的 \n 转义序列
        text = raw.replace("\\n", "\n").replace("\\t", "\t")
        self._log("USER", text)
        return text

    # ---- 输出（兼容旧 CLI 接口） ----

    def print_output(self, text: str):
        """Agent 输出（对应旧 CLI.print_output）"""
        self.fmt.result(text)
        self._log("AGENT", text)

    def print_info(self, text: str):
        """信息提示（对应旧 CLI.print_info）"""
        self.fmt.info(text)

    def print_thinking(self, text: str):
        """思考状态 — 仅 tty 时显示动画"""
        if sys.stdout.isatty() and not self.fmt.json_mode:
            sys.stdout.write("\r\033[K" + text)
            sys.stdout.flush()

    def print_thinking_done(self, elapsed: float):
        """思考完成"""
        if elapsed < 60:
            msg = f"已思考（耗时 {int(elapsed)} 秒）"
        else:
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            msg = f"已思考（耗时 {minutes} 分 {seconds} 秒）"
        if sys.stdout.isatty() and not self.fmt.json_mode:
            sys.stdout.write("\r\033[K" + msg + "\n")
            sys.stdout.flush()
        self._log("THINK", msg)

    def print_reasoning(self, text: str):
        """打印模型思考过程"""
        if not text or not text.strip():
            return
        if self.fmt.json_mode:
            return
        sys.stdout.write("\033[2m")
        print(f"\n{'─' * 40}")
        print(" 思考过程")
        print(f"{'─' * 40}")
        for line in text.strip().split("\n"):
            print(f" {line}")
        print(f"{'─' * 40}")
        sys.stdout.write("\033[22m")
        sys.stdout.flush()
        self._log("REASON", text[:2000])

    def print_tool_call(self, tool_name: str, brief: str):
        """工具调用提示"""
        self._current_tool_line = f"{tool_name} {brief}"
        if not self.fmt.json_mode:
            sys.stdout.write(self._current_tool_line)
            sys.stdout.flush()

    def print_tool_result(self, msg: str):
        """工具结果"""
        if not self.fmt.json_mode:
            sys.stdout.write(f" -> {msg}\n")
            sys.stdout.flush()
        self._log("TOOL", f"{self._current_tool_line} -> {msg}")

    def print_plan(self, plan_summary: dict):
        """格式化展示提取计划（json 模式下静默）"""
        if self.fmt.json_mode:
            self._log("PLAN", str(plan_summary)[:2000])
            return

        stats = plan_summary.get("stats", {})
        plan = plan_summary.get("plan", {})

        # 显示缺失字段警告
        warnings = plan_summary.get("warnings", [])
        if warnings:
            lines = ["⚠️  计划字段缺失警告："]
            for w in warnings:
                lines.append(f"  • {w}")
            lines.append("")
        else:
            lines = []

        lines += [
            "=" * 50,
            f"📋 知识提取计划 — 范围：第 {plan_summary.get('scope', '?')} 章",
            "=" * 50,
        ]

        if plan.get("new_category"):
            lines.append(f"\n📁 新增类别 ({len(plan['new_category'])} 个)：")
            for item in plan["new_category"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("new_wiki"):
            lines.append(f"\n📝 新增 Wiki ({len(plan['new_wiki'])} 个)：")
            for item in plan["new_wiki"]:
                lines.append(f"  • [{item['category']}] {item['name']}")
                lines.append(f"    章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("edit_wiki"):
            lines.append(f"\n✏️  修改 Wiki ({len(plan['edit_wiki'])} 个)：")
            for item in plan["edit_wiki"]:
                lines.append(f"  • [{item['category']}] {item['name']}")
                lines.append(f"    章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("new_rule"):
            lines.append(f"\n📄 新增规则 ({len(plan['new_rule'])} 个)：")
            for item in plan["new_rule"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("edit_rule"):
            lines.append(f"\n📄 修改规则 ({len(plan['edit_rule'])} 个)：")
            for item in plan["edit_rule"]:
                lines.append(f"  • {item['name']} — {item.get('reason', '')}")

        if plan.get("new_plot"):
            lines.append(f"\n🎬 新增剧情卡片 ({len(plan['new_plot'])} 个)：")
            for item in plan["new_plot"]:
                lines.append(f"  • {item['name']} — 章节：{item.get('chapters', '?')} | 理由：{item.get('reason', '')}")

        if plan.get("edit_plot"):
            lines.append(f"\n🎬 修改剧情卡片 ({len(plan['edit_plot'])} 个)：")
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

    # ---- 确认 ----

    def confirm(self, prompt: str) -> bool:
        """y/n 确认 — auto_yes 时直接返回 True"""
        if self.auto_yes:
            return True
        self.fmt.info(prompt)
        try:
            choice = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return choice == "y"

    # ---- 日志 ----

    def init_logger(self, session_dir, mode: str = "chat", cmd: str = ""):
        """初始化 session 日志"""
        from pathlib import Path
        self.session = SessionLogger(Path(session_dir), mode=mode, cmd=cmd)

    def close_logger(self):
        """关闭日志"""
        if self.session:
            self.session.close()
            self.session = None

    def log_cli(self, cmd: str):
        self._log("CLI", cmd)

    def _log(self, tag: str, text: str):
        if self.session:
            self.session.write(tag, text[:2000])
