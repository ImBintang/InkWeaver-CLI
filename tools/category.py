"""类别管理工具：创建、编辑类别和索引（v5：写入 categories 表）"""

import json
import re
from pathlib import Path

from tools.editor import _get_proxy


WIKI_DIR = "wiki"


def _wiki_root(workspace: Path) -> Path:
    return workspace / WIKI_DIR


def _index_file(workspace: Path) -> Path:
    return _wiki_root(workspace) / "index.md"


def _category_index_file(workspace: Path, category: str) -> Path:
    return _wiki_root(workspace) / category / "index.md"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()
    meta = {}
    for line in match.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, match.group(2).strip()


def _build_frontmatter(meta: dict) -> str:
    """构建 YAML frontmatter"""
    lines = []
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def category_list(workspace: Path) -> str:
    """查看 wiki 类别列表（v5：从 categories 表读取）

    Returns:
        格式化的类别列表
    """
    proxy = _get_proxy(workspace)
    cats = proxy.list_categories()
    if not cats:
        return "（暂无类别）"

    lines = ["Wiki 类别列表："]
    for c in cats:
        spec = c.get("spec", {})
        desc = spec.get("description", "")
        line = f"  - {c['name']}"
        if desc:
            line += f"：{desc}"
        lines.append(line)

    return "\n".join(lines)


def new_category(workspace: Path, name: str, description: str = "",
                 writing_guide: str = "", has_state: bool = False) -> str:
    """创建新类别（v5：写入 categories 表）

    Args:
        name: 类别名
        description: 类别描述
        writing_guide: 写作规范
        has_state: 是否需要 state 字段

    Returns:
        操作结果消息
    """
    # 防御拦截：禁止创建"宝物"类别，引导使用"世界观"
    if name == "宝物":
        return (
            f"错误：禁止创建「宝物」类别。\n"
            f"「宝物」是模糊归类，物品实体应优先归入「法宝」「丹药」「灵草」等具体类别。\n"
            f"如确实无法归入任何现有类别，请使用「世界观」类别作为兜底。"
        )

    proxy = _get_proxy(workspace)
    # 检查是否已存在
    existing = proxy.get_category_by_name(name)
    if existing:
        return f"错误：类别「{name}」已存在"

    spec = {"description": description or f"{name}类词条"}
    if writing_guide:
        spec["writing_guide"] = writing_guide
    if has_state:
        spec["state_required"] = True

    cat_id = proxy.create_category(name, "wiki", spec)
    return f"已创建类别：{name}（ID: {cat_id}）"


def edit_category(workspace: Path, name: str, description: str = None,
                  writing_guide: str = None, has_state: bool = None) -> str:
    """编辑类别（v5：更新 categories 表）

    Args:
        name: 类别名
        description: 新描述（None 表示不修改）
        writing_guide: 新写作规范（None 表示不修改）
        has_state: 新 state 配置（None 表示不修改）

    Returns:
        操作结果消息
    """
    proxy = _get_proxy(workspace)
    cat = proxy.get_category_by_name(name)
    if cat is None:
        return f"错误：类别「{name}」不存在"

    spec = cat.get("spec", {})
    if description is not None:
        spec["description"] = description
    if writing_guide is not None:
        spec["writing_guide"] = writing_guide
    if has_state is not None:
        spec["state_required"] = has_state

    proxy._db.update_category(cat["id"], spec=json.dumps(spec, ensure_ascii=False))
    return f"已更新类别：{name}"


def read_index(workspace: Path, category: str = None) -> str:
    """读取类别 index 信息（v5：从 categories 表读取）

    Args:
        category: 类别名（None 表示浏览所有类别）

    Returns:
        类别信息
    """
    proxy = _get_proxy(workspace)
    if category:
        cat = proxy.get_category_by_name(category)
        if cat is None:
            return f"错误：类别「{category}」不存在"
        spec = cat.get("spec", {})
        lines = [
            f"类别：{cat['name']}",
            f"描述：{spec.get('description', '')}",
        ]
        if spec.get("writing_guide"):
            lines.append(f"写作规范：{spec['writing_guide']}")
        if spec.get("state_required"):
            lines.append("state 字段：需要")
        return "\n".join(lines)
    else:
        return category_list(workspace)


def edit_index(workspace: Path, content: str, category: str = None) -> str:
    """编辑类别信息（v5：更新 categories 表 spec 字段）

    Args:
        content: 新 spec 内容（JSON 格式）
        category: 类别名（必填）

    Returns:
        操作结果消息
    """
    if not category:
        return "错误：请指定要编辑的类别名"
    proxy = _get_proxy(workspace)
    cat = proxy.get_category_by_name(category)
    if cat is None:
        return f"错误：类别「{category}」不存在"
    try:
        spec = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        spec = {"description": content}
    proxy._db.update_category(cat["id"], spec=json.dumps(spec, ensure_ascii=False))
    return f"已更新类别：{category}"


def _append_to_main_index(workspace: Path, category: str, description: str):
    """向总索引追加类别条目（v5：保留兼容，不再使用文件索引）"""
    pass
