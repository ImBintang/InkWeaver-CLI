"""MCP 知识写工具 — 外部编排模式的知识落库通道

让宿主编排 LLM 自己做「读章节 → 抽知识」推理后经这组工具落库，
不依赖 InkWeaver 内置 LLM（api_key），与内部子智能体模式互为替代。

持久化契约（与内部鉴知一致的两段式）：
- kb_create / kb_edit / rule_* / plot_* 只写 proxy 缓存（kb_show 等读工具可见），
  此时尚未写入 DB 版本。
- kb_commit 统一落库：finish_task 校验存在性 + 写 log.json + 标记章节已处理
  + lint 体检（自动修复）+ proxy.flush 版本快照与 wikilink 关系解析。
- commit 校验失败时缓存保留，修复后可重新 commit。

设计说明：外部模式下宿主 LLM 即编排者，计划审批职责由宿主自身的
用户确认能力承担，因此这里不再套内部 permission 审批门。
"""

from mcp_server.context import MCPContext


def _ok(**data) -> dict:
    return {"status": "success", **data}


def _err(e: Exception) -> dict:
    return {"status": "error", "message": str(e)}


def _result_text(result: str) -> dict:
    """tools 层函数统一返回字符串，'错误' 前缀转 error 结果"""
    if result.startswith("错误"):
        return _err(ValueError(result))
    return _ok(message=result)


