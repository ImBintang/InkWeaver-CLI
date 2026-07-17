"""章节工具：读取、统计、列表"""

import re
from pathlib import Path


def parse_chapter_spec(spec: str) -> list[int]:
    """解析范围表达式 "1-3,5,7-9" → [1,2,3,5,7,8,9]"""
    result = []
    parts = spec.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start.strip()), int(end.strip())
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def _chapter_file(doc_dir: Path, num: int) -> Path:
    return doc_dir / f"c{num:03d}.md"


def _read_chapter_file(doc_dir: Path, num: int) -> tuple[str | None, str | None]:
    """读取章节文件，返回 (title, body) 或 (None, None) 文件不存在"""
    fp = _chapter_file(doc_dir, num)
    if not fp.exists():
        return None, None
    text = fp.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    title = lines[0] if lines else f"第{num}章"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return title, body


def chapter_list(workspace_path: Path) -> str:
    """获取章节列表

    Returns:
        "第1章 楔子\n第2章 xxx\n..."
    """
    doc_dir = workspace_path / "document"
    if not doc_dir.exists():
        return "（尚无章节）"

    files = sorted(doc_dir.glob("c*.md"))
    if not files:
        return "（尚无章节）"

    entries = []
    for fp in files:
        text = fp.read_text(encoding="utf-8").strip()
        title = text.splitlines()[0] if text else fp.stem
        entries.append(title)

    return "\n".join(entries)


def read_chapters(workspace_path: Path, chapters: str) -> str:
    """读取指定章节正文

    Args:
        chapters: 范围表达式，如 "1-3,5,7-9"

    Returns:
        "## 第1章 楔子\n\n(正文)\n\n## 第2章 xxx\n\n(正文)"
    """
    doc_dir = workspace_path / "document"
    nums = parse_chapter_spec(chapters)
    if not nums:
        return "错误：章节号格式无效"

    parts = []
    for num in nums:
        title, body = _read_chapter_file(doc_dir, num)
        if title is None:
            parts.append(f"## 第{num}章（不存在）")
        else:
            parts.append(f"## {title}\n\n{body}")

    return "\n\n".join(parts)


def keywords_stat(workspace_path: Path, chapters: str, keywords: list[str]) -> str:
    """分章节统计关键词词频

    Args:
        chapters: 范围表达式
        keywords: 关键词列表 ["主角", "系统"]

    Returns:
        "第1章 楔子:\n  \"主角\": 5\n  \"系统\": 3\n..."
    """
    doc_dir = workspace_path / "document"
    nums = parse_chapter_spec(chapters)
    if not nums:
        return "错误：章节号格式无效"

    patterns = {kw: re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords}

    result_lines = []
    for num in nums:
        title, body = _read_chapter_file(doc_dir, num)
        if title is None:
            result_lines.append(f"第{num}章: （不存在）")
            continue

        label = title
        counts = []
        for kw in keywords:
            count = len(patterns[kw].findall(body))
            counts.append(f'  "{kw}": {count}')
        result_lines.append(f"{label}:\n" + "\n".join(counts))

    return "\n\n".join(result_lines)


def show_chapter(workspace_path: Path, num: int) -> str:
    """展示单个章节（show 指令使用）

    Returns:
        "第1章 楔子\n\n(正文)"
    """
    if num < 1:
        return "错误：章节号必须为正整数"

    doc_dir = workspace_path / "document"
    title, body = _read_chapter_file(doc_dir, num)
    if title is None:
        return f"错误：第{num}章不存在"

    return f"{title}\n\n{body}"
