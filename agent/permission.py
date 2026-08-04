"""v4 权限管家 — pipeline: deny → whitelist → allow

参考 learn-claude-code s07_permission_system.py 的 pipeline 设计：
  deny_rules → mode_check → allow_rules → ask_user

在 InkWeaver v4 中简化为：
  planning 阶段 → 写操作全拦截
  executing 阶段 → 写操作白名单检查
  review 模式 → 读操作白名单检查
"""

import json
from tools.chapter import parse_chapter_spec


# 写工具（planning 阶段 + executing 阶段白名单检查）
WRITE_TOOLS = {
    "new_wiki", "edit_wiki", "delete_wiki",
    "batch_create_wiki", "batch_edit_wiki",
    "new_category", "edit_category",
    "new_rule", "edit_rule", "delete_rule",
    "new_plot", "edit_plot", "end_plot", "delete_plot",
    "create_doc", "edit_doc", "edit_doc_text", "edit_doc_wikilink", "delete_doc",
    # P1-08：Knowledge 模式子代理派发与索引修改同样必须在执行阶段才能进行
    "knowledge_task", "plot_task", "edit_index",
}

# Review 模式下禁止的新建操作
# new_wiki / batch_create_wiki 不在此列 — 审核阶段需灵活补建漏建和遗漏词条，
# 由 Step 2 的例外逻辑放行
REVIEW_NEW_TOOLS = {
    "new_rule", "new_plot", "new_category",
    "create_doc",
}

# Review 模式读锁工具（计划外的内容不可读）
READ_LOCK_TOOLS = {
    "read_chapters", "read_wiki", "read_plot",
}

# 模式切换工具（不被规划阶段拦截）
MODE_SWITCH_TOOLS = {"submit_plan", "review_workflow", "finish_task"}


