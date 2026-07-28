"""版本决策层：时间线管理（v5.1）

职责：
- 查询词条时间线
- 选取重新提取的基础版本
- 决定写入策略（overwrite / insert）
- 执行版本写入 + 更新 current_version 指针

不直接操作 DB，通过 SQLiteService 执行。
"""

from __future__ import annotations

from tools.db.service import SQLiteService


class VersionManager:
    """时间线版本决策层"""

    def __init__(self, db: SQLiteService):
        self._db = db

    def get_timeline(self, doc_type: str, main_id: int) -> list[int]:
        """返回时间线 [2, 8, 14, 18]（所有版本的 updated_chapter 升序）"""
        versions = self._db.list_versions(doc_type, main_id)
        return [v["chapter"] for v in versions]

    def resolve_base_version(self, doc_type: str, main_id: int,
                             scope_max: int) -> int | None:
        """选取基础版本：时间线中 ≤ scope_max 且最大的版本 ID

        Returns:
            version_id（索引表主键），无匹配返回 None
        """
        versions = self._db.list_versions(doc_type, main_id)
        # versions 已按 chapter 升序
        base = None
        for v in versions:
            if v["chapter"] <= scope_max:
                base = v
            else:
                break
        return base["id"] if base else None

    def decide_write_strategy(self, doc_type: str, main_id: int,
                              new_chapter: int) -> str:
        """判定写入策略

        Returns:
            "overwrite" — 时间线中已存在 new_chapter，原地覆盖
            "insert"    — 不存在，插入新版本
        """
        timeline = self.get_timeline(doc_type, main_id)
        if new_chapter in timeline:
            return "overwrite"
        return "insert"

    def commit(self, doc_type: str, main_id: int,
               new_chapter: int, data: dict,
               strategy: str | None = None) -> int:
        """执行版本写入 + 更新 current_version 指针

        Args:
            doc_type: "wiki" | "plot" | "rule"
            main_id: 主表 ID
            new_chapter: 新版本的 updated_chapter
            data: 版本内容 dict（keywords, description, state, tags, content, relations）
            strategy: 可选，预判结果。为 None 时自动判定。

        Returns:
            version_id（索引表主键）
        """
        if strategy is None:
            strategy = self.decide_write_strategy(doc_type, main_id, new_chapter)

        if strategy == "overwrite":
            # 找到该 chapter 对应的 version_id，原地更新
            existing = self._db.get_version_by_chapter(doc_type, main_id, new_chapter)
            if existing is None:
                # 防御：理论上不应发生，退化为 insert
                strategy = "insert"
            else:
                version_id = existing["id"]
                self._db.update_version(doc_type, version_id, data)
                self._update_pointer(doc_type, main_id)
                return version_id

        # insert 路径
        creator = {
            "wiki": self._db.wiki_create_version,
            "plot": self._db.plot_create_version,
            "rule": self._db.rule_create_version,
        }[doc_type]
        version_id = creator(main_id, new_chapter, data)
        self._update_pointer(doc_type, main_id)
        return version_id

    def _update_pointer(self, doc_type: str, main_id: int):
        """更新 current_version → MAX(chapter)，created_chapter → MIN(chapter)"""
        versions = self._db.list_versions(doc_type, main_id)
        if not versions:
            return
        # versions 按 chapter 升序
        latest = versions[-1]
        earliest = versions[0]
        self._db.set_current_version(
            doc_type, main_id, latest["id"], latest["chapter"])
        # created_chapter 始终指向时间线最小章节（版本区间语义）
        updater = {
            "wiki": self._db.wiki_update_main,
            "plot": self._db.plot_update_main,
            "rule": self._db.rule_update_main,
        }[doc_type]
        updater(main_id, created_chapter=earliest["chapter"])
