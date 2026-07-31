"""知识库 HTTP API（wiki/rules/plots/categories）"""

from fastapi import APIRouter

from server.state import state
from server.router.books import _safe_book_path
from tools.db.service import SQLiteService

router = APIRouter()


def _get_db(book: str) -> SQLiteService:
    ws_path = _safe_book_path(book)
    return SQLiteService(ws_path / "wiki.db")


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
    """获取 wiki 词条详情"""
    db = None
    try:
        db = _get_db(book)
        main = db.wiki_find_main(name)
        if main is None:
            return {}
        version = db.latest_version_at("wiki", main["id"], 999999)
        cat = db.get_category(main["category_id"])
        result = {
            "name": main["name"],
            "category": cat["name"] if cat else "",
            "created_chapter": main.get("created_chapter", 0),
            "updated_chapter": main.get("updated_chapter", 0),
        }
        if version:
            result.update({
                "keywords": version.get("keywords", ""),
                "description": version.get("description", ""),
                "state": version.get("state", ""),
                "content": version.get("content", ""),
                "relations": version.get("relations", "[]"),
            })
        return result
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
