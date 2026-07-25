"""代码 lint 检查 — 知识库质量检测

纯 Python 模块，无 LLM 调用。
提供全套 lint 检查函数，用于检测 wiki/plot 知识库的结构、链接、内容质量等问题。
"""

import re
import json
from pathlib import Path
from datetime import datetime
from tools.wiki import _parse_frontmatter, _build_frontmatter

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


# ── 文件扫描辅助 ─────────────────────────────────────────────────────

def _get_changed_files(workspace: Path) -> list[Path]:
    """获取待检查的文件列表

    返回 wiki/ + plot/ + rules/ 下所有 .md 文件（排除 index.md 和 relations.yaml）
    """
    changed: list[Path] = []

    # 扫描 wiki/
    wiki_root = workspace / "wiki"
    if wiki_root.exists():
        for fp in sorted(wiki_root.rglob("*.md")):
            if fp.name in ("index.md", "relations.yaml"):
                continue
            changed.append(fp)

    # 扫描 plot/
    plot_root = workspace / "plot"
    if plot_root.exists():
        for fp in sorted(plot_root.rglob("*.md")):
            if fp.name == "index.md":
                continue
            changed.append(fp)

    # 扫描 rules/（规则文件也需要 YAML 结构等检查）
    rules_root = workspace / "rules"
    if rules_root.exists():
        for fp in sorted(rules_root.glob("*.md")):
            changed.append(fp)

    return changed


def _build_wiki_map(workspace: Path) -> dict[str, Path]:
    """扫描 wiki/ 下所有 .md 文件，构建 {stem: Path} 映射（含别名索引）"""
    wiki_map: dict[str, Path] = {}
    wiki_root = workspace / "wiki"
    if not wiki_root.exists():
        return wiki_map

    for fp in sorted(wiki_root.rglob("*.md")):
        if fp.name in ("index.md", "relations.yaml"):
            continue
        # 用文件名 stem 做索引
        stem = fp.stem
        wiki_map[stem] = fp

        # 读取 title 字段，如果 title != stem，也用 title 索引
        try:
            meta, _ = _parse_frontmatter(fp.read_text(encoding="utf-8"))
            title = meta.get("title", "")
            if title and title != stem and title not in wiki_map:
                wiki_map[title] = fp
            # 用别名索引
            source_name = title or stem
            for alias in get_aliases(source_name):
                if alias not in wiki_map:
                    wiki_map[alias] = fp
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to read %s for wiki map: %s", fp, e
            )

    return wiki_map


def _build_rules_map(workspace: Path) -> dict[str, Path]:
    """扫描 rules/ 下所有 .md 文件，构建 {stem: Path} 映射"""
    rules_map: dict[str, Path] = {}
    rules_root = workspace / "rules"
    if not rules_root.exists():
        return rules_map

    for fp in sorted(rules_root.glob("*.md")):
        rules_map[fp.stem] = fp

    return rules_map


def _get_category_state_info(workspace: Path) -> dict[str, bool]:
    """读取每个类别 index.md，确定该类别是否需要 state 字段

    Returns:
        {category_name: needs_state}
    """
    result: dict[str, bool] = {}
    wiki_root = workspace / "wiki"
    if not wiki_root.exists():
        return result

    for cat_dir in sorted(wiki_root.iterdir()):
        if not cat_dir.is_dir():
            continue
        index_fp = cat_dir / "index.md"
        needs_state = False
        if index_fp.exists():
            content = index_fp.read_text(encoding="utf-8")
            # 检查正文中"是否需要 state 字段"段落
            state_match = re.search(
                r"## 是否需要 state 字段\s*\n(.+)",
                content,
            )
            if state_match:
                value = state_match.group(1).strip()
                needs_state = value == "是"
        result[cat_dir.name] = needs_state

    return result


def _get_max_chapter(workspace: Path) -> int:
    """获取 document/ 目录中最大的章节号"""
    doc_root = workspace / "document"
    if not doc_root.exists():
        return 0

    max_num = 0
    for fp in doc_root.glob("c*.md"):
        m = re.match(r"c(\d+)\.md", fp.name)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return max_num


def _read_chapter_text(workspace: Path, num: int) -> str:
    """读取指定章节的正文文本"""
    fp = workspace / "document" / f"c{num:03d}.md"
    if not fp.exists():
        return ""
    return fp.read_text(encoding="utf-8")


# ── 检查函数 ─────────────────────────────────────────────────────────

