"""输出格式化 — 人类可读 / JSON 双模式"""

import json
import sys


class OutputFormatter:
    """根据 --json flag 切换输出模式"""

    def __init__(self, json_mode: bool = False, color: bool = True):
        self.json_mode = json_mode
        # color 参数保留接口兼容性，当前未启用

    def result(self, text: str):
        """最终结果输出"""
        if not self.json_mode:
            print(text)

    def info(self, text: str):
        """信息提示（json 模式下静默）"""
        if not self.json_mode:
            print(text)

    def error(self, text: str):
        """错误信息"""
        if self.json_mode:
            print(json.dumps({"status": "error", "message": text}, ensure_ascii=False))
        else:
            print(f"错误：{text}", file=sys.stderr)

    def summary(self, answer: str, tools_called: list | None = None,
                kb_queried: list | None = None, tokens: dict | None = None,
                elapsed: float = 0.0):
        """统计摘要输出"""
        if self.json_mode:
            data = {
                "status": "success",
                "answer": answer,
                "tools_called": tools_called or [],
                "kb_queried": kb_queried or [],
                "tokens": tokens,
                "elapsed_seconds": round(elapsed, 2),
            }
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(answer)
            if tokens:
                # v7.0.1: tokens 字典可能缺部分字段（如仅 total），用 get 兜底避免 KeyError
                print(f"\n--- Token: 输入={tokens.get('input', 0)} 输出={tokens.get('output', 0)} "
                      f"总计={tokens.get('total', 0)} | 耗时 {elapsed:.1f}s ---")
