"""Wiki 文档工具：增删查改、列表、检查

兼容性：new_wiki / edit_wiki / delete_wiki 保留为向后兼容的薄代理层，
核心实现在 tools/editor.py（统一编辑器，支持 wiki / plot / rule 三种类型）。
"""

import re
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


WIKI_DIR = "wiki"


def _wiki_root(workspace: Path) -> Path:
    return workspace / WIKI_DIR


def _ensure_wiki_dir(workspace: Path) -> Path:
    root = _wiki_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def _wiki_file(workspace: Path, category: str, name: str) -> Path:
    return _wiki_root(workspace) / category / f"{name}.md"


def _ensure_category_dir(workspace: Path, category: str) -> Path:
    root = _ensure_wiki_dir(workspace)
    cat_dir = root / category
    cat_dir.mkdir(exist_ok=True)
    return cat_dir


def _category_exists(workspace: Path, category: str) -> bool:
    """检查 wiki 类别是否存在（v5：从 DB categories 表查询）"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    return proxy.get_category_by_name(category) is not None


def new_wiki(workspace: Path, category: str, name: str, content: str = "",
             description: str = "", state: str = "", keywords: str = "",
             tags: list = None,
             updated: int | None = None) -> str:
    """新建 wiki 文档（薄代理层 → tools.editor.create_doc）"""
    # 防御：禁止在规则类别下创建 wiki 词条
    if category in ("规则", "rules", "rule"):
        return (
            f"错误：类别「{category}」是规则类别，请使用 new_rule / edit_rule 管理规则文档，"
            f"不要用 new_wiki 操作。规则文档存储在 rules/ 目录，不参与关系系统。"
        )
    # 防御：禁止在未创建的类别下创建词条
    if not _category_exists(workspace, category):
        return (
            f"错误：类别「{category}」不存在，无法创建词条。"
            f"请先用 new_category 创建类别，或用 category_list 查看已有类别。"
        )
    return _create_doc(
        workspace, doc_type="wiki", name=name, content=content,
        category=category, description=description, state=state,
        keywords=keywords, tags=tags, updated=updated,
    )


def edit_wiki(workspace: Path, category: str, name: str,
              content: str = None, description: str = None,
              state: str = None, keywords: str = None,
              tags: list = None,
              updated: int | None = None) -> str:
    """编辑 wiki 文档（薄代理层 → tools.editor.edit_doc）"""
    return _edit_doc(
        workspace, doc_type="wiki", name=name, content=content,
        category=category, description=description, state=state,
        keywords=keywords, tags=tags, updated=updated,
    )


def edit_wiki_text(workspace: Path, category: str, name: str,
                   old_text: str, new_text: str) -> str:
    """在 wiki 正文中精确匹配一段文本并替换（手术刀式，不传整个body）

    参考 learn-claude-code 的 edit_file 模式。只在 body 中替换第一次出现。
    """
    return _edit_doc_text(
        workspace, doc_type="wiki", name=name,
        old_text=old_text, new_text=new_text, category=category,
    )


def edit_wiki_wikilink(workspace: Path, category: str, name: str,
                       old_target: str, new_target: str = "",
                       mode: str = "redirect",
                       remember: bool = False) -> str:
    """替换 wiki 正文中所有指向 old_target 的 [[wikilink]]

    mode="redirect"（默认）：重定向目标
    mode="unlink"：取消链接，new_target 忽略
    remember=True 时将目标记入 unlink 黑名单
    """
    return _edit_doc_wikilink(
        workspace, doc_type="wiki", name=name,
        old_target=old_target, new_target=new_target, category=category,
        mode=mode, remember=remember,
    )


def delete_wiki(workspace: Path, category: str, name: str) -> str:
    """删除 wiki 文档（薄代理层 → tools.editor.delete_doc）"""
    return _delete_doc(workspace, doc_type="wiki", name=name, category=category)


def read_wiki(workspace: Path, category: str, name: str, yaml_only: bool = True) -> str:
    """读取 wiki 文档（v5：调 proxy）

    Args:
        category: 类别名
        name: 词条名
        yaml_only: True 只返回 frontmatter，False 返回全文

    Returns:
        文档全文（含 frontmatter）或错误消息
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    return proxy.read_doc("wiki", name, category=category, yaml_only=yaml_only)


