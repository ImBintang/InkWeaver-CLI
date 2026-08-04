"""SQLite 数据库服务 — 建表、CRUD、版本管理

单例模式，全局一个连接。不感知缓存层。
"""

import functools
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
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


def _count_words(text: str) -> int:
    """统计中文字符数（不含标点/空白），用于字数统计"""
    return sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')


class SQLiteService:
    """SQLite 数据库服务"""

    def __init__(self, db_path: Path):
        # 单连接跨线程访问必须互斥（GUI 请求线程与 Agent flush 线程共享同一连接）
        self._lock = threading.RLock()
        self._tx_depth = 0  # 显式事务嵌套深度，>0 时抑制 _commit 提前提交
        if isinstance(db_path, str) and db_path == ":memory:":
            self.conn = sqlite3.connect(":memory:", check_same_thread=False, timeout=30)
        else:
            self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.auto_commit = True  # 兼容旧调用方；flush 请使用 transaction() 上下文
        self._init_schema()

    def _commit(self):
        """受 auto_commit 控制的提交（显式事务期间跳过），线程安全"""
        if self.auto_commit and self._tx_depth == 0:
            with self._lock:
                self.conn.commit()

    @contextmanager
    def transaction(self):
        """显式事务上下文：期间抑制 _commit，退出时提交（异常则回滚）

        flush 等批量写入必须使用本上下文，避免与其它线程的写操作
        交错提交导致半事务状态。

        v7.0.1: 整个事务边界（_tx_depth 增减 + with conn）纳入 _lock——
        此前仅单条 SQL 加锁，跨线程并发 transaction() 时后退出者会
        提前 COMMIT 前者的未完成写入（半事务/静默丢写）。
        """
        with self._lock:  # RLock：同线程嵌套可重入，跨线程整个事务串行化
            self._tx_depth += 1
            try:
                with self.conn:
                    yield
            finally:
                self._tx_depth -= 1

    def _init_schema(self):
        from tools.db.schema import ALL_TABLES
        full_sql = "\n".join(ALL_TABLES)
        self.conn.executescript(full_sql)
        self.conn.commit()

    def close(self):
        """关闭连接（幂等，重复调用不抛异常）"""
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass  # 已关闭或连接不可用

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
                    "created_chapter", "updated_chapter", "updated_at"}
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
                    "end_notes", "created_chapter", "updated_chapter", "updated_at"}
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
        allowed = {"name", "current_version", "created_chapter",
                    "updated_chapter", "updated_at"}
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

    # ── 版本查询（v5.1 时间线管理）──

    _INDEX_TABLES = {"wiki": "wiki_index", "plot": "plot_index", "rule": "rules_index"}
    _MAIN_TABLES = {"wiki": "wiki_main", "plot": "plot_main", "rule": "rules_main"}

    def list_versions(self, doc_type: str, main_id: int) -> list[dict]:
        """列出某词条的所有版本（按 chapter 升序）"""
        table = self._INDEX_TABLES[doc_type]
        cur = self.conn.execute(
            f"SELECT id, chapter FROM {table} WHERE main_id = ? ORDER BY chapter",
            (main_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_version_by_chapter(self, doc_type: str, main_id: int,
                               chapter: int) -> dict | None:
        """按 updated_chapter 获取指定版本完整内容"""
        table = self._INDEX_TABLES[doc_type]
        cur = self.conn.execute(
            f"SELECT * FROM {table} WHERE main_id = ? AND chapter = ?",
            (main_id, chapter),
        )
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def get_version_by_id(self, doc_type: str, version_id: int) -> dict | None:
        """按索引表主键获取版本完整内容"""
        table = self._INDEX_TABLES[doc_type]
        cur = self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (version_id,))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def update_version(self, doc_type: str, version_id: int, data: dict):
        """原地更新索引行内容（overwrite 用）"""
        table = self._INDEX_TABLES[doc_type]
        self.conn.execute(
            f"UPDATE {table} SET keywords = ?, description = ?, state = ?, "
            f"tags = ?, content = ?, relations = ? WHERE id = ?",
            (
                data.get("keywords", ""),
                data.get("description", ""),
                data.get("state", ""),
                _ensure_json(data.get("tags", [])),
                data.get("content", ""),
                _ensure_json(data.get("relations", [])),
                version_id,
            ),
        )
        self._commit()

    def set_current_version(self, doc_type: str, main_id: int,
                            version_id: int, chapter: int):
        """统一更新 main 表的 current_version 指针"""
        table = self._MAIN_TABLES[doc_type]
        self.conn.execute(
            f"UPDATE {table} SET current_version = ?, updated_chapter = ?, "
            f"updated_at = ? WHERE id = ?",
            (version_id, chapter, _now(), main_id),
        )
        self._commit()

    # ── Chapters CRUD ──

    def chapter_upsert(self, num: int, title: str, content: str):
        """INSERT OR REPLACE 章节"""
        self.conn.execute(
            "INSERT OR REPLACE INTO chapters (chapter_num, title, content, imported_at) "
            "VALUES (?, ?, ?, ?)",
            (num, title, content, _now()),
        )
        self._commit()

    def chapter_get(self, num: int) -> dict | None:
        """读取单章"""
        cur = self.conn.execute(
            "SELECT chapter_num, title, content, imported_at "
            "FROM chapters WHERE chapter_num = ?", (num,))
        row = cur.fetchone()
        return dict(row) if row else None

    def chapter_get_range(self, nums: list[int]) -> list[dict]:
        """批量读取指定章节号列表"""
        if not nums:
            return []
        placeholders = ",".join("?" * len(nums))
        cur = self.conn.execute(
            f"SELECT chapter_num, title, content, imported_at "
            f"FROM chapters WHERE chapter_num IN ({placeholders}) "
            f"ORDER BY chapter_num",
            nums,
        )
        return [dict(r) for r in cur.fetchall()]

    def chapter_list_all(self) -> list[dict]:
        """列出所有章节（num + title）"""
        cur = self.conn.execute(
            "SELECT chapter_num, title FROM chapters ORDER BY chapter_num")
        return [dict(r) for r in cur.fetchall()]

    def chapter_list_all_with_count(self) -> list[dict]:
        """列出所有章节（num + title + word_count + imported_at + draft_count），供 GUI 章节管理展示

        word_count 按中文字符计数（去除空白和标点），与 Muse/Editor 保持一致。
        imported_at 为该章节导入时间戳（Unix epoch）。
        draft_count 为该章节拥有的草稿数量。
        """
        cur = self.conn.execute(
            "SELECT chapter_num, title, content, imported_at FROM chapters ORDER BY chapter_num")
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            content = d.pop("content", "") or ""
            d["word_count"] = _count_words(content)
            d["draft_count"] = self._count_drafts_for_chapter(d["chapter_num"])
            rows.append(d)
        return rows

    def _count_drafts_for_chapter(self, chapter_num: int) -> int:
        """返回某章节的草稿数量"""
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE chapter_num = ?", (chapter_num,))
        return cur.fetchone()[0]

    def chapter_max_num(self) -> int:
        """返回最大章节号，无章节时返回 0"""
        cur = self.conn.execute("SELECT MAX(chapter_num) FROM chapters")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0

    def chapter_count(self) -> int:
        """返回章节总数"""
        cur = self.conn.execute("SELECT COUNT(*) FROM chapters")
        return cur.fetchone()[0]

    def chapter_delete_all(self):
        """清空所有章节（重新导入前使用）"""
        self.conn.execute("DELETE FROM chapters")
        self._commit()

    # ── 物理删除（delete_doc 的 flush 落库） ──

    def wiki_delete(self, main_id: int):
        """物理删除词条及其全部版本记录"""
        self.conn.execute(
            "DELETE FROM wiki_index WHERE main_id = ?", (main_id,))
        self.conn.execute(
            "DELETE FROM wiki_main WHERE id = ?", (main_id,))
        self._commit()

    def plot_delete(self, main_id: int):
        """物理删除剧情卡片及其全部版本记录"""
        self.conn.execute(
            "DELETE FROM plot_index WHERE main_id = ?", (main_id,))
        self.conn.execute(
            "DELETE FROM plot_main WHERE id = ?", (main_id,))
        self._commit()

    def rule_delete(self, main_id: int):
        """物理删除规则文档及其全部版本记录"""
        self.conn.execute(
            "DELETE FROM rules_index WHERE main_id = ?", (main_id,))
        self.conn.execute(
            "DELETE FROM rules_main WHERE id = ?", (main_id,))
        self._commit()

    # ── 版本卡控查询（v5.3 妙笔章节锚定）──

    def latest_version_at(self, doc_type: str, main_id: int,
                          ceiling: int) -> dict | None:
        """获取 chapter ≤ ceiling 的最新版本

        Args:
            doc_type: "wiki" | "plot" | "rule"
            main_id: 主表 ID
            ceiling: 章节上限（含）

        Returns:
            版本行 dict，或 None（无符合版本）
        """
        table = self._INDEX_TABLES[doc_type]
        cur = self.conn.execute(
            f"SELECT * FROM {table} WHERE main_id = ? AND chapter <= ? "
            f"ORDER BY chapter DESC LIMIT 1",
            (main_id, ceiling),
        )
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tags"] = _parse_json(result.get("tags", "[]"))
        result["relations"] = _parse_json(result.get("relations", "[]"))
        return result

    def wiki_list_main_at(self, category_id: int, ceiling: int) -> list[dict]:
        """列出 created_chapter ≤ ceiling 的 wiki 词条"""
        cur = self.conn.execute(
            "SELECT * FROM wiki_main WHERE category_id = ? AND created_chapter <= ? "
            "ORDER BY name",
            (category_id, ceiling),
        )
        return [dict(row) for row in cur.fetchall()]

    def plot_list_main_at(self, ceiling: int) -> list[dict]:
        """列出 created_chapter ≤ ceiling 的剧情卡片"""
        cur = self.conn.execute(
            "SELECT * FROM plot_main WHERE created_chapter <= ? ORDER BY name",
            (ceiling,),
        )
        return [dict(row) for row in cur.fetchall()]

    def rule_list_main_at(self, ceiling: int) -> list[dict]:
        """列出 created_chapter ≤ ceiling 的规则"""
        cur = self.conn.execute(
            "SELECT * FROM rules_main WHERE created_chapter <= ? ORDER BY name",
            (ceiling,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── Memories CRUD（v5.3 记忆系统）──

    def memory_create(self, category: str, content: str,
                      source: str = None, chapter: int = None) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO memories (category, content, source, chapter, "
            "created_at, updated_at, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (category, content, source, chapter, now, now),
        )
        self._commit()
        return cur.lastrowid

    def memory_get(self, memory_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def memory_update(self, memory_id: int, content: str = None) -> bool:
        updates = {}
        if content is not None:
            updates["content"] = content
        if not updates:
            return False
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [memory_id]
        self.conn.execute(
            f"UPDATE memories SET {set_clause} WHERE id = ?", vals)
        self._commit()
        return True

    def memory_forget(self, memory_id: int) -> bool:
        """软删除（is_active=0）"""
        self.conn.execute(
            "UPDATE memories SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now(), memory_id),
        )
        self._commit()
        return True

    def memory_query(self, category: str = None, keyword: str = None,
                     limit: int = 20) -> list[dict]:
        """检索活跃记忆（支持类别过滤 + 关键词模糊匹配）"""
        sql = "SELECT * FROM memories WHERE is_active = 1"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if keyword:
            sql += " AND content LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def memory_list_active(self, category: str = None) -> list[dict]:
        """列出所有活跃记忆"""
        return self.memory_query(category=category, limit=999)

    # ── Drafts CRUD（v6.0 草稿系统）──

    def draft_create(self, chapter_num: int, content: str,
                     source: str = "user", title: str = "") -> int:
        from datetime import datetime
        now = datetime.now().isoformat()
        word_count = _count_words(content)
        cur = self.conn.execute(
            "INSERT INTO drafts (chapter_num, title, content, source, "
            "created_at, updated_at, word_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chapter_num, title, content, source, now, now, word_count),
        )
        self._commit()
        return cur.lastrowid

    def draft_get(self, draft_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def draft_update(self, draft_id: int, content: str = None, title: str = None) -> bool:
        """更新草稿内容/标题（部分更新）"""
        from datetime import datetime
        now = datetime.now().isoformat()
        draft = self.draft_get(draft_id)
        if not draft:
            return False
        new_content = content if content is not None else draft.get("content", "")
        new_title = title if title is not None else draft.get("title", "")
        word_count = _count_words(new_content)
        self.conn.execute(
            "UPDATE drafts SET content = ?, title = ?, word_count = ?, "
            "updated_at = ? WHERE id = ?",
            (new_content, new_title, word_count, now, draft_id),
        )
        self._commit()
        return True

    def draft_list(self, chapter_num: int = None) -> list[dict]:
        if chapter_num is not None:
            cur = self.conn.execute(
                "SELECT * FROM drafts WHERE chapter_num = ? ORDER BY created_at DESC",
                (chapter_num,))
        else:
            cur = self.conn.execute(
                "SELECT * FROM drafts ORDER BY created_at DESC")
        return [dict(row) for row in cur.fetchall()]

    def draft_delete(self, draft_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        self._commit()
        return cur.rowcount > 0

    def draft_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM drafts")
        return cur.fetchone()[0]


# ── 线程安全包装：所有公共方法自动加锁 ────────────────────────────────
# 单连接跨线程是设计事实（Agent flush 线程 + GUI 请求线程共享同一连接），
# 用 RLock 串行化所有公共方法调用；flush 的整段事务在锁内执行，
# 避免“接口成功、数据丢失”的静默写竞争。

def _synchronized(method):
    @functools.wraps(method)
    def _impl(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return _impl


for _name in list(SQLiteService.__dict__):
    if not _name.startswith("_"):
        setattr(SQLiteService, _name, _synchronized(getattr(SQLiteService, _name)))
