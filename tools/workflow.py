"""工作流工具：submit_plan / review_workflow / finish_task"""

import json
from pathlib import Path


def _reject_duplicate_keys(pairs: list) -> dict:
    """json.loads 辅助：检测顶层重复键

    v6.5.9: LLM 常把不同类别的词条拆成多个同名 new_wiki 字段，
    JSON 重复键后值覆盖前值，词条被静默丢弃（用户只见最后一部分）。
    """
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"JSON 包含重复键「{k}」")
        d[k] = v
    return d


def submit_plan(workspace: Path, plan_json_str: str) -> str:
    """提交知识提取/修改计划

    此工具由 Agent 调用，实际权限切换由 PermissionManager 处理。
    这里只做计划的 JSON 解析验证和格式化展示。

    Returns:
        JSON 字符串，包含解析后的计划概要供 CLI 展示
    """
    try:
        plan = json.loads(plan_json_str, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as e:
        return json.dumps({
            "status": "error",
            "message": f"计划 JSON 解析失败：{e}"
        }, ensure_ascii=False)
    except ValueError as e:
        # 重复键：明确报错而非静默覆盖（词条丢失是严重问题，必须让 LLM 修正后重试）
        return json.dumps({
            "status": "error",
            "message": f"计划 JSON 结构错误：{e}。所有同类条目必须合并到唯一一个数组字段中（如 new_wiki 一个数组装全部词条，用 category 区分类别），禁止拆成多个同名键！"
        }, ensure_ascii=False)

    # 验证 mode 字段（缺省 "extract"，可选 "re-extract"）
    mode = plan.get("mode", "extract")
    if mode not in ("extract", "re-extract"):
        return json.dumps({
            "status": "error",
            "message": f"无效的 mode 字段：{mode}（仅支持 extract / re-extract）"
        }, ensure_ascii=False)

    # 验证必要字段
    if "scope" not in plan:
        return json.dumps({
            "status": "error",
            "message": "计划缺少 scope 字段（提取章节范围）"
        }, ensure_ascii=False)

    # 检查章节数限制
    # P1-17：default_chapters 只能收紧上限，不能放宽——上限硬性固定为 20 章
    from tools.chapter import parse_chapter_spec
    chapters = parse_chapter_spec(plan.get("scope", ""))
    # default_chapters 无效时按默认 20 处理（非静默吞错：
    # 这是参数规范化，最终仍受 max_chapters 上限约束，超限计划照样被拒）
    try:
        default_cap = int(plan.get("default_chapters", 20))
    except (TypeError, ValueError):
        default_cap = 20
    max_chapters = max(1, min(20, default_cap))
    if len(chapters) > max_chapters:
        return json.dumps({
            "status": "error",
            "message": f"单次提取不得超过 {max_chapters} 章（当前 {len(chapters)} 章）"
        }, ensure_ascii=False)

    # 校验每项是否缺少必需字段
    field_warnings = []
    field_errors = []
    # chapters 为硬性必填（数据库 chapter 字段依赖它），缺失则拒绝计划
    for key, required_hard in [
        ("new_wiki", ["category", "name", "chapters"]),
        ("edit_wiki", ["category", "name", "chapters"]),
        ("new_plot", ["name", "chapters"]),
        ("edit_plot", ["name", "chapters"]),
    ]:
        for i, item in enumerate(plan.get(key, [])):
            missing = [f for f in required_hard if f not in item or not str(item.get(f, "")).strip()]
            if missing:
                field_errors.append(f"{key}[{i}]「{item.get('name', '?')}」缺少必填字段：{', '.join(missing)}")
    # reason 为软性警告
    for key, required_soft in [
        ("new_wiki", ["reason"]),
        ("edit_wiki", ["reason"]),
        ("new_rule", ["name", "reason"]),
        ("edit_rule", ["name", "reason"]),
        ("new_plot", ["reason"]),
        ("edit_plot", ["reason"]),
    ]:
        for i, item in enumerate(plan.get(key, [])):
            missing = [f for f in required_soft if f not in item or not str(item.get(f, "")).strip()]
            if missing:
                field_warnings.append(f"{key}[{i}]「{item.get('name', '?')}」缺少字段：{', '.join(missing)}")

    if field_errors:
        return json.dumps({
            "status": "error",
            "message": "计划校验失败，以下条目缺少必填字段（chapters 为必填）：\n" + "\n".join(field_errors)
        }, ensure_ascii=False)

    # v6.5.10: 章节范围硬校验——所有条目的 chapters/chapter 必须落在本次提取范围 scope 内。
    # 此前 LLM 曾在提取 1-20 章时为 new_rule 传 chapter=40（全书最大章节），
    # load_whitelist 直接采用导致规则 created/updated_chapter 写成 40，而内容实际未提取。
    chapter_set = set(chapters)
    range_errors = []

    def _in_scope(vals) -> list:
        """归一化章节列表并返回越界章节"""
        out = []
        if not isinstance(vals, list):
            vals = [vals]
        for v in vals:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n not in chapter_set:
                out.append(n)
        return out

    for key in ("new_wiki", "edit_wiki", "new_plot", "edit_plot"):
        for i, item in enumerate(plan.get(key, [])):
            bad = _in_scope(item.get("chapters", []))
            if bad:
                range_errors.append(
                    f"{key}[{i}]「{item.get('name', '?')}」章节 {bad} 不在本次提取范围 scope=「{plan.get('scope', '')}」（{sorted(chapter_set)}）内"
                )
    for key in ("new_rule", "edit_rule"):
        for i, item in enumerate(plan.get(key, [])):
            bad = _in_scope(item.get("chapter", []))
            if bad:
                range_errors.append(
                    f"{key}[{i}]「{item.get('name', '?')}」章节 {bad} 不在本次提取范围 scope=「{plan.get('scope', '')}」（{sorted(chapter_set)}）内"
                )

    if range_errors:
        return json.dumps({
            "status": "error",
            "message": (
                "计划校验失败：条目标注的章节必须落在本次提取范围 scope 内，"
                "只能基于已提取章节的内容创建/修改知识，超出范围的章节请留待后续提取：\n"
                + "\n".join(range_errors)
            )
        }, ensure_ascii=False)

    # v6.5.10: unlink_debts（审阅阶段强制债务取消链接+拉黑项）格式校验——
    # 必须为非空字符串列表；批准后由鉴知执行 unlink + 黑名单 + 解除 finish_task 拦截
    unlink_debts = plan.get("unlink_debts", [])
    if not isinstance(unlink_debts, list):
        return json.dumps({
            "status": "error",
            "message": "unlink_debts 必须为字符串数组（要取消链接并拉黑的断链目标名称列表）"
        }, ensure_ascii=False)
    bad_unlink = [t for t in unlink_debts if not isinstance(t, str) or not t.strip()]
    if bad_unlink:
        return json.dumps({
            "status": "error",
            "message": f"unlink_debts 包含非法条目（必须为非空字符串）：{bad_unlink}"
        }, ensure_ascii=False)
    plan["unlink_debts"] = [t.strip() for t in unlink_debts]

    # 统计概要
    summary = {
        "status": "pending_review",
        "mode": mode,
        "scope": plan.get("scope", ""),
        "warnings": field_warnings,
        "stats": {
            "new_wiki": len(plan.get("new_wiki", [])),
            "edit_wiki": len(plan.get("edit_wiki", [])),
            "new_rule": len(plan.get("new_rule", [])),
            "edit_rule": len(plan.get("edit_rule", [])),
            "new_plot": len(plan.get("new_plot", [])),
            "edit_plot": len(plan.get("edit_plot", [])),
            "new_category": len(plan.get("new_category", [])),
            "unlink_debts": len(plan.get("unlink_debts", [])),
        },
        "plan": plan,
    }
    return json.dumps(summary, ensure_ascii=False)


def review_workflow(workspace: Path) -> str:
    """切换到 Review 审核工作流

    实际上下文存档和清空由调用方处理。
    此工具只返回确认信息。
    """
    return json.dumps({
        "status": "switching_to_review",
        "message": "正在切换到 Review 审核模式，上下文将存档并清空。"
    }, ensure_ascii=False)


def finish_task(workspace: Path) -> str:
    """完成任务

    实际清理由调用方处理。
    此工具只返回确认信息。
    """
    return json.dumps({
        "status": "completed",
        "message": "任务已完成，上下文已清空，返回初始状态。"
    }, ensure_ascii=False)
