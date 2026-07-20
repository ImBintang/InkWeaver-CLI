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
    """解析 YAML frontmatter，返回 (meta, body)

    自动清理重复的 frontmatter 块：
    如果文件开头有多组 ---...--- 块，只取最后一组作为有效 frontmatter。
    正文中的 ---（如 Markdown 水平线）不会被误判为 frontmatter。
    """
    # 统一换行符
    text = text.replace("\r\n", "\n")

    # 第一层：匹配文件开头的第一组 frontmatter --- ... ---
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()

    fm_content = match.group(1).strip()
    body = match.group(2).strip()

    # 第二层：检查 body 是否以另一组 frontmatter 开头（重复 frontmatter）
    # 如果是，丢弃第一组，用第二组
    body_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", body, re.DOTALL)
    if body_match:
        fm_content = body_match.group(1).strip()
        body = body_match.group(2).strip()

    meta = {}
    for line in fm_content.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        # 尝试解析列表
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        meta[key] = value
    return meta, body


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


def check_wiki_yaml(workspace: Path, category: str = "",
                    name: str = "") -> str:
    """检查 wiki 文档的 YAML 结构完整性

    检查项目：
    - frontmatter 是否存在（以 --- 开头）
    - frontmatter 是否恰好一组（不允许多组重复）
    - 正文内容是否为空/None
    - 正文是否包含符合类别的基本章节结构

    Args:
        workspace: 工作区路径
        category: 类别名（为空则检查所有类别）
        name: 词条名（为空则检查该类别的所有词条）

    Returns:
        格式化的检查报告
    """
    root = _wiki_root(workspace)
    if not root.exists():
        return "错误：wiki 目录不存在"

    # 收集要检查的文件列表
    files_to_check: list[Path] = []
    if category:
        cat_dir = root / category
        if not cat_dir.exists():
            return f"错误：类别「{category}」不存在"
        if name:
            fp = cat_dir / f"{name}.md"
            if not fp.exists():
                return f"错误：词条「{name}」不存在"
            files_to_check.append(fp)
        else:
            files_to_check = sorted(cat_dir.glob("*.md"))
            files_to_check = [f for f in files_to_check if f.name != "index.md"]
            if not files_to_check:
                return f"类别「{category}」下暂无词条"
    else:
        # 所有类别
        for cat_dir in sorted(root.iterdir()):
            if cat_dir.is_dir():
                for fp in sorted(cat_dir.glob("*.md")):
                    if fp.name != "index.md":
                        files_to_check.append(fp)

    if not files_to_check:
        return "未找到任何 wiki 词条"

    # 逐文件检查
    report_lines = []
    issue_count = 0
    ok_count = 0

    for fp in files_to_check:
        relative = fp.relative_to(root)
        text = fp.read_text(encoding="utf-8")
        issues = []

        # 1. 检查 frontmatter 结构（只检测文件开头的 ---，不把正文水平线误判）
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not frontmatter_match:
            issues.append("❌ 缺少标准 frontmatter（不以 --- 开头和结尾）")
        else:
            # 检查 body 开头是否紧跟着另一组 frontmatter
            body_text = frontmatter_match.group(2)
            if re.match(r"^\s*---\s*\n", body_text):
                issues.append("❌ frontmatter 重复（文件开头有多组 --- 块）")

        # 2. 检查正文
        meta, body = _parse_frontmatter(text)
        if not body or body.strip() in ("", "None"):
            issues.append("❌ 正文为空")
        else:
            # 3. 检查正文长度（至少有一些实质性内容）
            if len(body.strip()) < 20:
                issues.append(f"⚠️ 正文过短（仅 {len(body.strip())} 字符）")

            # 4. 检查正文是否包含章节标题（有 ## 才算是结构化内容）
            if not re.search(r"^##\s+\S", body, re.MULTILINE):
                issues.append("⚠️ 正文缺少 Markdown 章节标题（##）")

        # 5. 检查必要字段
        cat_name = relative.parts[0]
        if meta:
            if "type" not in meta:
                issues.append("⚠️ frontmatter 缺少 type 字段")
            if "title" not in meta:
                issues.append("⚠️ frontmatter 缺少 title 字段")
            if "updated" not in meta:
                issues.append("⚠️ frontmatter 缺少 updated 字段")

        # 输出
        if issues:
            issue_count += 1
            report_lines.append(f"\n[{relative}]")
            for issue in issues:
                report_lines.append(f"  {issue}")
        else:
            ok_count += 1

    # 汇总
    summary = f"\n---\n检查完毕：共 {len(files_to_check)} 个词条，通过 {ok_count} 个，异常 {issue_count} 个。"
    report_lines.append(summary)

    return "\n".join(report_lines)


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
