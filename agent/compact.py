"""上下文管理：Token 估算、压缩追踪、/context 报告（参照 s06）"""

import json
import os
import threading
from pathlib import Path

# ─── tiktoken 词表缓存策略 ─────────────────────────────────────────
# 首次 get_encoding 需联网下载 BPE 词表，其内部 requests.get 无超时，
# 网络异常时会无限阻塞。因此：
# 1. 缓存目录固化到项目内 .tiktoken_cache/：首次下载成功后永久离线复用，
#    之后每次启动零网络、零等待（tiktoken 按 sha1(URL) 命中缓存即直读）；
# 2. 模块导入期不初始化（避免阻塞传导到 server 启动路径），首次调用时
#    在子线程中带超时加载，失败降级为字符粗估（P1-11 原则）。
_TIKTOKEN_CACHE_DIR = Path(__file__).resolve().parent.parent / ".tiktoken_cache"
_TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
_TIKTOKEN_TIMEOUT = 10.0  # 秒；仅首次无缓存下载时最多等待时长

_ENCODER = None
_ENCODER_FAILED = False  # P1-42：已尝试加载但失败（离线/超时），不再重复阻塞式重试
_ENCODER_LOCK = threading.Lock()


def _load_encoder():
    """在子线程中执行：优先读项目内缓存（纯离线），无缓存时联网下载一次

    下载成功后 tiktoken 自动写入 TIKTOKEN_CACHE_DIR（文件名 = sha1(URL)），
    此后所有启动直接从缓存读取，不再发起任何网络请求。
    """
    try:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(_TIKTOKEN_CACHE_DIR))
        _TIKTOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _get_encoder():
    """带超时的懒加载；不可用返回 None（调用方降级为字符粗估）

    P1-42：首次加载失败（下载超时/离线）后置失败标志，后续调用直接返回 None，
    避免每次估算都再次阻塞 _TIKTOKEN_TIMEOUT 秒（对话/妙笔流程周期性卡死）。
    """
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None or _ENCODER_FAILED:
        return _ENCODER
    with _ENCODER_LOCK:
        if _ENCODER is not None:
            return _ENCODER
        if _ENCODER_FAILED:
            return None
        result = {}

        def worker():
            result["enc"] = _load_encoder()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=_TIKTOKEN_TIMEOUT)
        if t.is_alive():
            # 下载卡住：放弃（daemon 线程随进程退出，不会阻塞主流程）
            # 注意：不能用 ⚠ 等 GBK 无法编码的符号，Windows 控制台会抛 UnicodeEncodeError
            print("[compact] tiktoken 词表下载超时，Token 估算降级为字符粗估")
            _ENCODER = None
            _ENCODER_FAILED = True
        else:
            _ENCODER = result.get("enc")
            if _ENCODER is None:
                _ENCODER_FAILED = True
                print("[compact] tiktoken 不可用，Token 估算降级为字符粗估")
    return _ENCODER


def estimate_tokens(messages: list) -> int:
    """使用 tiktoken 估算 token 数；不可用时降级为字符粗估"""
    text = json.dumps(messages, default=str, ensure_ascii=False)
    enc = _get_encoder()
    if enc is None:
        return len(text) // 2
    return len(enc.encode(text))


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

        # 2. 分层：保留最近 2 轮完整对话（以 assistant 为轮边界，保证
        #    tool_calls↔tool 配对不被拆散，避免压缩后下次 API 调用 400）
        recent_start = self._find_recent_start(messages, system_idx + 1)
        recent = messages[recent_start:]
        history = messages[system_idx + 1:recent_start]

        # 3. 压缩历史
        if len(history) <= 2:
            compressed = self._format_as_summary(history)
        else:
            summary_prompt = self._build_summary_prompt(history)
            try:
                resp = self.llm.chat(
                    messages=[{"role": "user", "content": summary_prompt}],
                    system_prompt="你是一个上下文压缩助手。",
                    tools=None,
                )
                compressed = resp.get("content", "").strip()
            except Exception as e:
                # P1-11：LLM 压缩失败不中断会话，降级为拼接摘要（不静默：打印 stderr）
                print(f"[compact] LLM 压缩失败，降级为拼接摘要：{e}")
                compressed = ""
            if not compressed:
                compressed = self._format_as_summary(history)

        # 4. 重组
        result = [system_msg]
        if compressed:
            result.append({"role": "system", "content": f"【历史摘要】\n{compressed}"})
        result.extend(recent)
        return result

    @staticmethod
    def _find_recent_start(messages: list, floor: int) -> int:
        """定位保留区起点：从末尾向前数 2 个 assistant 消息（轮边界）

        返回的起点保证 recent=messages[start:] 以 assistant 开头、
        不落在 tool 响应上（tool 响应必须与所属 assistant 同段保留）。
        """
        i = len(messages) - 1
        rounds = 0
        while i >= floor:
            if messages[i].get("role") == "assistant":
                rounds += 1
                if rounds == 2:
                    break
            i -= 1
        start = i
        # 起点若落在 tool 响应上（理论上不会，防御性收拢到其 assistant）
        while start > floor and messages[start].get("role") == "tool":
            start -= 1
        return max(start, floor)

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
}

