"""MCP Server 组装 — FastMCP 实例 + 工具注册 + 传输启动

关键点：
- stdio 传输下 stdout 是协议通道，任何业务 print 都会破坏 JSON-RPC 帧，
  因此 run_stdio 前必须把 sys.stdout 重定向到 stderr
- 业务模块全部延迟导入（含 tiktoken 依赖链），保证 server 进程秒启
"""

import os
import sys
from pathlib import Path

SERVER_NAME = "inkweaver"

# 项目根目录（InkWeaver-CLI/）——配置/工作区相对路径都以此为锚
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INSTRUCTIONS = """InkWeaver 小说知识库与写作辅助系统（MCP 版）。

能力分三层：
1. 只读查询（同步）：list_workspaces / chapter_* / kb_* / lint_* / token_stats
2. 写操作（同步，描述中标注副作用）：create_workspace / chapter_import / chapter_write 等
3. 子智能体任务（异步）：ask_jianzhi（鉴知问答）、extract_knowledge（知识提取）、
   muse_write（妙笔四步写作）。异步任务返回 task_id，用 task_wait/task_status 查询、
   task_confirm 响应挂起确认、task_result 获取成果、task_cancel 取消。

推荐工作流：
- 了解书籍：list_workspaces → chapter_status → kb_list
- 设定考证：ask_jianzhi
- 沉淀知识：extract_knowledge（auto_approve=false 时注意处理 awaiting_confirmation）
- 续写章节：chapter_status（确认最新章节）→ muse_write(outline=本章大纲)
"""


def build_server(workspace: str = ""):
    """构建 FastMCP 实例并注册全部工具"""
    from mcp.server.fastmcp import FastMCP

    from mcp_server.context import MCPContext
    from mcp_server.tasks import TaskManager
    from mcp_server.tools_read import register_read_tools
    from mcp_server.tools_write import register_write_tools
    from mcp_server.tools_agent import register_agent_tools

    mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    ctx = MCPContext(workspace_name=workspace)
    tasks = TaskManager()

    register_read_tools(mcp, ctx)
    register_write_tools(mcp, ctx)
    register_agent_tools(mcp, ctx, tasks)
    return mcp


def serve(workspace: str = "", transport: str = "stdio",
          host: str = "127.0.0.1", port: int = 8100):
    """启动 MCP Server

    Args:
        workspace: 绑定的默认工作区名
        transport: stdio（Qoder/Claude Desktop 等本地接入）
                   或 streamable-http（远程/多客户端接入）
        host/port: streamable-http 模式的监听地址
    """
    # 防呆：部分 MCP 客户端（如 Qoder 未配 cwd 时）会在任意目录拉起进程，
    # 而 config.yaml 的 workspace.dir 是相对路径（../workingArea），
    # 统一把 cwd 锚定到项目根，保证工作区解析与产物落盘位置稳定
    try:
        os.chdir(PROJECT_ROOT)
    except OSError:
        pass
    if transport == "stdio":
        # 协议通道是 sys.stdout.buffer（mcp SDK 直接包装原始缓冲区），
        # 不能替换 sys.stdout 对象；业务 print 改走自定义钩子：
        # 把 print 的默认 file 重定向到 stderr，防止污染 JSON-RPC 帧
        import builtins
        _orig_print = builtins.print

        def _safe_print(*args, **kwargs):
            kwargs.setdefault("file", sys.stderr)
            _orig_print(*args, **kwargs)

        builtins.print = _safe_print
        mcp = build_server(workspace)
        mcp.run(transport="stdio")
    elif transport in ("streamable-http", "http"):
        mcp = build_server(workspace)
        mcp.settings.host = host
        mcp.settings.port = port
        print(f"[mcp] InkWeaver MCP Server (streamable-http) http://{host}:{port}/mcp",
              file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        raise ValueError(f"未知传输方式：{transport}（可选 stdio / streamable-http）")
