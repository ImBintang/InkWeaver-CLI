"""剧情卡片工具：增删查改、列表、收尾

兼容性：new_plot / edit_plot / delete_plot 保留为向后兼容的薄代理层，
核心实现在 tools/editor.py（统一编辑器）。
plot 特有功能（end_plot / query_plot_by_chapters）保留在此文件。
"""

from pathlib import Path
from datetime import date

from tools.editor import (
    parse_frontmatter as _parse_frontmatter,
    build_frontmatter as _build_frontmatter,
    create_doc as _create_doc,
    edit_doc as _edit_doc,
    delete_doc as _delete_doc,
    edit_doc_text as _edit_doc_text,
    edit_doc_wikilink as _edit_doc_wikilink,
    _ensure_plot_index as _ensure_index,
    _parse_plot_index_line as _parse_index_line,
    _build_plot_index_line as _build_index_line,
    _update_plot_index_add as _update_index_add,
    _update_plot_index_edit as _update_index_edit,
    update_plot_index_end as _update_index_end,
)
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
             state: str = "", tags: list = None,
             updated: int | None = None) -> str:
    """新建剧情卡片（薄代理层 → tools.editor.create_doc）"""
    return _create_doc(
        workspace, doc_type="plot", name=name, content=content,
        description=description, state=state, tags=tags,
        chapters=chapters, updated=updated,
    )


def edit_plot(workspace: Path, name: str, chapters: str = None,
              content: str = None, description: str = None,
              state: str = None, tags: list = None,
              updated: int | None = None) -> str:
    """编辑剧情卡片（薄代理层 → tools.editor.edit_doc）"""
    return _edit_doc(
        workspace, doc_type="plot", name=name, content=content,
        description=description, state=state, tags=tags,
        chapters=chapters, updated=updated,
    )


def edit_plot_text(workspace: Path, name: str,
                   old_text: str, new_text: str) -> str:
    """在剧情卡片正文中精确匹配一段文本并替换（手术刀式）"""
    return _edit_doc_text(
        workspace, doc_type="plot", name=name,
        old_text=old_text, new_text=new_text,
    )


def edit_plot_wikilink(workspace: Path, name: str,
                       old_target: str, new_target: str) -> str:
    """替换剧情卡片正文中所有指向 old_target 的 [[wikilink]]"""
    return _edit_doc_wikilink(
        workspace, doc_type="plot", name=name,
        old_target=old_target, new_target=new_target,
    )


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
    """删除剧情卡片（薄代理层 → tools.editor.delete_doc）"""
    return _delete_doc(workspace, doc_type="plot", name=name)


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