# 读取类工具的结果必须完整保留，不能被 PersistCache 吞掉
# v6.4.5：补 read_rule —— wiki/rules/plot 均有 yaml_only 阅读选择，
# 完整阅读（yaml_only=false）时结果必须全量可见，截断会诱发模型幻觉
PERSIST_NEVER = {"read_chapters", "read_rule", "read_wiki", "read_plot", "lint_report"}


class PersistCache:
    """统一缓存文件管理 — 单文件，追加模式

    缓存路径: {workspace}/session/compact_cache.json
    用于持久化大工具输出，避免上下文膨胀。
    """

    def __init__(self, workspace: Path):
        self.cache_path = workspace / "session" / "compact_cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        # P1-42：进程内锁，防多任务（server/MCP 并发）交错写坏缓存文件
        self._lock = threading.Lock()
        if not self.cache_path.exists():
            self.cache_path.write_text("{}", encoding="utf-8")

    def _read_cache(self) -> dict:
        """读取缓存；JSON 损坏/文件不可读时返回空字典（不中断工具执行）"""
        try:
            if not self.cache_path.exists():
                return {}
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            print(f"[compact] 缓存文件读取失败（{self.cache_path.name}）：{e}")
            return {}

    def save(self, tool_call_id: str, data: dict):
        """保存数据到缓存 JSON 文件（加锁 + 临时文件原子替换）"""
        with self._lock:
            # 确保文件存在（可能被外部删除）
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache = self._read_cache()
            cache[tool_call_id] = data
            tmp = self.cache_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.cache_path)

    def load(self, tool_call_id: str) -> dict | None:
        """从缓存 JSON 文件加载指定条目"""
        with self._lock:
            return self._read_cache().get(tool_call_id)

    def get_preview(self, tool_call_id: str) -> str:
        """获取缓存条目的预览文本"""
        data = self.load(tool_call_id)
        if not data:
            return "(缓存未找到)"
        return data.get("result_preview", "(无预览)")

    def should_persist(self, tool_name: str, result: str) -> bool:
        """判断工具结果是否需要 persist

        注意：错误结果和空结果不应被缓存，否则 LLM 看不到失败信息，
        会误以为操作成功（v5.0.2 修复）。
        """
        if tool_name in PERSIST_NEVER:
            return False
        # 错误结果不缓存 — 让 LLM 看到失败信息
        if result.startswith("错误：") or result.startswith("错误:"):
            print(f"[DEBUG] should_persist: 工具返回错误结果，不缓存 (tool={tool_name})")
            return False
        # 批量操作空结果不缓存（如 {"success": 0, "failed": 0, "items": []}）
        if '"success": 0' in result and '"failed": 0' in result:
            print(f"[DEBUG] should_persist: 批量操作空结果，不缓存 (tool={tool_name})")
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
        # 构建友好的占位符（包含关键统计信息，避免 LLM 误判）
        name = params.get("name", params.get("category", ""))
        items = params.get("items", [])
        # 批量操作：从结果中提取成功/失败统计
        if tool_name.startswith("batch_"):
            import json as _json
            try:
                stats = _json.loads(result)
                s = stats.get("success", 0)
                f = stats.get("failed", 0)
                return f"[{tool_name} 已执行，成功 {s} 个，失败 {f} 个。详情已缓存 session/compact_cache.json]"
            except (ValueError, TypeError):
                # 结果非 JSON（如纯文本错误）：降级为通用占位符，
                # 完整内容仍在缓存中可查，不属静默抑制
                pass
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
