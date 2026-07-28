"""规则文档工具：列表和读取

兼容性：new_rule / edit_rule / delete_rule 保留为向后兼容的薄代理层，
核心实现在 tools/editor.py（统一编辑器，支持 wiki / plot / rule 三种类型）。
"""

from pathlib import Path

from tools.editor import (
    parse_frontmatter as _parse_frontmatter,
    build_frontmatter as _build_frontmatter,
    create_doc as _create_doc,
    edit_doc as _edit_doc,
    delete_doc as _delete_doc,
    edit_doc_text as _edit_doc_text,
    edit_doc_wikilink as _edit_doc_wikilink,
)


RULES_DIR = "rules"


def _rules_root(workspace: Path) -> Path:
    return workspace / RULES_DIR


def _ensure_rules_dir(workspace: Path) -> Path:
    root = _rules_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def rules_list(workspace: Path) -> str:
    """查看规则档案列表（v5：调 proxy）

    Returns:
        格式化的规则列表
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    return proxy.list_docs("rule")


def read_rule(workspace: Path, name: str, yaml_only: bool = True) -> str:
    """读取指定规则文档（v5：调 proxy）

    Args:
        name: 规则名（不含 .md 后缀）
        yaml_only: True 只返回 frontmatter，False 返回全文

    Returns:
        规则文档全文或错误消息
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    return proxy.read_doc("rule", name, yaml_only=yaml_only)


def new_rule(workspace: Path, name: str, content: str,
             keywords: str = "", updated: int | None = None) -> str:
    """新建规则文档（薄代理层 → tools.editor.create_doc）"""
    return _create_doc(
        workspace, doc_type="rule", name=name, content=content,
        keywords=keywords, updated=updated,
    )


def edit_rule(workspace: Path, name: str, content: str,
              keywords: str = None, updated: int | None = None) -> str:
    """编辑规则文档（薄代理层 → tools.editor.edit_doc）"""
    return _edit_doc(
        workspace, doc_type="rule", name=name, content=content,
        keywords=keywords, updated=updated,
    )


def edit_rule_text(workspace: Path, name: str,
                   old_text: str, new_text: str) -> str:
    """在规则文档正文中精确匹配一段文本并替换（手术刀式）"""
    return _edit_doc_text(
        workspace, doc_type="rule", name=name,
        old_text=old_text, new_text=new_text,
    )


def edit_rule_wikilink(workspace: Path, name: str,
                       old_target: str, new_target: str = "",
                       mode: str = "redirect") -> str:
    """替换规则文档正文中所有指向 old_target 的 [[wikilink]]

    mode="redirect"（默认）：重定向目标
    mode="unlink"：取消链接，new_target 忽略
    """
    return _edit_doc_wikilink(
        workspace, doc_type="rule", name=name,
        old_target=old_target, new_target=new_target,
        mode=mode,
    )


def delete_rule(workspace: Path, name: str) -> str:
    """删除规则文档（薄代理层 → tools.editor.delete_doc）"""
    return _delete_doc(workspace, doc_type="rule", name=name)
