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

        msg = f"✅ 剧情卡片「{name}」已标记为结束"
        if end_notes:
            msg += f"\n收尾语：{end_notes}"
        return msg

    def delete_doc(self, doc_type: str, name: str,
                   category: str = None) -> str:
        """标记删除（暂不实现物理删除）"""
        doc = self._find_in_cache(doc_type, name)
        if doc is None:
            return f"错误：{doc_type}「{name}」不在本次任务范围内，无法删除"
        doc.is_deleted = True
        doc.is_dirty = True
        return f"已标记删除：{name}（finish_task 时生效）"

    # ── 读取方法（缓存优先 → DB 兜底）──

    def _build_frontmatter(self, doc: CachedDoc) -> str:
        """构建与 v4 文件格式兼容的 frontmatter 字符串"""
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
            lines.append(f"description: {doc.description}")
        if doc.state:
            lines.append(f"state: {doc.state}")
        if doc.keywords:
            lines.append(f"keywords: {doc.keywords}")
        if doc.chapter:
            lines.append(f"chapter: {doc.chapter}")
        if doc.chapters:
            lines.append(f"chapters: {doc.chapters}")
        if doc.tags:
            lines.append(f"tags: [{', '.join(doc.tags)}]")
        if doc.ended:
            lines.append("ended: true")
            if doc.end_notes:
                lines.append(f"end_notes: {doc.end_notes}")
        lines.append("---")
        return "\n".join(lines)

    def read_doc(self, doc_type: str, name: str,
                 category: str = None, yaml_only: bool = True) -> str:
        """读取文档（缓存优先 → DB 兜底），返回格式与 v4 文件模式一致"""
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

        # 合并缓存中新增的、过滤删除的
        cache_entries = [
            v for v in self._cache.values()
            if v.doc_type == doc_type and not v.is_deleted
        ]

        all_items = list(mains)
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
        """白名单通过后，从 DB 拉取条目到缓存"""
        # 新增条目：用负 ID 占位
        for item in plan.get("new_wiki", []):
            tid = self._next_temp_id
            self._next_temp_id -= 1
            self._cache[("wiki", tid)] = CachedDoc(
                doc_type="wiki", main_id=tid,
                name=item["name"],
                category=item.get("category", ""),
                chapter=item.get("chapter", 0),
                is_new=True, is_dirty=True,
            )

        # 编辑条目：从 DB 拉取
        for item in plan.get("edit_wiki", []):
            name = item["name"]
            if self._find_in_cache("wiki", name):
                continue
            main = self._db.wiki_find_main(name)
            if main is None:
                continue
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
            self._cache[("wiki", main["id"])] = doc

        # 剧情卡片
        for item in plan.get("new_plot", []):
            tid = self._next_temp_id
            self._next_temp_id -= 1
            self._cache[("plot", tid)] = CachedDoc(
                doc_type="plot", main_id=tid,
                name=item["name"],
                chapters=item.get("chapters", ""),
                chapter=item.get("chapter", 0),
                is_new=True, is_dirty=True,
            )

        for item in plan.get("edit_plot", []):
            name = item["name"]
            if self._find_in_cache("plot", name):
                continue
            main = self._db.plot_find_main(name)
            if main is None:
                continue
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
        max_main_id = -1
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
            )
            self._cache[key] = doc
            if d["main_id"] and d["main_id"] < max_main_id:
                max_main_id = d["main_id"]
        self._next_temp_id = max_main_id - 1 if max_main_id < 0 else -1
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
          Pass 1 — 创建所有 main 记录，建立 name→actual_id 映射
          Pass 2 — 解析 [[wikilink]] 填充 relations，创建 version 并 set_current
        """
        if not self._cache:
            return

        from auto.relation_extractor import extract_wikilinks

        self._db.auto_commit = False
        try:
            with self._db.conn:  # 事务
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
                for (doc_type, main_id), doc in list(self._cache.items()):
                    if doc.is_deleted:
                        continue
                    if not (doc.is_new or doc.is_dirty):
                        continue

                    actual_id = created_map.get(doc.name, main_id)
                    # 新建条目用 first_chapter，更新条目用 scope_chapter（本次提取范围）
                    if doc.is_new:
                        actual_ch = doc.first_chapter or doc.chapter or scope_chapter
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

                    # 创建版本记录
                    creator = {
                        "wiki": self._db.wiki_create_version,
                        "plot": self._db.plot_create_version,
                        "rule": self._db.rule_create_version,
                    }[doc_type]
                    ver_id = creator(actual_id, actual_ch, doc.to_dict())

                    # set_current
                    if doc_type == "plot":
                        # Fix 5: 一次性更新所有 plot_main 字段
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

            # 清理缓存
            self._cache.clear()
            self._next_temp_id = -1
            if self._snapshot_path and self._snapshot_path.exists():
                self._snapshot_path.unlink()
                self._snapshot_path = None

        except Exception as e:
            raise RuntimeError(
                f"flush 失败，缓存已保留可重试：{e}")
        finally:
            self._db.auto_commit = True
