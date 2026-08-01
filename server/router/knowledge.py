"""知识库 HTTP API（wiki/rules/plots/categories）"""

import json

from fastapi import APIRouter

from server.state import state
from server.router.books import _safe_book_path
from tools.db.service import SQLiteService

router = APIRouter()


def _get_db(book: str) -> SQLiteService:
    ws_path = _safe_book_path(book)
    return SQLiteService(ws_path / "wiki.db")


def _resolve_relation_names(db: SQLiteService, rel_ids: list) -> list[str]:
    """将 relations 中的 main_id 列表解析为名称列表（复用 tools/relation.py 逻辑）"""
    names: list[str] = []
    for rid in rel_ids:
        for getter in (db.wiki_get_main, db.plot_get_main, db.rule_get_main):
            try:
                m = getter(rid)
            except Exception:
                m = None
            if m:
                if m["name"] not in names:
                    names.append(m["name"])
                break
    return names


# ─── 类别 ──────────────────────────────────────────────────────────

@router.get("/api/books/{book}/categories")
async def list_categories(book: str) -> list[dict]:
    """列出知识库类别"""
    db = None
    try:
        db = _get_db(book)
        rows = db.list_categories()
        return [{"name": r["name"], "type": r.get("type", "wiki")} for r in rows]
    except Exception as e:
        print(f"[WARN] 获取类别列表失败 (book={book}): {e}")
        return []
    finally:
        if db:
            db.close()


# ─── Wiki 词条 ─────────────────────────────────────────────────────

@router.get("/api/books/{book}/wiki")
async def list_wiki_cards(book: str, category: str | None = None) -> list[dict]:
    """列出 wiki 词条（可按类别过滤）"""
    db = None
    try:
        db = _get_db(book)
        pairs: list[tuple[dict, str]] = []  # (main 行, 类别名)
        if category:
            cat = db.get_category_by_name(category)
            if cat is None:
                return []
            for m in db.wiki_list_main(cat["id"]):
                pairs.append((m, category))
        else:
            for c in db.list_categories(type="wiki"):
                for m in db.wiki_list_main(c["id"]):
                    pairs.append((m, c["name"]))
        result = []
        for m, cat_name in pairs:
            ver = db.latest_version_at("wiki", m["id"], 999999)
            summary = ""
            if ver:
                summary = ver.get("description") or ver.get("content") or ""
            result.append({"name": m["name"], "category": cat_name, "summary": summary})
        return result
    except Exception as e:
        print(f"[WARN] 获取 wiki 列表失败 (book={book}): {e}")
        return []
    finally:
        if db:
            db.close()


@router.get("/api/books/{book}/wiki/{name}")
async def get_wiki_detail(book: str, name: str) -> dict:
    """获取 wiki 词条详情（未命中时回退查规则/剧情卡片，供详情页复用）"""
    db = None
    try:
        db = _get_db(book)
        main = db.wiki_find_main(name)
        if main is not None:
            version = db.latest_version_at("wiki", main["id"], 999999)
            cat = db.get_category(main["category_id"])
            result = {
                "name": main["name"],
                "type": "wiki",
                "category": cat["name"] if cat else "",
                "created_chapter": main.get("created_chapter", 0),
                "updated_chapter": main.get("updated_chapter", 0),
            }
            if version:
                # relations 存的是 main_id 列表（JSON 字符串），解析为词条名称
                raw_relations = version.get("relations", "[]")
                try:
                    rel_ids = json.loads(raw_relations) if isinstance(raw_relations, str) else (raw_relations or [])
                except (json.JSONDecodeError, TypeError):
                    rel_ids = []
                rel_names = _resolve_relation_names(db, rel_ids) if isinstance(rel_ids, list) else []
                result.update({
                    "keywords": version.get("keywords", ""),
                    "description": version.get("description", ""),
                    "state": version.get("state", ""),
                    "content": version.get("content", ""),
                    "relations": json.dumps(rel_names, ensure_ascii=False),
                })
            return result

        # 回退：规则
        rule = db.rule_find_main(name)
        if rule is not None:
            ver = db.latest_version_at("rule", rule["id"], 999999)
            return {
                "name": rule["name"],
                "type": "rule",
                "category": "规则",
                "created_chapter": rule.get("created_chapter", 0),
                "updated_chapter": rule.get("updated_chapter", 0),
                "keywords": (ver or {}).get("keywords", ""),
                "description": "",
                "state": "",
                "content": (ver or {}).get("content", ""),
                "relations": "[]",
            }

        # 回退：剧情卡片（额外携带 chapters/ended/end_notes 供详情页展示）
        plot = db.plot_find_main(name)
        if plot is not None:
            ver = db.latest_version_at("plot", plot["id"], 999999)
            rel_names = []
            if ver:
                raw_rel = ver.get("relations") or []
                if isinstance(raw_rel, str):
                    try:
                        raw_rel = json.loads(raw_rel)
                    except (json.JSONDecodeError, TypeError):
                        raw_rel = []
                if isinstance(raw_rel, list):
                    rel_names = _resolve_relation_names(db, raw_rel)
            return {
                "name": plot["name"],
                "type": "plot",
                "category": "剧情卡片",
                "created_chapter": plot.get("created_chapter", 0),
                "updated_chapter": plot.get("updated_chapter", 0),
                "keywords": (ver or {}).get("keywords", ""),
                "description": (ver or {}).get("description", ""),
                "state": (ver or {}).get("state", ""),
                "content": (ver or {}).get("content", ""),
                "relations": json.dumps(rel_names, ensure_ascii=False),
                "chapters": plot.get("chapters", ""),
                "ended": bool(plot.get("ended", 0)),
                "end_notes": plot.get("end_notes", ""),
            }

        return {}
    except Exception as e:
        print(f"[WARN] 获取 wiki 详情失败 (book={book}, name={name}): {e}")
        return {}
    finally:
        if db:
            db.close()


# ─── 规则 ──────────────────────────────────────────────────────────

@router.get("/api/books/{book}/rules")
async def list_rules(book: str) -> list[dict]:
    """列出规则"""
    db = None
    try:
        db = _get_db(book)
        rows = db.rule_list_main()
        result = []
        for r in rows:
            ver = db.latest_version_at("rule", r["id"], 999999)
            result.append({
                "id": r["id"],
                "name": r["name"],
                "content": (ver or {}).get("content", ""),
                "keywords": (ver or {}).get("keywords", ""),
                "summary": (ver or {}).get("description", ""),
            })
        return result
    except Exception as e:
        print(f"[WARN] 获取规则列表失败 (book={book}): {e}")
        return []
    finally:
        if db:
            db.close()


# ─── 剧情卡片 ──────────────────────────────────────────────────────

@router.get("/api/books/{book}/plots")
async def list_plots(book: str) -> list[dict]:
    """列出剧情卡片"""
    db = None
    try:
        db = _get_db(book)
        rows = db.plot_list_main()
        result = []
        for r in rows:
            ver = db.latest_version_at("plot", r["id"], 999999)
            result.append({
                "id": r["id"],
                "title": r["name"],
                "content": (ver or {}).get("content", ""),
                "description": (ver or {}).get("description", ""),
                "chapters": r.get("chapters", ""),
                "ended": bool(r.get("ended", 0)),
            })
        return result
    except Exception as e:
        print(f"[WARN] 获取剧情卡片列表失败 (book={book}): {e}")
        return []
    finally:
        if db:
            db.close()
