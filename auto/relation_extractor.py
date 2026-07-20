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


def _extract_file_relations(md_file: Path, relations: dict):
    """从单个文件中提取关系并更新到 relations dict"""
    if md_file.name in ("index.md", RELATIONS_FILE):
        return

    content = md_file.read_text(encoding="utf-8")
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]

    from tools.wiki import _parse_frontmatter
    meta, _ = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
    source_name = meta.get("title", md_file.stem)

    targets = extract_wikilinks(content)
    if targets:
        if source_name not in relations:
            relations[source_name] = set()
        for target in targets:
            relations[source_name].add(target)


def build_relations(workspace: Path, extra_dirs: list[str] | None = None) -> dict:
    """扫描所有 wiki 和剧情卡片文档，构建关系图

    Args:
        workspace: 工作区路径
        extra_dirs: 额外扫描的目录（相对路径），如 ["plot"]

    Returns:
        {词条名: [关联词条名, ...]} 的 dict
    """
    if extra_dirs is None:
        extra_dirs = ["plot"]

    wiki_root = workspace / WIKI_DIR
    relations = {}

    # 扫描 wiki/
    if wiki_root.exists():
        for md_file in sorted(wiki_root.rglob("*.md")):
            if md_file.name in ("index.md", RELATIONS_FILE):
                continue
            _extract_file_relations(md_file, relations)

    # 扫描额外目录
    for extra_dir in extra_dirs:
        extra_path = workspace / extra_dir
        if extra_path.exists():
            for md_file in sorted(extra_path.rglob("*.md")):
                if md_file.name == "index.md":
                    continue
                _extract_file_relations(md_file, relations)

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