class Whitelist:
    """从 plan JSON 构建的白名单"""

    def __init__(self):
        # 写白名单
        self.new_wiki: set[tuple[str, str]] = set()
        self.edit_wiki: set[tuple[str, str]] = set()
        self.new_rule: set[str] = set()
        self.edit_rule: set[str] = set()
        self.new_plot: set[str] = set()
        self.edit_plot: set[str] = set()
        self.new_category: set[str] = set()
        self.edit_category: set[str] = set()
        # Review 读白名单
        self.read_chapters: set[int] = set()
        self.read_wiki: set[tuple[str, str]] = set()
        self.read_plot: set[str] = set()

    def build(self, plan: dict):
        """从 plan JSON 构建所有白名单集合"""
        if not isinstance(plan, dict):
            return
        # v7.0.1: 列表字段显式 null / 非 dict 条目时安全降级（否则 for item in None 抛 TypeError）
        for item in plan.get("new_wiki") or []:
            if not isinstance(item, dict):
                continue
            self.new_wiki.add((item.get("category"), item.get("name")))
            self.read_wiki.add((item.get("category"), item.get("name")))
        for item in plan.get("edit_wiki") or []:
            if not isinstance(item, dict):
                continue
            self.edit_wiki.add((item.get("category"), item.get("name")))
            self.read_wiki.add((item.get("category"), item.get("name")))
        for item in plan.get("new_rule") or []:
            if not isinstance(item, dict):
                continue
            self.new_rule.add(item.get("name"))
        for item in plan.get("edit_rule") or []:
            if not isinstance(item, dict):
                continue
            self.edit_rule.add(item.get("name"))
        for item in plan.get("new_plot") or []:
            if not isinstance(item, dict):
                continue
            self.new_plot.add(item.get("name"))
            self.read_plot.add(item.get("name"))
        for item in plan.get("edit_plot") or []:
            if not isinstance(item, dict):
                continue
            self.edit_plot.add(item.get("name"))
            self.read_plot.add(item.get("name"))
        for item in plan.get("new_category") or []:
            if not isinstance(item, dict):
                continue
            self.new_category.add(item.get("name"))
        for item in plan.get("edit_category") or []:
            if not isinstance(item, dict):
                continue
            self.edit_category.add(item.get("name"))
        self.read_chapters.update(parse_chapter_spec(plan.get("scope") or ""))

    def merge(self, other: "Whitelist"):
        """合并两个白名单（提取计划 ∪ review 修改计划）"""
        for attr in ("new_wiki", "edit_wiki", "new_rule", "edit_rule",
                     "new_plot", "edit_plot", "new_category", "edit_category",
                     "read_wiki", "read_plot", "read_chapters"):
            getattr(self, attr).update(getattr(other, attr))

    def _check_doc_write(self, params: dict, wiki_set: set, rule_set: set, plot_set: set) -> bool:
        """统一检查 create_doc / edit_doc 等工具的白名单

        doc_type="wiki" → 检查 (category, name) in wiki_set
        doc_type="rule" → 检查 name in rule_set
        doc_type="plot" → 检查 name in plot_set
        """
        doc_type = params.get("doc_type", "")
        name = params.get("name", "")
        if doc_type == "wiki":
            return (params.get("category"), name) in wiki_set
        elif doc_type == "rule":
            return name in rule_set
        elif doc_type == "plot":
            return name in plot_set
        return False

    def allows_write(self, tool_name: str, params: dict) -> bool:
        """检查写操作是否在白名单内"""
        checks = {
            "new_wiki": lambda: (
                params.get("category"), params.get("name")
            ) in self.new_wiki,
            "edit_wiki": lambda: (
                params.get("category"), params.get("name")
            ) in self.edit_wiki,
            "batch_create_wiki": lambda: all(
                (item.get("category"), item.get("name")) in self.new_wiki
                for item in params.get("items", [])
            ),
            "batch_edit_wiki": lambda: all(
                (item.get("category"), item.get("name")) in self.edit_wiki
                for item in params.get("items", [])
            ),
            "new_rule": lambda: params.get("name") in self.new_rule,
            "edit_rule": lambda: params.get("name") in self.edit_rule,
            "new_plot": lambda: params.get("name") in self.new_plot,
            "edit_plot": lambda: params.get("name") in self.edit_plot,
            "new_category": lambda: params.get("name") in self.new_category,
            "edit_category": lambda: params.get("name") in self.edit_category,
            # 统一文档工具 → 映射到对应白名单
            "create_doc": lambda: self._check_doc_write(
                params, self.new_wiki, self.new_rule, self.new_plot
            ),
            "edit_doc": lambda: self._check_doc_write(
                params, self.edit_wiki, self.edit_rule, self.edit_plot
            ),
            "edit_doc_text": lambda: self._check_doc_write(
                params, self.edit_wiki, self.edit_rule, self.edit_plot
            ),
            "edit_doc_wikilink": lambda: self._check_doc_write(
                params, self.edit_wiki, self.edit_rule, self.edit_plot
            ),
            "delete_doc": lambda: self._check_doc_write(
                params, self.edit_wiki, self.edit_rule, self.edit_plot
            ),
            # P1-07：破坏性删除/收尾操作必须显式列白名单，否则默认拒绝
            "delete_wiki": lambda: (
                params.get("category"), params.get("name")
            ) in self.edit_wiki,
            "delete_rule": lambda: params.get("name") in self.edit_rule,
            "delete_plot": lambda: params.get("name") in self.edit_plot,
            "end_plot": lambda: params.get("name") in self.edit_plot,
        }
        check = checks.get(tool_name)
        if check is None:
            # fail-closed：未登记的写工具默认拒绝，避免新增破坏性工具时静默放行
            return False
        return check()

    def allows_read(self, tool_name: str, params: dict) -> bool:
        """检查读操作是否在白名单内（review 模式用）"""
        checks = {
            "read_chapters": lambda: self._chapters_in_scope(
                params.get("chapters", "")
            ),
            "read_wiki": lambda: (
                params.get("category"), params.get("name")
            ) in self.read_wiki,
            "read_plot": lambda: params.get("name") in self.read_plot,
        }
        check = checks.get(tool_name)
        return check() if check else True

    def _chapters_in_scope(self, spec: str) -> bool:
        """检查章节范围是否在白名单内"""
        requested = set(parse_chapter_spec(spec))
        return requested.issubset(self.read_chapters)

    def allow_review_edits(self):
        """审核模式下允许编辑新建的条目（new → edit 白名单）"""
        self.edit_wiki.update(self.new_wiki)
        self.edit_rule.update(self.new_rule)
        self.edit_plot.update(self.new_plot)


