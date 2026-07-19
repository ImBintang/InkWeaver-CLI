"""类别管理工具：创建、编辑类别和索引"""

import re
from pathlib import Path


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
    """查看 wiki 类别列表

    Returns:
        格式化的类别列表
    """
    wiki_root = _wiki_root(workspace)
    if not wiki_root.exists():
        return "（wiki 目录不存在）"

    categories = []
    for d in sorted(wiki_root.iterdir()):
        if d.is_dir():
            categories.append(d.name)

    if not categories:
        return "（暂无类别）"

    lines = ["Wiki 类别列表："]
    for cat in categories:
        index_fp = wiki_root / cat / "index.md"
        desc = ""
        if index_fp.exists():
            meta, body = _parse_frontmatter(index_fp.read_text(encoding="utf-8"))
            desc = meta.get("description", body[:50] if body else "")
        line = f"  - {cat}"
        if desc:
            line += f"：{desc}"
        lines.append(line)

    return "\n".join(lines)


def new_category(workspace: Path, name: str, description: str = "",
                 writing_guide: str = "", has_state: bool = False) -> str:
    """创建新类别

    Args:
        name: 类别名
        description: 类别描述
        writing_guide: 写作规范
        has_state: 是否需要 state 字段

    Returns:
        操作结果消息
    """
    wiki_root = _wiki_root(workspace)
    wiki_root.mkdir(exist_ok=True)

    cat_dir = wiki_root / name
    if cat_dir.exists():
        return f"错误：类别「{name}」已存在"

    cat_dir.mkdir()

    # 创建类别 index.md
    index_fp = cat_dir / "index.md"
    meta = {
        "name": name,
        "description": description or f"{name}类词条",
    }
    body = f"# {name}类索引\n\n"
    if description:
        body += f"## 描述\n{description}\n\n"
    body += f"## 写作规范\n"
    if writing_guide:
        body += writing_guide + "\n"
    else:
        body += f"- 请描述该类别词条应包含的信息字段\n"
    body += f"\n## 是否需要 state 字段\n{'是' if has_state else '否'}\n"

    index_fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")

    # 更新总索引
    _append_to_main_index(workspace, name, description or f"{name}类词条")

    return f"已创建类别：{name}"


def edit_category(workspace: Path, name: str, description: str = None,
                  writing_guide: str = None, has_state: bool = None) -> str:
    """编辑类别

    Args:
        name: 类别名
        description: 新描述（None 表示不修改）
        writing_guide: 新写作规范（None 表示不修改）
        has_state: 新 state 配置（None 表示不修改）

    Returns:
        操作结果消息
    """
    index_fp = _category_index_file(workspace, name)
    if not index_fp.exists():
        return f"错误：类别「{name}」不存在"

    meta, body = _parse_frontmatter(index_fp.read_text(encoding="utf-8"))
    if description is not None:
        meta["description"] = description
    if writing_guide is not None:
        # 替换写作规范部分
        body = re.sub(r"## 写作规范.*?(?=\n## |\Z)",
                      f"## 写作规范\n{writing_guide}\n", body, flags=re.DOTALL)
    if has_state is not None:
        body = re.sub(r"## 是否需要 state 字段.*",
                      f"## 是否需要 state 字段\n{'是' if has_state else '否'}\n", body)

    index_fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
    return f"已更新类别：{name}"


def read_index(workspace: Path, category: str = None) -> str:
    """读取总 index 或指定类别 index

    Args:
        category: 类别名（None 表示读取总索引）

    Returns:
        索引文档内容
    """
    if category:
        fp = _category_index_file(workspace, category)
        if not fp.exists():
            return f"错误：类别「{category}」的索引不存在"
    else:
        fp = _index_file(workspace)
        if not fp.exists():
            return "（总索引不存在）"

    return fp.read_text(encoding="utf-8")


def edit_index(workspace: Path, content: str, category: str = None) -> str:
    """编辑总 index 或指定类别 index

    Args:
        content: 新内容（全文）
        category: 类别名（None 表示编辑总索引）

    Returns:
        操作结果消息
    """
    if category:
        fp = _category_index_file(workspace, category)
        if not fp.exists():
            return f"错误：类别「{category}」的索引不存在"
    else:
        fp = _index_file(workspace)
        fp.parent.mkdir(parents=True, exist_ok=True)

    fp.write_text(content, encoding="utf-8")
    target = f"类别「{category}」索引" if category else "总索引"
    return f"已更新{target}"


def _append_to_main_index(workspace: Path, category: str, description: str):
    """向总索引追加类别条目"""
    index_fp = _index_file(workspace)
    index_fp.parent.mkdir(parents=True, exist_ok=True)

    if index_fp.exists():
        content = index_fp.read_text(encoding="utf-8")
        if f"## {category}" in content:
            return  # 已存在
        content += f"\n- **{category}**：{description}\n"
    else:
        content = f"# Wiki 总索引\n\n- **{category}**：{description}\n"

    index_fp.write_text(content, encoding="utf-8")
