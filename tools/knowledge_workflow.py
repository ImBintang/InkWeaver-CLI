"""knowledge_workflow — 先验知识 Workflow（纯 chat，无 tools）

入参由 MuseAgent 通过 call_knowledge_workflow 工具提交，
Workflow 层负责参数校验、内容解析、纯 chat 调用 LLM 压缩重写。
"""

from pathlib import Path

from api import LLMClient


class KnowledgeWorkflow:
    """先验知识 Workflow — 参数校验 + 内容解析 + 纯 chat 调用"""

    def __init__(self, llm: LLMClient, workspace: Path, cli=None):
        self.llm = llm
        self.workspace = workspace
        self.cli = cli
        self._last_usage = {}

    def _log(self, tag: str, text: str):
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

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

        v5：通过 proxy 从 DB 构建名称→类别映射。
        """
        from tools.editor import _get_proxy
        proxy = _get_proxy(self.workspace)

        # 从 DB 构建 词条名 → 类别 的映射
        name_to_category = {}
        cats = proxy.list_categories("wiki")
        for cat in cats:
            mains = proxy._db.wiki_list_main(cat["id"])
            for m in mains:
                name_to_category[m["name"]] = cat["name"]

        # 合并缓存中新增的
        for (dt, _), doc in proxy._cache.items():
            if dt == "wiki" and not doc.is_deleted and doc.category:
                name_to_category[doc.name] = doc.category

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

        # 5. 拼接消息内容
        sections = ["# 参考材料"]
        if wiki_only_contents:
            sections.append("## Wiki 词条（概要）\n" + "\n\n".join(wiki_only_contents))
        if wiki_full_contents:
            sections.append("## Wiki 词条（完整）\n" + "\n\n".join(wiki_full_contents))
        if rule_contents:
            sections.append("## 规则文档\n" + "\n\n".join(rule_contents))
        sections.append(
            "\n# 任务\n"
            "请根据以上参考材料，撰写一份完整的先验知识文档。\n"
            "要求：\n"
            "- 包括世界观基本规则、当前活跃角色/势力状态、关键物品/地点信息\n"
            "- 语言流畅、结构清晰\n"
            "- 不超过 10000 字\n"
            "- 不要出现'根据大纲''基于以上材料'等字眼\n"
            "- 输出格式如下：\n"
            "\n"
            "# 世界规则\n"
            "\n"
            "# 人物\n"
            "## 主角\n"
            "## 重要人物\n"
            "## 次要人物\n"
            "\n"
            "# 势力\n"
            "\n"
            "# 地图\n"
            "\n"
            "# 设定\n"
            "## 法宝……（根据类别名）"
        )

        user_content = "\n\n".join(sections)

        system_prompt = (
            "你是先验知识编写助手。你的任务是根据提供的参考材料（Wiki 词条和规则文档），"
            "撰写一份结构清晰的先验知识文档，供小说写作参考。"
            "注意：你不知道大纲内容，只基于提供的材料进行编写。"
        )

        self._log("KNOWLEDGE_WF_START", f"wiki_only={len(wiki_only_yaml)}, wiki_full={len(wiki_full)}, rules={len(rules)}")

        # 6. 纯 chat 调用
        messages = [{"role": "user", "content": user_content}]
        response = self.llm.chat(
            messages=messages,
            system_prompt=system_prompt,
            tools=None,
        )

        if "usage" in response:
            self._last_usage = response["usage"]

        result = response.get("content", "").strip()
        self._log("KNOWLEDGE_WF_END", result[:200])
        return result
