"""章节工具：读取、统计、列表（v5.1：数据源从文件迁移至 DB）"""

import json
import re
from pathlib import Path


def _extract_digits(text: str) -> int:
    """从字符串中提取数字，如 "第1章" → 1, "第10" → 10"""
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0


def parse_chapter_spec(spec: str) -> list[int]:
    """解析范围表达式 "1-3,5,7-9" → [1,2,3,5,7,8,9]
    兼容中文格式："第1章-第3章,第5,第7章-第9章" → [1,2,3,5,7,8,9]"""
    result = []
    if not spec or not spec.strip():
        return result
    parts = spec.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start, end = _extract_digits(start_str.strip()), _extract_digits(end_str.strip())
            if start == 0 and end == 0:
                continue
            if start <= end:
                result.extend(range(start, end + 1))
            else:
                result.extend(range(end, start + 1))
        else:
            num = _extract_digits(part)
            if num:
                result.append(num)
    return sorted(set(result))


def _get_db(workspace_path: Path):
    """获取 SQLiteService 实例"""
    from tools.editor import _get_proxy
    return _get_proxy(workspace_path)._db


def _load_processed_ranges(workspace_path: Path) -> list:
    """从 log.json 加载已处理的章节范围"""
    log_fp = workspace_path / "log.json"
    if not log_fp.exists():
        return []
    try:
        with open(log_fp, "r", encoding="utf-8") as f:
            log = json.load(f)
        return log.get("processed", {}).get("chapter_ranges", [])
    except (json.JSONDecodeError, OSError):
        return []


def _is_processed(num: int, ranges: list) -> bool:
    """判断章节号是否已被处理（覆盖于某个 range 内）"""
    for r in ranges:
        if isinstance(r, list) and len(r) == 2:
            if r[0] <= num <= r[1]:
                return True
        elif isinstance(r, str):
            nums = parse_chapter_spec(r)
            if num in nums:
                return True
    return False


# ---- Deprecated: 文件系统读取（保留兼容，后续版本移除）----

def _chapter_file(doc_dir: Path, num: int) -> Path:
    return doc_dir / f"c{num:03d}.md"


def _read_chapter_file(doc_dir: Path, num: int) -> tuple[str | None, str | None]:
    """[Deprecated] 读取章节文件，返回 (title, body) 或 (None, None)"""
    fp = _chapter_file(doc_dir, num)
    if not fp.exists():
        return None, None
    text = fp.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    title = lines[0] if lines else f"第{num}章"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return title, body


def chapter_list(workspace_path: Path) -> str:
    """获取章节列表（附带处理状态标记）

    Returns:
        "第1章 楔子 [已处理]\n第2章 xxx [未处理]\n..."
    """
    db = _get_db(workspace_path)
    rows = db.chapter_list_all()
    if not rows:
        return "（尚无章节）"

    processed_ranges = _load_processed_ranges(workspace_path)
    entries = []
    for row in rows:
        num = row["chapter_num"]
        title = row["title"] or f"第{num}章"
        status = "[已处理]" if _is_processed(num, processed_ranges) else "[未处理]"
        entries.append(f"{title} {status}")

    return "\n".join(entries)


def read_chapters(workspace_path: Path, chapters: str) -> str:
    """读取指定章节正文（从 DB）

    Args:
        chapters: 范围表达式，如 "1-3,5,7-9"

    Returns:
        "## 第1章 楔子\n\n(正文)\n\n## 第2章 xxx\n\n(正文)"
    """
    nums = parse_chapter_spec(chapters)
    if not nums:
        return "错误：章节号格式无效"

    db = _get_db(workspace_path)
    rows = db.chapter_get_range(nums)
    found = {r["chapter_num"]: r for r in rows}

    parts = []
    for num in nums:
        row = found.get(num)
        if row is None:
            parts.append(f"## 第{num}章（不存在）")
        else:
            title = row["title"] or f"第{num}章"
            parts.append(f"## {title}\n\n{row['content']}")

    return "\n\n".join(parts)


