"""缓存代理层 — 保持 'LLM 以为在写文件' 的幻觉

流程：
  白名单通过 → load_whitelist() 从 DB 拉取条目到内存缓存
  执行阶段 → 所有修改操作只改缓存，读操作优先走缓存
  进入审核 → snapshot() 序列化到磁盘
  审核完成 → restore() 从磁盘恢复
  finish_task → flush() 批量写入 DB → 清空缓存
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.db.service import SQLiteService


@dataclass
class CachedDoc:
    doc_type: str          # "wiki" | "plot" | "rule"
    main_id: int | None    # 主表 ID（新条目为 None）
    name: str
    category: str | None = None  # 仅 wiki 有
    content: str = ""
    description: str = ""
    state: str = ""
    keywords: str = ""
    tags: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    chapter: int = 0          # 当前版本对应章节号
    first_chapter: int = 0    # 首次创建时记录的章节号
    chapters: str = ""        # 仅 plot 有
    ended: bool = False       # 仅 plot 有
    end_notes: str = ""       # 仅 plot 有
    is_new: bool = False
    is_dirty: bool = False
    is_deleted: bool = False
    base_version_id: int | None = None  # re-extract 时的基础版本 ID
    write_mode: str = "insert"          # "insert" | "overwrite"

    def to_dict(self) -> dict:
        return {
            "keywords": self.keywords,
            "description": self.description,
            "state": self.state,
            "tags": self.tags,
            "content": self.content,
            "relations": self.relations,
        }


class ProxyService:
    """缓存代理层"""

    def __init__(self, db_service: SQLiteService):
        self._db = db_service
        self._cache: dict[tuple[str, int], CachedDoc] = {}
        self._next_temp_id = -1
        self._snapshot_path: Path | None = None

    # ── 类别代理 ──

    def create_category(self, name: str, type: str = "wiki",
                        spec: dict = None) -> int:
        return self._db.create_category(name, type, spec)

    def get_category(self, cat_id: int) -> dict | None:
        return self._db.get_category(cat_id)

    def get_category_by_name(self, name: str) -> dict | None:
        return self._db.get_category_by_name(name)

    def list_categories(self, type: str = None) -> list[dict]:
        return self._db.list_categories(type)

    # ── 缓存操作 ──

    def _cache_key(self, doc: CachedDoc) -> tuple[str, int]:
        return (doc.doc_type, doc.main_id or 0)

    def _find_in_cache(self, doc_type: str, name: str) -> CachedDoc | None:
        """在缓存中按名称查找"""
        for key, doc in self._cache.items():
            if key[0] == doc_type and doc.name == name:
                return doc
        return None

    def _load_from_db(self, doc_type: str, name: str) -> CachedDoc | None:
        """从 DB 加载文档到缓存"""
        finder = {
            "wiki": self._db.wiki_find_main,
            "plot": self._db.plot_find_main,
            "rule": self._db.rule_find_main,
        }
        getter = {
            "wiki": self._db.wiki_get_current,
            "plot": self._db.plot_get_current,
            "rule": self._db.rule_get_current,
        }
        main = finder[doc_type](name)
        if main is None:
            return None
        current = getter[doc_type](main["id"])
        if current is None:
            return None

        # 获取类别名
        category = None
        if doc_type == "wiki":
            cat_id = current.get("category_id")
            if cat_id:
                cat = self._db.get_category(cat_id)
                category = cat["name"] if cat else None

        doc = CachedDoc(
            doc_type=doc_type,
            main_id=main["id"],
            name=name,
            category=category,
            content=current.get("content", ""),
            description=current.get("description", ""),
            state=current.get("state", ""),
            keywords=current.get("keywords", ""),
            tags=current.get("tags", []),
            relations=current.get("relations", []),
            chapter=current.get("updated_chapter", 0),
            chapters=current.get("chapters", ""),
            ended=bool(current.get("ended", False)),
            end_notes=current.get("end_notes", ""),
            is_new=False,
            is_dirty=False,
        )
        self._cache[(doc_type, main["id"])] = doc
        return doc

    def add_doc(self, doc_type: str, name: str,
                category: str = None, chapter: int = 0,
                content: str = "", description: str = "",
                state: str = "", keywords: str = "",
                tags: list = None,
                chapters: str = "", **kwargs) -> str:
        """新增文档（只改缓存）

        如果同名条目已存在于缓存中（由 load_whitelist 预创建的占位条目），
        则用本次传入的内容覆盖占位，而非拒绝。这样 LLM 调用 new_plot/new_rule/create_doc
        时能正确填充内容。
        """
        # 检查是否已在缓存中
        existing = self._find_in_cache(doc_type, name)
        if existing and existing.is_new:
            # 白名单预加载的占位条目 → 用实际内容覆盖
            existing.content = content
            existing.description = description
            existing.state = state
            existing.keywords = keywords
            existing.tags = tags or []
            if chapters:
                existing.chapters = chapters
            if chapter:
                existing.chapter = chapter
                existing.first_chapter = chapter
            existing.is_dirty = True
            prefix = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}
            return f"已创建{prefix.get(doc_type, '文档')}：{name}（缓存中，finish_task 时写入 DB）"

        tid = self._next_temp_id
        self._next_temp_id -= 1

        doc = CachedDoc(
            doc_type=doc_type,
            main_id=tid,
            name=name,
            category=category,
            content=content,
            description=description,
            state=state,
            keywords=keywords,
            tags=tags or [],
            chapter=chapter,
            first_chapter=chapter,  # 记录原始创建章节号
            chapters=chapters,
            is_new=True,
            is_dirty=True,
        )
        self._cache[(doc_type, tid)] = doc

        prefix = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}
        return f"已创建{prefix.get(doc_type, '文档')}：{name}（缓存中，finish_task 时写入 DB）"

    def update_doc(self, doc_type: str, name: str,
                   category: str = None, chapter: int = 0,
                   content: str = None, description: str = None,
                   state: str = None, keywords: str = None,
                   tags: list = None,
                   chapters: str = None, **kwargs) -> str:
        """更新文档（只改缓存）"""
        # 1. 先查缓存
        doc = self._find_in_cache(doc_type, name)

        # 2. 缓存未命中 → 查并加载到缓存
        if doc is None:
            doc = self._load_from_db(doc_type, name)
            if doc is None:
                return f"错误：{doc_type}「{name}」不存在"

        # 3. 修改字段
        if content is not None:
            doc.content = content
            doc.is_dirty = True
        if description is not None:
            doc.description = description
            doc.is_dirty = True
        if state is not None:
            doc.state = state
            doc.is_dirty = True
        if keywords is not None:
            doc.keywords = keywords
            doc.is_dirty = True
        if tags is not None:
            doc.tags = tags
            doc.is_dirty = True
        if chapters is not None:
            doc.chapters = chapters
            doc.is_dirty = True
        if chapter:
            doc.chapter = chapter
            doc.is_dirty = True  # 章节号变更也必须落库，否则 flush Pass 2 会跳过

        return f"已更新{doc_type}：{name}（缓存中，finish_task 时写入 DB）"

    def end_plot(self, name: str, end_notes: str = "") -> str:
        """将剧情卡片标记为已结束（缓存优先 → DB 兜底）"""
        doc = self._find_in_cache("plot", name)
        if doc is None:
            doc = self._load_from_db("plot", name)
            if doc is None:
                return f"错误：剧情卡片「{name}」不存在"

        if doc.ended:
            return f"剧情卡片「{name}」已是结束状态"

        doc.ended = True
        doc.end_notes = end_notes
        doc.is_dirty = True

        msg = f"剧情卡片「{name}」已标记为结束"
        if end_notes:
            msg += f"\n收尾语：{end_notes}"
        return msg

    def delete_doc(self, doc_type: str, name: str,
                   category: str = None) -> str:
        """标记删除（缓存优先 → DB 兜底），flush 时物理删除"""
        doc = self._find_in_cache(doc_type, name)
        if doc is None:
            doc = self._load_from_db(doc_type, name)
            if doc is None:
                return f"错误：{doc_type}「{name}」不存在"
        doc.is_deleted = True
        doc.is_dirty = True
        return f"已标记删除：{name}（finish_task 时生效）"

    # ── 读取方法（缓存优先 → DB 兜底）──

    def _build_frontmatter(self, doc: CachedDoc) -> str:
        """构建与 v4 文件格式兼容的 frontmatter 字符串

        v7.0.1: 多行值（description/keywords 等）转义换行为 \\n——
        否则生成的 YAML 被注入新行，read 时解析错位/字段丢失。
        """
        def _one_line(value: str) -> str:
            return str(value).replace("\r\n", "\n").replace("\n", "\\n")

        lines = ["---"]
        lines.append(f"title: {doc.name}")
        if doc.category:
            cat_name = doc.category
            if isinstance(cat_name, int):
                cat = self._db.get_category(cat_name)
                if cat:
                    cat_name = cat["name"]
            lines.append(f"type: {cat_name}")
        if doc.description:
            lines.append(f"description: {_one_line(doc.description)}")
        if doc.state:
            lines.append(f"state: {_one_line(doc.state)}")
        if doc.keywords:
            lines.append(f"keywords: {_one_line(doc.keywords)}")
        if doc.chapter:
            lines.append(f"chapter: {doc.chapter}")
        if doc.chapters:
            lines.append(f"chapters: {_one_line(doc.chapters)}")
        if doc.tags:
            # v7.0.1: tag 内的 ] 与逗号转义，防 YAML 行内列表解析错位
            esc_tags = [str(t).replace("\\]", "]").replace("]", "\\]").replace(",", "\\,") for t in doc.tags]
            lines.append(f"tags: [{', '.join(esc_tags)}]")
        if doc.ended:
            lines.append("ended: true")
            if doc.end_notes:
                lines.append(f"end_notes: {_one_line(doc.end_notes)}")
        lines.append("---")
        return "\n".join(lines)

    def read_doc(self, doc_type: str, name: str,
                 category: str = None, yaml_only: bool = True) -> str:
        """读取文档（缓存优先 → DB 兖底），返回格式与 v4 文件模式一致"""
        # 1. 查缓存
        doc = self._find_in_cache(doc_type, name)
    
        # 2. 缓存未命中 → 查 DB 并加载到缓存
        if doc is None:
            doc = self._load_from_db(doc_type, name)
            if doc is None:
                return f"错误：{doc_type}「{name}」不存在"
    
        fm = self._build_frontmatter(doc)
        if yaml_only:
            return fm + "\n> （内容已省略，将 yaml_only 设为 false 可查看全文）\n"
        return fm + "\n" + doc.content
    
    def read_doc_version(self, doc_type: str, name: str,
                         version_chapter: int,
                         yaml_only: bool = True) -> str:
        """读取指定历史版本（不影响缓存，纯只读）
    
        Args:
            doc_type: "wiki" | "plot" | "rule"
            name: 词条名
            version_chapter: 版本的 updated_chapter 值
            yaml_only: 是否只返回 frontmatter
        """
        finder = {
            "wiki": self._db.wiki_find_main,
            "plot": self._db.plot_find_main,
            "rule": self._db.rule_find_main,
        }
        main = finder[doc_type](name)
        if main is None:
            return f"错误：{doc_type}「{name}」不存在"
    
        version_data = self._db.get_version_by_chapter(
            doc_type, main["id"], version_chapter)
        if version_data is None:
            # 列出可用版本供参考
            from tools.db.version_manager import VersionManager
            vm = VersionManager(self._db)
            timeline = vm.get_timeline(doc_type, main["id"])
            return (f"错误：{doc_type}「{name}」不存在 chapter={version_chapter} 的版本。"
                    f"可用版本：{timeline}")
    
        # 构建临时 CachedDoc 用于生成 frontmatter
        # 类别从 main 表读取（index 版本表无 category_id 字段）
        category = None
        if doc_type == "wiki":
            cat_id = main.get("category_id")
            if cat_id:
                cat = self._db.get_category(cat_id)
                category = cat["name"] if cat else None
    
        doc = CachedDoc(
            doc_type=doc_type,
            main_id=main["id"],
            name=name,
            category=category,
            content=version_data.get("content", ""),
            description=version_data.get("description", ""),
            state=version_data.get("state", ""),
            keywords=version_data.get("keywords", ""),
            tags=version_data.get("tags", []),
            relations=version_data.get("relations", []),
            chapter=version_data.get("chapter", 0),
            chapters=version_data.get("chapters", ""),
            ended=bool(version_data.get("ended", False)),
            end_notes=version_data.get("end_notes", ""),
        )
        fm = self._build_frontmatter(doc)
        header = f"[历史版本 chapter={version_chapter}]\n"
        if yaml_only:
            return header + fm + "\n> （内容已省略，将 yaml_only 设为 false 可查看全文）\n"
        return header + fm + "\n" + doc.content

    def list_docs(self, doc_type: str, category: str = None,
                  page: int = 1, page_size: int = 20,
                  ended: str = "false") -> str:
        """列出文档（格式与 v4 wiki_list / plot_list 一致）"""
        if doc_type == "wiki" and category:
            cat = self._db.get_category_by_name(category)
            if cat is None:
                return f"错误：类别「{category}」不存在"
            mains = self._db.wiki_list_main(cat["id"])
        elif doc_type == "plot":
            ended_filter = None
            if ended == "true":
                ended_filter = True
            elif ended == "false":
                ended_filter = False
            mains = self._db.plot_list_main(ended_filter)
        elif doc_type == "rule":
            mains = self._db.rule_list_main()
        else:
            return f"错误：不支持的查询类型"

        # 合并缓存中新增的、过滤删除的（含任务内标记删除的 DB 条目）
        cache_entries = [
            v for v in self._cache.values()
            if v.doc_type == doc_type and not v.is_deleted
        ]
        deleted_names = {
            v.name for v in self._cache.values()
            if v.doc_type == doc_type and v.is_deleted
        }

        all_items = [m for m in mains if m["name"] not in deleted_names]
        existing_names = {m["name"] for m in all_items}
        for ce in cache_entries:
            if ce.is_new:
                all_items.append({
                    "id": 0,
                    "name": ce.name,
                    "description": ce.description,
                    "state": ce.state,
                })
            else:
                # 已存在的，替换描述信息
                for m in all_items:
                    if m.get("name") == ce.name:
                        if ce.description:
                            m["description"] = ce.description
                        if ce.state:
                            m["state"] = ce.state
                        break

        total = len(all_items)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        start = (page - 1) * page_size
        end = start + page_size
        page_items = all_items[start:end]

        type_label = {"wiki": "词条", "plot": "剧情卡片", "rule": "规则文档"}
        lines = [f"{type_label.get(doc_type, '文档')}列表（第 {page}/{total_pages} 页，共 {total} 个）："]
        for item in page_items:
            line = f"  - {item['name']}"
            if item.get("description"):
                desc = item["description"]
                if len(desc) > 50:
                    desc = desc[:50] + "..."
                line += f"：{desc}"
            lines.append(line)

        return "\n".join(lines)

    def find_doc(self, doc_type: str, name: str) -> int | None:
        """按名称查找文档 main_id（缓存优先，含新增文档）"""
        doc = self._find_in_cache(doc_type, name)
        if doc and not doc.is_deleted:
            return doc.main_id  # 正数=DB已有，负数=本次新增
        finder = {
            "wiki": self._db.wiki_find_main,
            "plot": self._db.plot_find_main,
            "rule": self._db.rule_find_main,
        }
        main = finder[doc_type](name)
        return main["id"] if main else None

    # ── 白名单加载 ──

    def load_whitelist(self, plan: dict):
        """白名单通过后，从 DB 拉取条目到缓存

        re-extract 模式：对 edit 项加载基础版本（而非 current_version），
        并设置 base_version_id / write_mode 供 flush 时版本分叉使用。
        """
        from tools.chapter import parse_chapter_spec

        is_reextract = plan.get("mode") == "re-extract"
        scope_max = 0
        if is_reextract:
            scope_nums = parse_chapter_spec(plan.get("scope", ""))
            scope_max = max(scope_nums) if scope_nums else 0

        # 新增条目：用负 ID 占位
        for item in plan.get("new_wiki", []):
            tid = self._next_temp_id
            self._next_temp_id -= 1
            # 从计划的 chapters 字段解析第一个章节号
            ch_nums = parse_chapter_spec(str(item.get("chapters", "")))
            first_ch = ch_nums[0] if ch_nums else 0
            self._cache[("wiki", tid)] = CachedDoc(
                doc_type="wiki", main_id=tid,
                name=item["name"],
                category=item.get("category", ""),
                chapter=first_ch,
                is_new=True, is_dirty=True,
            )

        # 编辑条目：从 DB 拉取（re-extract 时加载基础版本）
        for item in plan.get("edit_wiki", []):
            name = item["name"]
            if self._find_in_cache("wiki", name):
                continue
            main = self._db.wiki_find_main(name)
            if main is None:
                continue

            if is_reextract and scope_max > 0:
                doc = self._load_base_version("wiki", main, scope_max)
            else:
                current = self._db.wiki_get_current(main["id"])
                if current is None:
                    continue
                cat = self._db.get_category(current.get("category_id"))
                cat_name = cat["name"] if cat else ""
                doc = CachedDoc(
                    doc_type="wiki", main_id=main["id"],
                    name=main["name"], category=cat_name,
                    content=current.get("content", ""),
                    description=current.get("description", ""),
                    state=current.get("state", ""),
                    keywords=current.get("keywords", ""),
                    tags=current.get("tags", []),
                    relations=current.get("relations", []),
                    chapter=main.get("updated_chapter", 0),
                    is_new=False, is_dirty=False,
                )
            if doc:
                self._cache[("wiki", main["id"])] = doc

        # 剧情卡片
        for item in plan.get("new_plot", []):
            tid = self._next_temp_id
            self._next_temp_id -= 1
            ch_nums = parse_chapter_spec(str(item.get("chapters", "")))
            first_ch = ch_nums[0] if ch_nums else 0
            self._cache[("plot", tid)] = CachedDoc(
                doc_type="plot", main_id=tid,
                name=item["name"],
                chapters=item.get("chapters", ""),
                chapter=first_ch,
                is_new=True, is_dirty=True,
            )

        for item in plan.get("edit_plot", []):
            name = item["name"]
            if self._find_in_cache("plot", name):
                continue
            main = self._db.plot_find_main(name)
            if main is None:
                continue

            if is_reextract and scope_max > 0:
                doc = self._load_base_version("plot", main, scope_max)
            else:
                current = self._db.plot_get_current(main["id"])
                if current is None:
                    continue
                doc = CachedDoc(
                    doc_type="plot", main_id=main["id"],
                    name=main["name"],
                    content=current.get("content", ""),
                    description=current.get("description", ""),
                    state=current.get("state", ""),
                    chapters=current.get("chapters", ""),
                    ended=bool(current.get("ended", False)),
                    end_notes=current.get("end_notes", ""),
                    relations=current.get("relations", []),
                    chapter=main.get("updated_chapter", 0),
                    is_new=False, is_dirty=False,
                )
            if doc:
                self._cache[("plot", main["id"])] = doc

        # 规则文档
        for item in plan.get("new_rule", []):
            tid = self._next_temp_id
            self._next_temp_id -= 1
            self._cache[("rule", tid)] = CachedDoc(
                doc_type="rule", main_id=tid,
                name=item["name"],
                chapter=item.get("chapter", 0),
                is_new=True, is_dirty=True,
            )

        for item in plan.get("edit_rule", []):
            name = item["name"]
            if self._find_in_cache("rule", name):
                continue
            main = self._db.rule_find_main(name)
            if main is None:
                continue

            if is_reextract and scope_max > 0:
                doc = self._load_base_version("rule", main, scope_max)
            else:
                current = self._db.rule_get_current(main["id"])
                if current is None:
                    continue
                doc = CachedDoc(
                    doc_type="rule", main_id=main["id"],
                    name=main["name"],
                    content=current.get("content", ""),
                    description=current.get("description", ""),
                    keywords=current.get("keywords", ""),
                    chapter=main.get("updated_chapter", 0),
                    is_new=False, is_dirty=False,
                )
            if doc:
                self._cache[("rule", main["id"])] = doc

    def _load_base_version(self, doc_type: str, main: dict,
                           scope_max: int) -> CachedDoc | None:
        """加载基础版本到 CachedDoc（re-extract 专用）

        基础版本 = 时间线中 ≤ scope_max 且最大的版本。
        同时设置 base_version_id 和 write_mode。
        """
        from tools.db.version_manager import VersionManager
        vm = VersionManager(self._db)

        base_vid = vm.resolve_base_version(doc_type, main["id"], scope_max)
        if base_vid is None:
            # 无匹配版本，退化为加载 current_version
            base_vid = None
            version_data = None
        else:
            version_data = self._db.get_version_by_id(doc_type, base_vid)

        if version_data is None:
            # 退化：加载 current_version
            getter = {
                "wiki": self._db.wiki_get_current,
                "plot": self._db.plot_get_current,
                "rule": self._db.rule_get_current,
            }
            version_data = getter[doc_type](main["id"])
            if version_data is None:
                return None
            base_vid = None  # 无基础版本 ID

        # 决定写入策略
        write_mode = vm.decide_write_strategy(doc_type, main["id"], scope_max)

        # 获取类别名（wiki 专用）：index 版本表无 category_id 字段，
        # 必须从 main 表读取，否则 re-extract 后词条被归入“未分类”（P1-30）
        category = None
        if doc_type == "wiki":
            cat_id = main.get("category_id")
            if cat_id:
                cat = self._db.get_category(cat_id)
                category = cat["name"] if cat else ""

        doc = CachedDoc(
            doc_type=doc_type,
            main_id=main["id"],
            name=main["name"],
            category=category,
            content=version_data.get("content", ""),
            description=version_data.get("description", ""),
            state=version_data.get("state", ""),
            keywords=version_data.get("keywords", ""),
            tags=version_data.get("tags", []),
            relations=version_data.get("relations", []),
            chapter=version_data.get("chapter", 0),
            chapters=version_data.get("chapters", ""),
            ended=bool(version_data.get("ended", False)),
            end_notes=version_data.get("end_notes", ""),
            is_new=False,
            is_dirty=False,
            base_version_id=base_vid,
            write_mode=write_mode,
        )
        return doc

    def is_cache_loaded(self) -> bool:
        """是否已有白名单加载的缓存"""
        return len(self._cache) > 0

    def clear(self):
        """清理缓存"""
        self._cache.clear()
        self._next_temp_id = -1
        if self._snapshot_path and self._snapshot_path.exists():
            self._snapshot_path.unlink()
            self._snapshot_path = None

    # ── 审核暂存 ──

    def snapshot(self, path: Path):
        """进入审核前序列化缓存到磁盘"""
        data = []
        for key, doc in self._cache.items():
            d = {
                "key": list(key),
                "doc_type": doc.doc_type,
                "main_id": doc.main_id,
                "name": doc.name,
                "category": doc.category,
                "content": doc.content,
                "description": doc.description,
                "state": doc.state,
                "keywords": doc.keywords,
                "tags": doc.tags,
                "relations": doc.relations,
                "chapter": doc.chapter,
                "first_chapter": doc.first_chapter,
                "chapters": doc.chapters,
                "ended": doc.ended,
                "end_notes": doc.end_notes,
                "is_new": doc.is_new,
                "is_dirty": doc.is_dirty,
                "is_deleted": doc.is_deleted,
                "base_version_id": doc.base_version_id,
                "write_mode": doc.write_mode,
            }
            data.append(d)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._snapshot_path = path

    def restore(self, path: Path):
        """审核完成后从磁盘恢复缓存"""
        data = json.loads(path.read_text(encoding="utf-8"))
        self._cache = {}
        for d in data:
            key = tuple(d["key"])
            doc = CachedDoc(
                doc_type=d["doc_type"],
                main_id=d["main_id"],
                name=d["name"],
                category=d.get("category"),
                content=d.get("content", ""),
                description=d.get("description", ""),
                state=d.get("state", ""),
                keywords=d.get("keywords", ""),
                tags=d.get("tags", []),
                relations=d.get("relations", []),
                chapter=d.get("chapter", 0),
                first_chapter=d.get("first_chapter", 0),
                chapters=d.get("chapters", ""),
                ended=d.get("ended", False),
                end_notes=d.get("end_notes", ""),
                is_new=d.get("is_new", False),
                is_dirty=d.get("is_dirty", False),
                is_deleted=d.get("is_deleted", False),
                base_version_id=d.get("base_version_id"),
                write_mode=d.get("write_mode", "insert"),
            )
            self._cache[key] = doc
        # v7.0.1: 修复临时 ID 恢复——原逻辑把初始 -1 无条件再减一，
        # 快照中无新增条目时下一个临时 ID 应为 -1 而非 -2（-1 从未被占用）
        neg_ids = [d["main_id"] for d in data
                   if d.get("main_id") and d["main_id"] < 0]
        self._next_temp_id = (min(neg_ids) - 1) if neg_ids else -1
        self._snapshot_path = path

    # ── 关系解析 ──

    def _resolve_name_to_id(self, name: str,
                            created_map: dict[str, int]) -> int | None:
        """将词条名解析为 main_id（本次新建 → 缓存 → DB）"""
        # 1. 本次 flush 中已创建的
        if name in created_map:
            return created_map[name]
        # 2. 缓存中已有的（非新增）
        for (dt, mid), doc in self._cache.items():
            if doc.name == name and not doc.is_deleted and mid > 0:
                return mid
        # 3. 查 DB
        for finder in (self._db.wiki_find_main, self._db.plot_find_main,
                       self._db.rule_find_main):
            main = finder(name)
            if main:
                return main["id"]
        return None

    # ── 批量写入 ──

    def flush(self, scope_chapter: int):
        """finish_task 时统一写入 DB（事务保证原子性）

        两遍遍历：
          Pass 0 — 物理删除标记删除的已有条目
          Pass 1 — 创建所有 main 记录，建立 name→actual_id 映射
          Pass 2 — 解析 [[wikilink]] 填充 relations，创建 version 并 set_current
        """
        if not self._cache:
            return

        from auto.relation_extractor import extract_wikilinks

        # 整段事务在连接锁内执行，与 GUI 等其它线程的写互斥；
        # transaction() 抑制 _commit 提前提交，退出时统一提交（异常回滚）。
        with self._db._lock:
            try:
                with self._db.transaction():
                    # ── Pass 0: 物理删除标记删除的已有条目 ──
                    deleter = {
                        "wiki": self._db.wiki_delete,
                        "plot": self._db.plot_delete,
                        "rule": self._db.rule_delete,
                    }
                    for (doc_type, main_id), doc in list(self._cache.items()):
                        if doc.is_deleted and main_id > 0:
                            deleter[doc_type](main_id)

                    # ── Pass 1: 创建 main 记录 ──
                    created_map: dict[str, int] = {}  # name → actual_id

                    for (doc_type, main_id), doc in list(self._cache.items()):
                        if doc.is_deleted:
                            continue
                        if not doc.is_new:
                            # 已存在的条目，记录到 created_map 供关系解析
                            if main_id > 0:
                                created_map[doc.name] = main_id
                            continue

                        create_ch = doc.first_chapter or doc.chapter or scope_chapter
                        if doc_type == "wiki":
                            cat = self._db.get_category_by_name(doc.category)
                            if cat is None:
                                # Fix 7: 自动创建缺失类别（防御性）
                                cat_id = self._db.create_category(
                                    doc.category or "未分类", "wiki", {})
                            else:
                                cat_id = cat["id"]
                            actual_id = self._db.wiki_create_main(
                                doc.name, cat_id, create_ch)
                        elif doc_type == "plot":
                            actual_id = self._db.plot_create_main(
                                doc.name, create_ch, doc.chapters)
                        else:
                            actual_id = self._db.rule_create_main(
                                doc.name, create_ch)

                        created_map[doc.name] = actual_id

                    # ── Pass 2: 解析 relations + 创建 version + set_current ──
                    from tools.db.version_manager import VersionManager
                    vm = VersionManager(self._db)

                    for (doc_type, main_id), doc in list(self._cache.items()):
                        if doc.is_deleted:
                            continue
                        if not (doc.is_new or doc.is_dirty):
                            continue

                        actual_id = created_map.get(doc.name, main_id)
                        # 新建条目用 doc.chapter（最新版本），更新条目用 scope_chapter（本次提取范围）
                        if doc.is_new:
                            actual_ch = doc.chapter or doc.first_chapter or scope_chapter
                        else:
                            actual_ch = scope_chapter or doc.chapter

                        # 解析 [[wikilink]] → relations（wiki/plot 参与关系系统）
                        if doc_type in ("wiki", "plot") and doc.content:
                            targets = extract_wikilinks(doc.content)
                            if targets:
                                rel_ids = []
                                seen = set()
                                for t in targets:
                                    tid = self._resolve_name_to_id(t, created_map)
                                    if tid and tid != actual_id and tid not in seen:
                                        rel_ids.append(tid)
                                        seen.add(tid)
                                doc.relations = rel_ids

                        # ── 版本写入：重新提取走 version_manager，正常提取走原逻辑 ──
                        if doc.write_mode == "overwrite" and doc.base_version_id:
                            # 重新提取 + 同章节覆盖：原地更新索引行
                            self._db.update_version(
                                doc_type, doc.base_version_id, doc.to_dict())
                            vm._update_pointer(doc_type, actual_id)
                            # plot 额外字段
                            if doc_type == "plot":
                                self._db.plot_set_current(
                                    actual_id, doc.base_version_id, actual_ch,
                                    chapters=doc.chapters,
                                    ended=int(doc.ended),
                                    end_notes=doc.end_notes,
                                )
                        elif not doc.is_new and doc.write_mode == "insert" and doc.base_version_id is not None:
                            # 重新提取 + 不同章节：插入新版本，由 vm 管理指针
                            vm.commit(doc_type, actual_id, actual_ch,
                                      doc.to_dict(), strategy="insert")
                            # plot 额外字段
                            if doc_type == "plot":
                                self._db.plot_set_current(
                                    actual_id,
                                    self._db.list_versions("plot", actual_id)[-1]["id"],
                                    actual_ch,
                                    chapters=doc.chapters,
                                    ended=int(doc.ended),
                                    end_notes=doc.end_notes,
                                )
                        else:
                            # 正常提取 / 非白名单编辑
                            if doc.is_new:
                                # 新建条目：直接插入
                                creator = {
                                    "wiki": self._db.wiki_create_version,
                                    "plot": self._db.plot_create_version,
                                    "rule": self._db.rule_create_version,
                                }[doc_type]
                                ver_id = creator(actual_id, actual_ch, doc.to_dict())
                                # set_current
                                if doc_type == "plot":
                                    self._db.plot_set_current(
                                        actual_id, ver_id, actual_ch,
                                        chapters=doc.chapters,
                                        ended=int(doc.ended),
                                        end_notes=doc.end_notes,
                                    )
                                else:
                                    setter = {
                                        "wiki": self._db.wiki_set_current,
                                        "rule": self._db.rule_set_current,
                                    }[doc_type]
                                    setter(actual_id, ver_id, actual_ch)
                            else:
                                # 已存在条目：由 vm 自动判定 overwrite/insert
                                # 防止同 chapter 重复插入（设计决策 #6）
                                ver_id = vm.commit(doc_type, actual_id,
                                                   actual_ch, doc.to_dict())
                            # plot 额外字段
                            if doc_type == "plot":
                                self._db.plot_set_current(
                                    actual_id,
                                    self._db.list_versions("plot", actual_id)[-1]["id"],
                                    actual_ch,
                                    chapters=doc.chapters,
                                    ended=int(doc.ended),
                                    end_notes=doc.end_notes,
                                )

                # ── 指针一致性修复：确保所有受影响 main 的 current_version 指向 MAX(chapter) ──
                affected_mains = set()
                for (doc_type2, main_id2), doc2 in list(self._cache.items()):
                    if not doc2.is_deleted and (doc2.is_new or doc2.is_dirty):
                        actual_id2 = created_map.get(doc2.name, main_id2)
                        if actual_id2 > 0:
                            affected_mains.add((doc_type2, actual_id2))
                for doc_type3, mid in affected_mains:
                    vm._update_pointer(doc_type3, mid)

                # 清理缓存（事务已提交）
                self._cache.clear()
                self._next_temp_id = -1
                if self._snapshot_path and self._snapshot_path.exists():
                    self._snapshot_path.unlink()
                    self._snapshot_path = None

            except Exception as e:
                raise RuntimeError(
                    f"flush 失败，缓存已保留可重试：{e}")
