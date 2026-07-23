"""统一文档编辑器 — wiki / plot / rule 共用一套创建、修改、删除工具

设计参考：
  - learn-claude-code s02: edit_file 的精确文本替换模式（old_text → new_text）
  - llm-wiki lint-fixes.ts: wikilink 定向替换（rewriteWikilinkTarget）
  - InkWeaver v3.2.0 教训：字段级 edit 需要 LLM 传整个 body，token 浪费严重

统一工具集（LLM 通过 doc_type 指定操作类型）：
  - create_doc:  新建文档
  - edit_doc:    编辑文档（字段级）
  - edit_doc_text:     正文内精确文本替换（手术刀式，节约 token）
  - edit_doc_wikilink: 正文中替换 [[wikilink]] 目标
  - delete_doc:  删除文档

兼容性：
  - tools/wiki.py / plot.py / rules.py 保留原函数名作为薄代理层
  - 原有 dispatch 路由不变，新增工具通过 doc_type 参数路由
"""

import re
from pathlib import Path


# ── 目录映射 ──────────────────────────────────────────────────────────────────

DOC_TYPE_DIRS = {
    "wiki": "wiki",
    "plot": "plot",
    "rule": "rules",
}

# ── Frontmatter 工具 ──────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)

    自动清理重复的 frontmatter 块：
    如果文件开头有多组 ---...--- 块，只取最后一组作为有效 frontmatter。
    正文中的 ---（如 Markdown 水平线）不会被误判为 frontmatter。
    """
    text = text.replace("\r\n", "\n")

    # 第一层：匹配文件开头的第一组 frontmatter --- ... ---
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()

    fm_content = match.group(1).strip()
    body = match.group(2).strip()

    # 第二层：检查 body 是否以另一组 frontmatter 开头（重复 frontmatter）
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
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        meta[key] = value
    return meta, body


def build_frontmatter(meta: dict) -> str:
    """将 meta dict 构建为 YAML frontmatter 字符串"""
    lines = []
    for key, value in meta.items():
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n"


# ── 路径解析 ──────────────────────────────────────────────────────────────────


def resolve_path(workspace: Path, doc_type: str, name: str,
                 category: str | None = None) -> Path:
    """根据 doc_type 解析文件路径"""
    subdir = DOC_TYPE_DIRS.get(doc_type)
    if not subdir:
        raise ValueError(f"不支持的文档类型：{doc_type}（支持：wiki / plot / rule）")

    if doc_type == "wiki":
        if not category:
            raise ValueError("wiki 类型需要提供 category 参数")
        return workspace / subdir / category / f"{name}.md"
    elif doc_type == "plot":
        return workspace / subdir / f"{name}.md"
    elif doc_type == "rule":
        return workspace / subdir / f"{name}.md"
    else:
        raise ValueError(f"不支持的文档类型：{doc_type}")


def ensure_parent_dir(fp: Path) -> Path:
    """确保文件父目录存在"""
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def read_doc(workspace: Path, doc_type: str, name: str,
             category: str | None = None) -> tuple[dict, str, Path]:
    """读取文档，返回 (meta, body, file_path) 或引发错误"""
    fp = resolve_path(workspace, doc_type, name, category)
    if not fp.exists():
        raise FileNotFoundError(f"{doc_type} 文档「{name}」不存在")
    meta, body = parse_frontmatter(fp.read_text(encoding="utf-8"))
    return meta, body, fp


# ── 统一创建 ──────────────────────────────────────────────────────────────────


def create_doc(workspace: Path, doc_type: str, name: str,
               content: str = "",
               category: str | None = None,
               description: str = "",
               state: str = "",
               tags: list | None = None,
               chapters: str = "",
               updated: int | None = None) -> str:
    """统一创建 wiki / plot / rule 文档

    Args:
        doc_type: "wiki" | "plot" | "rule"
        name: 文档名
        content: 正文内容
        category: wiki 类别（仅 wiki 需要）
        description: 描述（wiki / plot）
        state: 状态（wiki / plot）
        tags: 标签（wiki / plot）
        chapters: 覆盖章节（仅 plot 需要），如 "1-5,7-10"
        updated: 更新章节号，None 默认 0

    Returns:
        操作结果消息
    """
    fp = resolve_path(workspace, doc_type, name, category)
    if fp.exists():
        type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
        return f"错误：{type_label}「{name}」已存在"

    # 构建 frontmatter
    meta = {"title": name, "updated": updated if updated is not None else 0}
    if doc_type == "wiki":
        meta["type"] = category
        if description:
            meta["description"] = description
        if state:
            meta["state"] = state
        if tags:
            meta["tags"] = tags
    elif doc_type == "plot":
        meta["type"] = "plot"
        meta["description"] = description
        meta["chapters"] = chapters
        meta["ended"] = False
        if state:
            meta["state"] = state
        if tags:
            meta["tags"] = tags
    elif doc_type == "rule":
        meta["type"] = "rule"

    ensure_parent_dir(fp)
    fp.write_text(build_frontmatter(meta) + content, encoding="utf-8")

    # 更新 plot 索引
    if doc_type == "plot":
        _update_plot_index_add(workspace, name, chapters)

    # 记录到 log.json
    _record_extraction_log(workspace, doc_type, name, operation="create")

    type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
    detail = f"（章节 {chapters}）" if doc_type == "plot" else ""
    return f"已创建{type_label}：{name}{detail}"


# ── 统一编辑（字段级） ──────────────────────────────────────────────────────


def edit_doc(workspace: Path, doc_type: str, name: str,
             content: str | None = None,
             category: str | None = None,
             description: str | None = None,
             state: str | None = None,
             tags: list | None = None,
             chapters: str | None = None,
             updated: int | None = None) -> str:
    """统一编辑 wiki / plot / rule 文档（字段级）"""
    try:
        meta, body, fp = read_doc(workspace, doc_type, name, category)
    except FileNotFoundError as e:
        return str(e)

    if updated is not None:
        meta["updated"] = updated
    if description is not None:
        meta["description"] = description
    if state is not None:
        if state == "" and "state" in meta:
            del meta["state"]
        else:
            meta["state"] = state
    if tags is not None:
        meta["tags"] = tags
    if chapters is not None:
        meta["chapters"] = chapters
    if content is not None:
        body = content

    fp.write_text(build_frontmatter(meta) + body, encoding="utf-8")

    # 更新 plot 索引
    if doc_type == "plot":
        current_chapters = meta.get("chapters", chapters or "")
        _update_plot_index_edit(workspace, name, current_chapters)

    # 记录到 log.json
    _record_extraction_log(workspace, doc_type, name, operation="edit")

    type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
    return f"已更新{type_label}：{name}"


# ── 手术刀式编辑：正文精确文本替换 ──────────────────────────────────────────


def edit_doc_text(workspace: Path, doc_type: str, name: str,
                  old_text: str, new_text: str,
                  category: str | None = None) -> str:
    """在正文中精确匹配一段文本并替换（不涉及 frontmatter）

    参考 learn-claude-code s02 的 edit_file 模式：
    只替换 body 中第一次出现的 old_text，避免影响 frontmatter。

    Args:
        doc_type: "wiki" | "plot" | "rule"
        name: 文档名
        old_text: 要替换的原文（必须精确匹配）
        new_text: 替换后的文本
        category: wiki 类别（仅 wiki 需要）

    Returns:
        操作结果消息
    """
    try:
        meta, body, fp = read_doc(workspace, doc_type, name, category)
    except FileNotFoundError as e:
        return str(e)

    if old_text not in body:
        type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
        return f"错误：在{type_label}「{name}」正文中未找到匹配的文本"

    body = body.replace(old_text, new_text, 1)
    fp.write_text(build_frontmatter(meta) + body, encoding="utf-8")

    # 更新 plot 索引（正文变更不影响索引，跳过）
    _record_extraction_log(workspace, doc_type, name, operation="edit")

    type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
    return f"已更新{type_label}「{name}」正文（文本替换）"


# ── 手术刀式编辑：wikilink 定向替换 ─────────────────────────────────────────


def edit_doc_wikilink(workspace: Path, doc_type: str, name: str,
                      old_target: str, new_target: str,
                      category: str | None = None) -> str:
    """替换正文中所有指向 old_target 的 [[wikilink]] 为 new_target

    参考 llm-wiki lint-fixes.ts 的 rewriteWikilinkTarget：
    - 支持带别名的 wikilink（[[旧目标|别名]] → [[新目标|别名]]）
    - 大小写不敏感匹配目标名
    - 只替换 body，不碰 frontmatter

    Args:
        doc_type: "wiki" | "plot" | "rule"
        name: 文档名
        old_target: 旧 wikilink 目标
        new_target: 新 wikilink 目标
        category: wiki 类别（仅 wiki 需要）

    Returns:
        操作结果消息
    """
    try:
        meta, body, fp = read_doc(workspace, doc_type, name, category)
    except FileNotFoundError as e:
        return str(e)

    old_lower = old_target.strip().lower()
    new_clean = new_target.strip()

    def _replace_wikilink(match):
        raw_target = match.group(1)
        raw_alias = match.group(2) or ""
        if raw_target.strip().lower() == old_lower:
            return f"[[{new_clean}{raw_alias}]]"
        return match.group(0)

    new_body = re.sub(r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]", _replace_wikilink, body)

    if new_body == body:
        type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
        return f"错误：在{type_label}「{name}」中未找到指向「{old_target}」的 wikilink"

    fp.write_text(build_frontmatter(meta) + new_body, encoding="utf-8")
    _record_extraction_log(workspace, doc_type, name, operation="edit")

    type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
    return f"已更新{type_label}「{name}」中的 wikilink：{old_target} → {new_target}"


# ── 统一删除 ──────────────────────────────────────────────────────────────────


def delete_doc(workspace: Path, doc_type: str, name: str,
               category: str | None = None) -> str:
    """统一删除 wiki / plot / rule 文档"""
    try:
        fp = resolve_path(workspace, doc_type, name, category)
    except ValueError as e:
        return str(e)

    if not fp.exists():
        type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
        return f"错误：{type_label}「{name}」不存在"

    fp.unlink()

    # 从 plot 索引中移除
    if doc_type == "plot":
        _remove_from_plot_index(workspace, name)

    type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}.get(doc_type, "文档")
    return f"已删除{type_label}：{name}"


# ── Plot 索引管理（内部） ────────────────────────────────────────────────────


def _plot_index_file(workspace: Path) -> Path:
    return workspace / "plot" / "index.md"


def _ensure_plot_index(workspace: Path) -> str:
    idx = _plot_index_file(workspace)
    if not idx.exists():
        idx.parent.mkdir(parents=True, exist_ok=True)
        content = "# 剧情卡片索引\n\n## 未结束\n\n## 已结束\n"
        idx.write_text(content, encoding="utf-8")
    return idx.read_text(encoding="utf-8")


def _build_plot_index_line(name: str, chapters: str, ended: bool,
                           end_notes: str = "") -> str:
    parts = [f"- [[{name}]] | chapters: {chapters} | ended: {'true' if ended else 'false'}"]
    if end_notes:
        parts.append(f" | end_notes: {end_notes}")
    return "".join(parts)


def _parse_plot_index_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("- [["):
        return None
    match = re.match(
        r"- \[\[(.+?)\]\]\s*\|\s*chapters:\s*(.+?)\s*\|\s*ended:\s*(true|false)"
        r"(?:\s*\|\s*end_notes:\s*(.*))?$",
        line,
    )
    if not match:
        return None
    return {
        "name": match.group(1).strip(),
        "chapters": match.group(2).strip(),
        "ended": match.group(3).strip() == "true",
        "end_notes": (match.group(4) or "").strip(),
    }


def _update_plot_index_add(workspace: Path, name: str, chapters: str):
    idx_content = _ensure_plot_index(workspace)
    line = _build_plot_index_line(name, chapters, ended=False)
    idx_content = idx_content.replace(
        "## 未结束\n", f"## 未结束\n{line}\n", 1,
    )
    _plot_index_file(workspace).write_text(idx_content, encoding="utf-8")


def _update_plot_index_edit(workspace: Path, name: str, chapters: str):
    idx_content = _ensure_plot_index(workspace)
    lines = idx_content.split("\n")
    new_lines = []
    for line in lines:
        parsed = _parse_plot_index_line(line)
        if parsed and parsed["name"] == name:
            new_lines.append(
                _build_plot_index_line(name, chapters, parsed["ended"],
                                       parsed.get("end_notes", ""))
            )
        else:
            new_lines.append(line)
    _plot_index_file(workspace).write_text("\n".join(new_lines), encoding="utf-8")


def _remove_from_plot_index(workspace: Path, name: str):
    idx_content = _ensure_plot_index(workspace)
    lines = idx_content.split("\n")
    new_lines = [
        line for line in lines
        if not (_parse_plot_index_line(line)
                and _parse_plot_index_line(line)["name"] == name)
    ]
    _plot_index_file(workspace).write_text("\n".join(new_lines), encoding="utf-8")


def update_plot_index_end(workspace: Path, name: str, end_notes: str = ""):
    """将 plot 从「未结束」移到「已结束」（供 end_plot 调用）"""
    idx_content = _ensure_plot_index(workspace)
    lines = idx_content.split("\n")
    found = None
    new_lines = []
    for line in lines:
        parsed = _parse_plot_index_line(line)
        if parsed and parsed["name"] == name:
            found = (parsed["name"], parsed["chapters"])
        else:
            new_lines.append(line)

    if found:
        line = _build_plot_index_line(found[0], found[1], ended=True, end_notes=end_notes)
        idx_text = "\n".join(new_lines)
        idx_text = idx_text.replace(
            "## 已结束\n", f"## 已结束\n{line}\n", 1,
        )
        _plot_index_file(workspace).write_text(idx_text, encoding="utf-8")


# ── 日志记录 ──────────────────────────────────────────────────────────────────


def _record_extraction_log(workspace: Path, doc_type: str, name: str,
                           operation: str):
    """记录操作到 log.json"""
    try:
        from tools.diff import record_extraction
        if doc_type == "wiki":
            record_extraction(workspace, [], [name], [])
        elif doc_type == "plot":
            record_extraction(workspace, [], [], [name])
        elif doc_type == "rule":
            record_extraction(workspace, [], [], [])
    except Exception:
        pass


# ── 向后兼容别名（供原有模块引用） ──────────────────────────────────────────

# tools/wiki.py 等旧模块可通过 `from tools.editor import parse_frontmatter` 引用
# 避免循环导入
