"""知识库 lint 检查 — v5：从 DB/proxy 读取文档

纯 Python 模块，无 LLM 调用。
提供全套 lint 检查函数，用于检测 wiki/plot 知识库的结构、链接、内容质量等问题。
v5 改造：底层数据源从文件系统切换为 ProxyService（缓存 + DB）。
"""

import re
import json
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# ── 常量 ──────────────────────────────────────────────────────────────

DEBT_FILE = "lint-debt.json"
STATE_MAX_CHARS = 100
DOC_MAX_CHARS = 1500
DESC_MAX_CHARS = 100  # description 字段字数上限
APPEARANCE_SCAN_CHAPTERS = 50

# 匹配 [[target]] 或 [[target|display]] 格式
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
# 匹配中文括号内的别名：（别名）
ALIAS_PAREN_PATTERN = re.compile(r"（([^）]+)）")
# 匹配中文方括号内的消歧义：【消歧义】
ALIAS_BRACKET_PATTERN = re.compile(r"【([^】]+)】")


def extract_wikilinks(text: str) -> list[str]:
    """从文本中提取所有 wikilink 目标名称"""
    return [match.strip() for match in WIKILINK_PATTERN.findall(text)]


def get_aliases(title: str) -> list[str]:
    """根据标题生成别名列表（含自身）

    规则：
    - "叶寒（寒叔）" → ["叶寒（寒叔）", "寒叔"]
    - "叶家【紫玉大陆】" → ["叶家【紫玉大陆】"]
    - 无括号 → [title]
    """
    aliases = [title]

    # 仅当存在（）且不包含【】时才提取别名
    paren_match = ALIAS_PAREN_PATTERN.search(title)
    bracket_match = ALIAS_BRACKET_PATTERN.search(title)

    if paren_match and not bracket_match:
        alias = paren_match.group(1)
        if alias and alias not in aliases:
            aliases.append(alias)

    return aliases


# ── v5 文档收集（从 DB/proxy 获取） ──────────────────────────────────


@dataclass
class LintDoc:
    """文档快照（用于 lint 检查）"""
    doc_type: str       # "wiki" | "plot" | "rule"
    name: str
    category: str       # wiki 类别名（plot/rule 为空）
    content: str        # 正文
    description: str
    state: str
    chapters: str       # 仅 plot
    ended: bool         # 仅 plot


def _gather_all_docs(workspace: Path) -> list[LintDoc]:
    """从 DB 通过 proxy 获取所有文档（合并缓存中的新条目）"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    docs: list[LintDoc] = []

    # wiki
    cats = proxy.list_categories("wiki")
    for cat in cats:
        cat_name = cat["name"]
        mains = proxy._db.wiki_list_main(cat["id"])
        for m in mains:
            current = proxy._db.wiki_get_current(m["id"])
            if current is None:
                continue
            docs.append(LintDoc(
                doc_type="wiki", name=m["name"], category=cat_name,
                content=current.get("content", ""),
                description=current.get("description", ""),
                state=current.get("state", ""),
                chapters="", ended=False,
            ))
    # 缓存中新增的 wiki
    for (dt, _), cached in proxy._cache.items():
        if dt == "wiki" and cached.is_new and not cached.is_deleted:
            docs.append(LintDoc(
                doc_type="wiki", name=cached.name,
                category=cached.category or "",
                content=cached.content, description=cached.description,
                state=cached.state, chapters="", ended=False,
            ))

    # plot
    for m in proxy._db.plot_list_main():
        current = proxy._db.plot_get_current(m["id"])
        if current is None:
            continue
        docs.append(LintDoc(
            doc_type="plot", name=m["name"], category="",
            content=current.get("content", ""),
            description=current.get("description", ""),
            state=current.get("state", ""),
            chapters=m.get("chapters", ""),
            ended=bool(m.get("ended", False)),
        ))
    for (dt, _), cached in proxy._cache.items():
        if dt == "plot" and cached.is_new and not cached.is_deleted:
            docs.append(LintDoc(
                doc_type="plot", name=cached.name, category="",
                content=cached.content, description=cached.description,
                state=cached.state, chapters=cached.chapters,
                ended=cached.ended,
            ))

    # rule
    for m in proxy._db.rule_list_main():
        current = proxy._db.rule_get_current(m["id"])
        if current is None:
            continue
        docs.append(LintDoc(
            doc_type="rule", name=m["name"], category="",
            content=current.get("content", ""),
            description=current.get("description", ""),
            state=current.get("state", ""),
            chapters="", ended=False,
        ))
    for (dt, _), cached in proxy._cache.items():
        if dt == "rule" and cached.is_new and not cached.is_deleted:
            docs.append(LintDoc(
                doc_type="rule", name=cached.name, category="",
                content=cached.content, description=cached.description,
                state=cached.state, chapters="", ended=False,
            ))

    return docs


def _build_wiki_name_set(docs: list[LintDoc]) -> set[str]:
    """构建 wiki 名称集合（含别名索引）"""
    names: set[str] = set()
    for d in docs:
        if d.doc_type != "wiki":
            continue
        names.add(d.name)
        for alias in get_aliases(d.name):
            names.add(alias)
    return names


def _build_rules_name_set(docs: list[LintDoc]) -> set[str]:
    """构建规则名称集合"""
    return {d.name for d in docs if d.doc_type == "rule"}


def _get_category_state_info(workspace: Path) -> dict[str, bool]:
    """从 DB 读取每个类别是否需要 state 字段"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    result: dict[str, bool] = {}
    for cat in proxy.list_categories("wiki"):
        spec = cat.get("spec", {})
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except (json.JSONDecodeError, TypeError):
                spec = {}
        result[cat["name"]] = bool(spec.get("state_required", False))
    return result


