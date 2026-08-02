"""plot_workflow — 前情提要 Workflow（纯 chat，无 tools）

入参由 MuseAgent 通过 call_plot_workflow 工具提交，
Workflow 层负责参数校验、内容解析、纯 chat 调用 LLM 压缩重写。
注意：不读取章节正文，仅依赖剧情卡片数据。
"""

import re
from pathlib import Path

from api import LLMClient


class PlotWorkflow:
    """前情提要 Workflow — 参数校验 + 内容解析 + 纯 chat 调用"""

    def __init__(self, llm: LLMClient, workspace: Path, cli=None, bus=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        # GUI 模式注入事件总线：撰写过程逐 token 实时推送前端（source=muse）
        self.bus = bus
        self._last_usage = {}

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def _resolve_plot_names(self, names: list[str], full: bool = False) -> tuple[list[str], list[str]]:
        """解析剧情卡片名称列表，返回 (成功内容列表, 失败名称列表)

        P1-23：不再依赖 plot_list 文本输出（默认第一页 20 张限制），
        直接经 proxy 读取全部卡片名称（DB + 缓存合并），中后期作品不再误报未找到。
        """
        from tools.plot import read_plot
        from tools.editor import _get_proxy

        proxy = _get_proxy(self.workspace)
        known_names = set()
        try:
            for m in proxy._db.plot_list_main():
                known_names.add(m["name"])
        except Exception as e:
            return [], [f"（读取剧情卡片列表失败：{e}）"]
        # 合并缓存中的新增/修改卡片（任务内新建尚未落库）
        for v in proxy._cache.values():
            if v.doc_type == "plot" and not v.is_deleted:
                known_names.add(v.name)

        successes = []
        failures = []
        for name in names:
            if name not in known_names:
                failures.append(name)
                continue
            content = read_plot(self.workspace, name, yaml_only=not full)
            if isinstance(content, str) and content.startswith("错误"):
                failures.append(name)
            else:
                successes.append(f"## {name}\n{content}")
        return successes, failures

    def validate_and_run(self, plot_only_yaml: list[str], plot_full: list[str]) -> str:
        """校验参数并执行前情提要生成（不读章节正文）"""
        # 1. 去重检查
        set_only = set(plot_only_yaml)
        set_full = set(plot_full)
        dupes = set_only & set_full
        if dupes:
            return f"错误：以下剧情卡片同时出现在 plot_only_yaml 和 plot_full 中，请去重：{'、'.join(sorted(dupes))}"

        # 2. 上限检查
        if len(plot_only_yaml) > 24:
            return f"错误：plot_only_yaml 最多 24 个，当前传入了 {len(plot_only_yaml)} 个"
        if len(plot_full) > 12:
            return f"错误：plot_full 最多 12 个，当前传入了 {len(plot_full)} 个"

        # 3. 存在检查（plot）
        only_contents, only_fail = self._resolve_plot_names(plot_only_yaml, full=False)
        full_contents, full_fail = self._resolve_plot_names(plot_full, full=True)
        all_plot_fail = only_fail + full_fail
        if all_plot_fail:
            # 区分「读取失败」与「确实不存在」：失败项自带括号原因，
            # 不能统一误报为“未找到”，错误必须原样呈现给消费端（LLM）
            has_err = any(f.startswith("（") for f in all_plot_fail)
            verb = "读取失败或未找到" if has_err else "未找到"
            return f"错误：以下剧情卡片{verb}，请检查后重试：{'、'.join(sorted(all_plot_fail))}"

        # 4. 拼接消息
        sections = ["# 参考材料"]
        if only_contents:
            sections.append("## 剧情卡片（概要）\n" + "\n\n".join(only_contents))
        if full_contents:
            sections.append("## 剧情卡片（完整）\n" + "\n\n".join(full_contents))
        sections.append(
            "\n# 任务\n"
            "请根据以上参考材料，按时间顺序梳理一份完整的前情提要。\n"
            "要求：\n"
            "- 按时间顺序梳理\n"
            "- 与大纲相关性强的剧情优先\n"
            "- 不超过 10000 字\n"
            "- 不要出现'根据大纲''基于以上材料'等字眼\n"
            "- 输出格式如下：\n"
            "\n"
            "# 当前事件\n"
            "# 背景事件"
        )

        user_content = "\n\n".join(sections)

        system_prompt = (
            "你是前情提要编写助手。你的任务是根据提供的剧情卡片，"
            "按时间顺序梳理一份完整的前情提要，供小说写作参考。"
            "注意：你不知道大纲内容，只基于提供的材料进行编写。"
        )

        self._log("PLOT_WF_START", f"only_yaml={len(plot_only_yaml)}, full={len(plot_full)}")

        # 5. 调用 LLM：GUI 模式（注入 bus）流式推送 token/思考，CLI 模式一次性返回
        from core.events import EventType

        messages = [{"role": "user", "content": user_content}]
        if self.bus is not None:
            result_parts = []
            for chunk in self.llm.chat_stream(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
            ):
                ctype = chunk.get("type")
                try:
                    if ctype == "token":
                        result_parts.append(chunk.get("text", ""))
                        self.bus.emit(EventType.TOKEN, {"text": chunk.get("text", "")}, source="muse")
                    elif ctype == "reasoning":
                        self.bus.emit(EventType.REASONING, {"text": chunk.get("text", "")}, source="muse")
                    elif ctype == "done" and chunk.get("usage"):
                        self._last_usage = chunk["usage"]
                except Exception:
                    pass  # 事件上报失败不阻断写作主流程
            result = "".join(result_parts).strip()
        else:
            response = self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=None,
            )
            if "usage" in response:
                self._last_usage = response["usage"]
            result = response.get("content", "").strip()
        self._log("PLOT_WF_END", result[:200])
        return result
