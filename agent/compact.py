"""上下文管理：Token 估算、压缩追踪、/context 报告（参照 s06）"""

import json
from pathlib import Path

import tiktoken


# 使用 cl100k_base 编码（OpenAI GPT-4 标准，中文估算相对准确）
_ENCODER = tiktoken.get_encoding("cl100k_base")


def estimate_tokens(messages: list) -> int:
    """使用 tiktoken 估算 token 数"""
    text = json.dumps(messages, default=str, ensure_ascii=False)
    return len(_ENCODER.encode(text))


class ContextManager:
    """追踪对话上下文中的章节和技能，提供 /context 报告"""

    def __init__(self):
        self.has_compacted = False
        self.tracked_chapters: list[tuple[str, str]] = []  # [(id, title)]
        self.tracked_skills: list[str] = []
        self.tracked_wiki: list[str] = []      # 新增
        self.tracked_rules: list[str] = []     # 新增
        self.tracked_plots: list[str] = []     # 新增

    def track_chapter(self, chapter_ids: list[str], titles: list[str]):
        """记录已读章节"""
        for cid, title in zip(chapter_ids, titles):
            pair = (cid, title)
            if pair not in self.tracked_chapters:
                self.tracked_chapters.append(pair)

    def track_skill(self, skill_name: str):
        """记录已加载技能"""
        if skill_name not in self.tracked_skills:
            self.tracked_skills.append(skill_name)

    def track_entity(self, entity_type: str, names: list[str]):
        """追踪已引用的实体
        Args:
            entity_type: "wiki" / "rules" / "plots"
            names: 实体名称列表
        """
        target = {
            "wiki": self.tracked_wiki,
            "rules": self.tracked_rules,
            "plot": self.tracked_plots,  # 兼容旧调用方
            "plots": self.tracked_plots,
        }.get(entity_type)
        if target is None:
            return
        for name in names:
            if name and name not in target:
                target.append(name)

    def query_context(self, entity_type: str = "all") -> str:
        """查询上下文中追踪的实体名称列表"""
        type_map = {
            "wiki": ("Wiki 词条", self.tracked_wiki),
            "rules": ("规则文档", self.tracked_rules),
            "plots": ("剧情卡片", self.tracked_plots),
        }
        lines = ["# 上下文实体查询"]
        if entity_type == "all":
            target_items = type_map.items()
        else:
            target_items = [(t, type_map[t]) for t in entity_type.split(",") if t in type_map]
        for key, (label, items) in target_items:
            if not items:
                lines.append(f"\n## {label}\n（无）")
            else:
                lines.append(f"\n## {label}\n" + "、".join(items))
        return "\n".join(lines)

    def context_report(self, messages: list) -> str:
        """/context 输出"""
        tokens = estimate_tokens(messages)

        lines = [
            f"Token 总量：约 {tokens}",
        ]

        if self.tracked_chapters:
            chapters_str = "，".join(t for _, t in self.tracked_chapters)
            lines.append(f"已有章节：{chapters_str}")
        else:
            lines.append("已有章节：无")

        if self.tracked_skills:
            lines.append(f"已有技能：{', '.join(self.tracked_skills)}")
        else:
            lines.append("已有技能：无")

        # 新增：实体追踪
        if self.tracked_wiki:
            lines.append(f"上下文 Wiki：{'、'.join(self.tracked_wiki)}")
        else:
            lines.append("上下文 Wiki：无")
        if self.tracked_rules:
            lines.append(f"上下文规则：{'、'.join(self.tracked_rules)}")
        else:
            lines.append("上下文规则：无")
        if self.tracked_plots:
            lines.append(f"上下文剧情：{'、'.join(self.tracked_plots)}")
        else:
            lines.append("上下文剧情：无")

        if self.has_compacted:
            lines.append("上下文状态：已压缩过")

        return "\n".join(lines)

    def mark_compacted(self):
        self.has_compacted = True

    def compact_messages(self, messages: list, llm=None) -> list:
        """执行三层压缩"""
        if llm is None:
            return messages
        wf = CompactWorkflow(llm)
        result = wf.compress(messages)
        self.mark_compacted()
        return result


