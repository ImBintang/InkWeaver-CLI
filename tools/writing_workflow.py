"""writing_workflow — 写作 Workflow（纯 chat，无 tools）

由 MuseWorkflow 直接调用，不经过 MuseAgent。
上下文顺序：上一章全文 → 大纲 → 先验知识 → 前情提要 → 审阅意见

v5.4: 新增 run_revise() — 手术刀式修改轮，输出 edits JSON 而非全文。
"""

import json
from pathlib import Path

from api import LLMClient
from core.events import EventType, StreamBatcher

# v6.5.6: 妙笔写作保留思考模式——思维链是核心产出环节的质量保障，
# 但思考与正文共享 max_tokens 预算（deepseek-v4 自适应思考，无 budget_tokens 参数），
# 思考过长会吃光预算导致正文为空（e2e 实测根因）。
# 方案：reasoning_effort="low" 压低思考 token 消耗（deepseek-v4-flash 的 low 档真实生效，
# pro 的 low 会被映射回 high）+ muse/workflow.py 提高 WRITE_MAX_TOKENS 双保险。
THINKING_EFFORT = "low"
# 正文长度兜底（字符数）：目标 3000~4000 字 ≈ 5500 字符上限；
# 流式/非流式超出即截断到最近完整段落，防止正文失控写到 8000 字。
MAX_BODY_CHARS = 5500


