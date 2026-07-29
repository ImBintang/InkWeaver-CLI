"""Token 统计服务 — 全局独立数据库 .env/token_stats.db"""
import sqlite3
from pathlib import Path
from datetime import datetime

from tools.db.schema import TOKEN_STATS_TABLE


class TokenStatsService:
    """Token 消耗记录的写入与查询"""

    def __init__(self, db_path: Path | str = None):
        if db_path is None:
            # 默认路径：InkWeaver-CLI/.env/token_stats.db
            db_path = Path(__file__).parent.parent.parent / ".env" / "token_stats.db"
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(TOKEN_STATS_TABLE)
        self.conn.commit()

    def record(self, book: str, agent: str, model_id: str = "",
               model_name: str = "", input_tokens: int = 0,
               output_tokens: int = 0, purpose: str = ""):
        """记录一次 LLM 调用的 token 消耗"""
        now = datetime.now().isoformat()
        total = input_tokens + output_tokens
        self.conn.execute(
            "INSERT INTO token_traces (book, agent, model_id, model_name, "
            "input_tokens, output_tokens, total_tokens, purpose, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (book, agent, model_id, model_name, input_tokens, output_tokens,
             total, purpose, now),
        )
        self.conn.commit()

    def get_summary(self, book: str = None, agent: str = None,
                    days: int = 30) -> dict:
        """聚合统计"""
        conditions = []
        params = []
        if book:
            conditions.append("book = ?")
            params.append(book)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if days:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f"-{days} days")

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(input_tokens),0) as total_input, "
            f"COALESCE(SUM(output_tokens),0) as total_output, "
            f"COALESCE(SUM(total_tokens),0) as total, "
            f"COUNT(*) as call_count FROM token_traces{where}",
            params,
        ).fetchone()
        return dict(row)

    def get_history(self, limit: int = 50, offset: int = 0,
                    book: str = None) -> list[dict]:
        """历史记录列表"""
        conditions = []
        params = []
        if book:
            conditions.append("book = ?")
            params.append(book)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT * FROM token_traces{where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
