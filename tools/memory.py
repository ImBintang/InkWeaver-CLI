"""记忆工具：读取记忆文档"""

import re
from pathlib import Path


MEMORY_DIR = "memory"
MEMORY_INDEX = "MEMORY.md"


def _memory_root(workspace: Path) -> Path:
    return workspace / MEMORY_DIR


def _ensure_memory_dir(workspace: Path) -> Path:
    root = _memory_root(workspace)
    root.mkdir(exist_ok=True)
    return root


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text.strip()
    meta = {}
    for line in match.group(1).strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, match.group(2).strip()


def read_memory(workspace: Path, name: str = None) -> str:
    """读取记忆文档

    Args:
        name: 记忆名（None 表示读取索引 MEMORY.md）

    Returns:
        记忆文档内容或错误消息
    """
    root = _memory_root(workspace)
    if not root.exists():
        return "（memory 目录不存在，暂无记忆数据。可先用 list_memories 确认是否有记忆，或使用 read_memory(None) 查看索引）"

    if name is None or name.upper() == "MEMORY":
        fp = root / MEMORY_INDEX
        if not fp.exists():
            # 自动扫描生成索引
            memories = sorted([f.stem for f in root.glob("*.md") if f.name != MEMORY_INDEX])
            if not memories:
                return "（暂无记忆文档）"
            lines = ["# 记忆索引\n"]
            for m in memories:
                lines.append(f"- {m}")
            return "\n".join(lines)
        return fp.read_text(encoding="utf-8")

    fp = root / f"{name}.md"
    if not fp.exists():
        # 目录存在但文件不存在，列出可用记忆供参考
        available = sorted([f.stem for f in root.glob("*.md") if f.name != MEMORY_INDEX])
        if available:
            hint = "、".join(available[:10])
            return f"错误：记忆文档「{name}」不存在。可用记忆：{hint}"
        return f"错误：记忆文档「{name}」不存在（暂无任何记忆文档）"
    return fp.read_text(encoding="utf-8")


def memory_index(workspace: Path) -> str:
    """读取记忆索引

    Returns:
        记忆索引内容
    """
    root = _memory_root(workspace)
    if not root.exists():
        return "（memory 目录不存在）"

    index_fp = root / MEMORY_INDEX
    if not index_fp.exists():
        # 自动扫描生成索引
        memories = sorted([f.stem for f in root.glob("*.md") if f.name != MEMORY_INDEX])
        if not memories:
            return "（暂无记忆文档）"
        lines = ["# 记忆索引\n"]
        for m in memories:
            lines.append(f"- {m}")
        return "\n".join(lines)

    return index_fp.read_text(encoding="utf-8")


def list_memories(workspace: Path) -> list:
    """列出所有记忆文档名"""
    root = _memory_root(workspace)
    if not root.exists():
        return []
    return sorted([f.stem for f in root.glob("*.md") if f.name != MEMORY_INDEX])
