"""剧情卡片工具：增删查改、列表、收尾"""

import re
from pathlib import Path
from datetime import date

from tools.wiki import _parse_frontmatter, _build_frontmatter
from tools.diff import record_extraction


PLOT_DIR = "plot"


def _plot_root(workspace: Path) -> Path:
    return workspace / PLOT_DIR


def _ensure_plot_dir(workspace: Path) -> Path:
    root = _plot_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def _plot_file(workspace: Path, name: str) -> Path:
    return _plot_root(workspace) / f"{name}.md"


def _plot_index_file(workspace: Path) -> Path:
    return _plot_root(workspace) / "index.md"


def _ensure_index(workspace: Path) -> str:
    """确保 plot/index.md 存在，若不存在则创建"""
    idx = _plot_index_file(workspace)
    if not idx.exists():
        _ensure_plot_dir(workspace)
        content = (
            "# 剧情卡片索引\n"
            "\n"
            "## 未结束\n"
            "\n"
            "## 已结束\n"
        )
        idx.write_text(content, encoding="utf-8")
    return idx.read_text(encoding="utf-8")


def _parse_index_line(line: str) -> dict | None:
    """解析 index.md 中的一行，返回 {name, chapters, ended, end_notes} 或 None"""
    line = line.strip()
    if not line.startswith("- [["):
        return None
    # - [[name]] | chapters: X | ended: true/false | end_notes: ...
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


def _build_index_line(name: str, chapters: str, ended: bool, end_notes: str = "") -> str:
    parts = [f"- [[{name}]] | chapters: {chapters} | ended: {'true' if ended else 'false'}"]
    if end_notes:
        parts.append(f" | end_notes: {end_notes}")
    return "".join(parts)


def _update_index_add(workspace: Path, name: str, chapters: str):
    """new_plot: 在「未结束」区追加一行"""
    idx_content = _ensure_index(workspace)
    line = _build_index_line(name, chapters, ended=False)
    # 追加到 ## 未结束 之后
    idx_content = idx_content.replace(
        "## 未结束\n",
        f"## 未结束\n{line}\n",
        1,
    )
    _plot_index_file(workspace).write_text(idx_content, encoding="utf-8")


def _update_index_edit(workspace: Path, name: str, chapters: str):
    """edit_plot: 更新 index 中的 chapters 信息"""
    idx_content = _ensure_index(workspace)
    lines = idx_content.split("\n")
    new_lines = []
    for line in lines:
        parsed = _parse_index_line(line)
        if parsed and parsed["name"] == name:
            new_lines.append(_build_index_line(name, chapters, parsed["ended"], parsed.get("end_notes", "")))
        else:
            new_lines.append(line)
    _plot_index_file(workspace).write_text("\n".join(new_lines), encoding="utf-8")


def _update_index_end(workspace: Path, name: str, end_notes: str):
    """end_plot: 将行从「未结束」移到「已结束」，写入 end_notes"""
    idx_content = _ensure_index(workspace)
    lines = idx_content.split("\n")
    found = None
    new_lines = []
    for line in lines:
        parsed = _parse_index_line(line)
        if parsed and parsed["name"] == name:
            found = (parsed["name"], parsed["chapters"])
        else:
            new_lines.append(line)

    if found:
        line = _build_index_line(found[0], found[1], ended=True, end_notes=end_notes)
        # 插入到 ## 已结束 区第一行
        idx_text = "\n".join(new_lines)
        idx_text = idx_text.replace(
            "## 已结束\n",
            f"## 已结束\n{line}\n",
            1,
        )
        _plot_index_file(workspace).write_text(idx_text, encoding="utf-8")


def read_plot(workspace: Path, name: str, yaml_only: bool = True) -> str:
    """读取指定剧情卡片
    
    Args:
        name: 剧情卡片名称
        yaml_only: True 只返回 frontmatter，False 返回全文
    
    Returns:
        文档内容或错误消息
    """
    fp = _plot_file(workspace, name)
    if not fp.exists():
        return f"错误：剧情卡片「{name}」不存在"
    
    content = fp.read_text(encoding="utf-8")
    if not yaml_only:
        return content
    
    # 只返回 frontmatter
    meta, _ = _parse_frontmatter(content)
    if not meta:
        return content  # 没有 frontmatter 就返回全文
    return _build_frontmatter(meta) + "> （内容已省略，将 yaml_only 设为 false 可查看全文）\n"


def plot_list(workspace: Path, page: int = 1, page_size: int = 20, ended: str = "false") -> str:
    """列出剧情卡片（分页），支持 ended 过滤
    
    Args:
        page: 页码（从 1 开始）
        page_size: 每页数量
        ended: "true"=仅已结束, "false"=仅未结束, "all"=全部
    
    Returns:
        格式化的列表字符串
    """
    idx_content = _ensure_index(workspace)
    lines = idx_content.split("\n")
    
    plots = []
    current_section = None
    for line in lines:
        if line.strip() == "## 未结束":
            current_section = "unended"
        elif line.strip() == "## 已结束":
            current_section = "ended"
        else:
            parsed = _parse_index_line(line)
            if parsed:
                plots.append(parsed)
    
    # 过滤
    if ended == "true":
        plots = [p for p in plots if p["ended"]]
    elif ended == "false":
        plots = [p for p in plots if not p["ended"]]
    # "all" 不过滤
    
    if not plots:
        return "暂无剧情卡片"
    
    total = len(plots)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    page_plots = plots[start:end]
    
    status_label = "未结束" if ended == "false" else ("已结束" if ended == "true" else "全部")
    lines_out = [f"剧情卡片列表（{status_label}，第 {page}/{total_pages} 页，共 {total} 张）："]
    for p in page_plots:
        status = "✅ 已结束" if p["ended"] else "⏳ 未结束"
        notes = f" | {p['end_notes']}" if p.get("end_notes") else ""
        lines_out.append(f"  - [[{p['name']}]] | chapters: {p['chapters']} | {status}{notes}")
    
    return "\n".join(lines_out)


