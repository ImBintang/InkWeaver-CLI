"""文档差异对比工具（v5.1 已废弃 doc_diff，改用 chapter_list + DB）"""

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


def doc_diff(workspace: Path) -> str:
    """[DEPRECATED] 对比哈希，了解文档变更。v5.1 已改用 chapter_list 替代。

    现在直接返回 chapter_list 的结果（含 [已处理]/[未处理] 标记）。
    """
    from tools.chapter import chapter_list
    return chapter_list(workspace)


def get_changed_chapters(workspace: Path) -> list:
    """[DEPRECATED] 获取变更的章节文件名列表。v5.1 已改用 DB。"""
    return []


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
    """获取未处理的章节号列表（从小到大排序），至多 limit 个

    v5.1：从 DB chapters 表 + log.json processed.chapter_ranges 计算。
    """
    from tools.db.service import SQLiteService
    from tools.chapter import parse_chapter_spec

    db_path = workspace / "wiki.db"
    if not db_path.exists():
        return []

    db = SQLiteService(db_path)
    try:
        all_chapters = db.chapter_list_all()  # [{"num": 1, "title": "..."}]
        if not all_chapters:
            return []

        # 从 log.json 获取已处理范围
        log = _ensure_log(workspace)
        processed_ranges = log.get("processed", {}).get("chapter_ranges", [])
        processed_nums = set()
        for r in processed_ranges:
            processed_nums.update(parse_chapter_spec(r))

        # 过滤未处理的
        unprocessed = [ch["chapter_num"] for ch in all_chapters if ch["chapter_num"] not in processed_nums]
        return sorted(unprocessed)[:limit]
    finally:
        # v7.0.1: log.json 损坏（_ensure_log 抛异常）时也确保连接关闭，防泄漏
        db.close()


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
    lint_note = ""
    try:
        from tools.lint import run_lint
        lint_result = run_lint(workspace)
        # 从 lint 结果中提取自动修复和债务摘要
        fix_count = 0
        debt_count = 0
        for line in lint_result.splitlines():
            m = re.search(r"### 自动修复（(\d+) 项）", line)
            if m:
                fix_count = int(m.group(1))
            # v7.0.1: 与 lint.py 实际输出对齐（原正则漏写全角括号，债务数恒为 0）
            m = re.search(r"### 需人工处理的债务（(\d+) 项）", line)
            if m:
                debt_count = int(m.group(1))
        lint_note = f" | lint: 自动修复 {fix_count} 处，剩余债务 {debt_count} 项"
        # P1-28：全局 lint 的自动修复经 proxy 写入缓存，这里立即 flush 落库，
        # 避免报告宣称"已自动修复"但 DB 未变。无章节数据时缓存保留至下次 flush。
        if proxy.is_cache_loaded():
            max_ch = proxy._db.chapter_max_num()
            if max_ch > 0:
                proxy.flush(scope_chapter=max_ch)
                lint_note += "（自动修复已落库）"
            else:
                lint_note += "（DB 无章节数据，修复保留在缓存中）"
    except Exception as e:
        # 错误不静默：失败原因随结果回传，由 LLM/用户决定如何处理
        lint_note = f" | lint 刷新失败：{e}"

    return (f"任务已完成。已记录：{len(chapter_nums)} 章、"
            f"{len(new_wiki + updated_wiki)} 个 wiki 词条、"
            f"{len(new_rules + updated_rules)} 个规则、"
            f"{len(new_plots + updated_plots)} 个剧情卡片"
            f"{lint_note}")
