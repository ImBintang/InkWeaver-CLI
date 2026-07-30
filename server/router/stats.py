"""Token 统计 HTTP API"""

from fastapi import APIRouter

from server.state import state

router = APIRouter()


@router.get("/api/stats/tokens")
async def get_token_stats(book: str = "", agent: str | None = None, days: int = 30) -> dict:
    """获取 Token 统计汇总"""
    try:
        from tools.db.token_stats import TokenStatsService
        ts = TokenStatsService()
        result = ts.get_summary(
            book=book or state.current_book or None,
            agent=agent,
            days=days,
        )
        ts.close()
        return dict(result)
    except Exception:
        return {"total_input": 0, "total_output": 0, "total": 0, "call_count": 0}


@router.get("/api/stats/tokens/history")
async def get_token_history(limit: int = 50, offset: int = 0) -> list[dict]:
    """获取 Token 消耗历史"""
    try:
        from tools.db.token_stats import TokenStatsService
        ts = TokenStatsService()
        result = ts.get_history(
            limit=limit,
            offset=offset,
            book=state.current_book or None,
        )
        ts.close()
        return list(result)
    except Exception:
        return []
