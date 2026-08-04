"""MCP 写操作工具 — 工作区/章节/记忆的变更类操作

安全约定：所有写工具在描述中标注副作用；导入/覆盖类操作提供
overwrite/append 显式参数，不做隐式交互确认（MCP 无 TTY）。
"""

from mcp_server.context import MCPContext


def _ok(**data) -> dict:
    return {"status": "success", **data}


def _err(e: Exception) -> dict:
    return {"status": "error", "message": str(e)}


def register_write_tools(mcp, ctx: MCPContext):
    """注册写操作工具到 FastMCP 实例"""

    @mcp.tool()
    def create_workspace(name: str) -> dict:
        """创建新工作区（书籍项目）。【写操作】创建新目录与初始结构。

        Args:
            name: 工作区名（中文/字母/数字/下划线/连字符，禁止路径穿越字符）
        """
        try:
            from tools.workspace import create_workspace as _create
            ws = _create(ctx.workspaces_dir(), name)
            if ws is None:
                return _err(ValueError(f"工作区创建失败：{name}（名称非法或已存在）"))
            return _ok(message=f"工作区已创建：{name}", path=str(ws))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_import(file_path: str, append: bool = False, overwrite: bool = False,
                       workspace: str = "") -> dict:
        """导入小说文件（按章节标题自动拆分入库）。【写操作·高危】

        Args:
            file_path: 小说 txt 文件的绝对路径
            append: True=增量导入（不覆盖已有章节）；False=全量导入
            overwrite: 全量导入时，若已有章节必须显式传 True 确认清空重导
            workspace: 工作区名
        """
        try:
            from tools import workspace as workspace_tools
            from tools.editor import _get_proxy
            ws = ctx.resolve_ws(workspace)

            err = workspace_tools.check_novel_file(file_path)
            if err:
                return _err(ValueError(f"无法导入：{file_path}（{err}）"))

            existing = _get_proxy(ws)._db.chapter_count()
            if existing > 0 and not append:
                if not overwrite:
                    return _err(ValueError(
                        f"工作区已有 {existing} 章，全量导入将清空重导。"
                        f"确认后请重新调用并传 overwrite=true，或改用 append=true"))
                with _get_proxy(ws)._db.transaction():
                    _get_proxy(ws)._db.chapter_delete_all()
                    result = workspace_tools.import_novel(ws, file_path)
            else:
                result = workspace_tools.import_novel(ws, file_path)

            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(message=result)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_export(overwrite: bool = False, workspace: str = "") -> dict:
        """合并所有章节为单个 txt 文件（导出到工作区目录）。【写操作】

        Args:
            overwrite: 若导出文件已存在，必须显式传 True 确认覆盖
            workspace: 工作区名
        """
        try:
            from tools.workspace import export_novel
            ws = ctx.resolve_ws(workspace)
            txt_path = ws / f"{ws.name}.txt"
            if txt_path.exists() and not overwrite:
                return _err(ValueError(
                    f"文件 {txt_path.name} 已存在，确认后请重新调用并传 overwrite=true"))
            result = export_novel(ws)
            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(message=result, path=str(txt_path))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_write(num: int, content: str, workspace: str = "") -> dict:
        """写入/覆盖指定章节的正文内容。【写操作·高危】直接改章节文件。

        Args:
            num: 章节号
            content: 章节完整正文（不含标题行，标题由系统按章节号生成规范维护）
            workspace: 工作区名
        """
        try:
            from tools.chapter import write_chapter
            ws = ctx.resolve_ws(workspace)
            result = write_chapter(ws, num, content)
            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(message=result)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def memory_write(content: str, category: str = "preference", workspace: str = "") -> dict:
        """写入一条作者记忆（供鉴知 Agent 后续对话使用）。【写操作】

        Args:
            content: 记忆内容
            category: preference/observation/correction/style，默认 preference
            workspace: 工作区名
        """
        try:
            from tools.memory import memory_write as _write
            ws = ctx.resolve_ws(workspace)
            result = _write(ws, category=category, content=content, source="mcp")
            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(message=result)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def memory_forget(memory_id: int, workspace: str = "") -> dict:
        """删除指定记忆。【写操作】

        Args:
            memory_id: 记忆 ID（先用 kb_memory 查询）
            workspace: 工作区名
        """
        try:
            from tools.memory import memory_forget as _forget
            ws = ctx.resolve_ws(workspace)
            result = _forget(ws, id=memory_id)
            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(message=result)
        except Exception as e:
            return _err(e)
