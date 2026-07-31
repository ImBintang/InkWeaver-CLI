"""文件系统 → SQLite 一次性迁移"""

from pathlib import Path

from tools.editor import (
    parse_frontmatter, _get_proxy,
)
from tools.db.service import SQLiteService
from tools.db.proxy import ProxyService


def _parse_index_spec(index_fp: Path) -> dict:
    """解析类别 index.md 中的写作规范

    解析失败不静默：返回 (spec, error_msg)，由调用方决定如何呈现。
    """
    spec = {}
    try:
        content = index_fp.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("- state"):
                spec["state"] = line.split(":", 1)[1].strip()
                if spec["state"].startswith("required"):
                    spec["state_required"] = True
            elif line.startswith("- description"):
                spec["description"] = line.split(":", 1)[1].strip()
    except OSError as e:
        # 不静默：index 文件不可读会丢失类别写作规范，必须上报
        return spec, f"读取 index.md 失败（{index_fp}）：{e}"
    except UnicodeError as e:
        return spec, f"index.md 编码无法识别（{index_fp}）：{e}"
    return spec, None


def _db_has_data(db) -> bool:
    """检查 DB 中是否已有数据（含任何类别/词条/剧情卡片/规则文档）

    查询失败不静默：DB 损坏时不能假装"无数据"而触发覆盖式迁移，
    必须让迁移流程显式失败，由上层（CLI/API 消费端）呈现给用户。
    """
    if db.list_categories():
        return True
    # 检查任意主表是否有记录
    for table in ("wiki_main", "plot_main", "rules_main"):
        cur = db.conn.execute(f"SELECT COUNT(*) FROM {table}")
        if cur.fetchone()[0] > 0:
            return True
    return False


def migrate_workspace(workspace: Path) -> dict:
    """执行迁移，返回统计信息"""
    stats = {"categories": 0, "wiki": 0, "plot": 0, "rules": 0, "errors": []}

    # 创建 DB + Proxy
    db_path = workspace / "wiki.db"
    db = SQLiteService(db_path)
    
    # 检查 DB 是否已有数据（vs 仅仅文件存在）
    if _db_has_data(db):
        db.close()
        return {"skipped": True, "reason": f"数据库已包含数据，无需迁移"}

    proxy = ProxyService(db)

    # 阶段 1：迁移类别
    wiki_dir = workspace / "wiki"
    if wiki_dir.exists():
        for cat_dir in sorted(wiki_dir.iterdir()):
            if not cat_dir.is_dir():
                continue
            category = cat_dir.name
            index_fp = cat_dir / "index.md"
            spec, spec_err = _parse_index_spec(index_fp) if index_fp.exists() else ({}, None)
            if spec_err:
                # 不静默：规范解析失败收集到迁移错误，由调用方呈现给用户
                stats["errors"].append(f"category/{category}: {spec_err}")
            proxy.create_category(category, "wiki", spec)
            stats["categories"] += 1

    # 阶段 2：迁移 wiki 词条
    if wiki_dir.exists():
        for cat_dir in sorted(wiki_dir.iterdir()):
            if not cat_dir.is_dir():
                continue
            category = cat_dir.name
            for fp in sorted(cat_dir.glob("*.md")):
                if fp.name == "index.md":
                    continue
                try:
                    content = fp.read_text(encoding="utf-8")
                    meta, body = parse_frontmatter(content)
                    chapter = int(meta.get("chapter", meta.get("updated", 0)))
                    proxy.add_doc(
                        doc_type="wiki", name=fp.stem, category=category,
                        content=body, chapter=chapter,
                        description=meta.get("description", ""),
                        state=meta.get("state", ""),
                        tags=meta.get("tags", []),
                    )
                    stats["wiki"] += 1
                except Exception as e:
                    stats["errors"].append(f"wiki/{category}/{fp.name}: {e}")

    # 阶段 3：迁移 plot
    plot_dir = workspace / "plot"
    if plot_dir.exists():
        for fp in sorted(plot_dir.glob("*.md")):
            if fp.name == "index.md":
                continue
            try:
                content = fp.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(content)
                chapter = int(meta.get("chapter", meta.get("updated", 0)))
                ended = str(meta.get("ended", "false")).lower() == "true"
                proxy.add_doc(
                    doc_type="plot", name=fp.stem,
                    content=body, chapter=chapter,
                    description=meta.get("description", ""),
                    state=meta.get("state", ""),
                    chapters=meta.get("chapters", ""),
                    tags=meta.get("tags", []),
                )
                # 如果是已结束的剧情卡片，标记 ended
                if ended:
                    doc = proxy._find_in_cache("plot", fp.stem)
                    if doc:
                        doc.ended = True
                        doc.end_notes = meta.get("end_notes", "")
                stats["plot"] += 1
            except Exception as e:
                stats["errors"].append(f"plot/{fp.name}: {e}")

    # 阶段 4：迁移 rules
    rules_dir = workspace / "rules"
    if rules_dir.exists():
        for fp in sorted(rules_dir.glob("*.md")):
            try:
                content = fp.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(content)
                chapter = int(meta.get("chapter", meta.get("updated", 0)))
                proxy.add_doc(
                    doc_type="rule", name=fp.stem,
                    content=body, chapter=chapter,
                    description=meta.get("description", ""),
                    state=meta.get("state", ""),
                )
                stats["rules"] += 1
            except Exception as e:
                stats["errors"].append(f"rules/{fp.name}: {e}")

    # 阶段 5：flush 写入 DB
    try:
        proxy.flush(scope_chapter=0)
    except Exception as e:
        stats["errors"].append(f"flush: {e}")

    # 返回统计
    db.close()
    return stats