def register_kb_tools(mcp, ctx: MCPContext):
    """注册知识写工具组到 FastMCP 实例"""

    # ── 类别 ──────────────────────────────────────────────

    @mcp.tool()
    def category_create(name: str, description: str = "",
                        writing_guide: str = "", has_state: bool = False,
                        workspace: str = "") -> dict:
        """创建知识库类别（wiki 词条的分类体系）。【写操作】立即生效，无需 kb_commit。

        整本书首次沉淀知识前，先用本工具建好类别（如：人物/势力/地图/物品）。

        Args:
            name: 类别名（禁止创建「宝物」这类模糊类别，用具体类别或「世界观」兜底）
            description: 类别说明（收入 index.md）
            writing_guide: 该类别词条的写作规范（正文结构要求）
            has_state: True=该类别词条需要 state 字段（人物/势力等有状态实体）
            workspace: 工作区名
        """
        try:
            from tools.category import new_category
            ws = ctx.resolve_ws(workspace)
            return _result_text(new_category(ws, name, description,
                                             writing_guide, has_state))
        except Exception as e:
            return _err(e)

    # ── wiki 词条 ─────────────────────────────────────────

    @mcp.tool()
    def kb_create(category: str, name: str, content: str,
                  description: str = "", state: str = "",
                  keywords: str = "", chapter: int = 0,
                  workspace: str = "") -> dict:
        """新建 wiki 词条（人物/地点/物品等实体）。【写操作】暂存缓存，kb_commit 后落库。

        Args:
            category: 所属类别（必须已存在，先 kb_categories 查看或 category_create 创建）
            name: 词条名。命名约定：「词条名（别名）」括号内为有效同义词；
                「词条名【说明】」括号内仅消歧义不作为关键词
            content: 正文（建议 ≥300 字，按类别写作规范分段，用 [[词条]] 交叉引用）
            description: 30-80 字一句话概括核心身份（禁止只写名字/职位）
            state: 20-100 字当前状态快照（境界/位置/关系），has_state 类别必填
            keywords: 关键词（逗号分隔）
            chapter: 首次出场章节号（0=commit 时按范围推断）
            workspace: 工作区名
        """
        try:
            from tools.wiki import new_wiki
            ws = ctx.resolve_ws(workspace)
            if not content.strip():
                return _err(ValueError("content 不能为空，词条正文必须提供"))
            return _result_text(new_wiki(
                ws, category=category, name=name, content=content,
                description=description, state=state, keywords=keywords,
                updated=chapter or None))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def kb_edit(name: str, content: str = None, description: str = None,
                state: str = None, keywords: str = None,
                chapter: int = 0, workspace: str = "") -> dict:
        """编辑已有 wiki 词条（字段级更新，传 None 的字段保持不变）。【写操作】
        暂存缓存，kb_commit 后落库。

        Args:
            name: 词条名
            content: 新正文全文（覆盖式）
            description: 新描述
            state: 新状态快照
            keywords: 新关键词
            chapter: 本次更新关联章节号
            workspace: 工作区名
        """
        try:
            from tools.wiki import edit_wiki
            ws = ctx.resolve_ws(workspace)
            return _result_text(edit_wiki(
                ws, category="", name=name, content=content,
                description=description, state=state, keywords=keywords,
                updated=chapter or None))
        except Exception as e:
            return _err(e)

    # ── 规则文档 ──────────────────────────────────────────

    @mcp.tool()
    def rule_create(name: str, content: str, keywords: str = "",
                    chapter: int = 0, workspace: str = "") -> dict:
        """新建世界观规则文档（境界/力量体系/组织架构/时间线等底层设定）。【写操作】
        暂存缓存，kb_commit 后落库。

        分类原则：「定义世界如何运转的底层规则」用本工具；
        「故事中具体出现的人、物、地、事」用 kb_create。
        规则文档禁止包含 [[wikilink]]（不参与关系系统）。

        Args:
            name: 规则名（如「境界体系」）
            content: 规则正文（分级/分类/构成/机制的完整描述）
            keywords: 关键词（逗号分隔）
            chapter: 首次出现章节号
            workspace: 工作区名
        """
        try:
            from tools.rules import new_rule
            ws = ctx.resolve_ws(workspace)
            if not content.strip():
                return _err(ValueError("content 不能为空，规则正文必须提供"))
            return _result_text(new_rule(
                ws, name=name, content=content, keywords=keywords,
                updated=chapter or None))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def rule_edit(name: str, content: str, keywords: str = None,
                  chapter: int = 0, workspace: str = "") -> dict:
        """编辑已有规则文档（覆盖正文）。【写操作】暂存缓存，kb_commit 后落库。

        Args:
            name: 规则名
            content: 新正文全文
            keywords: 新关键词（不改传 None）
            chapter: 本次更新关联章节号
            workspace: 工作区名
        """
        try:
            from tools.rules import edit_rule
            ws = ctx.resolve_ws(workspace)
            return _result_text(edit_rule(
                ws, name=name, content=content, keywords=keywords,
                updated=chapter or None))
        except Exception as e:
            return _err(e)

    # ── 剧情卡片 ──────────────────────────────────────────

    @mcp.tool()
    def plot_create(name: str, chapters: str, content: str = "",
                    description: str = "", keywords: str = "",
                    workspace: str = "") -> dict:
        """新建剧情卡片（关键事件/伏笔）。【写操作】暂存缓存，kb_commit 后落库。

        只收录主线关键事件（开篇冲突/转折/高潮/结局），避免堆砌次要事件。

        Args:
            name: 卡片名
            chapters: 覆盖章节范围，如 "1-5,7"
            content: 事件描述正文（可用 [[词条]] 引用实体）
            description: 简述
            keywords: 必填——涉及的核心人物/地点/事件关键词（逗号分隔）
            workspace: 工作区名
        """
        try:
            from tools.plot import new_plot
            ws = ctx.resolve_ws(workspace)
            if not keywords.strip():
                return _err(ValueError("keywords 为必填：请填写卡片涉及的核心人物/地点/事件关键词"))
            return _result_text(new_plot(
                ws, name=name, chapters=chapters, content=content,
                description=description, keywords=keywords))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def plot_edit(name: str, chapters: str = None, content: str = None,
                  description: str = None, keywords: str = None,
                  workspace: str = "") -> dict:
        """编辑已有剧情卡片（字段级更新）。【写操作】暂存缓存，kb_commit 后落库。

        Args:
            name: 卡片名
            chapters: 新覆盖章节范围（剧情延续时扩展）
            content: 新正文全文
            description: 新简述
            keywords: 新关键词
            workspace: 工作区名
        """
        try:
            from tools.plot import edit_plot
            ws = ctx.resolve_ws(workspace)
            return _result_text(edit_plot(
                ws, name=name, chapters=chapters, content=content,
                description=description, keywords=keywords))
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def plot_end(name: str, end_notes: str, workspace: str = "") -> dict:
        """结束一张剧情卡片（剧情线已收尾时调用）。【写操作】

        伏笔回收/剧情线完结后及时结束，避免未结束卡片越积越多。

        Args:
            name: 卡片名
            end_notes: 必填——简述该剧情线如何完结
            workspace: 工作区名
        """
        try:
            from tools.plot import end_plot
            ws = ctx.resolve_ws(workspace)
            if not end_notes.strip():
                return _err(ValueError("end_notes 为必填：请简述该剧情线如何完结"))
            return _result_text(end_plot(ws, name=name, end_notes=end_notes))
        except Exception as e:
            return _err(e)

    # ── 统一提交 ──────────────────────────────────────────

    @mcp.tool()
    def kb_commit(chapters: str, workspace: str = "") -> dict:
        """统一提交本次知识写入：校验 + 版本快照落库 + 标记章节已处理 + lint 体检。
        【写操作·关键】所有 kb_create/kb_edit/rule_*/plot_* 的缓存内容
        必须经本工具才真正写入数据库。

        Args:
            chapters: 本次知识沉淀对应的章节范围，如 "11-20"
                （决定版本快照章节号与「已处理」标记）
            workspace: 工作区名
        """
        try:
            from tools.chapter import parse_chapter_spec
            from tools.diff import finish_task
            from tools.editor import _get_proxy

            ws = ctx.resolve_ws(workspace)
            nums = parse_chapter_spec(chapters)
            if not nums:
                return _err(ValueError(f"章节范围格式无效：「{chapters}」"))

            proxy = _get_proxy(ws)
            if not proxy.is_cache_loaded():
                return _err(ValueError(
                    "没有待提交的知识变更（缓存为空）。"
                    "请先用 kb_create/kb_edit/rule_*/plot_* 写入内容"))

            # 从缓存收集本次变更清单（供 finish_task 校验存在性）
            groups = {"wiki": {"new": [], "updated": []},
                      "rule": {"new": [], "updated": []},
                      "plot": {"new": [], "updated": []}}
            for (doc_type, _), doc in proxy._cache.items():
                if doc.is_deleted or doc_type not in groups:
                    continue
                if doc.is_new:
                    groups[doc_type]["new"].append(doc.name)
                elif doc.is_dirty:
                    groups[doc_type]["updated"].append(doc.name)

            # finish_task：校验存在性 + 写 log.json + 标记章节已处理 + lint（自动修复）
            result = finish_task(
                ws, chapters,
                new_wiki=groups["wiki"]["new"],
                updated_wiki=groups["wiki"]["updated"],
                new_rules=groups["rule"]["new"],
                updated_rules=groups["rule"]["updated"],
                new_plots=groups["plot"]["new"],
                updated_plots=groups["plot"]["updated"],
            )
            if result.startswith("错误"):
                return _err(ValueError(result))

            # 版本快照落库（finish_task 内部 flush 只覆盖 lint 自动修复场景，
            # 与内部 knowledge agent 一致：收尾再 flush 一次确保全部落库）
            scope_chapter = max(nums)
            if proxy.is_cache_loaded() and scope_chapter > 0:
                try:
                    proxy.flush(scope_chapter=scope_chapter)
                except Exception as e:
                    return _err(RuntimeError(
                        f"知识校验通过但 DB 落库失败：{e}（缓存仍保留，可重试 kb_commit）"))

            return _ok(message=result, scope=chapters,
                       committed={k: {kk: len(vv) for kk, vv in v.items()}
                                  for k, v in groups.items()})
        except Exception as e:
            return _err(e)
