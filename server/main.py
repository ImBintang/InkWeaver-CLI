"""FastAPI 应用实例 — 被 inkweaver serve 子命令导入"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from server.router.books import router as books_router
from server.router.chat import router as chat_router
from server.router.muse import router as muse_router
from server.router.knowledge import router as knowledge_router
from server.router.sessions import router as sessions_router
from server.router.settings import router as settings_router
from server.router.stats import router as stats_router
from server.sse import router as sse_router

app = FastAPI(title="InkWeaver Server", version="6.2.0")

# CORS — 允许 Vite dev server 跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册业务路由
app.include_router(books_router)
app.include_router(chat_router)
app.include_router(muse_router)
app.include_router(knowledge_router)
app.include_router(sessions_router)
app.include_router(settings_router)
app.include_router(stats_router)
app.include_router(sse_router)


# 生产模式：托管前端打包产物
# 静态文件路径 = InkWork CLI 目录向上一级再进入 InkWeaver-GUI/frontend/dist
DIST = Path(__file__).parent.parent.parent / "InkWeaver-GUI" / "frontend" / "dist"
if DIST.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(DIST), html=True),
        name="frontend",
    )