def wiki_list(workspace: Path, category: str, page: int = 1, page_size: int = 20) -> str:
    """查看类别下的 wiki 列表（分页，v5：调 proxy）

    Args:
        category: 类别名
        page: 页码（从 1 开始）
        page_size: 每页数量

    Returns:
        格式化的列表字符串
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    result = proxy.list_docs("wiki", category=category, page=page, page_size=page_size)

    # 翻页提示补充
    if "第 " in result and "共 " in result:
        import re
        m = re.search(r"第 (\d+)/(\d+) 页", result)
        if m:
            total_pages = int(m.group(2))
            page = int(m.group(1))
            if total_pages > 1 and page < total_pages:
                result += f"\n\n⚠️ 当前只显示第 {page} 页（共 {total_pages} 页），如需查找某个词条请继续翻页查看（传入 page={page + 1}）。"
                result += f"\n   不要因为本页没看到就下结论说词条不存在，先翻完所有页再判断！"
            elif total_pages > 1 and page >= total_pages:
                result += f"\n\n✅ 已是最后一页。"
    return result


def check_wiki(workspace: Path, name: str = None, chapters: str = None, text: str = None) -> str:
    """检查 wiki 词条在指定章节或文本中是否出现

    用法一：check_wiki(name="张三", chapters="1-5") — 查词条在章节中
    用法二：check_wiki(text="张三走进大殿") — 查文本包含哪些实体

    Args:
        name: 词条名（与 chapters 配合使用）
        chapters: 章节范围表达式
        text: 任意文本，自动匹配其中包含的实体名

    Returns:
        检查结果
    """
    if text:
        # 文本匹配模式：遍历所有 wiki 词条，看名称是否在 text 中出现（v5：从 DB 读取）
        from tools.editor import _get_proxy
        proxy = _get_proxy(workspace)
        cats = proxy.list_categories()
        if not cats:
            return "（暂无类别）"

        matches = []
        for cat in cats:
            mains = proxy._db.wiki_list_main(cat["id"])
            for m in mains:
                if m["name"] in text:
                    matches.append(f"[{cat['name']}] {m['name']}")

        if not matches:
            return "未在文本中匹配到已知实体。"
        return "文本中匹配到以下实体：\n" + "\n".join(matches)

    # 原有逻辑：查词条在章节中是否出现
    if not name or not chapters:
        return "错误：请提供 name+chapters（查章节匹配）或 text（查文本匹配）"

    from tools.chapter import read_chapters, parse_chapter_spec
    chapter_text = read_chapters(workspace, chapters)
    if not chapter_text.startswith("##"):
        return chapter_text  # 错误消息

    nums = parse_chapter_spec(chapters)
    found = []
    for num in nums:
        chapter_text = read_chapters(workspace, str(num))
        if name in chapter_text:
            found.append(num)

    if found:
        return f"词条「{name}」出现在第 {found} 章"
    else:
        return f"词条「{name}」未出现在指定章节中"


def check_wiki_yaml(workspace: Path, category: str = "",
                    name: str = "") -> str:
    """检查 wiki 文档的结构完整性（v5：从 DB/proxy 读取）

    检查项目：
    - 正文内容是否为空
    - 正文是否过短
    - 正文是否包含 Markdown 章节标题
    - 必要字段是否齐全

    Args:
        workspace: 工作区路径
        category: 类别名（为空则检查所有类别）
        name: 词条名（为空则检查该类别的所有词条）

    Returns:
        格式化的检查报告
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)

    # 收集要检查的文档列表: [(cat_name, doc_name, content)]
    docs_to_check: list[tuple[str, str, str]] = []

    cats = proxy.list_categories("wiki")
    if not cats:
        return "错误：暂无 wiki 类别"

    for cat in cats:
        cat_name = cat["name"]
        if category and cat_name != category:
            continue
        mains = proxy._db.wiki_list_main(cat["id"])
        for m in mains:
            if name and m["name"] != name:
                continue
            full = proxy.read_doc("wiki", m["name"], category=cat_name, yaml_only=False)
            if full.startswith("错误"):
                continue
            _, body = _parse_frontmatter(full)
            docs_to_check.append((cat_name, m["name"], body))

    if category and not any(c["name"] == category for c in cats):
        return f"错误：类别「{category}」不存在"
    if name and not docs_to_check:
        return f"错误：词条「{name}」不存在"
    if not docs_to_check:
        return "未找到任何 wiki 词条"

    # 逐项检查
    report_lines = []
    issue_count = 0
    ok_count = 0

    for cat_name, doc_name, body in docs_to_check:
        issues = []

        if not body or body.strip() in ("", "None"):
            issues.append("❌ 正文为空")
        else:
            if len(body.strip()) < 20:
                issues.append(f"⚠️ 正文过短（仅 {len(body.strip())} 字符）")
            if not re.search(r"^##\s+\S", body, re.MULTILINE):
                issues.append("⚠️ 正文缺少 Markdown 章节标题（##）")

        # 检查必要字段
        meta_full = proxy.read_doc("wiki", doc_name, category=cat_name, yaml_only=True)
        if not meta_full.startswith("错误"):
            meta, _ = _parse_frontmatter(meta_full)
            if "title" not in meta:
                issues.append("⚠️ frontmatter 缺少 title 字段")
            if "type" not in meta:
                issues.append("⚠️ frontmatter 缺少 type 字段")

        if issues:
            issue_count += 1
            report_lines.append(f"\n[{cat_name}/{doc_name}]")
            for issue in issues:
                report_lines.append(f"  {issue}")
        else:
            ok_count += 1

    summary = f"\n---\n检查完毕：共 {len(docs_to_check)} 个词条，通过 {ok_count} 个，异常 {issue_count} 个。"
    report_lines.append(summary)

    return "\n".join(report_lines)





