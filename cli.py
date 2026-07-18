"""终端 I/O：多行输入、指令识别、输出格式化、Session 日志归档"""

import datetime
import sys
from pathlib import Path


class SessionLogger:
    """Session 日志归档 — 每次启动写入新文件"""

    def __init__(self, session_dir: Path):
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = session_dir / f"session_{timestamp}.log"
        self._file = self.path.open("w", encoding="utf-8")

    def write(self, tag: str, text: str):
        self._file.write(f"[{tag}] {text}\n")
        self._file.flush()

    def close(self):
        self._file.close()


class CLI:
    """终端输入输出处理"""

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

    # ---- 日志 ----

    def _log(self, tag: str, text: str):
        if self.logger:
            self.logger.write(tag, text[:2000])

    def _log_user(self, text: str):
        self._log("USER", text)

    def log_cli(self, cmd: str):
        self._log("CLI", cmd)
