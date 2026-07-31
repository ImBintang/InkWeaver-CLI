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


def read_plot(workspace: Path, name: str, yaml_only: bool = True,
             version: int = None) -> str:
    """读取指定剧情卡片（v5：调 proxy）
    
    Args:
        name: 剧情卡片名称
        yaml_only: True 只返回 frontmatter，False 返回全文
        version: 可选，指定版本的 updated_chapter。为空时读取 current_version。
    
    Returns:
        文档内容或错误消息
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)

    if version is not None:
        return proxy.read_doc_version("plot", name, version, yaml_only=yaml_only)
    return proxy.read_doc("plot", name, yaml_only=yaml_only)


def plot_list(workspace: Path, page: int = 1, page_size: int = 20, ended: str = "false") -> str:
    """列出剧情卡片（分页），支持 ended 过滤（v5：调 proxy）
    
    Args:
        page: 页码（从 1 开始）
        page_size: 每页数量
        ended: "true"=仅已结束, "false"=仅未结束, "all"=全部
    
    Returns:
        格式化的列表字符串
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    return proxy.list_docs("plot", page=page, page_size=page_size, ended=ended)


def new_plot(workspace: Path, name: str, chapters: str,
             content: str = "", description: str = "",
             state: str = "", keywords: str = "",
             tags: list = None,
             updated: int | None = None) -> str:
    """新建剧情卡片（薄代理层 → tools.editor.create_doc）"""
    return _create_doc(
        workspace, doc_type="plot", name=name, content=content,
        description=description, state=state, keywords=keywords,
        tags=tags, chapters=chapters, updated=updated,
    )


def edit_plot(workspace: Path, name: str, chapters: str = None,
              content: str = None, description: str = None,
              state: str = None, keywords: str = None,
              tags: list = None,
              updated: int | None = None) -> str:
    """编辑剧情卡片（薄代理层 → tools.editor.edit_doc）"""
    return _edit_doc(
        workspace, doc_type="plot", name=name, content=content,
        description=description, state=state, keywords=keywords,
        tags=tags, chapters=chapters, updated=updated,
    )


def edit_plot_text(workspace: Path, name: str,
                   old_text: str, new_text: str) -> str:
    """在剧情卡片正文中精确匹配一段文本并替换（手术刀式）"""
    return _edit_doc_text(
        workspace, doc_type="plot", name=name,
        old_text=old_text, new_text=new_text,
    )


def edit_plot_wikilink(workspace: Path, name: str,
                       old_target: str, new_target: str = "",
                       mode: str = "redirect",
                       remember: bool = False) -> str:
    """替换剧情卡片正文中所有指向 old_target 的 [[wikilink]]

    mode="redirect"（默认）：重定向目标
    mode="unlink"：取消链接，new_target 忽略
    remember=True 时将目标记入 unlink 黑名单
    """
    return _edit_doc_wikilink(
        workspace, doc_type="plot", name=name,
        old_target=old_target, new_target=new_target,
        mode=mode, remember=remember,
    )


def end_plot(workspace: Path, name: str, end_notes: str = "") -> str:
    """将指定剧情卡片标注为已结束（v5：调 proxy）

    Args:
        name: 剧情卡片标题
        end_notes: 收尾语

    Returns:
        操作结果消息
    """
    from tools.editor import _get_proxy
    proxy = _get_proxy(workspace)
    result = proxy.end_plot(name, end_notes)
    # 记录到 log.json
    if not result.startswith("错误"):
        try:
            record_extraction(workspace, [], [], [name])
        except Exception as e:
            # 不静默：end_plot 本身成功，但提取记录写入失败必须告知消费端
            # （LLM 可见的告警），避免日志与 DB 状态不一致被当作正常
            result += f"\n⚠️ 已结束卡片，但提取记录写入失败（不影响结果）：{e}"
    return result


def delete_plot(workspace: Path, name: str) -> str:
    """删除剧情卡片（薄代理层 → tools.editor.delete_doc）"""
    return _delete_doc(workspace, doc_type="plot", name=name)


def query_plot_by_chapters(workspace: Path, chapters: str) -> str:
    """查询指定章节区间覆盖的剧情卡片（v5：调 proxy）。

    返回格式：
    | 标题 | 覆盖区间 | 状态 |
    | 萧炎历练魔兽山脉 | 1-5,7-10 | 未结束 |

    Args:
        chapters: 章节范围，如 "1-5,7-10"

    Returns:
        格式化的剧情卡片列表
    """
    from tools.chapter import parse_chapter_spec
    from tools.editor import _get_proxy
    target = set(parse_chapter_spec(chapters))

    proxy = _get_proxy(workspace)
    # 从 DB 获取所有剧情卡片（含已结束）
    mains = proxy._db.plot_list_main()

    # P1-24：合并缓存中新增/修改（is_new 或 is_dirty）的卡片；
    # DB 行若在缓存中有新/脏版本则跳过，避免陈旧重复
    cache_by_name = {
        v.name: v
        for v in proxy._cache.values()
        if v.doc_type == "plot" and not v.is_deleted and (v.is_new or v.is_dirty)
    }

    results = []
    for m in mains:
        if m["name"] in cache_by_name:
            continue
        card_chapters = m.get("chapters", "")
        if not card_chapters:
            continue
        card_set = set(parse_chapter_spec(card_chapters))
        if target & card_set:
            ended = "已结束" if m.get("ended") else "未结束"
            results.append(f"| {m['name']} | {card_chapters} | {ended} |")

    for doc in cache_by_name.values():
        if not doc.chapters:
            continue
        card_set = set(parse_chapter_spec(doc.chapters))
        if target & card_set:
            ended = "已结束" if doc.ended else "未结束"
            results.append(f"| {doc.name} | {doc.chapters} | {ended} |")

    if not results:
        return f"章节 {chapters} 范围内无关联的剧情卡片。"
    return "| 标题 | 覆盖区间 | 状态 |\n|------|----------|------|\n" + "\n".join(results)
