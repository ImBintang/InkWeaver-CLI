"""MCP 上下文工具 — 宿主 LLM 一键调出工作流上下文（v7.2.0）

设计动机（产品想法）：宿主 LLM 调度子智能体时，"InkWeaver 从知识库加载的
明确上下文块"（审核材料、债务 lint、妙笔知识准备/写作/审核）由服务端组装
并缓存，通过工具调用一键调出，宿主不必自己准备上下文塞给子智能体。

工具组：
- muse_context：写作 LLM 产物包（先验知识 + 前情提要，单章快照 · 用完即丢）
- review_context：审阅包（规则全文 + 人物词条 + 债务清单 + 审阅检查项）
- extract_context：提取包（章节范围 + 类别体系 + 已有词条清单）
- kb_staging：暂存区自检（未 commit 增改删清单，纯只读）

产物缺失返回 {status: "empty", hint}（不是错误）；组装异常层层上报。
"""

from mcp_server.context import MCPContext
from mcp_server.context_cache import (
    KIND_PLOT_SUMMARY,
    KIND_PRIOR_KNOWLEDGE,
)

# kb_staging 单条详情的字段截断长度（摘要视图；截断时返回 truncated 标记与全文指引）
DETAIL_MAX_CHARS = 500


# 审阅检查项（固定文本，与内部 muse_reviewer skill 对齐）
REVIEW_CHECKLIST = """审阅检查项：
1. 设定矛盾：对比规则文档与人物词条，检查言行/能力是否越界
2. 人物 OOC：对比人物 state/description，检查动机与性格是否偏移
3. 伏笔遗漏：对照剧情卡片，确认该回收的伏笔是否回收、该埋的是否埋下
4. 节奏：章节字数是否在 3000-4000 字，剧情推进是否平铺直叙
5. 信息堆砌：知识库词条搬运痕迹是否明显（应通过叙事展现，而非罗列设定）"""


def _ok(**data) -> dict:
    return {"status": "success", **data}


def _err(e: Exception) -> dict:
    return {"status": "error", "message": str(e)}


def _empty(message: str) -> dict:
    return {"status": "empty", "message": message}