def get_wiki_meta(workspace: Path, category: str, name: str) -> dict:
    """获取 wiki 文档的 frontmatter 元数据（v5：调 proxy）"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    full = proxy.read_doc("wiki", name, category=category, yaml_only=False)
    if full.startswith("错误"):
        return {}
    meta, _ = _parse_frontmatter(full)
    return meta


def get_wiki_body(workspace: Path, category: str, name: str) -> str:
    """获取 wiki 文档的正文（不含 frontmatter，v5：调 proxy）"""
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    full = proxy.read_doc("wiki", name, category=category, yaml_only=False)
    if full.startswith("错误"):
        return ""
    _, body = _parse_frontmatter(full)
    return body


def batch_create_wiki(workspace: Path, items: list[dict]) -> str:
    """批量创建 wiki 词条（部分成功模式）

    items 每个元素：{"category", "name", "content", "description"(opt), "state"(opt), "tags"(opt)}
    调用现有的 new_wiki 函数，返回 JSON 统计结果。

    Returns:
        JSON: {"success": N, "failed": M, "items": [...]}
    """
    import json

    results = []
    success_count = 0
    failed_count = 0

    for item in items:
        category = item.get("category", "")
        name = item.get("name", "")
        try:
            result = new_wiki(
                workspace,
                category=category,
                name=name,
                content=item.get("content", ""),
                description=item.get("description", ""),
                state=item.get("state", ""),
                keywords=item.get("keywords", ""),
                tags=item.get("tags"),
            )
            if result.startswith("错误："):
                results.append({
                    "category": category,
                    "name": name,
                    "status": "failed",
                    "error": result,
                })
                failed_count += 1
            else:
                results.append({
                    "category": category,
                    "name": name,
                    "status": "created",
                })
                success_count += 1
        except Exception as e:
            results.append({
                "category": category,
                "name": name,
                "status": "failed",
                "error": str(e),
            })
            failed_count += 1

    return json.dumps({
        "success": success_count,
        "failed": failed_count,
        "items": results,
    }, ensure_ascii=False)


def batch_edit_wiki(workspace: Path, items: list[dict]) -> str:
    """批量编辑 wiki 词条（部分成功模式）

    items 每个元素：{"category", "name", "content"(opt), "description"(opt), "state"(opt), "tags"(opt)}
    调用现有的 edit_wiki 函数，返回 JSON 统计结果。

    Returns:
        JSON: {"success": N, "failed": M, "items": [...]}
    """
    import json

    results = []
    success_count = 0
    failed_count = 0

    for item in items:
        category = item.get("category", "")
        name = item.get("name", "")
        try:
            result = edit_wiki(
                workspace,
                category=category,
                name=name,
                content=item.get("content"),
                description=item.get("description"),
                state=item.get("state"),
                keywords=item.get("keywords"),
                tags=item.get("tags"),
            )
            if result.startswith("错误："):
                results.append({
                    "category": category,
                    "name": name,
                    "status": "failed",
                    "error": result,
                })
                failed_count += 1
            else:
                results.append({
                    "category": category,
                    "name": name,
                    "status": "edited",
                })
                success_count += 1
        except Exception as e:
            results.append({
                "category": category,
                "name": name,
                "status": "failed",
                "error": str(e),
            })
            failed_count += 1

    return json.dumps({
        "success": success_count,
        "failed": failed_count,
        "items": results,
    }, ensure_ascii=False)
