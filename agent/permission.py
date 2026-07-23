"""统一权限管家 — 集中管理所有审核判定

参考 learn-claude-code s07_permission_system.py 的 pipeline 设计：
  deny_rules → mode_check → allow_rules → ask_user

在 InkWeaver 中简化为统一 check() 入口，覆盖两类审核：
  1. 模式切换审核（handoff_knowledge）— 需要用户确认后才能进入 Knowledge 模式
  2. 写操作审核（planning/executing）— 规划阶段拦截所有写操作

所有需要审核的工具集中在 TOOL_CATEGORIES 中管理，
新增审核工具只需在对应集合中添加工具名即可。
"""

# 写工具（Knowledge 专属，planning 阶段拦截）
WRITE_TOOLS = {
    "new_wiki", "edit_wiki", "delete_wiki",
    "new_category", "edit_category",
    "new_rule", "edit_rule", "delete_rule",
    "knowledge_task",
    "edit_index",
    "new_plot", "edit_plot", "end_plot", "delete_plot",
    "plot_task",
    # 统一文档管理工具
    "create_doc", "edit_doc", "edit_doc_text", "edit_doc_wikilink",
    "delete_doc",
}

# 模式切换工具（需用户确认后才放行）
MODE_SWITCH_TOOLS = {"handoff_knowledge"}


class PermissionManager:
    """统一审核管理器

    工作流：
      模式切换:  LLM 调 handoff_knowledge → check() 记录请求 → chat() 返回 True
                → 主循环询问用户 → 用户确认后切换 KnowledgeAgent

      写操作:    LLM 调 new_wiki → check() 拦截 → LLM 输出计划等用户确认
                → 用户确认后 LLM 调 confirm_plan → 写操作放行
    """

    def __init__(self):
        self._plan_phase = "planning"      # planning | executing
        self._handoff_blocked = True        # True=首次 handoff 需确认
        self._handoff_ever_passed = False   # 本 session 内是否已确认过

    @property
    def handoff_requested(self) -> bool:
        """主循环检测：LLM 是否请求了模式切换且需用户确认"""
        return not self._handoff_blocked and not self._handoff_ever_passed

    @property
    def phase(self) -> str:
        return self._plan_phase

    def confirm_handoff(self):
        """用户确认模式切换后调用"""
        self._handoff_ever_passed = True
        self._handoff_blocked = True  # 重置请求标记

    def confirm_plan(self):
        """用户确认计划后放行写操作"""
        self._plan_phase = "executing"

    def reset(self):
        """重置为初始状态（新 session）"""
        self._plan_phase = "planning"
        self._handoff_blocked = True
        self._handoff_ever_passed = False

    def check(self, tool_name: str) -> str | None:
        """统一检查入口

        Args:
            tool_name: 工具名

        Returns:
            None — 允许执行
            "__HANDOFF_KNOWLEDGE__" — 特殊标记，通知主循环切换模式
            其他字符串 — 被拦截的原因
        """
        # 1) 模式切换审核
        if tool_name in MODE_SWITCH_TOOLS:
            if not self._handoff_ever_passed:
                # 首次 handoff：记录请求，由 chat() 返回给主循环处理
                self._handoff_blocked = False
                return "__HANDOFF_KNOWLEDGE__"
            # 已确认过，直接放行
            return None

        # 2) 写操作审核（planning 阶段拦截）
        if tool_name in WRITE_TOOLS and self._plan_phase == "planning":
            return (
                f"[权限拦截] 当前处于「规划阶段」，{tool_name} 是写操作，已被阻止。\n"
                f"请先使用 agent_output 输出完整计划并等待用户确认，\n"
                f"用户确认后调用 confirm_plan 切换到执行阶段。"
            )

        return None
