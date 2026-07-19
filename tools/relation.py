"""关系查询工具"""

import yaml
from pathlib import Path


WIKI_DIR = "wiki"


def _relations_file(workspace: Path) -> Path:
    return workspace / WIKI_DIR / "relations.yaml"


def query_relations(workspace: Path, name: str) -> str:
    """查询指定词条的所有关联词条

    Args:
        name: 词条名

    Returns:
        格式化的关系列表
    """
    rel_file = _relations_file(workspace)
    if not rel_file.exists():
        return "（关系图尚未构建，请先运行关系提取脚本）"

    with open(rel_file, "r", encoding="utf-8") as f:
        relations = yaml.safe_load(f) or {}

    if name not in relations:
        return f"词条「{name}」暂无关联词条"

    related = relations[name]
    if not related:
        return f"词条「{name}」暂无关联词条"

    lines = [f"词条「{name}」的关联词条："]
    for r in sorted(related):
        lines.append(f"  - {r}")

    return "\n".join(lines)


def get_all_relations(workspace: Path) -> dict:
    """获取完整关系图（供脚本使用）"""
    rel_file = _relations_file(workspace)
    if not rel_file.exists():
        return {}
    with open(rel_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def set_relations(workspace: Path, relations: dict):
    """设置完整关系图（供脚本使用）"""
    rel_file = _relations_file(workspace)
    rel_file.parent.mkdir(parents=True, exist_ok=True)
    with open(rel_file, "w", encoding="utf-8") as f:
        yaml.dump(relations, f, allow_unicode=True, default_flow_style=False, sort_keys=True)