def new_plot(workspace: Path, name: str, chapters: str,
             content: str = "", description: str = "",
             state: str = "", tags: list = None) -> str:
    """新建剧情卡片
    
    Args:
        name: 剧情卡片标题
        chapters: 覆盖章节，如 "1-5,7-10"
        content: 正文内容
        description: 剧情概要（静态）
        state: 当前进展（动态，可选）
        tags: 标签列表
    
    Returns:
        操作结果消息
    """
    _ensure_plot_dir(workspace)
    fp = _plot_file(workspace, name)
    if fp.exists():
        return f"错误：剧情卡片「{name}」已存在"
    
    meta = {
        "type": "plot",
        "title": name,
        "description": description,
        "chapters": chapters,
        "ended": False,
        "updated": str(date.today()),
    }
    if state:
        meta["state"] = state
    if tags:
        meta["tags"] = tags
    
    fp.write_text(_build_frontmatter(meta) + content, encoding="utf-8")
    _update_index_add(workspace, name, chapters)
    # 记录到 log.json
    try:
        record_extraction(workspace, [], [name], [])
    except Exception:
        pass
    return f"已创建剧情卡片：{name}（章节 {chapters}）"


def edit_plot(workspace: Path, name: str, chapters: str = None,
              content: str = None, description: str = None,
              state: str = None, tags: list = None) -> str:
    """编辑剧情卡片
    
    Args:
        name: 剧情卡片标题
        chapters: 新章节范围（None 表示不修改）
        content: 新正文（None 表示不修改）
        description: 新描述（None 表示不修改）
        state: 新状态（None 表示不修改）
        tags: 新标签（None 表示不修改）
    
    Returns:
        操作结果消息
    """
    fp = _plot_file(workspace, name)
    if not fp.exists():
        return f"错误：剧情卡片「{name}」不存在"
    
    meta, body = _parse_frontmatter(fp.read_text(encoding="utf-8"))
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
    if chapters is not None:
        meta["chapters"] = chapters
    if content is not None:
        body = content
    
    fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
    
    # 更新 index
    current_chapters = meta.get("chapters", chapters or "")
    _update_index_edit(workspace, name, current_chapters)
    # 记录到 log.json
    try:
        record_extraction(workspace, [], [], [name])
    except Exception:
        pass
    return f"已更新剧情卡片：{name}"


def end_plot(workspace: Path, name: str, end_notes: str = "") -> str:
    """将指定剧情卡片标注为已结束
    
    Args:
        name: 剧情卡片标题
        end_notes: 收尾语
    
    Returns:
        操作结果消息
    """
    fp = _plot_file(workspace, name)
    if not fp.exists():
        return f"错误：剧情卡片「{name}」不存在"
    
    meta, body = _parse_frontmatter(fp.read_text(encoding="utf-8"))
    
    # _parse_frontmatter 返回字符串，需兼容 "True"/"true"/True
    ended_val = meta.get("ended")
    if ended_val is True or str(ended_val).lower() == "true":
        return f"剧情卡片「{name}」已是结束状态"
    
    meta["ended"] = True
    meta["updated"] = str(date.today())
    if end_notes:
        meta["end_notes"] = end_notes
    
    fp.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
    _update_index_end(workspace, name, end_notes)
    # 记录到 log.json
    try:
        record_extraction(workspace, [], [], [name])
    except Exception:
        pass
    msg = f"✅ 剧情卡片「{name}」已标记为结束"
    if end_notes:
        msg += f"\n收尾语：{end_notes}"
    return msg


def delete_plot(workspace: Path, name: str) -> str:
    """删除剧情卡片"""
    fp = _plot_file(workspace, name)
    if not fp.exists():
        return f"错误：剧情卡片「{name}」不存在"
    fp.unlink()
    
    # 从 index 中移除
    idx_content = _ensure_index(workspace)
    lines = idx_content.split("\n")
    new_lines = [line for line in lines if not (_parse_index_line(line) and _parse_index_line(line)["name"] == name)]
    _plot_index_file(workspace).write_text("\n".join(new_lines), encoding="utf-8")
    
    return f"已删除剧情卡片：{name}"


def query_plot_by_chapters(workspace: Path, chapters: str) -> str:
    """查询指定章节区间覆盖的剧情卡片。

    返回格式：
    | 标题 | 覆盖区间 | 状态 |
    | 萧炎历练魔兽山脉 | 1-5,7-10 | 未结束 |

    Args:
        chapters: 章节范围，如 "1-5,7-10"

    Returns:
        格式化的剧情卡片列表
    """
    from tools.chapter import parse_chapter_spec
    target = set(parse_chapter_spec(chapters))

    plot_dir = _plot_root(workspace)
    if not plot_dir.exists():
        return "暂无剧情卡片。"

    results = []
    for f in sorted(plot_dir.glob("*.md")):
        if f.name == "index.md":
            continue
        meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        if not meta:
            continue
        card_chapters = meta.get("chapters", "")
        card_set = set(parse_chapter_spec(card_chapters))
        if target & card_set:  # 有交集
            ended = "已结束" if meta.get("ended") in (True, "true", "True") else "未结束"
            results.append(f"| {meta.get('title', f.stem)} | {card_chapters} | {ended} |")

    if not results:
        return f"章节 {chapters} 范围内无关联的剧情卡片。"
    return "| 标题 | 覆盖区间 | 状态 |\n|------|----------|------|\n" + "\n".join(results)
