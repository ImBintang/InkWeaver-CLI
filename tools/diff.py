"""文档差异对比工具：检测新增/修改的章节"""

import hashlib
import json
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
