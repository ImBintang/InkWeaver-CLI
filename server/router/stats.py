"""Token 统计 HTTP API"""

from fastapi import APIRouter, HTTPException

from server.state import state

router = APIRouter()


@router.get("/api/stats/tokens")
async def get_token_stats(book: str = "", agent: str | None = None, days: int = 30) -> dict:
    """获取 Token 统计汇总"""
    try:
        from tools.db.token_stats import TokenStatsService
        ts = TokenStatsService()
        try:
            result = ts.get_summary(
                book=book or state.current_book or None,
                agent=agent,
                days=days,
            )
        finally:
            # P1-40：异常路径也必须关闭连接，避免句柄累积
            ts.close()
        return dict(result)
    except Exception as e:
        # 不静默：统计失败返回 500 而非全 0 假数据，前端才能提示用户
        raise HTTPException(500, detail=f"获取 Token 统计失败：{e}")


@router.get("/api/stats/tokens/history")
async def get_token_history(limit: int = 50, offset: int = 0) -> list[dict]:
    """获取 Token 消耗历史"""
    try:
        from tools.db.token_stats import TokenStatsService
        ts = TokenStatsService()
        try:
            result = ts.get_history(
                limit=limit,
                offset=offset,
                book=state.current_book or None,
            )
        finally:
            # P1-40：异常路径也必须关闭连接
            ts.close()
        return list(result)
    except Exception as e:
        # 不静默：返回 500 而非空列表
        raise HTTPException(500, detail=f"获取 Token 历史失败：{e}")