def keywords_stat(workspace_path: Path, chapters: str, keywords: list[str]) -> str:
    """分章节统计关键词词频（从 DB）

    v5.4：接入 name_utils.expand_keywords，同词条多名称统一统计。
    例如词条「叶寒（寒叔）」，搜索"寒叔"时会合并"叶寒"的词频。

    Args:
        chapters: 范围表达式
        keywords: 关键词列表 ["主角", "系统"]

    Returns:
        "第1章 楔子:\n  \"主角\": 5\n  \"系统\": 3\n..."
    """
    nums = parse_chapter_spec(chapters)
    if not nums:
        return "错误：章节号格式无效"

    db = _get_db(workspace_path)
    rows = db.chapter_get_range(nums)
    found = {r["chapter_num"]: r for r in rows}

    # v5.4: 别名扩展 — 将关键词扩展为含变体的搜索集
    from tools.name_utils import expand_keywords
    expanded = expand_keywords(workspace_path, keywords)

    # 为每个原始关键词构建所有变体的正则
    kw_patterns: dict[str, list[tuple[str, re.Pattern]]] = {}
    for kw in keywords:
        variants = expanded.get(kw, [kw])
        kw_patterns[kw] = [(v, re.compile(re.escape(v), re.IGNORECASE)) for v in variants]

    result_lines = []
    for num in nums:
        row = found.get(num)
        if row is None:
            result_lines.append(f"第{num}章: （不存在）")
            continue

        label = row["title"] or f"第{num}章"
        body = row["content"]
        counts = []
        for kw in keywords:
            variants = kw_patterns[kw]
            if len(variants) == 1:
                # 无扩展，直接统计
                count = len(variants[0][1].findall(body))
                counts.append(f'  "{kw}": {count}')
            else:
                # 多名称合并统计
                total = 0
                detail_parts = []
                for variant_name, pattern in variants:
                    c = len(pattern.findall(body))
                    total += c
                    if c > 0:
                        detail_parts.append(f"{variant_name}={c}")
                # 输出合并结果 + 标注
                other_names = [v for v, _ in variants if v != kw]
                annotation = f'(含"{"、".join(other_names)}")' if other_names else ""
                counts.append(f'  "{kw}"{annotation}: {total}')
        result_lines.append(f"{label}:\n" + "\n".join(counts))

    return "\n\n".join(result_lines)


def show_chapter(workspace_path: Path, num: int) -> str:
    """展示单个章节（show 指令使用，从 DB）

    Returns:
        "第1章 楔子\n\n(正文)"
    """
    if num < 1:
        return "错误：章节号必须为正整数"

    db = _get_db(workspace_path)
    row = db.chapter_get(num)
    if row is None:
        return f"错误：第{num}章不存在"

    title = row["title"] or f"第{num}章"
    return f"{title}\n\n{row['content']}"


# ---- 中文数字映射 ----
_CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000,
}

_CHAPTER_NUM_RE = re.compile(r"第\s*([\d零一二三四五六七八九十百千万两]+)\s*[章回节部卷]")


def _cn_to_int(cn: str) -> int:
    """中文数字转整数，支持「十一」「一百二十三」等"""
    cn = cn.strip()
    if cn.isdigit():
        return int(cn)
    total = 0
    current = 0
    for ch in cn:
        val = _CN_NUM_MAP.get(ch)
        if val is None:
            return 0
        if val >= 10:
            if current == 0:
                current = 1
            total += current * val
            current = 0
        else:
            current += val
    return total + current


def parse_chapter_title(line: str) -> tuple[int | None, str | None]:
    """从章节标题行解析出 (章节号, 标题)

    Args:
        line: 如 "第1章 楔子"、"第2章 曾经的天才【上】"

    Returns:
        (num, title) 或 (None, None) 解析失败
    """
    stripped = line.strip()
    if not stripped:
        return None, None
    m = _CHAPTER_NUM_RE.search(stripped)
    if not m:
        return None, None
    num = _cn_to_int(m.group(1))
    if num <= 0:
        return None, None
    return num, stripped


def write_chapter(workspace_path: Path, num: int, content: str) -> str:
    """写入单个章节到 DB

    Args:
        num: 章节号
        content: 全文内容（含标题行，自动拆分 title/body）

    Returns:
        操作结果消息
    """
    if num < 1:
        return "错误：章节号必须为正整数"

    content = content.strip()
    lines = content.splitlines()
    title = lines[0].strip() if lines else f"第{num}章"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    db = _get_db(workspace_path)
    db.chapter_upsert(num, title, body)
    return f"已写入第{num}章"
