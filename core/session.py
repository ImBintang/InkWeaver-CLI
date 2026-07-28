"""Session 日志归档"""

import datetime
from pathlib import Path


class SessionLogger:
    """Session 日志 — 每次启动写入新文件，首行 META 标注模式"""

    def __init__(self, session_dir: Path, mode: str = "chat", cmd: str = ""):
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = session_dir / f"session_{timestamp}.log"
        self._file = self.path.open("w", encoding="utf-8")
        # META 首行
        if cmd:
            self._file.write(f"[META] mode={mode} cmd={cmd}\n")
        else:
            self._file.write(f"[META] mode={mode}\n")
        self._file.flush()

    def write(self, tag: str, text: str):
        self._file.write(f"[{tag}] {text}\n")
        self._file.flush()

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()
