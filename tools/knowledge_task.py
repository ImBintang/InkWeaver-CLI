"""knowledge_task 工具 + KnowledgeSubagent 运行逻辑"""

import json
from pathlib import Path

from api import LLMClient
from agent.loop import agent_loop
from tools.shared_subagent_tools import build_shared_subagent_tools


# Subagent 可用的工具列表
SUBAGENT_TOOLS = [
    "read_chapters",
    "wiki_list",
    "read_wiki",
    "new_wiki",
    "edit_wiki",
    "read_memory",
    "check_wiki",
    "read_index",
    "query_relations",
    "agent_output",
]


class KnowledgeSubagent:
    """知识提取子智能体 — 按类别执行知识提取

    使用 fresh messages=[] 实现上下文隔离，共享文件系统。
    支持审核修复模式：review_notes 提供具体的修复建议。
    """

    MAX_TURNS = 30

    def __init__(self, llm: LLMClient, workspace: Path,
                 category: str, chapters: str, entries: list,
                 task_type: str = "new", cli=None,
                 review_notes: str = ""):
        """
        Args:
            llm: LLM 客户端实例
            workspace: 工作区路径
            category: 类别名（如 "人物"）
            chapters: 章节范围表达式（如 "1-5"）
            entries: 目标词条列表（如 ["张三", "李四"]）
            task_type: "new" 或 "update"
            cli: CLI 实例（用于日志记录）
            review_notes: 审核修复建议（由 review subagent 提供，可选）
        """
        self.llm = llm
        self.workspace = workspace
        self.category = category
        self.chapters = chapters
        self.entries = entries
        self.task_type = task_type
        self.cli = cli
        self.review_notes = review_notes
        self.messages: list = []  # fresh，上下文隔离
        self.tool_defs = build_shared_subagent_tools()
        self._input_tokens = 0
        self._output_tokens = 0

    def _build_system_prompt(self) -> str:
        """构建知识提取专用 system prompt"""
        from tools.category import read_index

        # 读取类别写作规范
        category_guide = read_index(self.workspace, self.category)
        if category_guide.startswith("错误"):
            category_guide = "（暂无写作规范）"

        entries_str = "、".join(self.entries)
        task_desc = "新建以下词条" if self.task_type == "new" else "更新以下词条"

        # 如果存在审核修复建议，追加到 prompt
        review_section = ""
        if self.review_notes:
            review_section = (
                f"\n"
                f"# 审核修复任务\n"
                f"**这不是一次全新的知识提取，而是根据审核意见修复现有词条。**\n"
                f"\n"
                f"## 审核意见/修复建议\n"
                f"{self.review_notes}\n"
                f"\n"
                f"请严格按照上述审核意见进行修复。"
            )

        return (
            f"你是知识提取子智能体，负责从小说章节中提取知识并写入 Wiki。\n"
            f"当前工作区：{self.workspace.name}\n"
            f"当前目录：{self.workspace}\n"
            f"\n"
            f"# 当前任务\n"
            f"类别：{self.category}\n"
            f"涉及章节：第 {self.chapters} 章\n"
            f"目标词条：{entries_str}\n"
            f"操作类型：{task_desc}\n"
            f"{review_section}\n"
            f"\n"
            f"# 类别写作规范\n"
            f"{category_guide}\n"
            f"\n"
            f"# 工作流程\n"
            f"1. **先用 Wiki 做 RAG**：使用 wiki_list 查看该类别下已有词条\n"
            f"2. 使用 read_wiki（yaml_only=false）读取已有词条的**完整内容**（含正文）\n"
            f"3. **必须使用 read_chapters 读取本次涉及的新章节原文**，获取新增信息\n"
            f"4. 写入/更新词条（根据改动范围选择最省 token 的方式）：\n"
            f"   - **改正文中一句话/一个词** → 用 `edit_doc_text`，只需说「把 A 改成 B」，不用传整个正文\n"
            f"   - **改 [[wikilink]] 指向** → 用 `edit_doc_wikilink`，只需说「把指向 X 的链接改成 Y」\n"
            f"   - **取消 [[wikilink]]**（不需要建词条的概念，如境界名、通用物品）→ 用 `edit_doc_wikilink(mode=\"unlink\")`，只需说「取消 [[X]] 的链接」，[[X]]→X / [[X|别名]]→别名\n"
            f"   - **大幅重写/新建** → 用 `new_wiki` 或 `edit_wiki(content=新全文)`\n"
            f"5. 词条正文使用 [[wikilink]] 格式建立交叉引用\n"
            f"\n"
            f"# 规则\n"
            f"- **禁止跳过 wiki 直接全文阅读章节**，优先用 wiki_list + read_wiki 获取已有信息\n"
            f"- new_wiki 的 content 参数为必填！必须提供正文内容，不能为空\n"
            f"- 本 subagent 只负责 wiki 词条创建，不处理规则文档（rules/ 由主 agent 管理）\n"
            f"- description 字段放客观介绍（静态信息）\n"
            f"- state 字段放近期事件带来的变化（动态信息）\n"
            f"- **人物/势力类词条必须包含 state 字段**（见类别 index.md 定义）\n"
            f"- 设定图鉴类不需要 state 字段\n"
            f"- 正文中的 [[wikilink]] 指向其他词条名\n"
            f"- 所有操作完成后，调用 agent_output 输出完整操作摘要（不要自己用文本输出，使用 agent_output 工具）\n"
            f"\n"
            f"# 更新操作特别规则（task_type=update 时）\n"
            f"- **只更新 description/state 不算完成更新**，必须同时更新正文内容\n"
            f"- 根据新章节的信息，更新正文中对应的章节：\n"
            f"  - 修为/境界变了 → 更新「实力等级」\n"
            f"  - 获得/失去物品 → 更新「持有物品」\n"
            f"  - 新增人际关系 → 更新「情感与关系」\n"
            f"  - 新增重要事件 → 更新相关章节\n"
            f"- **正文局部修改优先用 `edit_doc_text`**：只需改一句话时，不要传整个正文\n"
            f"- **断链修复优先用 `edit_doc_wikilink`**：只需改 wikilink 目标时，不要翻整篇正文\n"
            f"- **不需要建词条的断链（如境界名、通用物品等被 rules/ 覆盖的概念）**：用 `edit_doc_wikilink(mode=\"unlink\")` 取消链接，不要新建词条\n"
            f"- 只有**大幅重写正文**时才用 `edit_wiki(content=新全文)`\n"
            f"- 正文中已被新信息覆盖的旧内容可以删除或精简（例如「淬体境一重」→「淬体境六重」）"
        )

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        from tools.chapter import read_chapters, keywords_stat, chapter_list
        from tools.wiki import wiki_list, read_wiki, new_wiki, edit_wiki, check_wiki
        from tools.wiki import edit_wiki_text, edit_wiki_wikilink
        from tools.memory import read_memory
        from tools.category import read_index
        from tools.relation import query_relations

        # agent_output 特殊处理：透传输出内容并记录日志
        if name == "agent_output":
            text = args.get("text", "")
            self._log("SUBAGENT_OUTPUT", text)
            return "(已结束)"

        dispatch = {
            "read_chapters": lambda **kw: read_chapters(self.workspace, **kw),
            "wiki_list": lambda **kw: wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: read_wiki(self.workspace, **kw),
            "new_wiki": lambda **kw: new_wiki(
                self.workspace, **{**kw, "updated": kw.get("updated") or self._get_max_chapter()}
            ),
            "edit_wiki": lambda **kw: edit_wiki(
                self.workspace, **{**kw, "updated": kw.get("updated") or self._get_max_chapter()}
            ),
            "edit_wiki_text": lambda **kw: edit_wiki_text(self.workspace, **kw),
            "edit_wiki_wikilink": lambda **kw: edit_wiki_wikilink(self.workspace, **kw),
            # 统一工具名（subagent 工具定义来自 shared_subagent_tools.py，使用统一名）
            "edit_doc_text": lambda **kw: _edit_doc_text_fallback(self.workspace, **kw),
            "edit_doc_wikilink": lambda **kw: _edit_doc_wikilink_fallback(self.workspace, **kw),
            "read_memory": lambda **kw: read_memory(self.workspace, **kw),
            "check_wiki": lambda **kw: check_wiki(self.workspace, **kw),
            "read_index": lambda **kw: read_index(self.workspace, **kw),
            "query_relations": lambda **kw: query_relations(self.workspace, **kw),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            return handler(**args)
        except Exception as e:
            return f"错误：{e}"

    def _get_max_chapter(self) -> int:
        """从 chapters 范围中获取最大章节号"""
        from tools.chapter import parse_chapter_spec
        nums = parse_chapter_spec(self.chapters)
        return max(nums) if nums else 0

    def _log(self, tag: str, text: str):
        """记录日志到 session 日志文件"""
        if self.cli and self.cli.logger:
            self.cli.logger.write(tag, text)

    def run(self) -> str:
        """运行 subagent，返回操作摘要"""
        system_prompt = self._build_system_prompt()

        self._log("SUBAGENT_START",
                  f"类别={self.category}, 章节={self.chapters}, "
                  f"词条={','.join(self.entries)}, 类型={self.task_type}")

        # 构建初始 user 消息
        entries_str = "、".join(self.entries)
        task_desc = "新建以下词条" if self.task_type == "new" else "更新以下词条"
        initial_msg = (
            f"请执行知识提取任务：\n"
            f"- 类别：{self.category}\n"
            f"- 章节：第 {self.chapters} 章\n"
            f"- 词条：{entries_str}\n"
            f"- 操作：{task_desc}\n\n"
            f"请开始工作。"
        )

        self.messages.append({"role": "user", "content": initial_msg})

        # 运行 agent 循环
        for turn in range(self.MAX_TURNS):
            response = self.llm.chat(
                messages=self.messages,
                system_prompt=system_prompt,
                tools=self.tool_defs,
            )

            # 构建 assistant 消息
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = response["tool_calls"]
            self.messages.append(assistant_msg)

            # 记录 token 用量
            if "usage" in response:
                usage = response["usage"]
                pt = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
                ct = usage.get("completion_tokens") or usage.get("output_tokens", 0)
                self._input_tokens += pt
                self._output_tokens += ct
                self._log("SUBAGENT_TOKEN",
                          f"轮次={turn+1}, input={pt}, output={ct}, total={pt+ct} | "
                          f"累计: input={self._input_tokens}, output={self._output_tokens}")

            # 检查是否完成
            if response["stop_reason"] != "tool_use":
                # 记录最终输出
                final_content = response.get("content", "")
                if final_content:
                    self._log("SUBAGENT_OUTPUT", final_content)
                break

            # 处理 tool_calls
            tool_results = []
            for tc in response["tool_calls"]:
                tc_id = tc["id"]
                func = tc["function"]
                tool_name = func["name"]

                try:
                    tool_input = json.loads(func["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tool_input = {}

                # 记录工具调用（agent_output 已单独记录，不再重复）
                if tool_name != "agent_output":
                    brief_param = str(list(tool_input.values())[0]) if tool_input else ""
                    self._log("SUBAGENT_TOOL",
                              f"{tool_name}({brief_param})")

                result = self.dispatch_tool(tool_name, tool_input)

                # 记录工具结果（agent_output 的结果是 "(已结束)"，不需要截断）
                if tool_name == "agent_output":
                    pass  # agent_output 的内容已在 dispatch 中通过 _log 记录
                else:
                    result_preview = result[:120].replace("\n", " ")
                    self._log("SUBAGENT_RESULT", result_preview)

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result[:5000],  # 限制工具结果长度
                })

            self.messages.extend(tool_results)

        # 提取最终摘要
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                summary = msg["content"].strip()
                self._log("SUBAGENT_END", summary[:300])
                return summary

        self._log("SUBAGENT_END", "（subagent 未返回摘要）")
        return "（subagent 未返回摘要）"


# ── 统一工具回退路由 ──────────────────────────────────────────────────────
# subagent 的 tool_defs 来自 shared_subagent_tools.py（统一名 edit_doc_text/wikilink），
# dispatch 需要对应的处理函数。


def _edit_doc_text_fallback(workspace: Path, **kw) -> str:
    """回退路由：统一 edit_doc_text → wiki/plot 具体实现"""
    doc_type = kw.pop("doc_type", "wiki")
    if doc_type == "wiki":
        from tools.wiki import edit_wiki_text
        return edit_wiki_text(workspace, **kw)
    elif doc_type == "plot":
        from tools.plot import edit_plot_text
        return edit_plot_text(workspace, **kw)
    return f"错误：不支持的 doc_type「{doc_type}」"


def _edit_doc_wikilink_fallback(workspace: Path, **kw) -> str:
    """回退路由：统一 edit_doc_wikilink → wiki/plot 具体实现"""
    doc_type = kw.pop("doc_type", "wiki")
    if doc_type == "wiki":
        from tools.wiki import edit_wiki_wikilink
        return edit_wiki_wikilink(workspace, **kw)
    elif doc_type == "plot":
        from tools.plot import edit_plot_wikilink
        return edit_plot_wikilink(workspace, **kw)
    return f"错误：不支持的 doc_type「{doc_type}」"


def run_knowledge_task(llm: LLMClient, workspace: Path,
                      category: str, chapters: str,
                      entries: list, task_type: str = "new",
                      cli=None, review_notes: str = "",
                      token_callback=None) -> str:
    """便捷函数：创建并运行 KnowledgeSubagent

    Args:
        llm: LLM 客户端实例
        workspace: 工作区路径
        category: 类别名
        chapters: 章节范围表达式
        entries: 目标词条列表
        task_type: "new" 或 "update"
        cli: CLI 实例
        review_notes: 审核修复建议（由审核 subagent 提供，可选）
    """
    subagent = KnowledgeSubagent(
        llm=llm,
        workspace=workspace,
        category=category,
        chapters=chapters,
        entries=entries,
        task_type=task_type,
        cli=cli,
        review_notes=review_notes,
    )
    result = subagent.run()

    # 将 subagent 的 token 用量回调给主 agent
    if token_callback and (subagent._input_tokens or subagent._output_tokens):
        token_callback(subagent._input_tokens, subagent._output_tokens)

    # 记录 extraction 到 log.json
    try:
        from tools.diff import record_extraction
        from tools.chapter import parse_chapter_spec
        chapter_nums = parse_chapter_spec(chapters)
        chapter_files = [f"c{n:03d}.md" for n in chapter_nums]
        if task_type == "new":
            record_extraction(workspace, chapter_files, entries, [])
        else:
            record_extraction(workspace, chapter_files, [], entries)
    except Exception:
        pass  # log 记录失败不影响主流程

    return result
