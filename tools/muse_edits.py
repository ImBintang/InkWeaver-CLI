"""妙笔手术刀编辑 — Writer 修改轮编辑指令应用

v5.4 新增。Writer 修改轮输出 JSON 编辑指令列表，
本模块负责解析并应用这些指令到草稿文本上。

三种编辑工具：
- replace_text: 精确替换文本片段（old_text → new_text）
- delete_text: 删除指定文本（old_text → ""）
- rewrite_paragraph: 按段落首句定位并重写整段

Fuzzy match 策略：
- 精确匹配优先
- 失败时 difflib.SequenceMatcher 滑动窗口，threshold=0.85
- 仍失败则记录 warning，跳过该条 edit
"""

import difflib
import json
import re


# ── Fuzzy 匹配 ──────────────────────────────────────────────────────────────


def fuzzy_find(text: str, target: str, threshold: float = 0.85) -> tuple[int, int] | None:
    """在 text 中找到与 target 最相似的片段

    使用 difflib.SequenceMatcher 的 find_longest_match 做局部对齐，
    再用 get_close_matches 思路做滑动窗口验证。

    Args:
        text: 全文（草稿）
        target: 要定位的目标文本（LLM 引用的 old_text）
        threshold: 最低相似度阈值

    Returns:
        (start, end) 区间，或 None（未找到）
    """
    if not target or not text:
        return None

    # 精确匹配优先
    idx = text.find(target)
    if idx >= 0:
        return (idx, idx + len(target))

    # 快速排除：如果目标太长或太短
    tlen = len(target)
    if tlen < 4:
        return None  # 太短的片段不做 fuzzy（误匹配率高）

    # 滑动窗口：窗口大小在 [tlen*0.8, tlen*1.3] 范围
    best_ratio = 0.0
    best_span: tuple[int, int] | None = None

    # 使用 SequenceMatcher 的 autojunk=False 提高短文本精度
    min_win = max(4, int(tlen * 0.7))
    max_win = min(len(text), int(tlen * 1.4))

    # 优化：先用 get_matching_blocks 做粗定位
    # 对每个可能的起始位置做窗口匹配
    step = max(1, tlen // 10)  # 步长：目标长度的 10%
    for i in range(0, len(text) - min_win + 1, step):
        for win_size in (tlen, min_win, max_win):
            if i + win_size > len(text):
                continue
            window = text[i:i + win_size]
            ratio = difflib.SequenceMatcher(None, target, window, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_span = (i, i + win_size)

    # 精细化：在最佳位置附近逐字符微调
    if best_span and best_ratio >= threshold * 0.9:
        start, end = best_span
        # 向前后各扩展 step 个字符做精细搜索
        fine_start = max(0, start - step)
        fine_end = min(len(text), end + step)
        for i in range(fine_start, min(fine_end, len(text) - min_win + 1)):
            for win_size in range(max(min_win, tlen - step), min(max_win, tlen + step + 1)):
                if i + win_size > len(text):
                    continue
                window = text[i:i + win_size]
                ratio = difflib.SequenceMatcher(None, target, window, autojunk=False).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_span = (i, i + win_size)

    if best_ratio >= threshold:
        return best_span
    return None


# ── 段落重写 ─────────────────────────────────────────────────────────────────


def _split_paragraphs(text: str) -> list[str]:
    """将文本按段落分割（以连续空行或单个换行分段）

    小说文本通常以单个换行分段。保留段落间的分隔符。
    """
    # 按换行分段（保留空行作为段落分隔）
    paragraphs = text.split("\n")
    return paragraphs


def rewrite_paragraph(draft: str, paragraph_ref: str, new_text: str) -> tuple[str, bool]:
    """按段落首句定位并重写整段

    Args:
        draft: 完整草稿文本
        paragraph_ref: 段落首句的前 15+ 字符（用于定位）
        new_text: 新的段落内容

    Returns:
        (新草稿, 是否成功)
    """
    if not paragraph_ref:
        return draft, False

    paragraphs = _split_paragraphs(draft)

    # 查找包含 paragraph_ref 的段落
    ref_stripped = paragraph_ref.strip()
    target_idx = -1

    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        if not para_stripped:
            continue
        # 精确前缀匹配
        if para_stripped.startswith(ref_stripped):
            target_idx = i
            break
        # 包含匹配
        if ref_stripped in para_stripped:
            target_idx = i
            break

    # 精确匹配失败，尝试 fuzzy
    if target_idx < 0:
        best_ratio = 0.0
        for i, para in enumerate(paragraphs):
            para_stripped = para.strip()
            if not para_stripped or len(para_stripped) < 5:
                continue
            # 只比较段落开头部分
            head = para_stripped[:len(ref_stripped) + 10]
            ratio = difflib.SequenceMatcher(None, ref_stripped, head, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                target_idx = i

        if best_ratio < 0.7:
            return draft, False

    # 替换段落
    paragraphs[target_idx] = new_text
    return "\n".join(paragraphs), True


# ── 主入口：应用编辑指令列表 ─────────────────────────────────────────────────


def apply_writer_edits(draft: str, edits: list[dict]) -> tuple[str, list[str]]:
    """应用 Writer 的编辑指令列表

    Args:
        draft: 当前草稿全文
        edits: 编辑指令列表，每条格式：
            {"tool": "replace_text", "old_text": "...", "new_text": "..."}
            {"tool": "delete_text", "old_text": "..."}
            {"tool": "rewrite_paragraph", "paragraph_ref": "...", "new_text": "..."}

    Returns:
        (新草稿, 变更日志列表)
        变更日志每条描述一个成功/失败的操作
    """
    changes: list[str] = []
    result = draft

    for i, edit in enumerate(edits):
        tool = edit.get("tool", "")

        if tool == "replace_text":
            old_text = edit.get("old_text", "")
            new_text = edit.get("new_text", "")
            if not old_text:
                changes.append(f"[跳过] edit#{i+1}: old_text 为空")
                continue

            # 精确匹配
            if old_text in result:
                result = result.replace(old_text, new_text, 1)
                changes.append(f"[替换] \"{old_text[:30]}...\" → \"{new_text[:30]}...\""
                               if len(old_text) > 30 else
                               f"[替换] \"{old_text}\" → \"{new_text}\"")
            else:
                # Fuzzy 匹配
                span = fuzzy_find(result, old_text)
                if span:
                    start, end = span
                    matched_text = result[start:end]
                    result = result[:start] + new_text + result[end:]
                    changes.append(f"[替换(fuzzy)] \"{matched_text[:30]}...\" → \"{new_text[:30]}...\""
                                   if len(matched_text) > 30 else
                                   f"[替换(fuzzy)] \"{matched_text}\" → \"{new_text}\"")
                else:
                    changes.append(f"[失败] edit#{i+1} replace_text: 未找到匹配 \"{old_text[:40]}...\"")

        elif tool == "delete_text":
            old_text = edit.get("old_text", "")
            if not old_text:
                changes.append(f"[跳过] edit#{i+1}: old_text 为空")
                continue

            if old_text in result:
                result = result.replace(old_text, "", 1)
                changes.append(f"[删除] \"{old_text[:40]}...\""
                               if len(old_text) > 40 else
                               f"[删除] \"{old_text}\"")
            else:
                span = fuzzy_find(result, old_text)
                if span:
                    start, end = span
                    matched_text = result[start:end]
                    result = result[:start] + result[end:]
                    changes.append(f"[删除(fuzzy)] \"{matched_text[:40]}...\""
                                   if len(matched_text) > 40 else
                                   f"[删除(fuzzy)] \"{matched_text}\"")
                else:
                    changes.append(f"[失败] edit#{i+1} delete_text: 未找到匹配 \"{old_text[:40]}...\"")

        elif tool == "rewrite_paragraph":
            paragraph_ref = edit.get("paragraph_ref", "")
            new_text = edit.get("new_text", "")
            if not paragraph_ref or not new_text:
                changes.append(f"[跳过] edit#{i+1}: paragraph_ref 或 new_text 为空")
                continue

            result, success = rewrite_paragraph(result, paragraph_ref, new_text)
            if success:
                changes.append(f"[重写段落] \"{paragraph_ref[:30]}...\""
                               if len(paragraph_ref) > 30 else
                               f"[重写段落] \"{paragraph_ref}\"")
            else:
                changes.append(f"[失败] edit#{i+1} rewrite_paragraph: 未定位到段落 \"{paragraph_ref[:30]}...\"")

        else:
            changes.append(f"[跳过] edit#{i+1}: 未知工具 \"{tool}\"")

    return result, changes


# ── JSON 解析辅助 ────────────────────────────────────────────────────────────


def parse_edits_response(response: str) -> tuple[list[dict] | None, str]:
    """解析 LLM 修改轮的输出，提取 edits 列表

    尝试策略：
    1. 直接 json.loads
    2. 提取 ```json ... ``` 代码块
    3. 提取第一个 { ... } 块
    4. 全部失败 → 返回 None（视为全文模式 fallback）

    Args:
        response: LLM 原始输出文本

    Returns:
        (edits_list 或 None, 模式说明)
        - edits_list 非 None → "edits" 模式
        - edits_list 为 None → "fulltext" 模式（fallback）
    """
    text = response.strip()

    # 策略1：直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "edits" in data:
            return data["edits"], "edits"
        if isinstance(data, list):
            return data, "edits"
    except (json.JSONDecodeError, ValueError):
        pass

    # 策略2：提取 ```json 代码块
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1).strip())
            if isinstance(data, dict) and "edits" in data:
                return data["edits"], "edits"
            if isinstance(data, list):
                return data, "edits"
        except (json.JSONDecodeError, ValueError):
            pass

    # 策略3：提取第一个 { ... } 块（贪婪匹配最外层）
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            if isinstance(data, dict) and "edits" in data:
                return data["edits"], "edits"
        except (json.JSONDecodeError, ValueError):
            pass

    # 全部失败 → 视为全文输出
    return None, "fulltext"
