"""plot_workflow — 前情提要 Workflow（材料清单模式）"""

import re
from pathlib import Path

from api import LLMClient


class PlotWorkflow:
    """前情提要 Workflow — 参数校验 + 内容解析 + 材料清单"""

    def __init__(self, llm: LLMClient, workspace: Path, cli=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def _append_recent_chapters(self, chapters: list[int]) -> list[int]:
        """自动追加最近 3 章"""
        from tools.chapter import chapter_list
        raw = chapter_list(self.workspace)
        max_num = 0
        for line in raw.splitlines():
            m = re.match(r"第(\d+)章", line)
            if m:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num
        recent = list(range(max(1, max_num - 2), max_num + 1))
        all_chs = list(set(chapters + recent))
        return sorted(all_chs)

    def _build_material_list(self, only_contents, full_contents, chapter_contents):
        return {
            "plots": only_contents + full_contents,
            "chapters": chapter_contents,
        }

    def _material_to_review_text(self, material: dict) -> str:
        lines = ["📋 前情提要材料清单", ""]
        if material["plots"]:
            lines.append("【剧情卡片】")
            for p in material["plots"]:
                name = p.split("\n")[0].replace("## ", "").strip()
                lines.append(f"- {name}")
            lines.append("")
        if material["chapters"]:
            lines.append(f"【章节参考】（最近 3 章：{len(material['chapters'])} 章，后端自动注入）")
            lines.append("")
        lines.append("请确认以上清单：输入 y 确认，输入 n 修改。")
        return "\n".join(lines)

    def _resolve_plot_names(self, names: list[str], full: bool = False) -> tuple[list[str], list[str]]:
        """解析剧情卡片名称列表，返回 (成功内容列表, 失败名称列表)"""
        from tools.plot import read_plot, plot_list

        # 获取所有卡片名称
        all_plots_raw = plot_list(self.workspace, ended="all")
        known_names = set()
        if not (isinstance(all_plots_raw, str) and all_plots_raw.startswith("错误")):
            for line in all_plots_raw.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    # 格式: "- [[卡片名]] | chapters: ..."
                    m = re.match(r"-\s*\[\[(.+?)\]\]", line)
                    if m:
                        known_names.add(m.group(1))

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

    def _resolve_chapters(self, chapter_nums: list[int]) -> tuple[list[str], list[int]]:
        """解析章节号列表，返回 (成功内容列表, 失败章节号列表)"""
        from tools.chapter import chapter_list, read_chapters

        raw = chapter_list(self.workspace)
        if raw in ("（尚无章节）", ""):
            return [], chapter_nums

        # 获取最大章节号
        max_num = 0
        for line in raw.splitlines():
            m = re.match(r"第(\d+)章", line)
            if m:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num

        successes = []
        failures = []
        for num in chapter_nums:
            if num < 1 or num > max_num:
                failures.append(num)
                continue
            content = read_chapters(self.workspace, str(num))
            if isinstance(content, str) and content.startswith("错误"):
                failures.append(num)
            else:
                successes.append(f"## 第{num}章\n{content}")
        return successes, failures

    def validate_and_run(self, plot_only_yaml: list[str], plot_full: list[str],
                         chapters: list[int]) -> str:
        """校验参数并执行前情提要生成"""
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
            return f"错误：以下剧情卡片未找到，请检查并重试：{'、'.join(sorted(all_plot_fail))}"

        # 4. 存在检查（chapters）
        chapter_contents, chapter_fail = self._resolve_chapters(chapters)
        if chapter_fail:
            fail_str = "、".join(str(n) for n in sorted(chapter_fail))
            return f"错误：以下章节不存在，请检查并重试：{fail_str}"

        # 5. 自动注入最近 3 章
        chapters = self._append_recent_chapters(chapters)
        # Re-resolve chapters with the expanded list
        chapter_contents, chapter_fail = self._resolve_chapters(chapters)
        if chapter_fail:
            fail_str = "、".join(str(n) for n in sorted(chapter_fail))
            return f"错误：以下章节不存在，请检查并重试：{fail_str}"

        # 6. 构建材料清单
        material = self._build_material_list(only_contents, full_contents, chapter_contents)
        self._last_material = material

        # 7. 返回清单文本（不再调 LLM）
        return self._material_to_review_text(material)
