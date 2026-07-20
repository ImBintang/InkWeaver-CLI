"""Wiki 文档工具：增删查改、列表、检查"""

import re
import yaml
from pathlib import Path


WIKI_DIR = "wiki"


def _wiki_root(workspace: Path) -> Path:
    return workspace / WIKI_DIR


def _ensure_wiki_dir(workspace: Path) -> Path:
    root = _wiki_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()
    meta = {}
    for line in match.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        # 尝试解析列表
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        meta[key] = value
    return meta, match.group(2).strip()


def _build_frontmatter(meta: dict) -> str:
    """将 meta dict 构建为 YAML frontmatter 字符串"""
    lines = []
    for key, value in meta.items():
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def _wiki_file(workspace: Path, category: str, name: str) -> Path:
    return _wiki_root(workspace) / category / f"{name}.md"


def _ensure_category_dir(workspace: Path, category: str) -> Path:
    root = _ensure_wiki_dir(workspace)
    cat_dir = root / category
    cat_dir.mkdir(exist_ok=True)
    return cat_dir


def new_wiki(workspace: Path, category: str, name: str, content: str = "",
             description: str = "", state: str = "", tags: list = None) -> str:
    """新建 wiki 文档

    Args:
        category: 类别名（如 "人物"）
        name: 词条名（如 "张三"）
        content: 正文内容（不含 frontmatter）
        description: 描述（静态信息）
        state: 状态（动态信息，可选）
        tags: 标签列表

    Returns:
        操作结果消息
    """
    cat_dir = _ensure_category_dir(workspace, category)
    fp = cat_dir / f"{name}.md"
    if fp.exists():
        return f"错误：词条「{name}」已存在"

    from datetime import date
    meta = {
        "type": _category_to_type(category),
        "title": name,
        "updated": str(date.today()),
    }
    if tags:
        meta["tags"] = tags
    if description:
        meta["description"] = description
    if state:
        meta["state"] = state

    fp.write_text(_build_frontmatter(meta) + content, encoding="utf-8")
    return f"已创建词条：{category}/{name}"


def edit_wiki(workspace: Path, category: str, name: str,
              content: str = None, description: str = None,
              state: str = None, tags: list = None) -> str:
    """编辑 wiki 文档

    Args:
        category: 类别名
        name: 词条名
        content: 新正文（None 表示不修改）
        description: 新描述（None 表示不修改）
        state: 新状态（None 表示不修改）
        tags: 新标签（None 表示不修改）

    Returns:
        操作结果消息
    """
    fp = _wiki_file(workspace, category, name)
    if not fp.exists():
        return f"错误：词条「{name}」不存在"

    meta, body = _parse_frontmatter(fp.read_text(encoding="utf-8"))

    from datetime import date
    meta["updated"] = str(date.today())
    if description is not None:
        meta["description"] = description
    if state is not None:
        if state == "" and "state" in meta:
            del meta["state"]
        else:
            meta["state"] = state
    if tags is not None:
        meta["tags"] = tags
    if content is not None:
        body = content

    fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
    return f"已更新词条：{category}/{name}"


def delete_wiki(workspace: Path, category: str, name: str) -> str:
    """删除 wiki 文档

    Args:
        category: 类别名
        name: 词条名

    Returns:
        操作结果消息
    """
    fp = _wiki_file(workspace, category, name)
    if not fp.exists():
        return f"错误：词条「{name}」不存在"
    fp.unlink()
    return f"已删除词条：{category}/{name}"


def read_wiki(workspace: Path, category: str, name: str, yaml_only: bool = True) -> str:
    """读取 wiki 文档

    Args:
        category: 类别名
        name: 词条名
        yaml_only: True 只返回 frontmatter，False 返回全文

    Returns:
        文档全文（含 frontmatter）或错误消息
    """
    fp = _wiki_file(workspace, category, name)
    if not fp.exists():
        return f"错误：词条「{name}」不存在"

    content = fp.read_text(encoding="utf-8")
    if not yaml_only:
        return content

    meta, _ = _parse_frontmatter(content)
    if meta:
        return _build_frontmatter(meta) + "> （内容已省略，将 yaml_only 设为 false 可查看全文）\n"
    return content


def wiki_list(workspace: Path, category: str, page: int = 1, page_size: int = 20) -> str:
    """查看类别下的 wiki 列表（分页）

    Args:
        category: 类别名
        page: 页码（从 1 开始）
        page_size: 每页数量

    Returns:
        格式化的列表字符串
    """
    cat_dir = _wiki_root(workspace) / category
    if not cat_dir.exists():
        return f"错误：类别「{category}」不存在"

    files = sorted(cat_dir.glob("*.md"))
    # 过滤掉 index.md
    files = [f for f in files if f.name != "index.md"]
    if not files:
        return f"类别「{category}」下暂无词条"

    total = len(files)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    page_files = files[start:end]

    lines = [f"类别「{category}」词条列表（第 {page}/{total_pages} 页，共 {total} 个）："]
    for fp in page_files:
        meta, _ = _parse_frontmatter(fp.read_text(encoding="utf-8"))
        title = meta.get("title", fp.stem)
        desc = meta.get("description", "")
        if desc and len(desc) > 50:
            desc = desc[:50] + "..."
        line = f"  - {title}"
        if desc:
            line += f"：{desc}"
        lines.append(line)

    return "\n".join(lines)


def check_wiki(workspace: Path, name: str, chapters: str) -> str:
    """检查 wiki 词条在指定章节中是否出现

    Args:
        name: 词条名
        chapters: 章节范围表达式

    Returns:
        检查结果
    """
    from tools.chapter import read_chapters, parse_chapter_spec
    text = read_chapters(workspace, chapters)
    if not text.startswith("##"):
        return text  # 错误消息

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


def _category_to_type(category: str) -> str:
    """将类别名映射为 type 字段值"""
    mapping = {
        "人物": "character",
        "势力": "faction",
        "地图": "location",
        "设定图鉴": "artifact",
    }
    return mapping.get(category, "artifact")


def get_wiki_meta(workspace: Path, category: str, name: str) -> dict:
    """获取 wiki 文档的 frontmatter 元数据"""
    fp = _wiki_file(workspace, category, name)
    if not fp.exists():
        return {}
    meta, _ = _parse_frontmatter(fp.read_text(encoding="utf-8"))
    return meta


def get_wiki_body(workspace: Path, category: str, name: str) -> str:
    """获取 wiki 文档的正文（不含 frontmatter）"""
    fp = _wiki_file(workspace, category, name)
    if not fp.exists():
        return ""
    _, body = _parse_frontmatter(fp.read_text(encoding="utf-8"))
    return body
