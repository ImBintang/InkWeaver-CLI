"""从 wiki 文档中提取 wikilink，构建 relations.yaml 关系图"""

import re
import sys
from pathlib import Path

import yaml


WIKI_DIR = "wiki"
RELATIONS_FILE = "relations.yaml"

# 匹配 [[target]] 或 [[target|display]] 格式
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wikilinks(text: str) -> list:
    """从文本中提取所有 wikilink 目标"""
    return [match.strip() for match in WIKILINK_PATTERN.findall(text)]


def build_relations(workspace: Path) -> dict:
    """扫描所有 wiki 文档，构建关系图

    Returns:
        {词条名: [关联词条名, ...]} 的 dict
    """
    wiki_root = workspace / WIKI_DIR
    if not wiki_root.exists():
        print(f"错误：wiki 目录不存在 {wiki_root}")
        return {}

    relations = {}  # {source: set of targets}

    for md_file in sorted(wiki_root.rglob("*.md")):
        # 跳过 index.md 和 relations.yaml
        if md_file.name in ("index.md", RELATIONS_FILE):
            continue

        content = md_file.read_text(encoding="utf-8")
        # 跳过 frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2]

        # 获取当前词条名（从 frontmatter 或文件名）
        from tools.wiki import _parse_frontmatter
        meta, _ = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
        source_name = meta.get("title", md_file.stem)

        # 提取 wikilink
        targets = extract_wikilinks(content)
        if targets:
            if source_name not in relations:
                relations[source_name] = set()
            for target in targets:
                relations[source_name].add(target)

    # 转换为有序列表
    result = {}
    for source in sorted(relations):
        result[source] = sorted(relations[source])

    return result


def save_relations(workspace: Path, relations: dict):
    """保存关系到 relations.yaml"""
    rel_file = workspace / WIKI_DIR / RELATIONS_FILE
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
