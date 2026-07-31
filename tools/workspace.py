"""工作区 CRUD + 小说导入"""

import re
import shutil
import sys
from pathlib import Path

# 工作区名称允许的字符：字母、数字、中文、下划线、横线、点
_VALID_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff.-]+$")


def _validate_name(name: str) -> str | None:
    """校验工作区名称，非法时返回错误信息"""
    if not name or not _VALID_NAME_RE.match(name):
        return "错误：工作区名称包含非法字符"
    return None


def list_workspaces(workspaces_dir: Path) -> str:
    """列出所有工作区"""
    if not workspaces_dir.exists():
        return "尚无工作区。"
    entries = sorted([d.name for d in workspaces_dir.iterdir() if d.is_dir()])
    if not entries:
        return "尚无工作区。"
    lines = ["工作区列表："]
    for i, name in enumerate(entries, 1):
        lines.append(f"  {i}. {name}")
    return "\n".join(lines)


def switch_workspace(workspaces_dir: Path, name: str) -> Path | None:
    """切换工作区，返回 Path 或 None（不存在/非法）"""
    if not _VALID_NAME_RE.match(name):
        return None
    target = workspaces_dir / name
    if not target.exists() or not target.is_dir():
        return None
    return target


def create_workspace(workspaces_dir: Path, name: str) -> Path | None:
    """创建工作区目录结构，返回 Path 或 None（名称非法/已存在）"""
    if not _VALID_NAME_RE.match(name):
        return None
    target = workspaces_dir / name
    if target.exists():
        return None
    target.mkdir(parents=True)
    (target / "document").mkdir(exist_ok=True)
    (target / "session").mkdir(exist_ok=True)
    return target


def _close_proxy(workspace_path: Path):
    """注销并关闭工作区的代理实例与 DB 连接（P1-26）

    删除/重命名前必须执行，否则 Windows 下 rmtree/rename 常因
    SQLite 句柄占用失败，且旧 key 在代理注册表悬空。
    关闭失败不静默：打印到 stderr，让 CLI 用户感知后续 rmtree 失败的真实原因。
    """
    from tools.editor import unregister_proxy, _proxy_instances
    key = str(workspace_path.resolve())
    proxy = _proxy_instances.get(key)
    if proxy is not None:
        try:
            proxy._db.close()
        except Exception as e:
            # 不静默：close 失败通常意味着后续 rmtree/rename 会被句柄占用
            # 阻断，必须让用户看到具体原因（消费端：CLI 用户 / 日志）
            print(f"[workspace] 关闭 DB 连接失败（{workspace_path}）：{e}",
                  file=sys.stderr)
    unregister_proxy(workspace_path)


def update_workspace(old_path: Path, new_name: str) -> Path | str:
    """重命名工作区"""
    err = _validate_name(new_name)
    if err:
        return err
    parent = old_path.parent
    new_path = parent / new_name
    if new_path.exists():
        return f"错误：工作区「{new_name}」已存在"
    # P1-26：重命名前注销并关闭旧路径的代理，避免 SQLite 连接句柄占用
    _close_proxy(old_path)
    old_path.rename(new_path)
    return new_path


def delete_workspace(workspace_path: Path) -> bool:
    """删除工作区

    防御：拒绝删除路径本身为磁盘根目录的情况；
    P1-26：删除前关闭 SQLite 连接，避免 Windows 下 rmtree 句柄占用失败。

    返回 False 时具体原因打印到 stderr（消费端：CLI 用户可见）。
    """
    try:
        resolved = workspace_path.resolve()
    except OSError as e:
        print(f"[workspace] 无法解析路径（{workspace_path}）：{e}", file=sys.stderr)
        return False
    if resolved == Path(resolved.anchor):
        print(f"[workspace] 拒绝删除磁盘根目录：{resolved}", file=sys.stderr)
        return False
    _close_proxy(resolved)
    try:
        shutil.rmtree(resolved)
        return True
    except OSError as e:
        # 不静默：删除失败（通常是句柄占用/权限）必须给出具体原因
        print(f"[workspace] 删除失败（{resolved}）：{e}", file=sys.stderr)
        return False


# ---- 小说导入 ----

CHAPTER_PATTERNS = [
    re.compile(r"第[零一二三四五六七八九十百千万\d]+[章回节部卷]"),    # 第X章/回/节/部/卷
    re.compile(r"^\d+[章回节部卷]"),                                   # 数字+章/回/节/部/卷，如 "345章"
    re.compile(r"Chapter\s+\d+", re.IGNORECASE),                      # Chapter X
    re.compile(r"^(?:序章|序言|尾声|楔子|番外|后记|前言|引子)$"),     # 特殊章节标记
]


