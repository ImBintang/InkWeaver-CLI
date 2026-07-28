"""工作流工具：submit_plan / review_workflow / finish_task"""

import json
from pathlib import Path


def submit_plan(workspace: Path, plan_json_str: str) -> str:
    """提交知识提取/修改计划

    此工具由 Agent 调用，实际权限切换由 PermissionManager 处理。
    这里只做计划的 JSON 解析验证和格式化展示。

    Returns:
        JSON 字符串，包含解析后的计划概要供 CLI 展示
    """
    try:
        plan = json.loads(plan_json_str)
    except json.JSONDecodeError as e:
        return json.dumps({
            "status": "error",
            "message": f"计划 JSON 解析失败：{e}"
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
    from tools.chapter import parse_chapter_spec
    chapters = parse_chapter_spec(plan.get("scope", ""))
    max_chapters = plan.get("default_chapters", 10)
    if len(chapters) > max(20, max_chapters):
        return json.dumps({
            "status": "error",
            "message": f"单次提取不得超过 20 章（当前 {len(chapters)} 章）"
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
