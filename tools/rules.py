"""规则文档工具：列表和读取"""

from pathlib import Path


RULES_DIR = "rules"


def _rules_root(workspace: Path) -> Path:
    return workspace / RULES_DIR


def _ensure_rules_dir(workspace: Path) -> Path:
    root = _rules_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def rules_list(workspace: Path) -> str:
    """查看规则档案列表

    Returns:
        格式化的规则列表
    """
    root = _rules_root(workspace)
    if not root.exists():
        return "（rules 目录不存在）"

    files = sorted(root.glob("*.md"))
    if not files:
        return "（暂无规则文档）"

    lines = ["规则文档列表："]
    for fp in files:
        title = fp.stem
        # 读取第一行作为描述
        content = fp.read_text(encoding="utf-8").strip()
        first_line = content.splitlines()[0] if content else ""
        if first_line.startswith("# "):
            first_line = first_line[2:]
        line = f"  - {title}"
        if first_line and first_line != title:
            line += f"：{first_line}"
        lines.append(line)

    return "\n".join(lines)


def read_rule(workspace: Path, name: str) -> str:
    """读取指定规则文档

    Args:
        name: 规则名（不含 .md 后缀）

    Returns:
        规则文档全文或错误消息
    """
    fp = _rules_root(workspace) / f"{name}.md"
    if not fp.exists():
        return f"错误：规则文档「{name}」不存在"
    return fp.read_text(encoding="utf-8")


def new_rule(workspace: Path, name: str, content: str) -> str:
    """新建规则文档

    Args:
        name: 规则名
        content: 文档全文

    Returns:
        操作结果消息
    """
    root = _ensure_rules_dir(workspace)
    fp = root / f"{name}.md"
    if fp.exists():
        return f"错误：规则文档「{name}」已存在"

    from datetime import date
    # 确保有 frontmatter
    if not content.startswith("---"):
        content = f"---\ntitle: {name}\nupdated: {date.today()}\n---\n\n{content}"

    fp.write_text(content, encoding="utf-8")
    return f"已创建规则文档：{name}"


def edit_rule(workspace: Path, name: str, content: str) -> str:
    """编辑规则文档

    Args:
        name: 规则名
        content: 新全文

    Returns:
        操作结果消息
    """
    fp = _rules_root(workspace) / f"{name}.md"
    if not fp.exists():
        return f"错误：规则文档「{name}」不存在"

    fp.write_text(content, encoding="utf-8")
    return f"已更新规则文档：{name}"


def delete_rule(workspace: Path, name: str) -> str:
    """删除规则文档

    Args:
        name: 规则名

    Returns:
        操作结果消息
    """
    fp = _rules_root(workspace) / f"{name}.md"
    if not fp.exists():
        return f"错误：规则文档「{name}」不存在"
    fp.unlink()
    return f"已删除规则文档：{name}"