def check_yaml_structure(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 YAML frontmatter 结构问题

    - frontmatter 缺失（不以 --- 开头）→ debt, auto_fixed=False
    - frontmatter 重复（超过 2 组 ---）→ auto_fix=True，合并
    - body 为空 → debt, auto_fixed=False
    """
    debts: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        text_stripped = text.strip()

        if not text_stripped.startswith("---"):
            debts.append({
                "type": "yaml_missing",
                "file": rel,
                "detail": "frontmatter 缺失：文件不以 --- 开头",
                "auto_fixed": False,
            })
            continue

        # 统计 --- 出现次数
        parts = text_stripped.split("---")
        fm_sections = [p for p in parts if p.strip() and ":" in p]

        # 如果 body 部分又包含 ---，说明可能有重复 frontmatter
        # 标准结构：---\n...\n---\nbody → 3 个部分，第 2 个是 frontmatter
        if len(parts) >= 4:
            # 有重复 frontmatter
            meta, body = _parse_frontmatter(text)
            fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
            debts.append({
                "type": "yaml_duplicate",
                "file": rel,
                "detail": "重复 frontmatter：已自动合并",
                "auto_fixed": True,
            })

        # 检查 body 是否为空
        meta, body = _parse_frontmatter(text)
        if not body or not body.strip():
            debts.append({
                "type": "body_empty",
                "file": rel,
                "detail": "正文为空",
                "auto_fixed": False,
            })

    return debts


def check_wikilinks(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 wiki 文档中的 wikilink 是否指向已存在的目标

    构建 wiki_map + rules_map，遍历 changed_files 中的 [[wikilink]]，
    检查目标是否存在于任一映射中。
    """
    wiki_map = _build_wiki_map(workspace)
    rules_map = _build_rules_map(workspace)

    # 合并有效目标集合
    valid_targets = set(wiki_map.keys()) | set(rules_map.keys())

    debts: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        # 跳过 wiki map 的索引项（已通过文件名 stem 索引），直接扫描内容
        text = fp.read_text(encoding="utf-8")
        links = extract_wikilinks(text)
        for link in links:
            if link not in valid_targets:
                debts.append({
                    "type": "broken_link",
                    "file": rel,
                    "target": link,
                    "context": f"[[{link}]]",
                })

    return debts


def check_rules_wikilinks(workspace: Path) -> list[dict]:
    """检查规则文档中的 wikilink，替换为纯文本

    规则文档不应包含 [[wikilink]]，扫描 rules/*.md，
    将 [[target|display]] 或 [[target]] 替换为纯文本。
    """
    fixes: list[dict] = []
    rules_root = workspace / "rules"
    if not rules_root.exists():
        return fixes

    for fp in sorted(rules_root.glob("*.md")):
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        links = extract_wikilinks(text)
        if not links:
            continue

        # 替换所有 wikilink 为纯文本
        new_text = WIKILINK_PATTERN.sub(r"\1", text)
        fp.write_text(new_text, encoding="utf-8")

        fixes.append({
            "type": "rules_wikilink_removed",
            "file": rel,
            "detail": f"规则文档包含 [[wikilink]]（{len(links)} 处），已自动替换为纯文本",
            "auto_fixed": True,
        })

    return fixes


def check_state(workspace: Path, changed_files: list[Path]) -> tuple[list[dict], list[dict]]:
    """检查 state 字段问题

    根据类别 index.md 的配置判断：
    - needs_state=True 但无 state → debt: state_missing
    - needs_state=False 但有 state → auto_fix: 移除 state 行
    - needs_state=True 且 state > 100 字 → debt: state_verbose

    Returns:
        (debts, auto_fixes)
    """
    category_state = _get_category_state_info(workspace)
    debts: list[dict] = []
    auto_fixes: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        # 只检查 wiki/ 下的文件
        if not rel.startswith("wiki/"):
            continue

        # 提取类别（wiki/人物/张三.md → 人物）
        parts = Path(rel).parts
        if len(parts) < 2:
            continue
        category = parts[1] if parts[0] == "wiki" else parts[0]

        needs_state = category_state.get(category, False)

        text = fp.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        has_state = "state" in meta
        state_value = meta.get("state", "")

        if needs_state and not has_state:
            debts.append({
                "type": "state_missing",
                "file": rel,
                "detail": f"类别「{category}」需要 state 字段，但词条中缺少",
                "auto_fixed": False,
            })
        elif needs_state and has_state and len(state_value) > STATE_MAX_CHARS:
            debts.append({
                "type": "state_verbose",
                "file": rel,
                "detail": f"state 字段过长（{len(state_value)} 字，上限 {STATE_MAX_CHARS}）",
                "auto_fixed": False,
            })
        elif not needs_state and has_state:
            # 自动修复：移除 state 行
            new_meta = {k: v for k, v in meta.items() if k != "state"}
            new_body = _parse_frontmatter(text)[1]
            fp.write_text(_build_frontmatter(new_meta) + new_body, encoding="utf-8")
            auto_fixes.append({
                "type": "state_removed",
                "file": rel,
                "detail": f"类别「{category}」不需要 state 字段，已自动移除",
                "auto_fixed": True,
            })

    return debts, auto_fixes


def check_category(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 wiki 文档 frontmatter 的 type 字段是否与所在文件夹名称一致

    如果不一致，auto_fix: 重写 type 为文件夹名。
    """
    auto_fixes: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        if not rel.startswith("wiki/"):
            continue

        parts = Path(rel).parts
        if len(parts) < 3:
            continue
        # wiki/人物/张三.md → parts = ["wiki", "人物", "张三.md"]
        folder_name = parts[1]

        text = fp.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        current_type = meta.get("type", "")

        if current_type and current_type != folder_name:
            meta["type"] = folder_name
            fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
            auto_fixes.append({
                "type": "type_fixed",
                "file": rel,
                "detail": f"type 字段「{current_type}」与文件夹名「{folder_name}」不一致，已自动修正",
                "auto_fixed": True,
            })

    return auto_fixes


def check_doc_length(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 wiki 文档正文长度是否超过上限

    超过 DOC_MAX_CHARS (1500) → debt: length_overage
    """
    debts: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)
        body_len = len(body)

        if body_len > DOC_MAX_CHARS:
            debts.append({
                "type": "length_overage",
                "file": rel,
                "detail": f"正文过长（{body_len} 字，建议上限 {DOC_MAX_CHARS}）",
                "auto_fixed": False,
            })

    return debts


def check_description_length(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 wiki/plot/rules 文档 frontmatter 中 description 字段长度

    超过 DESC_MAX_CHARS (100) → debt: desc_verbose
    """
    debts: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        desc = meta.get("description", "")
        if len(desc) > DESC_MAX_CHARS:
            debts.append({
                "type": "desc_verbose",
                "file": rel,
                "detail": f"description 过长（{len(desc)} 字，建议上限 {DESC_MAX_CHARS}）",
                "auto_fixed": False,
            })

    return debts


def check_plot_links(workspace: Path) -> list[dict]:
    """检查剧情卡片中的 wikilink 是否指向已存在的 wiki 目标

    扫描 plot/ 目录，提取 [[wikilink]]，检查 wiki_map。
    """
    wiki_map = _build_wiki_map(workspace)
    valid_targets = set(wiki_map.keys())

    debts: list[dict] = []
    plot_root = workspace / "plot"
    if not plot_root.exists():
        return debts

    for fp in sorted(plot_root.rglob("*.md")):
        if fp.name == "index.md":
            continue
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        links = extract_wikilinks(text)
        for link in links:
            if link not in valid_targets:
                debts.append({
                    "type": "plot_broken_link",
                    "file": rel,
                    "target": link,
                    "context": f"[[{link}]]",
                })

    return debts


def check_plot_range(workspace: Path) -> list[dict]:
    """检查剧情卡片的 chapters 字段是否超出最大章节号

    如果 chapters 超出 document/ 中最大章节号，自动修正。
    """
    max_chapter = _get_max_chapter(workspace)
    if max_chapter == 0:
        return []

    auto_fixes: list[dict] = []
    plot_root = workspace / "plot"
    if not plot_root.exists():
        return auto_fixes

    for fp in sorted(plot_root.rglob("*.md")):
        if fp.name == "index.md":
            continue
        rel = fp.relative_to(workspace).as_posix()
        text = fp.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)

        chapters_raw = meta.get("chapters", meta.get("chapter", ""))
        if not chapters_raw:
            continue

        # 解析 chapters 字段中的最大数值
        numbers = re.findall(r"\d+", str(chapters_raw))
        if not numbers:
            continue

        max_in_field = max(int(n) for n in numbers)
        if max_in_field > max_chapter:
            # 自动修正：将超出部分截断
            # 简单方案：将 chapters 设为基础值
            from tools.chapter import parse_chapter_spec
            nums = parse_chapter_spec(str(chapters_raw))
            valid_nums = [n for n in nums if n <= max_chapter]
            if valid_nums:
                # 构建新的范围表达式
                new_chapters = _compact_chapter_list(valid_nums)
            else:
                new_chapters = str(max_chapter)

            meta["chapters"] = new_chapters
            fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
            auto_fixes.append({
                "type": "plot_range_fixed",
                "file": rel,
                "detail": f"chapters 超出最大章节 {max_chapter}，已自动修正为 {new_chapters}",
                "auto_fixed": True,
            })

    return auto_fixes


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


def check_appearance(workspace: Path, changed_files: list[Path]) -> list[dict]:
    """检查 wiki 词条在最近章节中的出场情况

    对每个 changed wiki 文件：
    1. 用 get_aliases 生成关键词
    2. 扫描最近 APPEARANCE_SCAN_CHAPTERS 章
    3. 返回出场信息
    """
    max_chapter = _get_max_chapter(workspace)
    if max_chapter == 0:
        return []

    # 确定扫描范围（最近 N 章）
    start_chapter = max(1, max_chapter - APPEARANCE_SCAN_CHAPTERS + 1)
    scan_range = list(range(start_chapter, max_chapter + 1))

    results: list[dict] = []

    for fp in changed_files:
        rel = fp.relative_to(workspace).as_posix()
        if not rel.startswith("wiki/"):
            continue

        text = fp.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        title = meta.get("title", fp.stem)

        keywords = get_aliases(title)
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
            "page": rel,
            "title": title,
            "keywords": keywords,
            "appeared_in": appeared_in,
            "recent_activity": len(appeared_in) > 0,
        })

    return results


# ── 短名→全名自动修复 ──────────────────────────────────────────────


def _build_short_name_map(workspace: Path) -> dict[str, str]:
    """构建短名→全名的映射，用于自动修复 wikilink 断链

    规则：
    - "叶蛮（阿蛮）" → 短名 "叶蛮"（括号前缀）+ 别名 "阿蛮"（来自 get_aliases）
    - "叶家【紫玉大陆】" → 短名 "叶家"（【】前缀）
    - 冲突时（同一短名指向多个全名）→ 跳过，不加入映射

    Returns:
        {short_name: full_stem_name}
    """
    short_map: dict[str, str] = {}
    wiki_root = workspace / "wiki"
    if not wiki_root.exists():
        return short_map

    for fp in sorted(wiki_root.rglob("*.md")):
        if fp.name in ("index.md", "relations.yaml"):
            continue
        stem = fp.stem

        # 候选短名列表
        candidates = []

        # 1. 来自 get_aliases 的别名（如 "阿蛮" ← "叶蛮（阿蛮）"）
        for alias in get_aliases(stem):
            if alias != stem:
                candidates.append(alias)

        # 2. 括号前缀：如 "叶蛮" ← "叶蛮（阿蛮）"
        paren_match = re.match(r"^(.+?)（[^）]+）$", stem)
        if paren_match:
            prefix = paren_match.group(1)
            if prefix != stem:
                candidates.append(prefix)

        # 3. 消歧义前缀：如 "叶家" ← "叶家【紫玉大陆】"
        bracket_match = re.match(r"^(.+?)【[^】]+】$", stem)
        if bracket_match:
            prefix = bracket_match.group(1)
            if prefix != stem:
                candidates.append(prefix)

        # 加入映射，冲突则跳过
        for c in candidates:
            if c in short_map and short_map[c] != stem:
                # 冲突：同一短名指向不同全名 → 删除（不自动修复）
                del short_map[c]
            elif c not in short_map:
                short_map[c] = stem

    return short_map


def auto_fix_short_name_wikilinks(workspace: Path) -> list[dict]:
    """自动修复短名 wikilink 为全名

    遍历所有 wiki/ 和 plot/ 下的 .md 文件，将 [[短名]] 自动替换为 [[全名]]。
    处理整个文件（含 frontmatter），因为 description/state 字段也可能包含 wikilink。
    在断链检查之前执行，修掉后不再报债务。

    Returns:
        修复记录列表
    """
    short_map = _build_short_name_map(workspace)
    if not short_map:
        return []

    # 按名称长度降序排列，避免短名是长名子串时误匹配
    short_names = sorted(short_map.keys(), key=len, reverse=True)

    fixes: list[dict] = []
    wikilink_re = re.compile(r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]")

    for root_dir in ["wiki", "plot"]:
        root = workspace / root_dir
        if not root.exists():
            continue
        for fp in sorted(root.rglob("*.md")):
            if fp.name in ("index.md", "relations.yaml"):
                continue
            rel = fp.relative_to(workspace).as_posix()
            text = fp.read_text(encoding="utf-8")

            new_text = text
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

                new_text = wikilink_re.sub(
                    _make_replacer(old_lower, new_clean),
                    new_text,
                )

            if new_text != text:
                fp.write_text(new_text, encoding="utf-8")
                fixes.append({
                    "type": "short_name_fixed",
                    "file": rel,
                    "detail": "短名 wikilink 已自动修复为全名",
                    "auto_fixed": True,
                })

    return fixes


# ── 入口函数 ─────────────────────────────────────────────────────────

def run_lint(workspace: Path, chapters: str | None = None) -> str:
    """运行全套 lint 检查，写入 debt JSON，返回格式化摘要"""
    changed_files = _get_changed_files(workspace)

    if not changed_files:
        return "（无文件需要检查）"

    # 1. YAML 结构
    yaml_debts = check_yaml_structure(workspace, changed_files)

    # 1.5 短名→全名 wikilink 自动修复（在断链检查之前执行，修掉后不再报债务）
    short_name_fixes = auto_fix_short_name_wikilinks(workspace)

    # 2. Wikilink 断链
    link_debts = check_wikilinks(workspace, changed_files)

    # 3. 规则文档 wikilink
    rules_fixes = check_rules_wikilinks(workspace)

    # 4. State 检查
    state_debts, state_fixes = check_state(workspace, changed_files)

    # 5. 类别检查
    category_fixes = check_category(workspace, changed_files)

    # 6. 正文长度
    length_debts = check_doc_length(workspace, changed_files)

    # 6.5 description 长度
    desc_debts = check_description_length(workspace, changed_files)

    # 7. 剧情链接
    plot_link_debts = check_plot_links(workspace)

    # 8. 剧情范围
    plot_range_fixes = check_plot_range(workspace)

    # 9. 出场检查
    appearance_results = check_appearance(workspace, changed_files)

    # ── 构建 debt_data ──
    debt_data: dict[str, list] = {
        "broken_links": [d for d in link_debts if d["type"] == "broken_link"],
        "state_missing": [d for d in state_debts if d["type"] == "state_missing"],
        "state_verbose": [d for d in state_debts if d["type"] == "state_verbose"],
        "length_overage": [d for d in length_debts if d["type"] == "length_overage"],
        "desc_verbose": [d for d in desc_debts if d["type"] == "desc_verbose"],
        "plot_broken_links": [d for d in plot_link_debts if d["type"] == "plot_broken_link"],
        "appearance": appearance_results,
        "file_errors": [d for d in yaml_debts if not d.get("auto_fixed")],
    }

    # 写入 debt JSON
    debt_fp = workspace / DEBT_FILE
    with open(debt_fp, "w", encoding="utf-8") as f:
        json.dump(debt_data, f, ensure_ascii=False, indent=2)

    # ── 构建关系图（可选，如果 import 可用） ──
    try:
        from auto.relation_extractor import build_relations, save_relations
        relations = build_relations(workspace)
        if relations:
            save_relations(workspace, relations)
    except ImportError:
        pass

    # ── 格式化摘要 ──
    lines = [
        "📋 Lint 检查报告",
        f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"检查文件数：{len(changed_files)}",
        "",
    ]

    # 自动修复汇总（含 yaml 自动修复）
    yaml_auto_fixes = [d for d in yaml_debts if d.get("auto_fixed")]
    all_fixes = (
        short_name_fixes + rules_fixes + state_fixes + category_fixes + plot_range_fixes + yaml_auto_fixes
    )
    total_fixed = len(all_fixes)
    if all_fixes:
        lines.append("## 代码 lint 完成\n")
        lines.append(f"### 自动修复（{total_fixed} 项）")
        for fix in all_fixes:
            lines.append(f"  ✅ {fix['file']}: {fix['detail']}")
        lines.append("")

    # 需人工处理的债务
    debt_groups = [
        ("broken_links", link_debts),
        ("state_missing", [d for d in state_debts if d["type"] == "state_missing"]),
        ("state_verbose", [d for d in state_debts if d["type"] == "state_verbose"]),
        ("length_overage", length_debts),
        ("desc_verbose", desc_debts),
        ("plot_broken_links", plot_link_debts),
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
                for item in items[:5]:
                    detail = item.get("detail", item.get("target", ""))
                    lines.append(f"  ⚠️ {detail}")
                if len(items) > 5:
                    lines.append(f"  ...还有 {len(items)-5} 项")
        lines.append("")

    if plot_range_fixes:
        lines.append(f"### 剧情范围修正（{len(plot_range_fixes)} 处）")
        for d in plot_range_fixes:
            lines.append(f"  🔧 {d['file']}：{d['detail']}")
        lines.append("")

    # 出场统计
    active_count = sum(1 for r in appearance_results if r["recent_activity"])
    if appearance_results:
        lines.append(f"### 出场统计")
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
