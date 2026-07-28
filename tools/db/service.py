"""SQLite 数据库服务 — 建表、CRUD、版本管理

单例模式，全局一个连接。不感知缓存层。
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _now() -> int:
    return int(time.time())


def _ensure_json(value: Any) -> str:
    """确保 tags/relations 字段为 JSON 字符串"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_json(value: str) -> Any:
    """解析 tags/relations JSON 字段"""
    if not value:
        return [] if value == "" else value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class SQLiteService:
    """SQLite 数据库服务"""

    def __init__(self, db_path: Path):
        if isinstance(db_path, str) and db_path == ":memory:":
            self.conn = sqlite3.connect(":memory:")
        else:
            self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.auto_commit = True  # False 时由调用方管理事务
        self._init_schema()

    def _commit(self):
        """受 auto_commit 控制的提交（flush 事务期间跳过）"""
        if self.auto_commit:
            self.conn.commit()

    def _init_schema(self):
        from tools.db.schema import ALL_TABLES
        full_sql = "\n".join(ALL_TABLES)
        self.conn.executescript(full_sql)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def integrity_check(self) -> list[str]:
        cursor = self.conn.execute("PRAGMA integrity_check")
        return [row[0] for row in cursor.fetchall()]

    # ── 类别 ──

    def create_category(self, name: str, type: str = "wiki",
                        spec: dict = None) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO categories (name, type, spec, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, type, json.dumps(spec or {}, ensure_ascii=False), now, now),
        )
        self._commit()
        return cur.lastrowid

    def get_category(self, cat_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM categories WHERE id = ?", (cat_id,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["spec"] = json.loads(d.get("spec", "{}"))
        return d

    def get_category_by_name(self, name: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM categories WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["spec"] = json.loads(d.get("spec", "{}"))
        return d

    def list_categories(self, type: str = None) -> list[dict]:
        if type:
            cur = self.conn.execute(
                "SELECT * FROM categories WHERE type = ? ORDER BY name", (type,))
        else:
            cur = self.conn.execute(
                "SELECT * FROM categories ORDER BY name")
        result = []
        for row in cur.fetchall():
            d = dict(row)
            d["spec"] = json.loads(d.get("spec") or "{}")
            result.append(d)
        return result

    def update_category(self, cat_id: int, **fields) -> bool:
        allowed = {"name", "type", "spec", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates.setdefault("updated_at", _now())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [cat_id]
        self.conn.execute(
            f"UPDATE categories SET {set_clause} WHERE id = ?", vals)
        self._commit()
        return True

    # ── Wiki ──

    def wiki_create_main(self, name: str, category_id: int,
                         chapter: int) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO wiki_main (name, category_id, current_version, "
            "created_chapter, updated_chapter, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?, ?)",
            (name, category_id, chapter, chapter, now, now),
        )
        self._commit()
        return cur.lastrowid

    def wiki_get_main(self, main_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM wiki_main WHERE id = ?", (main_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def wiki_find_main(self, name: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM wiki_main WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def wiki_list_main(self, category_id: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM wiki_main WHERE category_id = ? ORDER BY name",
            (category_id,))
        return [dict(row) for row in cur.fetchall()]

    def wiki_update_main(self, main_id: int, **fields) -> bool:
        allowed = {"name", "category_id", "current_version",
                    "updated_chapter", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates.setdefault("updated_at", _now())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [main_id]
        self.conn.execute(
            f"UPDATE wiki_main SET {set_clause} WHERE id = ?", vals)
        self._commit()
        return True

    def wiki_create_version(self, main_id: int, chapter: int,
                            data: dict) -> int:
        """创建新版本记录，返回 index_id"""
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO wiki_index (main_id, chapter, keywords, description, "
            "state, tags, content, relations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                main_id, chapter,
                data.get("keywords", ""),
                data.get("description", ""),
                data.get("state", ""),
                _ensure_json(data.get("tags", [])),
                data.get("content", ""),
                _ensure_json(data.get("relations", [])),
                now,
            ),
        )
        self._commit()
        return cur.lastrowid

    def wiki_get_current(self, main_id: int) -> dict | None:
        """读取当前最新版本"""
        main = self.wiki_get_main(main_id)
        if main is None:
            return None
        cur = self.conn.execute(
            "SELECT * FROM wiki_index WHERE id = ?",
            (main["current_version"],))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(main)
        result.update(dict(row))
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def wiki_set_current(self, main_id: int, version_id: int,
                         chapter: int):
        self.conn.execute(
            "UPDATE wiki_main SET current_version = ?, updated_chapter = ?, "
            "updated_at = ? WHERE id = ?",
            (version_id, chapter, _now(), main_id),
        )
        self._commit()

    # ── Plot ──

    def plot_create_main(self, name: str, chapter: int,
                         chapters: str = "") -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO plot_main (name, current_version, chapters, ended, "
            "end_notes, created_chapter, updated_chapter, created_at, updated_at) "
            "VALUES (?, 0, ?, 0, '', ?, ?, ?, ?)",
            (name, chapters, chapter, chapter, now, now),
        )
        self._commit()
        return cur.lastrowid

    def plot_get_main(self, main_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM plot_main WHERE id = ?", (main_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def plot_find_main(self, name: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM plot_main WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def plot_list_main(self, ended: bool = None) -> list[dict]:
        if ended is True:
            cur = self.conn.execute(
                "SELECT * FROM plot_main WHERE ended = 1 ORDER BY name")
        elif ended is False:
            cur = self.conn.execute(
                "SELECT * FROM plot_main WHERE ended = 0 ORDER BY name")
        else:
            cur = self.conn.execute(
                "SELECT * FROM plot_main ORDER BY name")
        return [dict(row) for row in cur.fetchall()]

    def plot_update_main(self, main_id: int, **fields) -> bool:
        allowed = {"name", "current_version", "chapters", "ended",
                    "end_notes", "updated_chapter", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates.setdefault("updated_at", _now())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [main_id]
        self.conn.execute(
            f"UPDATE plot_main SET {set_clause} WHERE id = ?", vals)
        self._commit()
        return True

    def plot_create_version(self, main_id: int, chapter: int,
                            data: dict) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO plot_index (main_id, chapter, keywords, description, "
            "state, tags, content, relations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                main_id, chapter,
                data.get("keywords", ""),
                data.get("description", ""),
                data.get("state", ""),
                _ensure_json(data.get("tags", [])),
                data.get("content", ""),
                _ensure_json(data.get("relations", [])),
                now,
            ),
        )
        self._commit()
        return cur.lastrowid

    def plot_get_current(self, main_id: int) -> dict | None:
        main = self.plot_get_main(main_id)
        if main is None:
            return None
        cur = self.conn.execute(
            "SELECT * FROM plot_index WHERE id = ?",
            (main["current_version"],))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(main)
        result.update(dict(row))
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def plot_set_current(self, main_id: int, version_id: int, chapter: int,
                         chapters: str = None, ended: int = None,
                         end_notes: str = None):
        """更新 plot_main 指向当前版本，可同时更新 chapters/ended/end_notes"""
        sql = ("UPDATE plot_main SET current_version = ?, updated_chapter = ?, "
               "updated_at = ?")
        params: list = [version_id, chapter, _now()]
        if chapters is not None:
            sql += ", chapters = ?"
            params.append(chapters)
        if ended is not None:
            sql += ", ended = ?"
            params.append(ended)
        if end_notes is not None:
            sql += ", end_notes = ?"
            params.append(end_notes)
        sql += " WHERE id = ?"
        params.append(main_id)
        self.conn.execute(sql, params)
        self._commit()

    # ── Rules ──

    def rule_create_main(self, name: str, chapter: int) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO rules_main (name, current_version, "
            "created_chapter, updated_chapter, created_at, updated_at) "
            "VALUES (?, 0, ?, ?, ?, ?)",
            (name, chapter, chapter, now, now),
        )
        self._commit()
        return cur.lastrowid

    def rule_get_main(self, main_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM rules_main WHERE id = ?", (main_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def rule_find_main(self, name: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM rules_main WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def rule_list_main(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM rules_main ORDER BY name")
        return [dict(row) for row in cur.fetchall()]

    def rule_update_main(self, main_id: int, **fields) -> bool:
        allowed = {"name", "current_version", "updated_chapter", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates.setdefault("updated_at", _now())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [main_id]
        self.conn.execute(
            f"UPDATE rules_main SET {set_clause} WHERE id = ?", vals)
        self._commit()
        return True

    def rule_create_version(self, main_id: int, chapter: int,
                            data: dict) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO rules_index (main_id, chapter, keywords, description, "
            "state, tags, content, relations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                main_id, chapter,
                data.get("keywords", ""),
                data.get("description", ""),
                data.get("state", ""),
                _ensure_json(data.get("tags", [])),
                data.get("content", ""),
                "[]",  # rules 不参与关系系统
                now,
            ),
        )
        self._commit()
        return cur.lastrowid

    def rule_get_current(self, main_id: int) -> dict | None:
        main = self.rule_get_main(main_id)
        if main is None:
            return None
        cur = self.conn.execute(
            "SELECT * FROM rules_index WHERE id = ?",
            (main["current_version"],))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(main)
        result.update(dict(row))
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def rule_set_current(self, main_id: int, version_id: int, chapter: int):
        self.conn.execute(
            "UPDATE rules_main SET current_version = ?, updated_chapter = ?, "
            "updated_at = ? WHERE id = ?",
            (version_id, chapter, _now(), main_id),
        )
        self._commit()