def _find_chapter_title(line: str) -> str | None:
    """检测一行是否为章节标题，是则返回标题文本"""
    stripped = line.strip()
    if not stripped:
        return None
    for pattern in CHAPTER_PATTERNS:
        if pattern.match(stripped):
            return stripped
    return None


# 用于剥离章节号前缀，提取纯标题
_CHAPTER_PREFIX_RE = re.compile(
    r"^(?:第[零一二三四五六七八九十百千万\d]+[章回节部卷]|\d+[章回节部卷]|Chapter\s+\d+|序章|序言|尾声|楔子|番外|后记|前言|引子)\s*[:：]?\s*",
    re.IGNORECASE
)


def strip_chapter_prefix(title: str) -> str:
    """从章节标题行中剥离章节号前缀，返回纯标题

    例："第35章 方四娘" → "方四娘"，"序章" → ""
    """
    return _CHAPTER_PREFIX_RE.sub("", title).strip()


def import_novel(workspace_path: Path, file_path: str) -> str:
    """导入小说，按章节拆分，写入 DB

    返回格式: "成功导入 12 章" 或 "错误：..."
    """
    src = Path(file_path)
    if not src.exists():
        return f"错误：文件不存在 - {file_path}"

    # 自动检测编码：优先 utf-8，fallback 到 gb18030
    encodings = ["utf-8", "gb18030", "gbk"]
    text = None
    for enc in encodings:
        try:
            text = src.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        return "错误：无法识别文件编码（尝试 utf-8 / gb18030 均失败）"
    lines = text.splitlines()

    # 按章节标题拆分
    chapters = []  # [(title, [content_lines])]
    current_title = None
    current_lines = []

    for line in lines:
        title = _find_chapter_title(line)
        if title:
            if current_title is not None:
                chapters.append((current_title, current_lines))
            current_title = title
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        chapters.append((current_title, current_lines))

    if not chapters:
        return "错误：未找到任何章节标题。支持的格式：第X章、345章、Chapter X、序章/尾声等"

    # 写入 DB（v5.1：章节入库，title 与 content 分离）
    from tools.editor import _get_proxy
    db = _get_proxy(workspace_path)._db

    for i, (title, content_lines) in enumerate(chapters, 1):
        body = "\n".join(content_lines).strip()
        db.chapter_upsert(i, strip_chapter_prefix(title), body)

    return f"成功导入 {len(chapters)} 章"


def check_novel_file(file_path: str) -> str | None:
    """检查小说文件是否有章节（用于 import 前的校验）"""
    src = Path(file_path)
    if not src.exists():
        return "文件不存在"
    encodings = ["utf-8", "gb18030", "gbk"]
    for enc in encodings:
        try:
            text = src.read_text(encoding=enc)
            for line in text.splitlines():
                if _find_chapter_title(line):
                    return None
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "未找到章节标题"


def get_workspace_name(workspace_path: Path) -> str:
    return workspace_path.name


def list_latest_chapters(workspace_path: Path, n: int = 50) -> str:
    """列出最新N章的章节号和标题（v5.2：从 DB 读取）

    Args:
        workspace_path: 工作区路径
        n: 显示章节数，默认50

    Returns:
        格式化的章节列表
    """
    from tools.editor import _get_proxy
    db = _get_proxy(workspace_path)._db
    all_chapters = db.chapter_list_all()

    if not all_chapters:
        return "（尚无章节）"

    # 取最新N章
    latest = all_chapters[-n:] if n < len(all_chapters) else all_chapters

    lines = []
    for ch in latest:
        num = ch['chapter_num']
        title = ch['title']
        label = f"第{num}章 {title}" if title else f"第{num}章"
        lines.append(f"  {label}")

    total = len(all_chapters)
    shown = len(lines)
    header = f"最新 {shown} 章（共 {total} 章）："
    return header + "\n" + "\n".join(lines)


def export_novel(workspace_path: Path) -> str:
    """将工作区所有章节合并导出为 txt 文件（v5.2：从 DB 读取）

    Returns:
        成功/失败信息
    """
    from tools.editor import _get_proxy
    db = _get_proxy(workspace_path)._db
    all_chapters = db.chapter_list_all()

    if not all_chapters:
        return "错误：工作区尚无章节"

    # 合并内容
    parts = []
    for ch in all_chapters:
        full = db.chapter_get(ch["chapter_num"])
        if full and full.get("content"):
            num = full['chapter_num']
            title = full['title']
            heading = f"第{num}章 {title}" if title else f"第{num}章"
            parts.append(f"{heading}\n{full['content']}")

    if not parts:
        return "错误：所有章节均为空"

    # 写入文件
    output_path = workspace_path / f"{workspace_path.name}.txt"
    content = "\n\n".join(parts) + "\n"
    output_path.write_text(content, encoding="utf-8")

    return f"已导出 {len(parts)} 章到 {output_path.name}"
