"""knowledge_workflow — 先验知识 Workflow（材料清单模式）

入参由 MuseAgent 通过 call_knowledge_workflow 工具提交，
Workflow 层负责参数校验、内容解析、构建材料清单供用户审阅。
"""

from pathlib import Path

from api import LLMClient


class KnowledgeWorkflow:
    """先验知识 Workflow — 参数校验 + 内容解析 + 材料清单"""

    def __init__(self, llm: LLMClient, workspace: Path, cli=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def _build_material_list(self, wiki_only_contents, wiki_full_contents, rule_contents):
        """构建材料清单数据结构"""
        return {
            "rules": rule_contents,
            "wiki_important": wiki_full_contents,
            "wiki_supplement": wiki_only_contents,
        }

    def _material_to_review_text(self, material: dict) -> str:
        """生成用户审阅用的清单文本"""
        lines = ["📋 先验知识材料清单", ""]
        if material["rules"]:
            lines.append("【规则文档】")
            for r in material["rules"]:
                name = r.split("\n")[0].replace("## ", "").strip()
                lines.append(f"- {name}")
            lines.append("")
        if material["wiki_important"]:
            lines.append("【重要 Wiki 词条（完整）】")
            for w in material["wiki_important"]:
                name = w.split("\n")[0].replace("## ", "").strip()
                lines.append(f"- {name}")
            lines.append("")
        if material["wiki_supplement"]:
            lines.append("【补充 Wiki 词条（概要）】")
            for w in material["wiki_supplement"]:
                name = w.split("\n")[0].replace("## ", "").strip()
                lines.append(f"- {name}")
            lines.append("")
        lines.append("请确认以上清单：输入 y 确认，输入 n 修改。")
        return "\n".join(lines)

    def _find_wiki(self, category: str, name: str) -> str | None:
        """在指定类别下查找 wiki 词条，返回完整内容或 None"""
        from tools.wiki import read_wiki
        result = read_wiki(self.workspace, category, name, yaml_only=False)
        if result.startswith("错误"):
            return None
        return result

    def _find_wiki_yaml(self, category: str, name: str) -> str | None:
        """查找 wiki 词条，只返回 frontmatter"""
        from tools.wiki import read_wiki
        result = read_wiki(self.workspace, category, name, yaml_only=True)
        if result.startswith("错误"):
            return None
        return result

    def _find_rule(self, name: str) -> str | None:
        """查找规则文档"""
        from tools import rules as rules_tools
        result = rules_tools.read_rule(self.workspace, name, yaml_only=False)
        if result.startswith("错误"):
            return None
        return result

    def _resolve_wiki_names(self, names: list[str], full: bool = False) -> tuple[list[str], list[str]]:
        """解析 wiki 名称列表，返回 (成功内容列表, 失败名称列表)

        直接扫描文件系统避免 wiki_list 分页导致的漏查问题。
        """
        from tools.wiki import _wiki_root
        root = _wiki_root(self.workspace)
        if not root.exists():
            return [], names

        # 直接扫描文件系统，构建 词条名 → 类别 的映射
        name_to_category = {}
        for cat_dir in sorted(root.iterdir()):
            if not cat_dir.is_dir():
                continue
            category = cat_dir.name
            for fp in cat_dir.glob("*.md"):
                if fp.name == "index.md":
                    continue
                name_to_category[fp.stem] = category

        successes = []
        failures = []
        for name in names:
            cat = name_to_category.get(name)
            if cat is None:
                failures.append(name)
                continue
            if full:
                content = self._find_wiki(cat, name)
            else:
                content = self._find_wiki_yaml(cat, name)
            if content is None:
                failures.append(name)
            else:
                successes.append(f"## {name}（{cat}）\n{content}")
        return successes, failures

    def _resolve_rule_names(self, names: list[str]) -> tuple[list[str], list[str]]:
        """解析规则文档名称列表"""
        successes = []
        failures = []
        for name in names:
            content = self._find_rule(name)
            if content is None:
                failures.append(name)
            else:
                successes.append(f"## {name}\n{content}")
        return successes, failures

    def validate_and_run(self, wiki_only_yaml: list[str], wiki_full: list[str],
                         rules: list[str]) -> str:
        """校验参数并执行先验知识生成

        Returns:
            成功时返回生成的先验知识文档
            失败时返回错误消息（LLM 可据此修正）
        """
        # 1. 去重检查
        wiki_set_only = set(wiki_only_yaml)
        wiki_set_full = set(wiki_full)
        dupes = wiki_set_only & wiki_set_full
        if dupes:
            return f"错误：以下词条同时出现在 wiki_only_yaml 和 wiki_full 中，请去重：{'、'.join(sorted(dupes))}"

        # 2. 上限检查
        if len(wiki_only_yaml) > 36:
            return f"错误：wiki_only_yaml 最多 36 个，当前传入了 {len(wiki_only_yaml)} 个"
        if len(wiki_full) > 18:
            return f"错误：wiki_full 最多 18 个，当前传入了 {len(wiki_full)} 个"

        # 3. 存在检查（wiki）
        wiki_only_contents, wiki_only_fail = self._resolve_wiki_names(wiki_only_yaml, full=False)
        wiki_full_contents, wiki_full_fail = self._resolve_wiki_names(wiki_full, full=True)
        all_failures = wiki_only_fail + wiki_full_fail
        if all_failures:
            return f"错误：以下词条未找到，请检查并重试：{'、'.join(sorted(all_failures))}"

        # 4. 存在检查（rules）
        rule_contents, rule_fail = self._resolve_rule_names(rules)
        if rule_fail:
            return f"错误：以下规则文档未找到，请检查并重试：{'、'.join(sorted(rule_fail))}"

        # 5. 构建材料清单
        material = self._build_material_list(wiki_only_contents, wiki_full_contents, rule_contents)
        self._last_material = material

        # 6. 返回清单文本（不再调 LLM）
        return self._material_to_review_text(material)
