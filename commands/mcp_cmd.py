"""mcp 命令 — 启动 InkWeaver MCP Server（v7.0.0）

用法：
  inkweaver mcp                              # stdio 模式（Qoder/Claude Desktop 等接入）
  inkweaver mcp --workspace 补天纪            # 绑定默认工作区
  inkweaver mcp --transport streamable-http --port 8100  # HTTP 模式
"""

import typer


def mcp(
    workspace: str = typer.Option("", "--workspace", "-w", help="绑定默认工作区名"),
    transport: str = typer.Option("stdio", "--transport", "-t",
                                  help="传输方式：stdio / streamable-http"),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP 模式监听地址"),
    port: int = typer.Option(8100, "--port", help="HTTP 模式监听端口"),
):
    """启动 MCP Server（供 Qoder/Claude/Cursor 等 Agent 应用接入）"""
    # 延迟导入：mcp SDK 依赖链较重，且 stdio 模式下此前不能有任何 stdout 输出
    from mcp_server.server import serve
    serve(workspace=workspace, transport=transport, host=host, port=port)