def register_context_tools(mcp, ctx: MCPContext):
    """注册上下文工具组到 FastMCP 实例"""

    # ── 写作产物包 ────────────────────────────────────────

    @mcp.tool()
    def muse_context(workspace: str = "", chapter: int = 0) -> dict:
        """一键调出妙笔写作的 LLM 产物包：先验知识 + 前情提要。

        用于写作工作流第三步「新开子智能体起草」：宿主先取本包，连同
        chapter_read 上一章 + kb 检索结果一起注入子智能体 prompt。

        产物是「单章快照 · 用完即丢」：对应最近一次 muse_write 任务
        （新任务启动时旧产物自动作废）。传 chapter 时若与产物章节不符，
        返回 stale=true 提示产物已过期，应先跑 muse_write 或自行组装。

        Args:
            workspace: 工作区名
            chapter: 当前目标章节号（>0 时校验产物是否过期）
        """
        try:
            ws = ctx.resolve_ws(workspace)
        except Exception as e:
            return _err(e)
        ws_name = ws.name
        prior = ctx.cache.get(ws_name, KIND_PRIOR_KNOWLEDGE)
        summary = ctx.cache.get(ws_name, KIND_PLOT_SUMMARY)
        if prior is None and summary is None:
            return _empty(
                "暂无妙笔产物（先验知识/前情提要）。可先运行 muse_write "
                "生成后再取，或用 review_context / extract_context 获取审阅/提取材料。"
            )
        result = {
            "workspace": ws_name,
            "prior_knowledge": prior.content if prior else "",
            "plot_summary": summary.content if summary else "",
        }
        if prior and prior.meta:
            result["chapter"] = prior.meta.get("chapter")
            result["generated_at"] = prior.meta.get("generated_at")
        if chapter > 0 and result.get("chapter") is not None \
                and result["chapter"] != chapter:
            result["stale"] = True
            result["hint"] = (
                f"产物对应第 {result['chapter']} 章，与当前目标第 {chapter} 章不一致。"
                "建议先 muse_write 生成新产物，或自行组装本章材料。"
            )
        if prior is None or summary is None:
            result["partial"] = True
            result["hint"] = "产物不完整：仅生成了一部分，可考虑重新运行 muse_write"
        return _ok(**result)

    # ── 审阅包 ────────────────────────────────────────────

    @mcp.tool()
    def review_context(workspace: str = "", chapter: int = 0,
                       character_limit: int = 10) -> dict:
        """一键调出子智能体审阅所需上下文：规则全文 + 人物词条 + 债务 + 检查项。

        审稿子智能体拿到本包即可审阅，无需再查库。
        适用于：外部编排模式下派审稿子智能体对草稿评分/挑错。

        Args:
            workspace: 工作区名
            chapter: 目标章节号；>0 时只取 updated_chapter ≤ 该章的最近
                人物词条（通常审第 N 章草稿传 N）；0=取全部人物词条
            character_limit: 人物词条条数上限（防爆上下文），默认 10，上限 30
        """
        try:
            from tools.editor import _get_proxy
            from tools.lint import read_debt

            ws = ctx.resolve_ws(workspace)
            proxy = _get_proxy(ws)
        except Exception as e:
            return _err(e)

        try:
            character_limit = max(1, min(int(character_limit), 30))
            chapter = max(0, int(chapter))

            # v7.2.1: 读工具统一缓存优先——暂存（未 kb_commit）的新增/修改
            # 规则与词条对 review_context 也可见，与 kb_show 契约一致
            has_state_cats = {c["id"] for c in proxy.list_categories()
                              if c.get("spec", {}).get("state_required")}
            cat_by_name = {c["name"]: c["id"] for c in proxy.list_categories()}

            # 1) 规则文档全文（DB 全量 + 缓存暂存合并；read_doc 缓存优先）
            rule_names = {m["name"] for m in proxy._db.rule_list_main()}
            staged_rules = {
                d.name for (dt, _), d in proxy._cache.items()
                if dt == "rule" and (d.is_new or d.is_dirty) and not d.is_deleted
            }
            rule_lines = []
            for rname in sorted(rule_names | staged_rules):
                full = proxy.read_doc("rule", rname, yaml_only=False)
                if full.startswith("错误"):
                    continue
                rule_lines.append(f"## 规则：{rname}")
                rule_lines.append(full)
                rule_lines.append("")
            rules_text = "\n".join(rule_lines).strip() or "（无规则文档）"

            # 2) 人物/势力词条（state_required 类别的 wiki，含 state）
            #    DB 列表 + 缓存暂存合并；chapter 过滤后按更新时间倒序
            char_map: dict = {}   # name -> (cat_name, updated_chapter)
            for cat in proxy.list_categories():
                if cat["id"] not in has_state_cats:
                    continue
                for m in proxy._db.wiki_list_main(cat["id"]):
                    char_map[m["name"]] = (cat["name"],
                                            m.get("updated_chapter", 0) or 0)
            for (dt, _), d in proxy._cache.items():
                if dt != "wiki" or d.is_deleted:
                    continue
                if not (d.is_new or d.is_dirty):
                    continue
                cid = cat_by_name.get(d.category or "")
                if cid is not None and cid in has_state_cats:
                    char_map[d.name] = (d.category, d.chapter or 0)
            if chapter > 0:
                char_map = {k: v for k, v in char_map.items()
                            if v[1] and v[1] <= chapter}
            candidates = sorted(char_map.items(), key=lambda kv: kv[1][1],
                                reverse=True)[:character_limit]
            char_lines = []
            for cname, (cat_name, _ch) in candidates:
                doc = proxy._find_in_cache("wiki", cname)
                if doc is None:
                    proxy.read_doc("wiki", cname, category=cat_name, yaml_only=False)
                    doc = proxy._find_in_cache("wiki", cname)
                if doc is None:
                    continue
                line = f"- [{cat_name}] {cname}"
                if doc.description:
                    line += f"\n  简介：{doc.description}"
                if doc.state:
                    line += f"\n  状态：{doc.state}"
                char_lines.append(line)
            chars_text = "\n".join(char_lines) if char_lines else "（无人物词条）"

            # 3) 债务清单（与 lint_debt 同源）
            debts_text = read_debt(ws)

            # 4) 组装完整包
            answer = (
                f"## 审阅上下文（{ws.name}）\n\n"
                f"{REVIEW_CHECKLIST}\n\n"
                f"## 规则文档\n{rules_text}\n\n"
                f"## 人物词条（{len(candidates)} 条）\n{chars_text}\n\n"
                f"## Lint 债务\n{debts_text}"
            )
            return _ok(answer=answer, summary={
                "rules": len(rule_names | staged_rules),
                "characters": len(candidates),
                "chapter_filter": chapter,
            })
        except Exception as e:
            return _err(e)

    # ── 提取包 ────────────────────────────────────────────

    @mcp.tool()
    def extract_context(workspace: str = "", chapters: str = "") -> dict:
        """一键调出知识提取所需上下文：章节范围 + 类别体系 + 已有词条清单。

        提取子智能体拿到本包即可开始抽取（原文请用 chapter_read 按批自取，
        本包不含原文以控制上下文体积）。
        适用于：外部编排模式下派提取子智能体处理一批章节。

        Args:
            workspace: 工作区名
            chapters: 章节范围如 "21-30"；空=自动取未处理的下一批（最多 10 章）
        """
        try:
            from tools.editor import _get_proxy
            from commands.extract import _compute_range

            ws = ctx.resolve_ws(workspace)
            proxy = _get_proxy(ws)
        except Exception as e:
            return _err(e)

        try:
            # 1) 章节范围（复用内部提取的范围计算）
            start, end = _compute_range(ws, chapters or "")
            if start is None:
                return _empty(
                    "没有可提取的章节（指定范围无效，或全部章节已处理）。"
                    "可先 chapter_list 查看章节，或续写新章节后再提取。"
                )
            scope = f"{start}-{end}"

            # 2) 类别体系（含写作规范）
            cat_lines = []
            for cat in proxy.list_categories():
                spec = cat.get("spec", {}) or {}
                line = f"- {cat['name']}"
                if spec.get("description"):
                    line += f"：{spec['description']}"
                cat_lines.append(line)
                if spec.get("writing_guide"):
                    cat_lines.append(f"  写作规范：{spec['writing_guide']}")
            cats_text = "\n".join(cat_lines) if cat_lines else "（尚无类别，先 category_create 建类别）"

            # 3) 已有词条清单（防重复创建）
            wiki_lines = []
            for cat in proxy.list_categories():
                for m in proxy._db.wiki_list_main(cat["id"]):
                    desc = m.get("description", "")
                    line = f"- [{cat['name']}] {m['name']}"
                    if desc:
                        line += f"：{desc}"
                    wiki_lines.append(line)
            wiki_text = "\n".join(wiki_lines) if wiki_lines else "（尚无词条）"

            answer = (
                f"## 提取上下文（{ws.name}，章节 {scope}）\n\n"
                f"本次提取范围：第 {start}~{end} 章（原文用 chapter_read('{scope}') 读取）\n\n"
                f"## 已有类别\n{cats_text}\n\n"
                f"## 已有词条（{len(wiki_lines)} 条，避免重复创建）\n{wiki_text}"
            )
            return _ok(answer=answer, scope=scope, categories=len(proxy.list_categories()),
                       wiki_count=len(wiki_lines))
        except Exception as e:
            return _err(e)

    # ── 暂存区自检 ────────────────────────────────────────

    @mcp.tool()
    def kb_staging(workspace: str = "", name: str = "") -> dict:
        """查看知识写入暂存区：未 kb_commit 的增/改/删清单（纯只读）。

        宿主中断后恢复用：一批 kb_create/kb_edit 后若会话断开，用本工具
        查看暂存内容，决定继续 kb_commit 还是重新整理。
        不提供丢弃/回滚（防误删），commit 失败缓存保留，修复后可重试。

        Args:
            workspace: 工作区名
            name: 指定条目名查看详情（正文/简介/状态/关键词，超过 500 字截断
                并返回 truncated 标记与全文获取指引），空=仅清单
        """
        try:
            from tools.editor import _get_proxy

            ws = ctx.resolve_ws(workspace)
            proxy = _get_proxy(ws)
        except Exception as e:
            return _err(e)

        try:
            items = []
            for (doc_type, _mid), doc in proxy._cache.items():
                if doc.is_deleted:
                    action = "删除"
                elif doc.is_new:
                    action = "新增"
                elif doc.is_dirty:
                    action = "修改"
                else:
                    continue
                item = {
                    "type": doc_type,
                    "name": doc.name,
                    "action": action,
                    "chapter": doc.chapter,
                }
                items.append(item)

            if not items:
                return _empty(
                    "暂存区为空：当前没有未提交的知识变更。"
                    "（kb_create/kb_edit/rule_*/plot_* 写入后、kb_commit 前可见）"
                )

            if name:
                for (doc_type, _mid), doc in proxy._cache.items():
                    if doc.name != name:
                        continue
                    if not (doc.is_new or doc.is_dirty or doc.is_deleted):
                        continue
                    detail = {
                        "type": doc_type,
                        "name": doc.name,
                        "action": ("删除" if doc.is_deleted else
                                   "新增" if doc.is_new else "修改"),
                    }
                    # v7.2.1: 截断必须显式告知并给出全文获取通道——
                    # 宿主拿到截断内容时能直接续调 kb_show 取全文，不再"摸黑"
                    truncated = []
                    for field in ("description", "state", "keywords", "content"):
                        val = getattr(doc, field, "") or ""
                        if len(val) > DETAIL_MAX_CHARS:
                            truncated.append(field)
                        detail[field] = val[:DETAIL_MAX_CHARS]
                    if truncated:
                        detail["truncated"] = truncated
                        detail["truncated_hint"] = (
                            f"以下字段超过 {DETAIL_MAX_CHARS} 字已被截断："
                            f"{', '.join(truncated)}。需要全文请调 "
                            f"kb_show(name, workspace)（缓存优先，暂存内容可见全文）"
                        )
                    return _ok(item=detail)
                return _err(KeyError(f"暂存区中没有条目「{name}」"))

            return _ok(items=items, count=len(items),
                       hint="用 name 参数查看单条详情；确认无误后调 kb_commit 落库")
        except Exception as e:
            return _err(e)