class PermissionManager:
    """v4 权限管家 — pipeline: deny → whitelist → allow

    状态流转：
      extract mode: planning → (submit_plan) → executing
      review mode:  planning → (submit_plan) → executing → (finish_task) → reset
    """

    def __init__(self):
        self.phase = "planning"          # planning | executing
        self.mode = "extract"            # extract | review
        self.whitelist = Whitelist()
        self._unlocked = False           # P1-05：Knowledge 模式 confirm_plan 后的全放行标志

    def confirm_plan(self) -> str:
        """Knowledge 专家模式：确认计划，切换至执行阶段（写权限全放行）

        与 submit_plan（结构化白名单）不同：Knowledge 模式没有 plan JSON，
        用户在 planning 阶段确认后直接解锁写权限（planning 阶段已由
        WRITE_TOOLS 拦截兜底，杜绝规划期写入）。
        """
        if self.phase == "executing":
            return "已处于执行阶段"
        self.phase = "executing"
        self._unlocked = True
        return "OK"

    def submit_plan(self, plan_json: dict) -> str:
        """提交计划，构建白名单，切换至执行阶段"""
        self.whitelist.build(plan_json)
        # 将 new_* 同步到 edit_*：能创建就应该能编辑（如 batch_create 后补内容）
        self.whitelist.allow_review_edits()
        self.phase = "executing"
        # v7.0.1: 结构化计划生效后撤销 Knowledge 全放行标志，恢复白名单约束
        self._unlocked = False
        return "OK"

    def submit_review_plan(self, plan_json: dict) -> str:
        """审核阶段补充计划：合并到现有白名单，不切换阶段

        用于审核过程中 LLM 发现计划外重要遗漏词条时的白名单扩展。
        与 submit_plan 不同——不重置 whitelist，只追加项目，保持 phase=planning。
        """
        if self.mode != "review":
            return "错误：submit_review_plan 仅能在 review 模式下使用"

        # 字段缺失不静默：逐项校验并返回可读错误（否则 KeyError 被上层
        # 吞成泛化"工具调度异常"，LLM 无法定位问题）
        def _check(item: dict, key: str) -> str | None:
            if not isinstance(item, dict) or not item.get(key):
                return f"补充计划条目缺少字段「{key}」：{json.dumps(item, ensure_ascii=False)}"
            return None

        for item in plan_json.get("new_wiki") or []:
            err = _check(item, "category") or _check(item, "name")
            if err:
                return f"错误：{err}"
            key = (item["category"], item["name"])
            self.whitelist.new_wiki.add(key)
            self.whitelist.edit_wiki.add(key)  # 创建后需能编辑补内容
            self.whitelist.read_wiki.add(key)
        for item in plan_json.get("edit_wiki") or []:
            err = _check(item, "category") or _check(item, "name")
            if err:
                return f"错误：{err}"
            key = (item["category"], item["name"])
            self.whitelist.edit_wiki.add(key)
            self.whitelist.read_wiki.add(key)
        for item in plan_json.get("new_rule") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.new_rule.add(item["name"])
        for item in plan_json.get("edit_rule") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.edit_rule.add(item["name"])
        for item in plan_json.get("new_plot") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.new_plot.add(item["name"])
            self.whitelist.read_plot.add(item["name"])
        for item in plan_json.get("edit_plot") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.edit_plot.add(item["name"])
            self.whitelist.read_plot.add(item["name"])
        # v5.4.2：支持审核阶段申请创建类别
        for item in plan_json.get("new_category") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.new_category.add(item["name"])
        for item in plan_json.get("edit_category") or []:
            err = _check(item, "name")
            if err:
                return f"错误：{err}"
            self.whitelist.edit_category.add(item["name"])
        return json.dumps({
            "status": "approved",
            "message": "补充计划已合并到白名单，可以继续执行。"
        }, ensure_ascii=False)

    def switch_review(self):
        """切换到 review 模式"""
        self.mode = "review"
        self.phase = "planning"
        # v7.0.1: 重置 Knowledge 全放行标志——否则 _unlocked 永久短路白名单检查
        self._unlocked = False
        # 允许编辑新建的条目（lint 修复需要）
        self.whitelist.allow_review_edits()

    def reset(self):
        """重置为初始状态"""
        self.phase = "planning"
        self.mode = "extract"
        self.whitelist = Whitelist()
        self._unlocked = False

    def _param_summary(self, tool_name: str, params: dict) -> str:
        """生成参数摘要用于拦截信息"""
        if tool_name in ("new_wiki", "edit_wiki", "read_wiki"):
            return f"{params.get('category', '?')}/{params.get('name', '?')}"
        if tool_name in ("read_chapters",):
            return f"章节 {params.get('chapters', '?')}"
        if tool_name in ("read_plot", "new_plot", "edit_plot"):
            return f"剧情卡片 {params.get('name', '?')}"
        if tool_name in ("new_rule", "edit_rule"):
            return f"规则 {params.get('name', '?')}"
        if tool_name in ("batch_create_wiki", "batch_edit_wiki"):
            items = params.get("items", [])
            return f"{len(items)} 个条目"
        if tool_name in ("create_doc", "edit_doc", "edit_doc_text", "edit_doc_wikilink", "delete_doc"):
            return f"{params.get('doc_type', '?')}/{params.get('name', '?')}"
        return str(params)[:60]

    def check(self, tool_name: str, params: dict) -> str | None:
        """统一检查入口

        Returns:
            None — 允许执行
            str — 被拦截的原因
        """

        # Step 1: 规划阶段 → 写操作全拦截（review 模式下允许白名单内写操作）
        if self.phase == "planning" and self.mode != "review":
            if tool_name in WRITE_TOOLS:
                return (
                    f"[权限拦截] 当前处于「规划阶段」，{tool_name} 是写操作，已被阻止。\n"
                    f"请先调 submit_plan 提交计划，用户确认后写权限才会开放。"
                )

        # Step 1.5: Review 模式 → 禁止新建操作（仅允许修改已有条目）
        if self.mode == "review" and tool_name in REVIEW_NEW_TOOLS:
            param_info = self._param_summary(tool_name, params)
            return (
                f"[权限拦截] review 模式下禁止新建操作。"
                f"{tool_name}({param_info}) 是创建行为，如需新增请退出审核后重新提交计划。\n"
                f"审核阶段仅允许修改已存在的条目（edit_doc/edit_doc_text 等）。"
            )

        # Step 2: 执行阶段 / Review 模式 → 写操作白名单检查
        if (self.phase == "executing" or self.mode == "review") and tool_name in WRITE_TOOLS:
            # P1-05：Knowledge 模式 confirm_plan 后解锁（无结构化白名单）
            if not self._unlocked and not self.whitelist.allows_write(tool_name, params):
                param_info = self._param_summary(tool_name, params)
                return (
                    f"[权限拦截] {tool_name}({param_info}) 不在计划白名单内。\n"
                    f"只有计划内允许的操作才可执行。"
                )

        # Step 3: Review 模式 → 读操作白名单检查
        if self.mode == "review" and tool_name in READ_LOCK_TOOLS:
            if not self.whitelist.allows_read(tool_name, params):
                param_info = self._param_summary(tool_name, params)
                return (
                    f"[权限拦截] review 模式下，{tool_name}({param_info}) "
                    f"不在本次计划范围内，无权读取。\n"
                    f"可以使用 wiki_list/plot_list/chapter_list/read_index 等工具查看存在性与写作规范。"
                )

        return None  # 放行
