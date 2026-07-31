"""关系查询工具（v5：从 DB 索引表的 relations 字段读取）"""

from pathlib import Path

from tools.editor import _get_proxy


def _resolve_relation_names(proxy, doc_type: str, rel_ids: list[int]) -> list[str]:
    """将 relations 中的 main_id 列表解析为名称列表"""
    names = []
    for rid in rel_ids:
        # 先查缓存
        for (dt, mid), doc in proxy._cache.items():
            if doc.main_id == rid and doc.name not in names:
                names.append(doc.name)
                break
        else:
            # 缓存未命中 → 查 DB
            for t in ("wiki", "plot", "rule"):
                getter = {
                    "wiki": proxy._db.wiki_get_main,
                    "plot": proxy._db.plot_get_main,
                    "rule": proxy._db.rule_get_main,
                }[t]
                m = getter(rid)
                if m:
                    names.append(m["name"])
                    break
    return names


def query_relations(workspace: Path, name: str) -> str:
    """查询指定词条/卡片关联的所有目标（v5：从索引表的 relations 字段读取）

    Args:
        name: 词条名

    Returns:
        格式化的关系列表
    """
    proxy = _get_proxy(workspace)

    # 在 wiki 和 plot 中查找
    for doc_type in ("wiki", "plot"):
        # 查缓存
        doc = proxy._find_in_cache(doc_type, name)
        if doc and doc.relations:
            names = _resolve_relation_names(proxy, doc_type, doc.relations)
            if names:
                return f"「{name}」关联：{'、'.join(names)}"
            return f"「{name}」暂无关联"

        # 查 DB
        finder = {
            "wiki": proxy._db.wiki_find_main,
            "plot": proxy._db.plot_find_main,
        }
        getter = {
            "wiki": proxy._db.wiki_get_current,
            "plot": proxy._db.plot_get_current,
        }
        main = finder[doc_type](name)
        if main:
            current = getter[doc_type](main["id"])
            if current and current.get("relations"):
                names = _resolve_relation_names(proxy, doc_type, current["relations"])
                if names:
                    return f"「{name}」关联：{'、'.join(names)}"
            return f"「{name}」暂无关联"

    return f"错误：未找到「{name}」"


def get_all_relations(workspace: Path) -> dict:
    """获取完整关系图（v5：遍历 DB 的 relations 字段 + 合并缓存）

    返回 {name: [related_name, ...]} 格式，保持与 v4 兼容。
    P1-25：任务内 edit_doc(relations=...) 的变更（缓存 is_new/is_dirty）不再丢失。
    """
    proxy = _get_proxy(workspace)
    result = {}

    # 先收集缓存中的关系（新/脏条目优先，覆盖 DB 结果）
    cache_relations = {}
    for (dt, _), doc in proxy._cache.items():
        if doc.is_deleted or not doc.relations:
            continue
        if dt in ("wiki", "plot") and (doc.is_new or doc.is_dirty):
            names = _resolve_relation_names(proxy, dt, doc.relations)
            cache_relations[doc.name] = names

    for doc_type in ("wiki", "plot"):
        finder = {
            "wiki": proxy._db.wiki_find_main,
            "plot": proxy._db.plot_find_main,
        }
        getter = {
            "wiki": proxy._db.wiki_get_current,
            "plot": proxy._db.plot_get_current,
        }

        if doc_type == "wiki":
            cats = proxy._db.list_categories("wiki")
            for cat in cats:
                mains = proxy._db.wiki_list_main(cat["id"])
                for m in mains:
                    if m["name"] in cache_relations:
                        continue
                    current = getter[doc_type](m["id"])
                    if current and current.get("relations"):
                        names = _resolve_relation_names(proxy, doc_type, current["relations"])
                        result[m["name"]] = names
        else:
            mains = proxy._db.plot_list_main()
            for m in mains:
                if m["name"] in cache_relations:
                    continue
                current = getter[doc_type](m["id"])
                if current and current.get("relations"):
                    names = _resolve_relation_names(proxy, doc_type, current["relations"])
                    result[m["name"]] = names

    # 缓存中的新/脏关系以缓存为准覆盖（含 DB 不存在的新卡片）
    result.update(cache_relations)
    return result


def set_relations(workspace: Path, relations: dict):
    """设置关系图（v5：不直接使用，保留 API 兼容）"""
    pass
