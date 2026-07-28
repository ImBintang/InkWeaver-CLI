"""关系图提取 — v5：从 DB/proxy 读取文档

扫描所有 wiki 和剧情卡片文档中的 [[wikilink]]，构建关系图。
v5 改造：底层数据源从文件系统切换为 ProxyService（缓存 + DB）。
"""

import re
import sys
from pathlib import Path

import yaml


RELATIONS_FILE = "relations.yaml"

# 匹配 [[target]] 或 [[target|display]] 格式
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wikilinks(text: str) -> list:
    """从文本中提取所有 wikilink 目标"""
    return [match.strip() for match in WIKILINK_PATTERN.findall(text)]


def build_relations(workspace: Path, extra_dirs: list[str] | None = None) -> dict:
    """扫描所有 wiki 和剧情卡片文档，构建关系图

    v5：通过 ProxyService 从 DB 获取文档内容。

    Args:
        workspace: 工作区路径
        extra_dirs: 保留参数（v5 中不再使用，plot 已包含在 DB 中）

    Returns:
        {词条名: [关联词条名, ...]} 的 dict
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    relations: dict[str, set] = {}

    # 扫描 wiki（DB + 缓存）
    cats = proxy.list_categories("wiki")
    for cat in cats:
        mains = proxy._db.wiki_list_main(cat["id"])
        for m in mains:
            current = proxy._db.wiki_get_current(m["id"])
            if current is None:
                continue
            content = current.get("content", "")
            targets = extract_wikilinks(content)
            if targets:
                if m["name"] not in relations:
                    relations[m["name"]] = set()
                relations[m["name"]].update(targets)

    # 缓存中新增的 wiki
    for (dt, _), cached in proxy._cache.items():
        if dt == "wiki" and cached.is_new and not cached.is_deleted:
            targets = extract_wikilinks(cached.content)
            if targets:
                if cached.name not in relations:
                    relations[cached.name] = set()
                relations[cached.name].update(targets)

    # 扫描 plot（DB + 缓存）
    for m in proxy._db.plot_list_main():
        current = proxy._db.plot_get_current(m["id"])
        if current is None:
            continue
        content = current.get("content", "")
        targets = extract_wikilinks(content)
        if targets:
            if m["name"] not in relations:
                relations[m["name"]] = set()
            relations[m["name"]].update(targets)

    for (dt, _), cached in proxy._cache.items():
        if dt == "plot" and cached.is_new and not cached.is_deleted:
            targets = extract_wikilinks(cached.content)
            if targets:
                if cached.name not in relations:
                    relations[cached.name] = set()
                relations[cached.name].update(targets)

    # 转换为有序列表
    result = {}
    for source in sorted(relations):
        result[source] = sorted(relations[source])

    return result


def save_relations(workspace: Path, relations: dict):
    """保存关系到 relations.yaml"""
    rel_file = workspace / RELATIONS_FILE
    rel_file.parent.mkdir(parents=True, exist_ok=True)
    with open(rel_file, "w", encoding="utf-8") as f:
        yaml.dump(relations, f, allow_unicode=True, default_flow_style=False, sort_keys=True)
    print(f"已保存关系图：{rel_file}")


def main():
    """命令行入口"""
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1])
    else:
        workspace = Path.cwd()

    if not workspace.exists():
        print(f"错误：工作区不存在 {workspace}")
        sys.exit(1)

    print(f"扫描工作区：{workspace}")
    relations = build_relations(workspace)

    if not relations:
        print("未发现任何 wikilink 关系")
        return

    save_relations(workspace, relations)

    # 打印摘要
    total_links = sum(len(targets) for targets in relations.values())
    print(f"共发现 {len(relations)} 个词条，{total_links} 条关系")


if __name__ == "__main__":
    main()