class CompactWorkflow:
    """上下文压缩 Workflow — 纯 chat，无 tools

    三层压缩策略：
    1. 保留 system prompt + skill 全文
    2. 保留最近 2 轮对话（user + assistant + tool_calls）
    3. 其余消息 → LLM 生成连续摘要
    """

    def __init__(self, llm):
        self.llm = llm

    def _build_summary_prompt(self, history_messages: list[dict]) -> str:
        """构建压缩 prompt"""
        history_text = ""
        for msg in history_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = str(content)[:200]
            elif isinstance(content, str):
                content = content[:500]
            else:
                content = str(content)[:200]
            history_text += f"\n[{role}]: {content}"

        return (
            "你是一个上下文压缩助手。请阅读以下历史对话，生成一份连贯、完整的摘要。\n"
            "要求：\n"
            "- 保留所有重要的决策、结论和已执行的操作\n"
            "- 保留所有引用的 wiki 词条名、规则名、剧情卡片名、章节号\n"
            "- 省略工具调用的技术细节和中间输出\n"
            "- 用简洁的中文，不超过 1000 字\n"
            "- 不要添加任何原文中没有的信息\n"
            "\n"
            "历史对话：\n" + history_text
        )

    def compress(self, messages: list) -> list:
        """三层压缩：保留 system + 摘要 + 最近 2 轮"""
        if len(messages) <= 4:
            return messages  # 消息太少，不压缩

        # 1. 提取 system prompt
        system_msg = None
        system_idx = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                system_msg = msg
                system_idx = i
                break

        if system_msg is None:
            return messages  # 没有 system prompt，不压缩

        # 2. 分层：保留最近 4 条（≈ 2 轮 user+assistant）+ 其余历史
        recent = messages[-4:]
        history_start = system_idx + 1 if system_idx is not None else 0
        history_end = max(history_start, len(messages) - 4)
        history = messages[history_start:history_end]

        # 3. 压缩历史
        if len(history) <= 2:
            compressed = self._format_as_summary(history)
        else:
            summary_prompt = self._build_summary_prompt(history)
            resp = self.llm.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                system_prompt="你是一个上下文压缩助手。",
                tools=None,
            )
            compressed = resp.get("content", "").strip()
            if not compressed:
                compressed = self._format_as_summary(history)

        # 4. 重组
        result = [system_msg]
        if compressed:
            result.append({"role": "system", "content": f"【历史摘要】\n{compressed}"})
        result.extend(recent)
        return result

    @staticmethod
    def _format_as_summary(messages: list[dict]) -> str:
        """简单拼接格式（LLM 不可用时的 fallback）"""
        parts = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = str(content)[:300]
            parts.append(f"[{role}]: {str(content)[:300]}")
        return "\n".join(parts)


# ─── v4.0 PersistCache + Micro-compact ─────────────────────────────

PERSIST_THRESHOLD = 5000
PERSIST_ALWAYS = {
    "new_wiki", "edit_wiki", "batch_create_wiki", "batch_edit_wiki",
    "new_rule", "edit_rule", "new_plot", "edit_plot",
    "create_doc", "edit_doc", "edit_doc_text",
    "lint_report",
}

# 读取类工具的结果必须完整保留，不能被 PersistCache 吞掉
PERSIST_NEVER = {"read_chapters", "read_wiki", "read_plot"}


class PersistCache:
    """统一缓存文件管理 — 单文件，追加模式

    缓存路径: {workspace}/session/compact_cache.json
    用于持久化大工具输出，避免上下文膨胀。
    """

    def __init__(self, workspace: Path):
        self.cache_path = workspace / "session" / "compact_cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self.cache_path.write_text("{}", encoding="utf-8")

    def save(self, tool_call_id: str, data: dict):
        """保存数据到缓存 JSON 文件"""
        # 确保文件存在（可能被外部删除）
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self.cache_path.write_text("{}", encoding="utf-8")
        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        cache[tool_call_id] = data
        self.cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, tool_call_id: str) -> dict | None:
        """从缓存 JSON 文件加载指定条目"""
        if not self.cache_path.exists():
            return None
        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        return cache.get(tool_call_id)

    def get_preview(self, tool_call_id: str) -> str:
        """获取缓存条目的预览文本"""
        data = self.load(tool_call_id)
        if not data:
            return "(缓存未找到)"
        return data.get("result_preview", "(无预览)")

    def should_persist(self, tool_name: str, result: str) -> bool:
        """判断工具结果是否需要 persist"""
        if tool_name in PERSIST_NEVER:
            return False
        if tool_name in PERSIST_ALWAYS:
            return True
        return len(result) > PERSIST_THRESHOLD

    def persist_result(self, tool_name: str, params: dict, result: str,
                       tool_call_id: str) -> str:
        """写入缓存并返回短文本占位符"""
        self.save(tool_call_id, {
            "tool": tool_name,
            "params": params,
            "result_preview": result[:200],
            "full_output": result,
        })
        # 构建友好的占位符
        name = params.get("name", params.get("category", ""))
        items = params.get("items", [])
        if items:
            return f"[{tool_name} 已执行 ({len(items)} items)，结果已缓存 session/compact_cache.json]"
        if name:
            return f"[{tool_name} 已执行 ({name})，结果已缓存 session/compact_cache.json]"
        return f"[{tool_name} 已执行，结果已缓存 session/compact_cache.json]"


def micro_compact(messages: list, keep_recent: int = 5) -> list:
    """压缩超过 keep_recent 轮的旧 tool_result

    在每轮 agent_loop 开始前调用，将旧 tool 消息的 content 替换为占位符。
    - 短结果（<=200 chars）不压缩
    - read_chapters 的结果不压缩（章节原文需要完整）
    - 非字符串 content 跳过
    """
    tool_indices = [i for i, msg in enumerate(messages)
                    if msg.get("role") == "tool"]
    if len(tool_indices) <= keep_recent:
        return messages

    READ_KEEP = {"read_chapters", "read_wiki", "read_plot"}

    for idx in tool_indices[:-keep_recent]:
        content = messages[idx].get("content", "")
        if not isinstance(content, str) or len(content) <= 200:
            continue
        # 读取类工具的结果必须完整保留，压缩会导致 LLM 反复重读
        if messages[idx].get("tool_name") in READ_KEEP:
            continue
        messages[idx]["content"] = (
            "[旧工具结果已压缩，使用 tools_log_check 查询详情]"
        )
    return messages
