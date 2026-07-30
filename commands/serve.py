"""serve 命令 — 启动 InkWeaver FastAPI 后端"""

import typer

def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="绑定地址"),
    port: int = typer.Option(8000, "--port", "-p", help="绑定端口"),
    reload: bool = typer.Option(False, "--reload", help="开发模式（代码变动自动重启）"),
):
    """启动 InkWeaver FastAPI 后端（前后端一体化服务）"""
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
