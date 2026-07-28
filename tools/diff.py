"""文档差异对比工具：检测新增/修改的章节"""

import hashlib
import json
import re
from pathlib import Path


LOG_FILE = "log.json"


def _log_file(workspace: Path) -> Path:
    return workspace / LOG_FILE


def _ensure_log(workspace: Path) -> dict:
    """确保 log.json 存在，返回内容"""
    log_fp = _log_file(workspace)
    if not log_fp.exists():
        return {"files": {}, "extractions": []}
    with open(log_fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_log(workspace: Path, log: dict):
    """保存 log.json"""
    log_fp = _log_file(workspace)
    with open(log_fp, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _md5(text: str) -> str:
    """计算字符串的 MD5"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _is_covered_by_ranges(filename: str, ranges: list[str]) -> bool:
    """检查文件名（如 c005.md）是否被某个章节区间覆盖"""
    from tools.chapter import parse_chapter_spec
    m = re.match(r"c?0*(\d+)\.md", filename)
    if not m:
        return False
    num = int(m.group(1))
    for r in ranges:
        nums = parse_chapter_spec(r)
        if num in nums:
            return True
    return False


def doc_diff(workspace: Path) -> str:
    """对比哈希，了解文档变更（新增/修改的章节）

    Returns:
        变更摘要
    """
    doc_dir = workspace / "document"
    if not doc_dir.exists() or not list(doc_dir.glob("*.md")):
        return "（document 目录不存在或无章节文件）"

    log = _ensure_log(workspace)
    old_files = log.get("files", {})

    current_files = {}
    for fp in sorted(doc_dir.glob("c*.md")):
        content = fp.read_text(encoding="utf-8")
        current_files[fp.name] = _md5(content)

    new_files = []
    modified_files = []
    deleted_files = []

    for name, md5 in current_files.items():
        if name not in old_files:
            new_files.append(name)
        elif old_files[name] != md5:
            modified_files.append(name)

    for name in old_files:
        if name not in current_files:
            deleted_files.append(name)

    # 过滤已处理的章节
    processed_ranges = log.get("processed", {}).get("chapter_ranges", [])
    if processed_ranges:
        new_files = [f for f in new_files if not _is_covered_by_ranges(f, processed_ranges)]
        modified_files = [f for f in modified_files if not _is_covered_by_ranges(f, processed_ranges)]

    # 更新 log.json 中的文件记录
    log["files"] = current_files
    _save_log(workspace, log)

    if not new_files and not modified_files and not deleted_files:
        return "（无变更）"

    lines = ["文档变更："]
    if new_files:
        lines.append(f"  新增（{len(new_files)} 个）：{', '.join(new_files)}")
    if modified_files:
        lines.append(f"  修改（{len(modified_files)} 个）：{', '.join(modified_files)}")
    if deleted_files:
        lines.append(f"  删除（{len(deleted_files)} 个）：{', '.join(deleted_files)}")

    return "\n".join(lines)


def get_changed_chapters(workspace: Path) -> list:
    """获取变更的章节文件名列表（供程序调用）"""
    doc_dir = workspace / "document"
    if not doc_dir.exists():
        return []

    log = _ensure_log(workspace)
    old_files = log.get("files", {})

    current_files = {}
    for fp in sorted(doc_dir.glob("c*.md")):
        content = fp.read_text(encoding="utf-8")
        current_files[fp.name] = _md5(content)

    changed = []
    for name, md5 in current_files.items():
        if name not in old_files or old_files[name] != md5:
            changed.append(name)

    # 更新 log
    log["files"] = current_files
    _save_log(workspace, log)

    return changed


def record_extraction(workspace: Path, chapters: list, new_entries: list, updated_entries: list):
    """记录知识提取操作到 log.json"""
    log = _ensure_log(workspace)
    from datetime import datetime
    extraction = {
        "timestamp": datetime.now().isoformat(),
        "chapters": chapters,
        "new_entries": new_entries,
        "updated_entries": updated_entries,
    }
    log.setdefault("extractions", []).append(extraction)
    _save_log(workspace, log)


def get_unprocessed_chapters(workspace: Path, limit: int = 10) -> list[int]:
    """获取未处理的章节号列表（从小到大排序），至多 limit 个"""
    doc_dir = workspace / "document"
    if not doc_dir.exists():
        return []

    log = _ensure_log(workspace)
    old_files = log.get("files", {})
    processed_ranges = log.get("processed", {}).get("chapter_ranges", [])

    unprocessed = set()
    for fp in sorted(doc_dir.glob("c*.md")):
        name = fp.name
        m = re.match(r"c?0*(\d+)\.md", name)
        if not m:
            continue
        num = int(m.group(1))
        # 全新文件或已修改但未处理的文件
        if name not in old_files:
            if not processed_ranges or not _is_covered_by_ranges(name, processed_ranges):
                unprocessed.add(num)
        elif processed_ranges and not _is_covered_by_ranges(name, processed_ranges):
            unprocessed.add(num)

    result = sorted(unprocessed)
    return result[:limit]


def finish_task(workspace, chapters, new_wiki=None, updated_wiki=None,
                new_rules=None, updated_rules=None,
                new_plots=None, updated_plots=None) -> str:
    """完成知识提取任务：校验存在性 + 记录 log.json + 构建关系图"""
    from tools.chapter import parse_chapter_spec

    new_wiki = new_wiki or []
    updated_wiki = updated_wiki or []
    new_rules = new_rules or []
    updated_rules = updated_rules or []
    new_plots = new_plots or []
    updated_plots = updated_plots or []

    # 1. 校验 chapters 格式
    chapter_nums = parse_chapter_spec(chapters)
    if not chapter_nums:
        return f"错误：章节区间格式无效「{chapters}」"

    # 2. 校验所有声明的 wiki 存在（v5：通过 proxy 查找）
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    for name in new_wiki + updated_wiki:
        if proxy.find_doc("wiki", name) is None:
            return f"错误：wiki 词条「{name}」不存在，请检查并重试"

    # 3. 校验所有声明的 rules 存在
    for name in new_rules + updated_rules:
        if proxy.find_doc("rule", name) is None:
            return f"错误：规则文档「{name}」不存在，请检查并重试"

    # 4. 校验所有声明的 plots 存在
    for name in new_plots + updated_plots:
        if proxy.find_doc("plot", name) is None:
            return f"错误：剧情卡片「{name}」不存在，请检查并重试"

    # 5. 全部校验通过 → 写入 log.json
    log = _ensure_log(workspace)

    # 5a. 追加 extraction 记录
    import datetime
    extraction = {
        "timestamp": datetime.datetime.now().isoformat(),
        "chapters": chapter_nums,
        "new_entries": new_wiki + new_rules + new_plots,
        "updated_entries": updated_wiki + updated_rules + updated_plots,
    }
    log.setdefault("extractions", []).append(extraction)

    # 5b. 合并 processed.chapter_ranges
    processed = log.setdefault("processed", {"chapter_ranges": [], "last_finish": ""})
    existing_ranges = processed.get("chapter_ranges", [])
    existing_ranges.append(chapters)
    processed["chapter_ranges"] = existing_ranges
    processed["last_finish"] = extraction["timestamp"]

    _save_log(workspace, log)

    # 6. 债务治理后重跑 lint，刷新债务数据
    try:
        from tools.lint import run_lint
        lint_result = run_lint(workspace)
        # 从 lint 结果中提取自动修复和债务摘要
        fix_count = 0
        debt_count = 0
        for line in lint_result.splitlines():
            if "自动修复" in line:
                m = re.search(r"（(\d+) 项）", line)
                if m:
                    fix_count = int(m.group(1))
            if "待处理债务" in line:
                m = re.search(r"（(\d+) 项）", line)
                if m:
                    debt_count = int(m.group(1))
        lint_note = f" | lint: 自动修复 {fix_count} 处，剩余债务 {debt_count} 项"
    except Exception:
        lint_note = " | lint 刷新失败（跳过）"

    return (f"✅ 任务已完成。已记录：{len(chapter_nums)} 章、"
            f"{len(new_wiki + updated_wiki)} 个 wiki 词条、"
            f"{len(new_rules + updated_rules)} 个规则、"
            f"{len(new_plots + updated_plots)} 个剧情卡片"
            f"{lint_note}")
