"""妙笔工作流 — 四步写作状态机"""

import json
import textwrap
from pathlib import Path
from typing import Optional

from api import LLMClient
from agent.loop import agent_loop
from core.events import EventType
from tools.muse_io import MuseIO
from tools import workspace as workspace_tools
from tools.polish import polish_draft
from muse.agent import MuseAgent
from muse.review_session import ReviewSession


class MuseWorkflow:
    """妙笔工作流——四步状态机

    步骤：
    ① 大纲输入
    ② 知识准备（先验知识 + 前情提要）
    ③ 润色写作
    ④ 写作审阅（可循环③④直至通过）
    """

    # 问题等级映射
    LEVEL_MAP = {0: "严重", 1: "重要", 2: "一般", 3: "可优化"}

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, workspaces_dir: Optional[Path] = None,
                 io=None, outline_text: str = "", auto_approve: bool = False,
                 chapter: int | None = None):
        self.workspace = workspace
        self.skills_dir = skills_dir
        self.workspaces_dir = workspaces_dir
        self.llm_config = config["api"]
        self.io = MuseIO(workspace)
        self.outline: str = outline_text
        self.prior_knowledge: str = ""
        self.plot_summary: str = ""
        self.current_draft: str = ""
        self.issues: list = []  # 携带到下一轮的 issue 列表
        self.change_log: list[str] = []  # v5.4: Writer 修改轮的变更日志
        self._token_stats = {}  # step_name -> {input, output, total}
        self._token_total = {"input": 0, "output": 0, "total": 0}
        self._auto_approve = auto_approve  # 自动确认模式
        # v5.3: 章节锚定
        self._chapter_arg = chapter  # 用户传入的目标章节号（None 表示自动）
        self.target_chapter: int | None = None  # run() 时解析
        self.chapter_ceiling: int | None = None  # target_chapter - 1，知识版本卡控上限
        # P1-19: 追踪已创建的 agent，异常路径统一清理 drain 消费线程
        self._agents: list = []

    def run(self):
        """运行妙笔工作流

        P1-19：整体兜底——任一步骤异常时仍清理已创建 agent 的 drain 线程，
        然后重新抛出（错误层层上报，由消费端决定如何展示）。
        """
        try:
            self._resolve_chapter_anchor()
            if not self.outline:
                self._step_input_outline()
            else:
                self.io.save_outline(self.outline)
            self._step_knowledge_prep()
            self._step_writing_loop()
        except BaseException:
            # P2：BaseException 覆盖 KeyboardInterrupt/SystemExit——
            # 用户 Ctrl+C 中断也必须先清理 drain 线程再重新抛出
            self._cleanup_agents()
            raise
        self._cleanup_agents()
        self._finish()

    def _cleanup_agents(self):
        """停止所有已创建 Agent 的 drain 消费线程（P1-19：异常路径也必须执行）

        单个 agent 清理失败不静默：打印到 stderr（消费端：服务日志），
        线程泄漏会导致进程句柄累积，必须可发现。
        """
        import sys as _sys
        for agent in getattr(self, "_agents", []):
            try:
                self._stop_drain(agent)
            except Exception as e:
                print(f"[muse] 停止 drain 线程失败（{agent}）：{e}",
                      file=_sys.stderr)
        self._agents = []

    def _resolve_chapter_anchor(self):
        """解析目标章节号和版本卡控上限"""
        from tools.db.service import SQLiteService
        db = SQLiteService(self.workspace / "wiki.db")
        max_ch = db.chapter_max_num()
        db.close()

        if self._chapter_arg:
            self.target_chapter = self._chapter_arg
        else:
            self.target_chapter = max_ch + 1 if max_ch > 0 else 1

        # ceiling = N-1，用于知识版本卡控
        self.chapter_ceiling = self.target_chapter - 1 if self.target_chapter > 1 else None

        # 校验上一章是否存在（target=1 时无需上一章）
        # P2：sys.exit(1) 在 GUI 中会杀死宿主进程，改为抛异常由消费端统一处理
        if self.target_chapter > 1:
            prev_num = self.target_chapter - 1
            from tools.chapter import read_chapters
            prev_text = read_chapters(self.workspace, str(prev_num))
            if "（不存在）" in prev_text or prev_text.startswith("错误"):
                raise RuntimeError(
                    f"第 {prev_num} 章不存在，请先导入后再写第 {self.target_chapter} 章。"
                )

        print(f"目标章节：第 {self.target_chapter} 章")
        if self.chapter_ceiling:
            print(f"知识版本卡控：≤ 第 {self.chapter_ceiling} 章")

    # ---- v5.2 I/O 辅助 ----

    def _confirm(self, prompt: str) -> bool:
        """y/n 确认，尊重 _auto_approve"""
        if self._auto_approve:
            return True
        print(prompt)
        try:
            return input().strip().lower() == "y"
        except EOFError:
            # 不静默：输入流结束不可当作"确认放行"（无 TTY 时终止流程而非自动通过）
            raise RuntimeError("输入流已结束（EOF），无法进行人工确认，流程终止。")
        # KeyboardInterrupt 不捕获：Ctrl+C 直接中断（run() 兜底会清理 drain 线程）

    def _input(self, prompt: str = "") -> str:
        """读取输入，_auto_approve 时返回空"""
        if self._auto_approve:
            return ""
        if prompt:
            print(prompt)
        try:
            return input().strip()
        except EOFError:
            # 不静默：输入流结束不可当作空输入继续（打回理由缺失会掩盖真实意图）
            raise RuntimeError("输入流已结束（EOF），无法继续读取输入，流程终止。")
        # KeyboardInterrupt 不捕获：Ctrl+C 直接中断

    # ---- 工作区切换 ----

    def _switch_workspace_interactive(self):
        """交互式切换工作区"""
        if not self.workspaces_dir:
            print("（未配置工作区目录，无法切换）")
            return
        ws_list = workspace_tools.list_workspaces(self.workspaces_dir)
        print("\n" + ws_list)
        print("请输入编号切换工作区（直接回车取消）：")
        try:
            choice = input().strip()
        except EOFError:
            choice = ""  # EOF 视为取消切换
        # KeyboardInterrupt 不捕获：Ctrl+C 直接中断
        if not choice:
            print("已取消切换。")
            return
        try:
            idx = int(choice) - 1
            entries = sorted([d for d in self.workspaces_dir.iterdir() if d.is_dir()])
            if idx < 0 or idx >= len(entries):
                print(f"错误：编号 {choice} 超出范围")
                return
            new_workspace = entries[idx]
        except ValueError:
            print(f"错误：无效编号「{choice}」")
            return

        old_name = self.workspace.name
        self.workspace = new_workspace
        self.io = MuseIO(self.workspace)
        print(f"已切换到工作区「{self.workspace.name}」")

    # ---- 步骤①：大纲输入 ----

    def _step_input_outline(self):
        """① 大纲输入"""
        print("=" * 40)
        print("妙笔写作工作流 - 第一步：大纲输入")

        # 确认/切换工作区
        print(f"当前工作区：{self.workspace.name}")
        print("确认使用当前工作区？[y/n]")
        try:
            choice = input().strip().lower()
        except EOFError:
            choice = ""  # EOF 不误作确认（此前默认 y 属静默放行）
        # KeyboardInterrupt 不捕获：Ctrl+C 直接中断
        if choice == "n":
            self._switch_workspace_interactive()

        print("请输入大纲或章节草稿（多行输入，输入 qqq 结束）：")
        print("-" * 40)

        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break  # EOF：保留已输入内容结束输入
            # KeyboardInterrupt 不捕获：Ctrl+C 直接中断
            if line.strip() == "qqq":
                break
            lines.append(line)

        self.outline = "\n".join(lines).strip()
        self.io.save_outline(self.outline)
        print(f"\n已保存大纲（{len(self.outline)} 字）")

    # ---- Token 统计 ----

    def _update_token_stats(self, step_name: str, usage: dict):
        """记录一步的 token 用量"""
        input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        total = input_tokens + output_tokens
        self._token_stats[step_name] = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
        }
        self._token_total["input"] += input_tokens
        self._token_total["output"] += output_tokens
        self._token_total["total"] += total

    def _update_token_stats_from_agent(self, step_name: str, agent):
        """从 agent._token_accum 记录 token 用量"""
        accum = getattr(agent, "_token_accum", None)
        if accum and (accum["input"] > 0 or accum["output"] > 0):
            self._token_stats[step_name] = {
                "input": accum["input"],
                "output": accum["output"],
                "total": accum["total"],
            }
            self._token_total["input"] += accum["input"]
            self._token_total["output"] += accum["output"]
            self._token_total["total"] += accum["total"]

    def _update_token_stats_from_wf(self, step_name: str, wf):
        """从 Workflow._last_usage 记录 token 用量"""
        usage = getattr(wf, "_last_usage", {})
        if usage:
            self._update_token_stats(step_name, usage)

    def _save_token_stats(self):
        """将 token 统计写入任务目录下的 token.json"""
        data = {
            "steps": dict(self._token_stats),
            "total": dict(self._token_total),
        }
        path = self.io.task_dir / "token.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 步骤②：知识准备 ----

    def _step_knowledge_prep(self):
        """② 知识准备

        顺序：先生成先验知识 → 用户确认 → 再生成前情提要 → 用户确认

        P1-14：打回后走增量修正路径——修正结果作为下一轮展示内容，
        不再被下一轮全量重生成覆盖，用户的打回理由因此真正生效。
        """
        print("=" * 40)
        print("第二步：知识准备")

        # ---- 先验知识 ----
        reason = ""
        while True:
            if reason:
                # 修正轮：基于当前内容 + 打回理由增量修正，不重新全量生成
                print("正在根据反馈增量修正先验知识...")
                self.prior_knowledge = self._revise_prior_knowledge(reason)
                self.io.save_prior_knowledge(self.prior_knowledge)
                reason = ""
            else:
                print("正在生成先验知识...")
                self.prior_knowledge = self._run_researcher()
                self.io.save_prior_knowledge(self.prior_knowledge)
            print("\n" + "=" * 40)
            print("先验知识：")
            print(self.prior_knowledge)
            print("\n确认知识准备通过？[y/n]")
            if self._confirm(""):
                break
            reason = self._input("请输入打回理由：")
            if not reason:
                reason = "（未提供具体理由，请根据展示内容自行修正明显问题）"

        # ---- 前情提要 ----
        reason = ""
        while True:
            if reason:
                print("正在根据反馈增量修正前情提要...")
                self.plot_summary = self._revise_plot_summary(reason)
                self.io.save_plot_summary(self.plot_summary)
                reason = ""
            else:
                print("正在生成前情提要...")
                self.plot_summary = self._run_plot_summary()
                self.io.save_plot_summary(self.plot_summary)
            print("\n" + "=" * 40)
            print("前情提要：")
            self.plot_summary = textwrap.dedent(self.plot_summary)
            print(self.plot_summary)
            print("\n确认前情提要通过？[y/n]")
            if self._confirm(""):
                break
            reason = self._input("请输入打回理由：")
            if not reason:
                reason = "（未提供具体理由，请根据展示内容自行修正明显问题）"

    @staticmethod
    def _check_word_count(text: str) -> list[dict]:
        """自动字数检查：统计正文中文字数，返回审阅问题列表

        阈值（对齐 skill 要求 3000～4000 字）：
          <2200 或 >5000 → level 0（严重）
          <2600 或 >4500 → level 1（重要）
          <2800 或 >4200 → level 2（一般）
          <3000 或 >4000 → level 3（可优化）
        """
        cn_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')

        if cn_count < 2200 or cn_count > 5000:
            level = 0
            if cn_count < 2200:
                quote = f"正文共 {cn_count} 字（不足 2200 字）"
                desc = f"正文字数不足 2200 字（当前 {cn_count} 字），篇幅过短，内容展开不足。"
                sug = f"建议扩充至 3000 字以上（需补充约 {3000 - cn_count} 字），增加细节描写或情节铺垫。"
            else:
                quote = f"正文共 {cn_count} 字（超过 5000 字）"
                desc = f"正文字数超过 5000 字（当前 {cn_count} 字），篇幅过长，可能拖慢叙事节奏。"
                sug = f"建议精简至 4000 字以内（需精简约 {cn_count - 4000} 字），删减冗余描写或拆分段落。"
        elif cn_count < 2600 or cn_count > 4500:
            level = 1
            if cn_count < 2600:
                quote = f"正文共 {cn_count} 字（不足 2600 字）"
                desc = f"正文字数不足 2600 字（当前 {cn_count} 字），篇幅偏短。"
                sug = f"建议适当扩充至 3000 字以上，使情节更加丰满。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4500 字）"
                desc = f"正文字数超过 4500 字（当前 {cn_count} 字），篇幅偏长。"
                sug = f"建议适当精简至 4000 字以内，避免节奏拖沓。"
        elif cn_count < 2800 or cn_count > 4200:
            level = 2
            if cn_count < 2800:
                quote = f"正文共 {cn_count} 字（不足 2800 字）"
                desc = f"正文字数不足 2800 字（当前 {cn_count} 字），篇幅略短。"
                sug = f"可考虑补充部分细节，使内容更加充实，目标 3000 字以上。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4200 字）"
                desc = f"正文字数超过 4200 字（当前 {cn_count} 字），篇幅略长。"
                sug = f"可考虑适当精简至 4000 字以内，保持节奏紧凑。"
        elif cn_count < 3000 or cn_count > 4000:
            level = 3
            if cn_count < 3000:
                quote = f"正文共 {cn_count} 字（不足 3000 字）"
                desc = f"正文字数不足 3000 字（当前 {cn_count} 字），篇幅稍短。"
                sug = f"若感觉内容偏少，可适当增加描写，最佳 3500 字左右。"
            else:
                quote = f"正文共 {cn_count} 字（超过 4000 字）"
                desc = f"正文字数超过 4000 字（当前 {cn_count} 字），篇幅稍长。"
                sug = f"若感觉内容偏多，可适当精简，最佳 3500 字左右。"
        else:
            return []  # 字数在合理范围内，不生成问题

        return [{
            "level": level,
            "quote": quote,
            "description": desc,
            "suggestion": sug,
        }]

    def _run_researcher(self) -> str:
        """运行 Researcher Agent 生成先验知识"""
        agent = self._create_agent(["muse_knowledge.skill.md"])
        messages = [{"role": "user", "content": f"以下是大纲/草稿：\n\n{self.outline}"}]
        messages = agent_loop(agent, messages)
        self._stop_drain(agent)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("knowledge_prep", agent)
        self._save_token_stats()
        # 优先取 workflow 输出（call_knowledge_workflow 存入了 _last_subagent_output）
        if agent._last_subagent_output:
            return agent._last_subagent_output
        return self._extract_last_text(messages)

    def _run_plot_summary(self) -> str:
        """运行 Plot Summary Agent 生成前情提要"""
        agent = self._create_agent(["muse_plot.skill.md"])
        messages = [{"role": "user", "content": f"以下是大纲/草稿：\n\n{self.outline}"}]
        messages = agent_loop(agent, messages)
        self._stop_drain(agent)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("plot_summary", agent)
        self._save_token_stats()
        # 优先取 workflow 输出
        if agent._last_subagent_output:
            return agent._last_subagent_output
        return self._extract_last_text(messages)

    def _revise_prior_knowledge(self, reason: str) -> str:
        """增量修正先验知识（P1-14：基于当前内容 + 打回理由，不重新全量生成）"""
        context = (
            f"## 当前先验知识\n{self.prior_knowledge}\n\n"
            f"## 打回理由\n{reason}"
        )
        agent = self._create_agent(["muse_knowledge.skill.md"])
        messages = [{
            "role": "user",
            "content": context + "\n\n请根据打回理由修正上述先验知识，输出修正后的完整文档。"
        }]
        messages = agent_loop(agent, messages)
        self._stop_drain(agent)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("knowledge_revise", agent)
        self._save_token_stats()
        result = self._extract_last_text(messages)
        # 修正无输出时保留原内容，不让空结果覆盖已有文档
        return result if result else self.prior_knowledge

    def _revise_plot_summary(self, reason: str) -> str:
        """增量修正前情提要（P1-14）"""
        context = (
            f"## 当前前情提要\n{self.plot_summary}\n\n"
            f"## 打回理由\n{reason}"
        )
        agent = self._create_agent(["muse_plot.skill.md"])
        messages = [{
            "role": "user",
            "content": context + "\n\n请根据打回理由修正上述前情提要，输出修正后的完整文档。"
        }]
        messages = agent_loop(agent, messages)
        self._stop_drain(agent)
        self.io.save_session_log(messages)
        self._update_token_stats_from_agent("plot_summary_revise", agent)
        self._save_token_stats()
        result = self._extract_last_text(messages)
        return result if result else self.plot_summary

    # ---- 步骤③→④：写作与审阅循环 ----

    MAX_WRITING_ROUNDS = 3  # 最大写作-审阅轮次

    def _step_writing_loop(self):
        """③→④ 写作与审阅循环

        v5.4: R2+ Writer 使用手术刀编辑，审阅者收到 change_log + 上轮 issues。
        """
        round_count = 0
        previous_review_result: dict | None = None  # v5.4: 保存上轮审阅结果
        while True:
            round_count += 1
            # ③ 润色写作
            print("=" * 40)
            print(f"第三步：润色写作（第 {round_count} 轮）")
            self.current_draft = self._run_writer()
            polished = polish_draft(self.current_draft)
            self.io.save_draft(polished)
            # 更新 current_draft 为润色版，供下一轮重写时传入
            self.current_draft = polished

            # ④ 写作审阅
            print("正在进行写作审阅...")
            review_session = ReviewSession()

            # v5.4: R2+ 注入上轮 issues 和分数（供分数保底逻辑）
            if previous_review_result:
                review_session.previous_issues = previous_review_result["issues"]
                review_session.previous_score = previous_review_result["score"]

            # 自动字数检查：将字数问题注入审阅会话
            word_count_issues = self._check_word_count(polished)
            cn_count = sum(1 for ch in polished if '\u4e00' <= ch <= '\u9fff')
            for issue in word_count_issues:
                review_session.report_issue(**issue)
            lv = self.LEVEL_MAP.get(word_count_issues[0]["level"], "正常") if word_count_issues else "正常"
            print(f"  [自动字数检查] {lv}：正文共 {cn_count} 字")

            review_result = self._run_reviewer(polished, review_session)
            previous_review_result = review_result  # v5.4: 保存本轮结果

            # 保存审阅意见到 review.md
            review_md_lines = [
                f"# 审阅报告",
                f"**分数**：{review_result['score']} / 100",
                f"**判定**：{'✅ 通过' if review_result['pass'] else '❌ 未通过（< 85）'}",
                f"**问题数量**：{len(review_result['issues'])}",
                "",
            ]
            if review_result["issues"]:
                for i, issue in enumerate(review_result["issues"], 1):
                    lv = self.LEVEL_MAP.get(issue.get("level"), "用户")
                    review_md_lines.append(f"### 问题 {i}（{lv}）")
                    review_md_lines.append(f"- **原文**：{issue['quote']}")
                    review_md_lines.append(f"- **描述**：{issue['description']}")
                    review_md_lines.append(f"- **建议**：{issue['suggestion']}")
                    review_md_lines.append("")
            self.io.save_review("\n".join(review_md_lines))

            # 展示审阅意见
            print(f"\n审阅分数：{review_result['score']}")
            print(f"问题数量：{len(review_result['issues'])}")

            if review_result["pass"]:
                # 展示给用户确认
                print("\n" + "=" * 40)
                print("最终正文：")
                print(polished)
                print("\n审阅意见已保存至 muse/ 目录。")
                print("\n确认通过？[y/n]（输入 n 可写自定义意见打回重写）")
                # 修复：用户拒绝也计入总轮次防止无限循环
                if round_count >= self.MAX_WRITING_ROUNDS:
                    print(f"已达最大轮次（{self.MAX_WRITING_ROUNDS}），强制通过。")
                    self.io.save_final(polished)
                    break
                if self._confirm(""):
                    self.io.save_final(polished)
                    break
                else:
                    user_feedback = self._input("请输入修改意见（可选，直接回车跳过）：")
                    self.issues = review_result["issues"]
                    if user_feedback:
                        self.issues.append({
                            "level": None,
                            "quote": "",
                            "description": user_feedback,
                            "suggestion": "",
                        })
                    self.io.next_round()
            else:
                # 自动打回重写
                if round_count >= self.MAX_WRITING_ROUNDS:
                    print(f"已达最大轮次（{self.MAX_WRITING_ROUNDS}），强制通过。")
                    self.io.save_final(polished)
                    break
                print(f"分数 {review_result['score']} < 85，自动打回重写。")
                self.issues = review_result["issues"]
                self.io.next_round()

    def _run_writer(self) -> str:
        """运行 Writer 创作正文

        v5.4: R1 走 wf.run()（全文创作）；R2+ 走 wf.run_revise()（手术刀编辑）。
        修改轮输出 edits JSON，后端 apply 到 draft 上，大幅减少 output token。
        """
        from tools.writing_workflow import WritingWorkflow
        from agent.skill import SkillRegistry

        skill_reg = SkillRegistry(self.skills_dir)

        # 有审阅意见 → 修改轮，使用专门的修改 skill
        is_revise = bool(self.issues)
        skill_name = "muse_writer_revise" if is_revise else "muse_writer"
        writer_skill_text = skill_reg.load_full_text(skill_name)

        wf = WritingWorkflow(
            llm=LLMClient(self.llm_config),
            workspace=self.workspace,
            writer_skill_text=writer_skill_text,
            cli=None,
        )

        last_chapter = self._get_last_chapter_full()

        # v5.3: 注入 style 记忆
        from tools.memory import get_memories_for_prompt
        memory_block = get_memories_for_prompt(self.workspace, ["style"], limit=5)

        if is_revise and self.current_draft:
            # v5.4: 手术刀修改轮 — 输出 edits JSON
            new_draft, changes = wf.run_revise(
                draft=self.current_draft,
                review_issues=self.issues,
                outline=self.outline,
                last_chapter=last_chapter,
                change_log=self.change_log if self.change_log else None,
                memory_block=memory_block,
            )
            self.change_log = changes  # 保存变更日志供审阅者参考
            self.io.save_session_log([{"role": "assistant", "content": new_draft}])
            step_name = f"writing_round_{self.io.round}"
            self._update_token_stats_from_wf(step_name, wf)
            self._save_token_stats()
            return new_draft
        else:
            # R1 或无草稿：全文创作模式
            result = wf.run(
                outline=self.outline,
                prior_knowledge=self.prior_knowledge,
                plot_summary=self.plot_summary,
                last_chapter=last_chapter,
                review_issues=self.issues if self.issues else None,
                previous_draft="",
                memory_block=memory_block,
            )
            self.change_log = []  # R1 无变更日志
            self.io.save_session_log([{"role": "assistant", "content": result}])
            step_name = f"writing_round_{self.io.round}"
            self._update_token_stats_from_wf(step_name, wf)
            self._save_token_stats()
            return result

    def _run_reviewer(self, draft: str, review_session: ReviewSession) -> dict:
        """运行 Reviewer 审阅正文（仅 report_issue / review_done / agent_output 工具）

        上下文：上一章全文 → 大纲 → 正文
        v5.4 R2+：额外注入修改记录 + 上轮审阅意见（增量审阅模式）。
        """
        agent = self._create_restricted_agent(
            skill_names=["muse_reviewer.skill.md"],
            allowed_tools=["agent_output", "report_issue", "review_done"],
        )
        # 覆盖角色定义：审阅Agent绝不能去写作，否则会跑去写下一章
        agent.system_prompt = agent.system_prompt.replace(
            "你是妙笔（Muse），一个专业的长篇小说写作辅助助手。",
            "你是妙笔审阅官（Muse Reviewer），你的唯一职责是审阅正文并报告问题。你绝不创作、绝不续写。",
        )
        agent.review_session = review_session
        last_chapter = self._get_last_chapter_full()
        context_parts = []
        if last_chapter:
            context_parts.append(f"## 上一章全文\n{last_chapter}")
        context_parts.append(f"## 大纲/草稿\n{self.outline}")
        context_parts.append(f"## 正文\n{draft}")

        # v5.4: R2+ 注入修改记录和上轮审阅意见（触发增量审阅模式）
        if review_session.previous_issues:
            # 修改记录（本轮 Writer 变更）
            if self.change_log:
                change_lines = "\n".join(f"- {c}" for c in self.change_log)
                context_parts.append(f"## 修改记录（本轮 Writer 变更）\n{change_lines}")

            # 上轮审阅意见
            prev_lines = []
            for idx, issue in enumerate(review_session.previous_issues, 1):
                level = issue.get("level", "?")
                desc = issue.get("description", "")
                sug = issue.get("suggestion", "")
                prev_lines.append(f"{idx}. [{level}] {desc} → {sug}")
            context_parts.append(
                f"## 上轮审阅意见（共{len(review_session.previous_issues)}条）\n"
                + "\n".join(prev_lines)
            )

        # v5.3: 审阅注入 style + correction 记忆
        from tools.memory import get_memories_for_prompt
        review_memory = get_memories_for_prompt(
            self.workspace, ["style", "correction"], limit=5)
        if review_memory:
            context_parts.append(review_memory)

        context = "\n\n".join(context_parts)
        messages = [{"role": "user", "content": context}]
        messages = agent_loop(agent, messages)
        self._stop_drain(agent)
        self.io.save_session_log(messages)
        step_name = f"review_round_{self.io.round}"
        self._update_token_stats_from_agent(step_name, agent)
        self._save_token_stats()
        return review_session.review_done()

    # ---- 辅助方法 ----

    def _get_last_chapter_full(self) -> str:
        """读取上一章全文（v5.3：使用锚定的 target_chapter - 1）"""
        from tools.chapter import read_chapters

        if self.target_chapter and self.target_chapter > 1:
            prev_num = self.target_chapter - 1
            text = read_chapters(self.workspace, str(prev_num))
            if "（不存在）" in text or text.startswith("错误"):
                return ""
            return text

        # 兼容旧行为：无锚定时取 DB 最后一章
        from tools.chapter import chapter_list
        import re
        raw = chapter_list(self.workspace)
        if raw in ("（尚无章节）", ""):
            return ""
        lines = raw.strip().splitlines()
        last_line = lines[-1]
        m = re.match(r"第(\d+)章", last_line)
        if not m:
            return ""
        last_num = int(m.group(1))
        text = read_chapters(self.workspace, str(last_num))
        if "（不存在）" in text or text.startswith("错误"):
            return ""
        return text

    def _create_agent(self, skill_names: list[str]) -> MuseAgent:
        """创建妙笔 Agent 实例，将 skill 文件内容注入 system prompt"""
        from core.events import EventBus
        import threading
        bus = EventBus()
        stop_event = threading.Event()

        # 轻量级消费线程：排干队列，避免事件累积；确认类事件自动放行
        def _drain():
            while not stop_event.is_set():
                evt = bus.get(timeout=0.2)
                if evt is None:
                    continue
                if evt.type == EventType.CONFIRM_REQUEST:
                    cid = evt.data.get("confirm_id", "")
                    if cid:
                        # 妙笔为无人值守流程且 MuseAgent 不发起确认：
                        # 任何意外确认请求默认拒绝（fail-safe，与 P1-18 一致）
                        bus.resolve_confirm(cid, {"action": "reject"})

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()

        agent = MuseAgent(
            config={"api": self.llm_config},
            workspace=self.workspace,
            skills_dir=self.skills_dir,
            bus=bus,
            chapter_ceiling=self.chapter_ceiling,
        )
        # 保存 drain 线程引用，供 agent_loop 结束后清理（P1-19：异常时统一清理）
        agent._drain_thread = drain_thread
        agent._drain_stop = stop_event
        self._agents.append(agent)
        # 将 skill 全文注入 system prompt（LLM 才能看到工作流指引）
        # SkillRegistry 使用 frontmatter 中的 name 字段做 key，不是文件名
        for name in skill_names:
            skill_key = name.replace(".skill.md", "")
            skill_text = agent.skills.load_full_text(skill_key)
            agent.system_prompt += "\n\n" + skill_text
        return agent

    def _create_restricted_agent(self, skill_names: list[str], allowed_tools: list[str]) -> MuseAgent:
        """创建工具受限的妙笔 Agent（写作/审阅阶段使用，防止 LLM 回去翻 wiki/章节）"""
        agent = self._create_agent(skill_names)
        # 先保存旧工具指南文本（在过滤 tool_defs 之前）
        old_tool_guide_text = "\n\n" + agent._build_tool_guide()
        # 只保留 allowed_tools 中的工具
        agent.tool_defs = [
            t for t in agent.tool_defs
            if t["function"]["name"] in allowed_tools
        ]
        # 重写 system prompt 末尾的工具指南，与实际 tool_defs 一致
        # 注意：不能简单 rsplit，因为 skill 文本在 tool guide 之后追加
        new_tool_guide_text = "\n\n" + agent._build_tool_guide()
        if old_tool_guide_text in agent.system_prompt:
            agent.system_prompt = agent.system_prompt.replace(old_tool_guide_text, new_tool_guide_text, 1)
        else:
            agent.system_prompt = agent.system_prompt.rstrip() + new_tool_guide_text
        return agent

    def _extract_last_text(self, messages: list) -> str:
        """从消息列表中提取最后一条 assistant 的文本回复"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"].strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _stop_drain(agent):
        """停止 Agent 的 drain 消费线程"""
        stop = getattr(agent, "_drain_stop", None)
        thread = getattr(agent, "_drain_thread", None)
        if stop:
            stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def _finish(self):
        print(f"\n妙笔任务完成！")
        print(f"输出目录：{self.io.task_dir}")
        # 显示 token 统计
        if self._token_stats:
            sep = "-" * 40
            print(f"\nToken 用量统计：")
            print(sep)
            for step_name, stats in self._token_stats.items():
                label = {
                    "knowledge_prep": "知识准备（先验知识）",
                    "plot_summary": "知识准备（前情提要）",
                }.get(step_name, step_name)
                print(f"  {label}: 输入={stats['input']}, 输出={stats['output']}, 总计={stats['total']}")
            print(sep)
            print(f"  总计: 输入={self._token_total['input']}, 输出={self._token_total['output']}, 总计={self._token_total['total']}")
