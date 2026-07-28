"""名称解析工具 — 同词条多名称统一统计

v5.4 新增。为 keywords_stat / check_wiki 等查询工具提供别名扩展能力。

括号语义规则：
- （）为别名括号：叶寒（寒叔） → 搜索集 ["叶寒", "寒叔"]
- 【】为消歧义括号：叶家【紫玉大陆】 → 搜索集 ["叶家"]（仅本体）
- 无括号：秦鸣 → ["秦鸣"]

与 lint.py 的 get_aliases 区别：
- lint 的 get_aliases 返回含全名自身的别名列表（用于 wikilink 断链检查）
- 本模块的 get_search_names 返回用于文本搜索的名称变体（不含全名本身）
"""

import re
from pathlib import Path

# 匹配 "本体（别名）" 格式（全角括号）
_ALIAS_PATTERN = re.compile(r"^(.+?)（([^）]+)）$")
# 匹配 "本体【消歧义】" 格式（全角方括号）
_DISAMBIG_PATTERN = re.compile(r"^(.+?)【[^】]+】$")


def get_search_names(title: str) -> list[str]:
    """从词条名解析出所有应参与文本搜索的名称变体

    规则：
    - "叶寒（寒叔）" → ["叶寒", "寒叔"]  （本体 + 别名）
    - "叶家【紫玉大陆】" → ["叶家"]        （仅本体）
    - "秦鸣" → ["秦鸣"]                   （原样）
    - "叶寒（寒叔） extra" → ["叶寒（寒叔） extra"]  （格式不标准，fallback）

    Args:
        title: wiki 词条的规范名称

    Returns:
        名称变体列表（去重，保持顺序）
    """
    title = title.strip()
    if not title:
        return [title]

    # 别名括号：提取本体和别名
    m = _ALIAS_PATTERN.match(title)
    if m:
        stem = m.group(1).strip()
        alias = m.group(2).strip()
        names = []
        if stem:
            names.append(stem)
        if alias and alias != stem:
            names.append(alias)
        return names if names else [title]

    # 消歧义括号：只取本体
    m = _DISAMBIG_PATTERN.match(title)
    if m:
        stem = m.group(1).strip()
        return [stem] if stem else [title]

    # 无括号
    return [title]


def build_alias_map(workspace: Path) -> dict[str, str]:
    """从 DB 构建 alias→canonical_name 反查表

    遍历 wiki_main 表所有词条，对每个词条名调用 get_search_names，
    将所有变体映射回规范名。

    示例返回：
    {
        "叶寒": "叶寒（寒叔）",
        "寒叔": "叶寒（寒叔）",
        "叶家": "叶家【紫玉大陆】",
        "秦鸣": "秦鸣",
    }

    Args:
        workspace: 工作区路径

    Returns:
        alias→canonical_name 字典
    """
    from tools.db.service import SQLiteService

    db_path = workspace / "wiki.db"
    if not db_path.exists():
        return {}

    db = SQLiteService(db_path)
    alias_map: dict[str, str] = {}

    try:
        # 遍历所有类别下的 wiki 词条
        cats = db.list_categories("wiki")
        for cat in cats:
            mains = db.wiki_list_main(cat["id"])
            for m in mains:
                canonical = m["name"]
                # 规范名自身也加入映射
                alias_map[canonical] = canonical
                # 展开搜索名
                for variant in get_search_names(canonical):
                    if variant not in alias_map:
                        alias_map[variant] = canonical
                    # 如果冲突（两个词条共享同一变体），保留先注册的
    finally:
        db.close()

    return alias_map


def resolve_name(workspace: Path, query: str) -> str | None:
    """将任意查询名解析为规范名（wiki_main 中的全名）

    Args:
        workspace: 工作区路径
        query: 用户查询名（可能是别名、本体、或规范名）

    Returns:
        规范名，未找到返回 None
    """
    alias_map = build_alias_map(workspace)
    return alias_map.get(query.strip())


def expand_keywords(workspace: Path, keywords: list[str]) -> dict[str, list[str]]:
    """将关键词列表扩展为含别名变体的搜索集

    对每个关键词：
    1. 尝试通过 alias_map 找到规范名
    2. 如果找到，用 get_search_names 展开所有变体
    3. 如果未找到，保持原样

    示例：
        expand_keywords(ws, ["寒叔"]) → {"寒叔": ["叶寒", "寒叔"]}
        expand_keywords(ws, ["叶匀"]) → {"叶匀": ["叶匀"]}

    Args:
        workspace: 工作区路径
        keywords: 原始关键词列表

    Returns:
        {原始关键词: [扩展后的搜索名列表]}
    """
    alias_map = build_alias_map(workspace)
    result: dict[str, list[str]] = {}

    for kw in keywords:
        kw_stripped = kw.strip()
        canonical = alias_map.get(kw_stripped)
        if canonical:
            variants = get_search_names(canonical)
            # 确保原始关键词也在列表中
            if kw_stripped not in variants:
                variants.append(kw_stripped)
            result[kw] = variants
        else:
            result[kw] = [kw_stripped]

    return result
