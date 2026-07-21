"""正文后处理工具"""

import re


def polish_draft(text: str) -> str:
    """后处理 Writer 产出的草稿：
    1. strip 首尾空白
    2. 删除所有以 # 开头的行（AI 自加标题）
    3. 全角置换标点符号
    """
    # 1. strip
    text = text.strip()

    # 2. 删除标题行
    lines = text.split("\n")
    lines = [l for l in lines if not l.strip().startswith("#")]
    text = "\n".join(lines).strip()

    # 3. 全角置换
    replacements = [
        ('"', "\u201c"),  # 左双引号
        ('"', "\u201d"),  # 右双引号
        ("'", "\u2018"),  # 左单引号
        ("'", "\u2019"),  # 右单引号
        ("---", "\u2014\u2014"),  # 双破折号
        ("--", "\u2014"),  # 单破折号
        ("...", "\u2026\u2026"),  # 省略号
        (":", "\uff1a"),  # 冒号
    ]

    for char, replacement in replacements:
        text = text.replace(char, replacement)

    return text