class WritingWorkflow:
    """写作 Workflow — 纯 chat 调用，组装上下文"""

    def __init__(self, llm: LLMClient, workspace: Path, writer_skill_text: str = "", cli=None, bus=None,
                 stop_event=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}
        self.writer_skill_text = writer_skill_text
        # v6.5.6: 宿主注入的终止信号——流式循环内即时检查，直接打断写作
        self.stop_event = stop_event
        # 全局事件总线（GUI 模式注入）：注入后 run() 走流式调用，
        # 正文逐 token 发射 TOKEN 事件 → SSE → 前端实时展示写作过程；
        # CLI/修改轮（run_revise）bus=None 保持原有非流式行为
        self.bus = bus

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self, outline: str, prior_knowledge: str = "", plot_summary: str = "",
            last_chapter: str = "", review_issues: list = None,
            previous_draft: str = "", memory_block: str = "",
            max_tokens: int | None = None) -> str:
        """执行写作

        Args:
            outline: 大纲/草稿
            prior_knowledge: 先验知识（LLM 压缩重写后的结构化文本）
            plot_summary: 前情提要（LLM 压缩重写后的结构化文本）
            last_chapter: 上一章全文
            review_issues: 上一轮审阅意见列表
            previous_draft: 上一轮被驳回的草稿（重写时传入）
            memory_block: v5.3 记忆注入（style 类）
            max_tokens: v6.5.3 输出上限硬约束（配合字数 skill 约束）

        Returns:
            生成的正文文本
        """
        # 按顺序组装上下文
        sections = []

        if last_chapter:
            sections.append(f"## 上一章全文\n{last_chapter}")

        # v7.0.1: outline 为 None 时不再注入字面量 "None" 误导模型
        if outline:
            sections.append(f"## 大纲/草稿\n{outline}")

        if prior_knowledge:
            sections.append(f"## 先验知识\n{prior_knowledge}")

        if plot_summary:
            sections.append(f"## 前情提要\n{plot_summary}")
        if previous_draft:
            sections.append(f"## 上一轮草稿（需根据审阅意见修改）\n{previous_draft}")
        if review_issues:
            issues_lines = []
            for i in review_issues:
                level = i.get('level', '用户')
                desc = i['description']
                sug = i.get('suggestion', '')
                if sug:
                    issues_lines.append(f"- [{level}] {desc}\n  → 建议：{sug}")
                else:
                    issues_lines.append(f"- [{level}] {desc}")
            sections.append(f"## 上一轮审阅意见\n" + "\n".join(issues_lines))

        # v5.3: 记忆注入（style 类）
        if memory_block:
            sections.append(memory_block)

        context = "\n\n".join(sections)

        # 优先使用外部传入的 skill 文本，否则用内置兜底
        if self.writer_skill_text:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说写作助手。\n"
                "你的风格细腻、克制、充满'活人感'。你拒绝网文化、碎片化表达和AI式陈词滥调。\n"
                "\n"
                "# Skill 指令\n"
                f"{self.writer_skill_text}"
            )
        else:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说写作助手。\n"
                "你的风格细腻、克制、充满'活人感'。你拒绝网文化、碎片化表达和AI式陈词滥调。\n"
                "\n"
                "## 核心规则\n"
                "1. 严格遵循'前因 → 发展 → 高潮 → 结局'叙事架构\n"
                "2. 长句为主，短句占比不超过8%\n"
                "3. 禁用词汇：兜住、接住、稳、守、极其、扭曲、疯狂、空洞、麻木\n"
                "4. 禁用句式：'想说什么…喉咙却发不出声''……就够了''心脏像是被……攥紧'\n"
                "5. 禁用关联词：不是……而是……、既……又……、即使……也……\n"
                "6. 禁止倒叙、禁止括号、禁止代码块\n"
                "7. 心理描写点到为止，多用白描\n"
                "8. 直接输出正文，不要使用任何 Markdown 语法\n"
                "9. 不要输出任何外部解释或上下文说明"
            )

        messages = [{"role": "user", "content": context}]
        if self.bus is not None:
            # 流式模式：正文逐 token 推送前端，让用户实时看到写作过程
            # v6.5.6: 保留思考模式（思维链质量保障），low 强度控思考长度；
            # 正文累积达 MAX_BODY_CHARS 主动停止拉流（防正文失控），
            # 思考块发射 REASONING 事件（前端复用“妙笔作家正在思考”展示）
            body_parts = []
            total_chars = 0
            response = None
            # v6.5.7: 批量发射（事件风暴治理）——TOKEN 16 个合并一次、REASONING 64 个合并一次
            token_batcher = StreamBatcher(self.bus, EventType.TOKEN, 16, source="muse")
            reason_batcher = StreamBatcher(self.bus, EventType.REASONING, 64, source="muse")
            for chunk in self.llm.chat_stream(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
                max_tokens=max_tokens,
                thinking=True,
                reasoning_effort=THINKING_EFFORT,
            ):
                # v6.5.6: 用户终止时即时打断（不等流式调用自然结束）
                if self.stop_event is not None and self.stop_event.is_set():
                    token_batcher.flush()
                    reason_batcher.flush()
                    from muse.workflow import MuseStopped
                    raise MuseStopped("妙笔任务已终止")
                ctype = chunk.get("type")
                try:
                    if ctype == "token":
                        text = chunk.get("text", "") or ""
                        body_parts.append(text)
                        total_chars += len(text)
                        token_batcher.add(text)
                        # 正文长度兜底：超过目标上限即停止拉流
                        if total_chars >= MAX_BODY_CHARS:
                            break
                    elif ctype == "reasoning":
                        # v6.5.6: 恢复思考后思考块实时展示
                        text = chunk.get("text", "") or ""
                        if text:
                            reason_batcher.add(text)
                    elif ctype == "done":
                        response = chunk
                except Exception:
                    pass  # 事件上报失败不阻断写作
            token_batcher.flush()
            reason_batcher.flush()
            if response and response.get("usage"):
                self._last_usage = response["usage"]
            result = "".join(body_parts).strip()
            # 截断到最近完整段落（防截断句；仅在超限兜底时触发）
            if len(result) > MAX_BODY_CHARS:
                cut = result.rfind("\n\n", 0, MAX_BODY_CHARS)
                if cut > 0:
                    result = result[:cut].strip()
        else:
            response = self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
                max_tokens=max_tokens,
                thinking=True,
                reasoning_effort=THINKING_EFFORT,
            )
            if "usage" in response:
                self._last_usage = response["usage"]
            result = response.get("content", "").strip()
            # 非流式同样做段落截断兜底（防正文失控）
            if len(result) > MAX_BODY_CHARS:
                cut = result.rfind("\n\n", 0, MAX_BODY_CHARS)
                if cut > 0:
                    result = result[:cut].strip()

        self._log("WRITING_WF_END", result[:200])
        return result

    def run_revise(self, draft: str, review_issues: list,
                   outline: str = "", last_chapter: str = "",
                   change_log: list[str] | None = None,
                   memory_block: str = "", max_tokens: int | None = None) -> tuple[str, list[str]]:
        """执行手术刀式修改轮（v5.4）

        LLM 输出 edits JSON，后端 apply 到 draft 上。
        如果 LLM 输出纯文本（fallback），则视为全文重写。

        Args:
            draft: 当前草稿全文
            review_issues: 审阅意见列表
            outline: 大纲（供参考）
            last_chapter: 上一章全文（供参考衍接）
            change_log: 上轮变更日志
            memory_block: 记忆注入
            max_tokens: v6.5.3 输出上限硬约束

        Returns:
            (new_draft, change_log)
        """
        from tools.muse_edits import apply_writer_edits, parse_edits_response

        # 组装上下文
        sections = []
        if last_chapter:
            sections.append(f"## 上一章全文\n{last_chapter}")
        if outline:
            sections.append(f"## 大纲/草稿\n{outline}")
        sections.append(f"## 上一轮草稿（需根据审阅意见修改）\n{draft}")

        # 审阅意见
        if review_issues:
            issues_lines = []
            for i in review_issues:
                level = i.get('level', '用户')
                desc = i['description']
                sug = i.get('suggestion', '')
                if sug:
                    issues_lines.append(f"- [{level}] {desc}\n  → 建议：{sug}")
                else:
                    issues_lines.append(f"- [{level}] {desc}")
            sections.append("## 审阅意见\n" + "\n".join(issues_lines))

        # 上轮变更记录
        if change_log:
            sections.append("## 上轮修改记录\n" + "\n".join(f"- {c}" for c in change_log))

        # 记忆注入
        if memory_block:
            sections.append(memory_block)

        context = "\n\n".join(sections)

        # 使用修改轮 skill
        if self.writer_skill_text:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说修改专家。\n"
                "你的任务是精确修正草稿中的问题，而非重写全文。\n"
                "\n"
                "# Skill 指令\n"
                f"{self.writer_skill_text}"
            )
        else:
            system_prompt = (
                "你是妙笔（Muse），一个专业的长篇小说修改专家。\n"
                "输出格式必须是 JSON：{\"edits\": [...], \"notes\": \"...\"}\n"
                "工具：replace_text / delete_text / rewrite_paragraph\n"
                "只改有问题的地方，不要输出全文。"
            )

        messages = [{"role": "user", "content": context}]
        if self.bus is not None:
            # v6.5.5: 修改轮流式化——思考过程逐块发射 REASONING 事件，
            # 前端实时展示“妙笔作家正在思考”，修复二次打回后长时间无反馈
            # v6.5.6: 保留思考（low 强度控长度），保证 edits JSON 质量与产出
            result_parts = []
            response = None
            # v6.5.7: 思考流批量发射（事件风暴治理）——64 个合并一次
            reason_batcher = StreamBatcher(self.bus, EventType.REASONING, 64, source="muse")
            for chunk in self.llm.chat_stream(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
                max_tokens=max_tokens,
                thinking=True,
                reasoning_effort=THINKING_EFFORT,
            ):
                # v6.5.6: 用户终止时即时打断（不等流式调用自然结束）
                if self.stop_event is not None and self.stop_event.is_set():
                    reason_batcher.flush()
                    from muse.workflow import MuseStopped
                    raise MuseStopped("妙笔任务已终止")
                ctype = chunk.get("type")
                try:
                    if ctype == "token":
                        result_parts.append(chunk.get("text", ""))
                    elif ctype == "reasoning":
                        text = chunk.get("text", "")
                        if text:
                            reason_batcher.add(text)
                    elif ctype == "done":
                        response = chunk
                        if chunk.get("usage"):
                            self._last_usage = chunk["usage"]
                except Exception:
                    pass  # 事件上报失败不阻断修改主流程
            reason_batcher.flush()
            raw_output = "".join(result_parts).strip()
            # v6.5.6: 流式 token 未产出但 done 汇总带 content（个别供应商非逐块流式）时兜底，
            # 否则修改轮会静默返回空正文（R2 审阅“正文为空”0 级严重问题循环的根因之一）
            if not raw_output and response and response.get("content"):
                raw_output = str(response["content"]).strip()
        else:
            response = self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
                max_tokens=max_tokens,
                thinking=True,
                reasoning_effort=THINKING_EFFORT,
            )
            if "usage" in response:
                self._last_usage = response["usage"]
            raw_output = response.get("content", "").strip()
        self._log("REVISE_WF_END", raw_output[:200])

        # 解析 LLM 输出
        edits, mode = parse_edits_response(raw_output)

        if mode == "edits":
            # v7.0.1: LLM 输出了合法 edits JSON 但列表为空 → 意味着审阅意见
            # 无需实质修改（如原文已符合要求），保留原稿返回；
            # 原逻辑落入 fallback 全文重写分支，误把原稿当新稿走 diff。
            if not edits:
                return draft, ["[无修改] 审阅意见无需修改（edits 为空），保留原稿"]
            # 手术刀模式：应用编辑指令
            new_draft, changes, applied = apply_writer_edits(draft, edits)
            # v6.5.6: 修改轮的流式 token 是 edits JSON（前端正文区看不到），
            # 应用后的完整新草稿一次性推给前端，避免写作/审阅阶段“正文原文”空白
            if self.bus is not None and new_draft:
                try:
                    self.bus.emit(EventType.TOKEN, {"text": new_draft}, source="muse")
                except Exception:
                    pass
            # v6.5.8: 成功应用的编辑清单推给前端——前端据此把被修改的字段
            # 用雾霾蓝底色标注（new_text 在新稿中定位），用户能直观看到改了哪里
            if self.bus is not None and applied:
                try:
                    self.bus.emit(EventType.MUSE_EDITS, {"edits": applied}, source="muse")
                except Exception:
                    pass
            return new_draft, changes
        else:
            # Fallback：LLM 输出了全文（旧模式兼容）
            # 简单生成 change_log
            changes = ["[全文重写] LLM 未输出 edits JSON，回退到全文模式"]
            # v6.5.8: 清洗 LLM 输出的“修改说明”前缀（如“我根据审阅意见…以下是修改后的全文：”），
            # 只保留正文部分用于 diff 与展示，避免前缀混入新稿
            clean_text = raw_output
            for marker in ("以下是修改后的全文", "修改后的全文如下", "修改后全文如下",
                           "以下是修改后的正文", "修改后的正文如下"):
                idx = clean_text.find(marker)
                if idx >= 0:
                    clean_text = clean_text[idx + len(marker):].lstrip("：:—\n\r \t-～")
                    break
            display_text = clean_text if clean_text.strip() else raw_output
            # v6.5.5: 全文重写时把完整正文推给前端（修改轮过程中未发射正文 token）
            if self.bus is not None and display_text:
                try:
                    self.bus.emit(EventType.TOKEN, {"text": display_text}, source="muse")
                except Exception:
                    pass
            # v6.5.8: fallback 全文模式没有 edits 清单，但用户仍需要看到“改了哪里”——
            # 对新旧草稿做段落级 diff，把新稿中发生变化的段落标记为被修改（前端雾霾蓝高亮）
            if self.bus is not None and draft and display_text:
                try:
                    from tools.muse_edits import diff_paragraphs
                    applied = diff_paragraphs(draft, display_text)
                    if applied:
                        self.bus.emit(EventType.MUSE_EDITS, {"edits": applied}, source="muse")
                except Exception:
                    pass
            # v6.5.6: 修改轮未产出任何内容（如思考耗尽 max_tokens 截断）时显式上报，
            # 消费端（workflow 层）据此拦截空正文，而不是带着空稿继续审阅
            if not raw_output and self.bus is not None:
                try:
                    self.bus.emit(EventType.ERROR, {
                        "text": "修改轮未产出修改内容（LLM 思考过长被输出上限截断），已保留原稿",
                    }, source="muse")
                except Exception:
                    pass
            return display_text, changes
