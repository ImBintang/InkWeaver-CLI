"""记忆工具：DB 读写（v5.3 重做）

v5.3 之前：文件系统 memory/*.md（只读空壳）
v5.3 之后：SQLite memories 表，Agent 可读写，四类分类
"""

from pathlib import Path


def _get_db(workspace: Path):
    """获取工作区 DB 服务

    P1-42：复用 editor 的共享代理连接（单例），避免每个工具调用新建独立
    SQLite 连接与 agent flush 长事务并发触发 "database is locked" 丢记忆。
    连接生命周期由代理管理，本模块不负责关闭。
    """
    from tools.editor import _get_proxy
    return _get_proxy(workspace)._db


# ── Agent 工具接口 ──

def memory_query(workspace: Path, category: str = None,
                 keyword: str = None, limit: int = 20) -> str:
    """检索记忆（支持类别过滤 + 关键词模糊匹配）

    Args:
        category: 按类别过滤（preference/observation/correction/style）
        keyword: 关键词模糊匹配
        limit: 返回上限

    Returns:
        格式化的记忆列表
    """
    db = _get_db(workspace)
    results = db.memory_query(category=category, keyword=keyword, limit=limit)

    if not results:
        return "（无匹配记忆）"

    lines = []
    for m in results:
        src = f" [{m['source']}]" if m.get("source") else ""
        lines.append(f"[{m['id']}][{m['category']}]{src} {m['content']}")
    return "\n".join(lines)


def memory_write(workspace: Path, category: str, content: str,
                 source: str = None, chapter: int = None) -> str:
    """写入新记忆（静默，不需用户确认）

    Args:
        category: 类别（preference/observation/correction/style）
        content: 记忆正文
        source: 来源（chat/extract/muse/user）
        chapter: 产生时的章节上下文

    Returns:
        操作结果
    """
    valid_categories = ("preference", "observation", "correction", "style")
    if category not in valid_categories:
        return f"错误：category 必须为 {valid_categories} 之一，收到「{category}」"

    if not content or not content.strip():
        return "错误：content 不能为空"

    db = _get_db(workspace)
    memory_id = db.memory_create(category=category, content=content.strip(),
                                 source=source, chapter=chapter)
    return f"已写入记忆 #{memory_id}（{category}）"


def memory_update(workspace: Path, id: int, content: str = None) -> str:
    """更新记忆内容

    Args:
        id: 记忆 ID
        content: 新内容

    Returns:
        操作结果
    """
    db = _get_db(workspace)
    existing = db.memory_get(id)
    if existing is None:
        return f"错误：记忆 #{id} 不存在"
    if not existing.get("is_active"):
        return f"错误：记忆 #{id} 已被删除"

    # content 为 None 表示不修改；空串可显式覆盖清空（v7.0.1 修复）
    if content is not None:
        db.memory_update(id, content=content.strip())
    return f"已更新记忆 #{id}"


def memory_forget(workspace: Path, id: int) -> str:
    """软删除记忆（is_active=0）

    Args:
        id: 记忆 ID

    Returns:
        操作结果
    """
    db = _get_db(workspace)
    existing = db.memory_get(id)
    if existing is None:
        return f"错误：记忆 #{id} 不存在"

    db.memory_forget(id)
    return f"已删除记忆 #{id}"


# ── 消费接口（供 prompt 注入使用）──

def get_memories_for_prompt(workspace: Path, categories: list[str],
                            limit: int = 10) -> str:
    """获取指定类别的记忆，格式化为 prompt 注入文本

    Args:
        categories: 要注入的类别列表
        limit: 每个类别的上限

    Returns:
        格式化的记忆文本（为空时返回空字符串）
    """
    db = _get_db(workspace)
    all_memories = []
    for cat in categories:
        results = db.memory_query(category=cat, limit=limit)
        all_memories.extend(results)

    if not all_memories:
        return ""

    lines = ["## Agent 记忆（自动学习）"]
    for m in all_memories:
        lines.append(f"- [{m['category']}] {m['content']}")
    return "\n".join(lines)


# ── 向后兼容（旧 read_memory 接口）──

def read_memory(workspace: Path, name: str = None) -> str:
    """读取记忆（向后兼容接口）

    v5.3: 改为从 DB 读取。name=None 时列出所有活跃记忆。
    """
    db = _get_db(workspace)
    if name is None or name.upper() == "MEMORY":
        # 列出所有活跃记忆
        results = db.memory_list_active()
        if not results:
            return "（暂无记忆）"
        lines = ["# 记忆索引\n"]
        for m in results:
            lines.append(f"- [{m['category']}] #{m['id']}: {m['content'][:60]}")
        return "\n".join(lines)

    # 按 ID 读取
    try:
        memory_id = int(name.lstrip("#"))
        result = db.memory_get(memory_id)
        if result is None:
            return f"错误：记忆 #{memory_id} 不存在"
        return (f"[{result['category']}] #{result['id']}\n"
                f"内容：{result['content']}\n"
                f"来源：{result.get('source', '未知')}\n"
                f"活跃：{'是' if result.get('is_active') else '否'}")
    except ValueError:
        # 按关键词搜索
        results = db.memory_query(keyword=name, limit=10)
        if not results:
            return f"未找到匹配「{name}」的记忆"
        lines = [f"搜索「{name}」结果："]
        for m in results:
            lines.append(f"- [{m['category']}] #{m['id']}: {m['content']}")
        return "\n".join(lines)


def list_memories(workspace: Path) -> list:
    """列出所有活跃记忆（向后兼容）"""
    db = _get_db(workspace)
    return db.memory_list_active()