def _get_max_chapter(workspace: Path) -> int:
    """获取最大章节号（从 DB）"""
    from tools.editor import _get_proxy
    db = _get_proxy(workspace)._db
    return db.chapter_max_num()


def _read_chapter_text(workspace: Path, num: int) -> str:
    """读取指定章节的正文文本（从 DB）"""
    from tools.editor import _get_proxy
    db = _get_proxy(workspace)._db
    row = db.chapter_get(num)
    if row is None:
        return ""
    return row["content"]


def _compact_chapter_list(nums: list[int]) -> str:
    """将排序后的章节号列表压缩为范围表示法

    [1, 2, 3, 5, 7, 8, 9] → "1-3,5,7-9"
    """
    if not nums:
        return ""
    nums = sorted(set(nums))
    ranges: list[str] = []
    start = nums[0]
    end = nums[0]

    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = n
            end = n
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(ranges)


# ── 检查函数（v5：操作 LintDoc 列表） ────────────────────────────────


def check_content(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查文档正文是否为空"""
    debts: list[dict] = []
    for d in docs:
        label = f"{d.doc_type}/{d.name}"
        if not d.content or not d.content.strip():
            debts.append({
                "type": "body_empty",
                "file": label,
                "detail": "正文为空",
                "auto_fixed": False,
            })
    return debts


def check_wikilinks(workspace: Path, docs: list[LintDoc],
                    all_docs: list[LintDoc] | None = None) -> list[dict]:
    """检查文档中的 wikilink 是否指向已存在的目标

    Args:
        docs: 需要检查的文档列表（白名单过滤后）
        all_docs: 全量文档列表（用于构建合法目标集）。为 None 时用 docs。

    额外检查 unlink 黑名单：对于黑名单内的目标，自动取消链接并跳过债务。
    """
    from tools.editor import is_in_unlink_blacklist, _get_proxy

    target_docs = all_docs if all_docs is not None else docs
    wiki_names = _build_wiki_name_set(target_docs)
    rules_names = _build_rules_name_set(target_docs)
    valid_targets = wiki_names | rules_names

    proxy = _get_proxy(workspace)
    debts: list[dict] = []

    for d in docs:
        label = f"{d.doc_type}/{d.name}"
        links = extract_wikilinks(d.content)

        # 黑名单自动取消链接
        blacklist_targets = {
            link for link in links
            if link not in valid_targets and is_in_unlink_blacklist(workspace, link)
        }
        if blacklist_targets:
            new_content = d.content
            for target in blacklist_targets:
                old_lower = target.strip().lower()

                def _make_unlinker(old_lower):
                    def _replace(match):
                        raw_target = match.group(1)
                        raw_alias = match.group(2) or ""
                        if raw_target.strip().lower() == old_lower:
                            return raw_alias.lstrip("|") if raw_alias else raw_target
                        return match.group(0)
                    return _replace

                new_content = re.sub(
                    r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]",
                    _make_unlinker(old_lower), new_content,
                )
            proxy.update_doc(doc_type=d.doc_type, name=d.name,
                             category=d.category or None,
                             content=new_content, chapter=0)
            d.content = new_content
            links = extract_wikilinks(new_content)

        for link in links:
            if link not in valid_targets:
                debts.append({
                    "type": "broken_link",
                    "file": label,
                    "target": link,
                    "context": f"[[{link}]]",
                })

    return debts


def check_rules_wikilinks(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查规则文档中的 wikilink，替换为纯文本"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    fixes: list[dict] = []

    for d in docs:
        if d.doc_type != "rule":
            continue
        links = extract_wikilinks(d.content)
        if not links:
            continue

        new_content = WIKILINK_PATTERN.sub(r"\1", d.content)
        proxy.update_doc(doc_type="rule", name=d.name,
                         content=new_content, chapter=0)
        d.content = new_content

        fixes.append({
            "type": "rules_wikilink_removed",
            "file": f"rule/{d.name}",
            "detail": f"规则文档包含 [[wikilink]]（{len(links)} 处），已自动替换为纯文本",
            "auto_fixed": True,
        })

    return fixes


def check_state(workspace: Path, docs: list[LintDoc]) -> tuple[list[dict], list[dict]]:
    """检查 state 字段问题

    - needs_state=True 但无 state → debt: state_missing
    - needs_state=True 且 state > 100 字 → debt: state_verbose
    """
    from tools.editor import _get_proxy
    category_state = _get_category_state_info(workspace)
    proxy = _get_proxy(workspace)
    debts: list[dict] = []
    auto_fixes: list[dict] = []

    for d in docs:
        if d.doc_type != "wiki":
            continue
        needs_state = category_state.get(d.category, False)
        label = f"wiki/{d.category}/{d.name}"

        if needs_state and not d.state:
            debts.append({
                "type": "state_missing",
                "file": label,
                "detail": f"类别「{d.category}」需要 state 字段，但词条中缺少",
                "auto_fixed": False,
            })
        elif needs_state and len(d.state) > STATE_MAX_CHARS:
            debts.append({
                "type": "state_verbose",
                "file": label,
                "detail": f"state 字段过长（{len(d.state)} 字，上限 {STATE_MAX_CHARS}）",
                "auto_fixed": False,
            })

    return debts, auto_fixes


def check_category(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """v5 中类别由 DB 维护，不再出现 type 与目录不一致的问题"""
    return []


def check_doc_length(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查文档正文长度是否超过上限"""
    debts: list[dict] = []
    for d in docs:
        body_len = len(d.content)
        if body_len > DOC_MAX_CHARS:
            debts.append({
                "type": "length_overage",
                "file": f"{d.doc_type}/{d.name}",
                "detail": f"正文过长（{body_len} 字，建议上限 {DOC_MAX_CHARS}）",
                "auto_fixed": False,
            })
    return debts


def check_description_length(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查 description 字段长度"""
    debts: list[dict] = []
    for d in docs:
        if len(d.description) > DESC_MAX_CHARS:
            debts.append({
                "type": "desc_verbose",
                "file": f"{d.doc_type}/{d.name}",
                "detail": f"description 过长（{len(d.description)} 字，建议上限 {DESC_MAX_CHARS}）",
                "auto_fixed": False,
            })
    return debts


def check_plot_links(workspace: Path, docs: list[LintDoc],
                     all_docs: list[LintDoc] | None = None) -> list[dict]:
    """检查剧情卡片中的 wikilink 是否指向已存在的 wiki 目标

    Args:
        docs: 需要检查的文档列表（白名单过滤后）
        all_docs: 全量文档列表（用于构建合法目标集）。为 None 时用 docs。
    """
    from tools.editor import is_in_unlink_blacklist, _get_proxy

    target_docs = all_docs if all_docs is not None else docs
    wiki_names = _build_wiki_name_set(target_docs)
    proxy = _get_proxy(workspace)
    debts: list[dict] = []

    for d in docs:
        if d.doc_type != "plot":
            continue
        label = f"plot/{d.name}"
        links = extract_wikilinks(d.content)

        blacklist_targets = {
            link for link in links
            if link not in wiki_names and is_in_unlink_blacklist(workspace, link)
        }
        if blacklist_targets:
            new_content = d.content
            for target in blacklist_targets:
                old_lower = target.strip().lower()

                def _make_unlinker(old_lower):
                    def _replace(match):
                        raw_target = match.group(1)
                        raw_alias = match.group(2) or ""
                        if raw_target.strip().lower() == old_lower:
                            return raw_alias.lstrip("|") if raw_alias else raw_target
                        return match.group(0)
                    return _replace

                new_content = re.sub(
                    r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]",
                    _make_unlinker(old_lower), new_content,
                )
            proxy.update_doc(doc_type="plot", name=d.name,
                             content=new_content, chapter=0)
            d.content = new_content
            links = extract_wikilinks(new_content)

        for link in links:
            if link not in wiki_names:
                debts.append({
                    "type": "plot_broken_link",
                    "file": label,
                    "target": link,
                    "context": f"[[{link}]]",
                })

    return debts


def check_plot_range(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查剧情卡片的 chapters 字段是否超出最大章节号"""
    max_chapter = _get_max_chapter(workspace)
    if max_chapter == 0:
        return []

    from tools.editor import _get_proxy
    from tools.chapter import parse_chapter_spec
    proxy = _get_proxy(workspace)
    auto_fixes: list[dict] = []

    for d in docs:
        if d.doc_type != "plot" or not d.chapters:
            continue

        numbers = re.findall(r"\d+", str(d.chapters))
        if not numbers:
            continue

        max_in_field = max(int(n) for n in numbers)
        if max_in_field > max_chapter:
            nums = parse_chapter_spec(str(d.chapters))
            valid_nums = [n for n in nums if n <= max_chapter]
            new_chapters = _compact_chapter_list(valid_nums) if valid_nums else str(max_chapter)

            proxy.update_doc(doc_type="plot", name=d.name,
                             chapters=new_chapters, chapter=0)
            d.chapters = new_chapters
            auto_fixes.append({
                "type": "plot_range_fixed",
                "file": f"plot/{d.name}",
                "detail": f"chapters 超出最大章节 {max_chapter}，已自动修正为 {new_chapters}",
                "auto_fixed": True,
            })

    return auto_fixes


# ── 未结束剧情卡片检测 ──────────────────────────────────────────

UNENDED_PLOT_GAP = 10  # 卡片最大章节比最新章节落后超过此值即判定可收尾


def check_unended_plots(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检测可收尾但未结束的剧情卡片"""
    max_chapter = _get_max_chapter(workspace)
    if max_chapter <= UNENDED_PLOT_GAP:
        return []

    debts: list[dict] = []
    threshold = max_chapter - UNENDED_PLOT_GAP

    for d in docs:
        if d.doc_type != "plot" or d.ended or not d.chapters:
            continue

        numbers = re.findall(r"\d+", str(d.chapters))
        if not numbers:
            continue

        max_in_card = max(int(n) for n in numbers)
        if max_in_card <= threshold:
            debts.append({
                "type": "unended_plot",
                "file": f"plot/{d.name}",
                "name": d.name,
                "detail": f"最新章节最大章节 {max_chapter}，本卡最大章节 {max_in_card}，建议使用 end_plot 结束",
            })

    return debts


def check_appearance(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """检查 wiki 词条在最近章节中的出场情况"""
    max_chapter = _get_max_chapter(workspace)
    if max_chapter == 0:
        return []

    start_chapter = max(1, max_chapter - APPEARANCE_SCAN_CHAPTERS + 1)
    scan_range = list(range(start_chapter, max_chapter + 1))

    results: list[dict] = []
    for d in docs:
        if d.doc_type != "wiki":
            continue

        keywords = get_aliases(d.name)
        appeared_in: list[int] = []

        for ch_num in scan_range:
            chapter_text = _read_chapter_text(workspace, ch_num)
            if not chapter_text:
                continue
            for kw in keywords:
                if kw in chapter_text:
                    appeared_in.append(ch_num)
                    break

        results.append({
            "page": f"wiki/{d.category}/{d.name}",
            "title": d.name,
            "keywords": keywords,
            "appeared_in": appeared_in,
            "recent_activity": len(appeared_in) > 0,
        })

    return results


# ── 短名→全名自动修复 ──────────────────────────────────────────────


def _build_short_name_map(docs: list[LintDoc]) -> dict[str, str]:
    """构建短名→全名的映射，用于自动修复 wikilink 断链"""
    short_map: dict[str, str] = {}

    for d in docs:
        if d.doc_type != "wiki":
            continue
        stem = d.name
        candidates = []

        for alias in get_aliases(stem):
            if alias != stem:
                candidates.append(alias)

        paren_match = re.match(r"^(.+?)（[^）]+）$", stem)
        if paren_match:
            prefix = paren_match.group(1)
            if prefix != stem:
                candidates.append(prefix)

        bracket_match = re.match(r"^(.+?)【[^】]+】$", stem)
        if bracket_match:
            prefix = bracket_match.group(1)
            if prefix != stem:
                candidates.append(prefix)

        for c in candidates:
            if c in short_map and short_map[c] != stem:
                del short_map[c]
            elif c not in short_map:
                short_map[c] = stem

    return short_map


def auto_fix_short_name_wikilinks(workspace: Path, docs: list[LintDoc]) -> list[dict]:
    """自动修复短名 wikilink 为全名"""
    from tools.editor import _get_proxy
    short_map = _build_short_name_map(docs)
    if not short_map:
        return []

    proxy = _get_proxy(workspace)
    short_names = sorted(short_map.keys(), key=len, reverse=True)
    fixes: list[dict] = []
    wikilink_re = re.compile(r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]")

    for d in docs:
        if d.doc_type == "rule" or not d.content:
            continue

        new_content = d.content
        for short_name in short_names:
            full_name = short_map[short_name]
            old_lower = short_name.strip().lower()
            new_clean = full_name.strip()

            def _make_replacer(old_lower, new_clean):
                def _replace(match):
                    raw_target = match.group(1)
                    raw_alias = match.group(2) or ""
                    if raw_target.strip().lower() == old_lower:
                        return f"[[{new_clean}{raw_alias}]]"
                    return match.group(0)
                return _replace

            new_content = wikilink_re.sub(
                _make_replacer(old_lower, new_clean), new_content)

        if new_content != d.content:
            proxy.update_doc(doc_type=d.doc_type, name=d.name,
                             category=d.category or None,
                             content=new_content, chapter=0)
            d.content = new_content
            fixes.append({
                "type": "short_name_fixed",
                "file": f"{d.doc_type}/{d.name}",
                "detail": "短名 wikilink 已自动修复为全名",
                "auto_fixed": True,
            })

    return fixes


# ── 断链重要性评分 ─────────────────────────────────────────────────

IMPORTANCE_MENTION_THRESHOLD = 3   # 提及条目数阈值
IMPORTANCE_FREQUENCY_THRESHOLD = 10  # 词频阈值
IMPORTANCE_CHAPTER_THRESHOLD = 3   # 章节范围阈值


def score_link_importance(workspace: Path, link_debts: list[dict],
                          chapter_scope: list[int]) -> dict[str, dict]:
    """对断链目标整合评分

    三维度：
      1. 提及条目数（多少个已有条目引用了该断链目标）
      2. 词频（在提取章节正文中的出现次数）
      3. 章节范围（在多少章正文中出现）

    Returns:
        {target: {mention_count, frequency, chapter_count, chapters, level, sources}}
    """
    # 1. 按 target 聚合
    aggregated: dict[str, dict] = {}
    for debt in link_debts:
        target = debt.get("target", "")
        if not target:
            continue
        if target not in aggregated:
            aggregated[target] = {"sources": set(), "frequency": 0, "chapters": []}
        aggregated[target]["sources"].add(debt.get("file", ""))

    if not aggregated:
        return {}

    # 2. 预加载章节正文（避免重复读取）
    chapter_texts: dict[int, str] = {}
    for ch_num in chapter_scope:
        text = _read_chapter_text(workspace, ch_num)
        if text:
            chapter_texts[ch_num] = text

    # 3. 对每个 target 计算三维度
    scores: dict[str, dict] = {}
    for target, info in aggregated.items():
        mention_count = len(info["sources"])

        # 词频 + 章节范围
        frequency = 0
        appeared_chapters: list[int] = []
        for ch_num, text in chapter_texts.items():
            count = text.count(target)
            if count > 0:
                frequency += count
                appeared_chapters.append(ch_num)

        chapter_count = len(appeared_chapters)

        # 等级计算
        level = 0
        if mention_count >= IMPORTANCE_MENTION_THRESHOLD:
            level += 1
        if frequency >= IMPORTANCE_FREQUENCY_THRESHOLD:
            level += 1
        if chapter_count >= IMPORTANCE_CHAPTER_THRESHOLD:
            level += 1

        scores[target] = {
            "mention_count": mention_count,
            "frequency": frequency,
            "chapter_count": chapter_count,
            "chapters": sorted(appeared_chapters),
            "level": level,
            "sources": sorted(info["sources"]),
        }

    return scores


def auto_unlink_low_importance(workspace: Path, docs: list[LintDoc],
                               scores: dict[str, dict]) -> list[dict]:
    """对重要性等级 0 的断链目标自动取消链接

    不记入黑名单。返回自动修复记录列表。
    """
    from tools.editor import _get_proxy

    # 筛选等级 0 的 target
    unlink_targets = {
        target for target, info in scores.items() if info["level"] == 0
    }
    if not unlink_targets:
        return []

    proxy = _get_proxy(workspace)
    fixes: list[dict] = []

    for d in docs:
        if not d.content:
            continue

        # 检查该文档是否包含需要 unlink 的目标
        links_in_doc = extract_wikilinks(d.content)
        targets_to_unlink = {lk for lk in links_in_doc if lk in unlink_targets}
        if not targets_to_unlink:
            continue

        new_content = d.content
        for target in targets_to_unlink:
            target_lower = target.strip().lower()

            def _make_unlinker(t_lower):
                def _replace(match):
                    raw_target = match.group(1)
                    raw_alias = match.group(2) or ""
                    if raw_target.strip().lower() == t_lower:
                        return raw_alias.lstrip("|") if raw_alias else raw_target
                    return match.group(0)
                return _replace

            new_content = re.sub(
                r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]",
                _make_unlinker(target_lower), new_content,
            )

        if new_content != d.content:
            proxy.update_doc(doc_type=d.doc_type, name=d.name,
                             category=d.category or None,
                             content=new_content, chapter=0)
            d.content = new_content
            fixes.append({
                "type": "importance_auto_unlink",
                "file": f"{d.doc_type}/{d.name}",
                "detail": f"重要性等级0，自动取消链接：{', '.join(sorted(targets_to_unlink))}",
                "auto_fixed": True,
            })

    return fixes


# ── 入口函数 ─────────────────────────────────────────────────────────


def run_lint(workspace: Path, chapters: str | None = None,
             whitelist: list[tuple[str, str]] | None = None,
             chapter_scope: list[int] | None = None) -> str:
    """运行全套 lint 检查，写入 debt JSON，返回格式化摘要

    Args:
        workspace: 工作区路径
        chapters: 可选，章节范围（未使用）
        whitelist: 可选，白名单 [(doc_type, name), ...]。
                   传入时只检查白名单内的文档（任务内 lint）。
                   为 None 时检查全量（全局 lint）。
        chapter_scope: 可选，提取计划章节号列表。
                       传入时启用断链重要性评分与等级0自动unlink。
    """
    docs = _gather_all_docs(workspace)

    # 白名单过滤（保留全量用于断链目标判定）
    all_docs = docs
    if whitelist is not None:
        wl_set = set(whitelist)
        docs = [d for d in docs if (d.doc_type, d.name) in wl_set]

    if not docs:
        return "（无文档需要检查）"

    # 1. 正文结构
    content_debts = check_content(workspace, docs)

    # 1.5 短名→全名 wikilink 自动修复（在断链检查之前执行）
    short_name_fixes = auto_fix_short_name_wikilinks(workspace, docs)

    # 2. Wikilink 断链（用全量 docs 构建合法目标集）
    link_debts = check_wikilinks(workspace, docs, all_docs=all_docs)

    # 2.5 断链重要性评分（仅在传入 chapter_scope 时启用）
    importance_scores: dict[str, dict] = {}
    importance_unlink_fixes: list[dict] = []
    if chapter_scope and link_debts:
        importance_scores = score_link_importance(workspace, link_debts, chapter_scope)
        # 等级0自动unlink
        importance_unlink_fixes = auto_unlink_low_importance(workspace, docs, importance_scores)
        # 从 link_debts 中移除已自动处理的等级0条目
        link_debts = [
            d for d in link_debts
            if importance_scores.get(d.get("target", ""), {}).get("level", 0) > 0
        ]

    # 3. 规则文档 wikilink
    rules_fixes = check_rules_wikilinks(workspace, docs)

    # 4. State 检查
    state_debts, state_fixes = check_state(workspace, docs)

    # 5. 类别检查（v5 中由 DB 保证一致性）
    category_fixes = check_category(workspace, docs)

    # 6. 正文长度
    length_debts = check_doc_length(workspace, docs)

    # 6.5 description 长度
    desc_debts = check_description_length(workspace, docs)

    # 7. 剧情链接（用全量 docs 构建合法目标集）
    plot_link_debts = check_plot_links(workspace, docs, all_docs=all_docs)

    # 8. 剧情范围
    plot_range_fixes = check_plot_range(workspace, docs)

    # 8.5 未结束剧情卡片检测
    unended_plot_debts = check_unended_plots(workspace, docs)

    # 9. 出场检查
    appearance_results = check_appearance(workspace, docs)

    # ── 构建 debt_data ──
    debt_data: dict[str, list] = {
        "broken_links": [d for d in link_debts if d["type"] == "broken_link"],
        "state_missing": [d for d in state_debts if d["type"] == "state_missing"],
        "state_verbose": [d for d in state_debts if d["type"] == "state_verbose"],
        "length_overage": [d for d in length_debts if d["type"] == "length_overage"],
        "desc_verbose": [d for d in desc_debts if d["type"] == "desc_verbose"],
        "plot_broken_links": [d for d in plot_link_debts if d["type"] == "plot_broken_link"],
        "appearance": appearance_results,
        "file_errors": [d for d in content_debts if d.get("auto_fixed") is not True],
        "unended_plots": unended_plot_debts,
        "importance_scores": importance_scores,
    }

    # 写入 debt JSON
    debt_fp = workspace / DEBT_FILE
    with open(debt_fp, "w", encoding="utf-8") as f:
        json.dump(debt_data, f, ensure_ascii=False, indent=2)

    # ── 构建关系图（可选） ──
    try:
        from auto.relation_extractor import build_relations, save_relations
        relations = build_relations(workspace)
        if relations:
            save_relations(workspace, relations)
    except Exception:
        pass

    # ── 格式化摘要 ──
    lines = [
        "📋 Lint 检查报告",
        f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"检查文档数：{len(docs)}",
        "",
    ]

    # 自动修复汇总
    all_fixes = short_name_fixes + rules_fixes + state_fixes + category_fixes + plot_range_fixes + importance_unlink_fixes
    total_fixed = len(all_fixes)
    if all_fixes:
        lines.append("## 代码 lint 完成\n")
        lines.append(f"### 自动修复（{total_fixed} 项）")
        for fix in all_fixes:
            lines.append(f"  ✅ {fix['file']}: {fix['detail']}")
        lines.append("")

    # 断链重要性评估摘要
    if importance_scores:
        forced = {t: s for t, s in importance_scores.items() if s["level"] >= 2}
        level1 = {t: s for t, s in importance_scores.items() if s["level"] == 1}
        level0 = {t: s for t, s in importance_scores.items() if s["level"] == 0}
        lines.append("### 断链重要性评估")
        if forced:
            lines.append(f"🔴 强制债务（等级≥2，{len(forced)} 项 — 必须创建）")
            for t, s in sorted(forced.items(), key=lambda x: (-x[1]["level"], -x[1]["frequency"])):
                lines.append(f"  {t}（等级{s['level']} / {s['mention_count']}条目提及 / 词频{s['frequency']} / 覆盖{s['chapter_count']}章）")
        if level1:
            lines.append(f"🟡 LLM判断（等级1，{len(level1)} 项）")
            for t, s in sorted(level1.items(), key=lambda x: -x[1]["frequency"]):
                lines.append(f"  {t}（{s['mention_count']}条目 / 词频{s['frequency']} / {s['chapter_count']}章）")
        if level0:
            lines.append(f"⚪ 已自动unlink（等级0，{len(level0)} 项）：{', '.join(sorted(level0.keys())[:10])}")
            if len(level0) > 10:
                lines.append(f"  ... 另有 {len(level0) - 10} 项")
        lines.append("")

    # 需人工处理的债务
    debt_groups = [
        ("broken_links", link_debts),
        ("state_missing", [d for d in state_debts if d["type"] == "state_missing"]),
        ("state_verbose", [d for d in state_debts if d["type"] == "state_verbose"]),
        ("length_overage", length_debts),
        ("desc_verbose", desc_debts),
        ("plot_broken_links", plot_link_debts),
        ("unended_plots", unended_plot_debts),
    ]
    all_debt_items = []
    for _, items in debt_groups:
        all_debt_items.extend(items)
    total_debts = len(all_debt_items)

    if all_debt_items:
        lines.append(f"### 需人工处理的债务（{total_debts} 项）")
        for debt_type, items in debt_groups:
            if items:
                lines.append(f"**{debt_type}**（{len(items)} 项）")
                for item in items:
                    detail = item.get("detail", item.get("target", ""))
                    lines.append(f"  ⚠️ {detail}")
        lines.append("")

    if plot_range_fixes:
        lines.append(f"### 剧情范围修正（{len(plot_range_fixes)} 处）")
        for d in plot_range_fixes:
            lines.append(f"  🔧 {d['file']}：{d['detail']}")
        lines.append("")

    # 出场统计
    active_count = sum(1 for r in appearance_results if r["recent_activity"])
    if appearance_results:
        lines.append("### 出场统计")
        lines.append(f"活跃 {active_count}/{len(appearance_results)} 个词条")
        lines.append("")

    lines.append(f"总计：自动修复 {total_fixed} 处，待处理债务 {total_debts} 项")

    return "\n".join(lines)


def read_debt(workspace: Path) -> str:
    """读取 lint-debt.json 并返回格式化字符串"""
    debt_fp = workspace / DEBT_FILE
    if not debt_fp.exists():
        return "（暂无 lint debt 数据，请先运行 lint 检查）"

    with open(debt_fp, "r", encoding="utf-8") as f:
        debt_data = json.load(f)

    lines = ["📋 Lint Debt 报告", ""]

    sections = [
        ("broken_links", "断链", "🔗"),
        ("state_missing", "State 缺失", "📌"),
        ("state_verbose", "State 过长", "📌"),
        ("length_overage", "正文过长", "📏"),
        ("desc_verbose", "description 过长", "📝"),
        ("plot_broken_links", "剧情断链", "🎬"),
        ("file_errors", "文件错误", "❌"),
    ]

    has_any = False
    for key, label, icon in sections:
        items = debt_data.get(key, [])
        if items:
            has_any = True
            lines.append(f"{icon}【{label}】共 {len(items)} 处")
            for item in items[:10]:
                file = item.get("file", item.get("page", "?"))
                detail = item.get("detail", item.get("context", ""))
                lines.append(f"  - {file}：{detail}")
            if len(items) > 10:
                lines.append(f"  ... 另有 {len(items) - 10} 处")
            lines.append("")

    appearances = debt_data.get("appearance", [])
    if appearances:
        has_any = True
        active = sum(1 for r in appearances if r.get("recent_activity"))
        lines.append(f"👤【出场统计】活跃 {active}/{len(appearances)} 个词条")
        lines.append("")

    if not has_any:
        lines.append("✅ 未发现任何债务问题。")

    return "\n".join(lines)
