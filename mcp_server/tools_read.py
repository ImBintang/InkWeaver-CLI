"""MCP 只读原子工具 — 工作区/章节/知识库/体检/统计/技能清单

全部为同步工具，直接复用 tools/* 层函数，不含 LLM 调用。
"""

from mcp_server.context import MCPContext


def _ok(**data) -> dict:
    return {"status": "success", **data}


def _err(e: Exception) -> dict:
    return {"status": "error", "message": str(e)}


def register_read_tools(mcp, ctx: MCPContext):
    """注册只读工具到 FastMCP 实例"""

    # ── 服务器与工作区 ──────────────────────────────────────

    @mcp.tool()
    def server_info() -> dict:
        """查看 InkWeaver MCP 服务器信息：版本、当前绑定工作区、工作区目录、
        内置 LLM 可用性（决定子智能体任务工具能否使用）"""
        try:
            from api import validate_api_key
            config = ctx.config()
            models = [{"id": m.get("id"), "model": m.get("model")}
                      for m in config.get("models", [])]
            # v7.1.0：报告内置 LLM key 可用性，供编排者选择双模式
            bad = [m.get("id", "?") for m in config.get("models", [])
                   if validate_api_key(m.get("api_key", ""))]
            llm_ready = bool(models) and not bad
            if llm_ready:
                llm_hint = "内置 LLM 已配置：可用 ask_jianzhi/extract_knowledge/muse_write 子智能体任务"
            elif not models:
                llm_hint = ("未配置任何模型：子智能体任务不可用，请用外部编排模式"
                            "（chapter_read 读原文 + kb_create/rule_create/plot_create 落库 + kb_commit 提交）")
            else:
                llm_hint = (f"以下模型的 api_key 未配置或为占位符：{', '.join(bad)}。"
                            "子智能体任务不可用，请改用外部编排模式，"
                            "或在 .env/config.yaml 配置真实 key")
            return _ok(
                app="InkWeaver", version="7.2.0",
                bound_workspace=ctx.bound_workspace or "(未绑定，使用默认)",
                current_workspace=ctx.current_workspace_name(),
                workspaces_dir=str(ctx.workspaces_dir()),
                models=models,
                assignments=config.get("assignments", {}),
                llm_ready=llm_ready,
                llm_hint=llm_hint,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def list_workspaces() -> dict:
        """列出所有工作区（书籍项目）"""
        try:
            from tools.workspace import list_workspaces as _list
            return _ok(answer=_list(ctx.workspaces_dir()))
        except Exception as e:
            return _err(e)

    # ── 章节 ──────────────────────────────────────────────

    @mcp.tool()
    def chapter_list(n: int = 50, workspace: str = "") -> dict:
        """列出最新 N 章的章节号与标题

        Args:
            n: 显示最新 N 章，默认 50
            workspace: 工作区名（缺省用绑定/默认工作区）
        """
        try:
            from tools.workspace import list_latest_chapters
            ws = ctx.resolve_ws(workspace)
            # v7.0.1: 限幅防御负数/超大值
            n = max(1, min(int(n), 500))
            return _ok(answer=list_latest_chapters(ws, n))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_status(workspace: str = "") -> dict:
        """查看章节处理状态（含知识提取已处理/未处理标记）"""
        try:
            from tools.chapter import chapter_list
            ws = ctx.resolve_ws(workspace)
            return _ok(answer=chapter_list(ws))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_show(num: int, workspace: str = "") -> dict:
        """查看某一章的完整内容（标题+正文）

        Args:
            num: 章节号
            workspace: 工作区名
        """
        try:
            from tools.chapter import show_chapter
            ws = ctx.resolve_ws(workspace)
            result = show_chapter(ws, num)
            if result.startswith("错误"):
                return _err(ValueError(result))
            return _ok(answer=result)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def chapter_read(spec: str, workspace: str = "") -> dict:
        """按范围批量读取章节正文（用于外部 Agent 获取原始文本）

        Args:
            spec: 章节范围表达式，如 "1-3"、"1,3,5"、"2-"
            workspace: 工作区名
        """
        try:
            from tools.chapter import read_chapters
            ws = ctx.resolve_ws(workspace)
            return _ok(answer=read_chapters(ws, spec))
        except Exception as e:
            return _err(e)

    # ── 知识库（wiki/rule/plot）──────────────────────────────

    @mcp.tool()
    def kb_categories(workspace: str = "") -> dict:
        """列出知识库所有类别（含类别说明）"""
        try:
            from tools.editor import _get_proxy
            ws = ctx.resolve_ws(workspace)
            cats = _get_proxy(ws).list_categories()
            return _ok(categories=cats)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def kb_list(type_filter: str = "", category: str = "", workspace: str = "") -> dict:
        """列出知识库条目（wiki 词条/plot 剧情卡片/rule 规则）

        Args:
            type_filter: 按类型过滤 wiki/plot/rule，空=全部
            category: 按类别过滤（仅对 wiki 有效）
            workspace: 工作区名
        """
        try:
            from tools.editor import _get_proxy
            ws = ctx.resolve_ws(workspace)
            proxy = _get_proxy(ws)
            lines = []
            if not type_filter or type_filter == "wiki":
                for cat in proxy.list_categories():
                    if category and cat["name"] != category:
                        continue
                    for m in proxy._db.wiki_list_main(cat["id"]):
                        lines.append(f"[wiki/{cat['name']}] {m['name']}")
            if (not type_filter or type_filter == "plot") and not category:
                for m in proxy._db.plot_list_main():
                    lines.append(f"[plot] {m['name']}")
            if (not type_filter or type_filter == "rule") and not category:
                for m in proxy._db.rule_list_main():
                    lines.append(f"[rule] {m['name']}")
            return _ok(answer="\n".join(lines) if lines else "（知识库为空）",
                       count=len(lines))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def kb_show(name: str, version: int = 0, workspace: str = "") -> dict:
        """查看知识库条目详情（自动遍历 wiki/plot/rule 定位），wiki/rule/plot 均返回全文。

        当前版本（version=0，默认）：缓存优先，暂存未提交（未 kb_commit）的
        新增/修改内容同样可见，用于合并更新等需要最新内容的场景。
        历史版本（version=N）：读取该条目 updated_chapter=N 的历史版本快照
        （仅已 kb_commit 落库的内容有历史版本；传错版本会返回可用版本列表）。

        Args:
            name: 条目名称
            version: 历史版本章节号（即该版本的 updated_chapter），0=当前版本
            workspace: 工作区名
        """
        try:
            from tools.editor import _get_proxy
            ws = ctx.resolve_ws(workspace)
            version = max(0, int(version))
            proxy = _get_proxy(ws)

            def _kb_read(doc_type: str, name: str, version: int,
                         category: str = None) -> tuple:
                """读取一类条目：返回 (result, found)

                found=True 表示已命中（成功，或条目存在但版本不存在——
                后者错误消息已含可用版本列表，直接透传给宿主）。
                """
                if version > 0:
                    result = proxy.read_doc_version(doc_type, name, version,
                                                    yaml_only=False)
                    if result.startswith("错误"):
                        if "可用版本" in result:
                            return result, True   # 条目存在、版本不对 → 透传
                        return result, False      # 条目不存在 → 继续遍历其他类型
                    return result, True
                if doc_type == "wiki":
                    result = proxy.read_doc(doc_type, name, category=category,
                                            yaml_only=False)
                else:
                    result = proxy.read_doc(doc_type, name, yaml_only=False)
                return result, not result.startswith("错误")

            for cat in proxy.list_categories():
                result, found = _kb_read("wiki", name, version, category=cat["name"])
                if found:
                    if result.startswith("错误"):
                        return _err(ValueError(result))
                    return _ok(type="wiki", category=cat["name"], answer=result)
            result, found = _kb_read("plot", name, version)
            if found:
                if result.startswith("错误"):
                    return _err(ValueError(result))
                return _ok(type="plot", answer=result)
            # v7.2.0: rule 分支必须 yaml_only=False 返回全文，与 wiki/plot 一致；
            # 否则外部编排 LLM 无法拿到规则全文（MCP 无独立 read_rule 工具，
            # kb_show 又不暴露 yaml_only），rule_edit 合并更新会卡死。
            result, found = _kb_read("rule", name, version)
            if found:
                if result.startswith("错误"):
                    return _err(ValueError(result))
                return _ok(type="rule", answer=result)
            return _err(KeyError(f"条目「{name}」不存在（已遍历 wiki/plot/rule）"))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def kb_relation(name: str, workspace: str = "") -> dict:
        """查询词条的关联关系（与其他词条/剧情卡片的双向链接）

        Args:
            name: 词条名
            workspace: 工作区名
        """
        try:
            from tools.relation import query_relations
            ws = ctx.resolve_ws(workspace)
            return _ok(answer=query_relations(ws, name))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def kb_memory(category: str = "", workspace: str = "") -> dict:
        """列出活跃记忆（作者偏好/观察/纠正/文风）

        Args:
            category: 按类别过滤 preference/observation/correction/style，空=全部
            workspace: 工作区名
        """
        try:
            from tools.memory import list_memories
            ws = ctx.resolve_ws(workspace)
            memories = list_memories(ws)
            if category:
                memories = [m for m in memories if m["category"] == category]
            return _ok(memories=memories, count=len(memories))
        except Exception as e:
            return _err(e)

    # ── 质量体检 ──────────────────────────────────────────

    @mcp.tool()
    def lint_run(workspace: str = "") -> dict:
        """对知识库执行全量健康检查（Lint），返回问题摘要并写入债务档案

        注意：会更新工作区内的 lint 债务文件（debts.json），属于轻量写操作。
        """
        try:
            from tools.lint import run_lint
            ws = ctx.resolve_ws(workspace)
            return _ok(answer=run_lint(ws))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def lint_debt(workspace: str = "") -> dict:
        """读取当前知识库债务档案（未解决的 Lint 问题清单）"""
        try:
            from tools.lint import read_debt
            ws = ctx.resolve_ws(workspace)
            return _ok(answer=read_debt(ws))
        except Exception as e:
            return _err(e)

    # ── 统计 ──────────────────────────────────────────────

    @mcp.tool()
    def token_stats(book: str = "", agent: str = "", limit: int = 20) -> dict:
        """查询 LLM token 消耗统计（全局，可按书/Agent 过滤）

        Args:
            book: 按书名（工作区名）过滤，空=全部
            agent: 按 Agent 名过滤（jianzhi/muse 等），空=全部
            limit: 返回最近 N 条明细，默认 20
        """
        try:
            from tools.db.token_stats import TokenStatsService
            svc = TokenStatsService()
            try:
                # v7.0.1: limit 限幅防超大值拖垮查询
                limit = max(1, min(int(limit), 200))
                summary = svc.get_summary(book=book or None, agent=agent or None)
                history = svc.get_history(limit=limit)
            finally:
                svc.close()
            return _ok(summary=summary, history=history)
        except Exception as e:
            return _err(e)

    # ── 技能（内部 Agent 的方法论文档）──────────────────────

    @mcp.tool()
    def list_skills() -> dict:
        """列出 InkWeaver 内部 Agent 技能清单（鉴知提取/妙笔写作/审阅等方法论）

        这些技能驱动内部子 Agent；外部 Agent 通常不需要直接读取，
        而是通过 ask_jianzhi / extract_knowledge / muse_write 任务间接使用。
        """
        try:
            from agent.skill import SkillRegistry
            from commands.common import SKILLS_DIR
            registry = SkillRegistry(SKILLS_DIR)
            skills = [{"name": n,
                       "description": registry.documents[n].manifest.description}
                      for n in registry.skill_names()]
            return _ok(skills=skills, count=len(skills))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def read_skill(name: str) -> dict:
        """读取指定技能的完整方法论文档

        Args:
            name: 技能名（先用 list_skills 查看可用名称）
        """
        try:
            from agent.skill import SkillRegistry
            from commands.common import SKILLS_DIR
            registry = SkillRegistry(SKILLS_DIR)
            text = registry.load_full_text(name)
            if text.startswith("错误"):
                return _err(KeyError(text))
            return _ok(name=name, content=text)
        except Exception as e:
            return _err(e)
