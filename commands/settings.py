"""settings 子命令组 — 配置管理（模型 CRUD + 角色分配）

v6.5.8：与 GUI 设置页对齐——鉴知与知识提取模型合二为一（extract 并入 chat），
共 3 个角色：chat（鉴知，含对话+知识提取）/ write（写作）/ review（审阅）。
"""

import json
import typer

from commands.common import load_config, save_config
from core.output import OutputFormatter

app = typer.Typer(help="配置管理（模型/分配）")

# 3 角色分配（extract 已并入 chat，与 GUI 设置页一致）
ROLES: dict[str, str] = {
    "chat": "鉴知（对话 + 知识提取）",
    "write": "写作（妙笔）",
    "review": "审阅",
}


def _mask_key(key: str) -> str:
    """脱敏 api_key"""
    if not key:
        return ""
    if len(key) > 8:
        return key[:4] + "***" + key[-4:]
    return "***"


def _find_model(config: dict, model_id: str) -> dict | None:
    return next((m for m in config.get("models", []) if m.get("id") == model_id), None)


def _render_models(fmt: OutputFormatter, models: list[dict]):
    if not models:
        fmt.result("（暂无已配置模型）")
        return
    for m in models:
        fmt.result(
            f"- [{m.get('id')}] {m.get('name') or m.get('id')}"
            f"（model: {m.get('model', '')} / "
            f"base_url: {m.get('base_url', '')} / "
            f"key: {_mask_key(m.get('api_key', ''))}）"
        )


@app.command("show")
def settings_show(
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """显示当前配置（模型列表 + 角色分配）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    if json_mode:
        print(json.dumps({
            "status": "success",
            "models": config.get("models", []),
            "assignments": config.get("assignments", {}),
        }, ensure_ascii=False, indent=2))
        return
    fmt.result("═══ 模型列表 ═══")
    _render_models(fmt, config.get("models", []))
    fmt.result("")
    fmt.result("═══ 角色分配 ═══")
    assignments = config.get("assignments", {})
    for role, label in ROLES.items():
        model = _find_model(config, assignments.get(role, ""))
        model_name = model.get("name") if model else "(未分配)"
        fmt.result(f"- {label} → {model_name}")
    # 兼容旧配置：仍存在 extract 分配时提示（读取时映射到 chat）
    if "extract" in assignments:
        fmt.result("（提示：旧 extract 分配已并入 chat，读取时自动映射）")


@app.command("assignments")
def settings_assignments(
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看角色分配"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    assignments = config.get("assignments", {})
    if json_mode:
        print(json.dumps({"status": "success", "assignments": assignments},
                         ensure_ascii=False, indent=2))
        return
    for role, label in ROLES.items():
        model = _find_model(config, assignments.get(role, ""))
        model_name = model.get("name") if model else "(未分配)"
        fmt.result(f"- {label} → {model_name}")


@app.command("assign")
def settings_assign(
    role: str = typer.Argument(..., help="角色：chat / write / review"),
    model_id: str = typer.Argument(..., help="模型 ID，如 model_001"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """设置角色绑定的模型（extract 已并入 chat，传 extract 自动映射到 chat）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    # v6.5.8：鉴知统一——extract 映射到 chat，与 resolve_api_config 一致
    if role == "extract":
        role = "chat"
    if role not in ROLES:
        fmt.error(f"未知角色：{role}（可选：{', '.join(ROLES.keys())}）")
        raise typer.Exit(1)
    if _find_model(config, model_id) is None:
        fmt.error(f"模型不存在：{model_id}（先使用 settings models add 添加）")
        raise typer.Exit(1)
    config.setdefault("assignments", {})[role] = model_id
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "role": role, "model_id": model_id},
                         ensure_ascii=False))
    else:
        fmt.result(f"已分配：{ROLES[role]} → {model_id}")


# ─── 模型 CRUD ─────────────────────────────────────────────────────

models_app = typer.Typer(help="模型管理")


@app.command("models")
def settings_models(
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出所有模型"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    models = config.get("models", [])
    if json_mode:
        print(json.dumps({"status": "success", "models": models},
                         ensure_ascii=False, indent=2))
        return
    _render_models(fmt, models)


@models_app.command("add")
def models_add(
    name: str = typer.Option(..., "--name", help="模型显示名称"),
    model: str = typer.Option(..., "--model", help="模型 ID，如 deepseek-v4-flash"),
    api_key: str = typer.Option("", "--api-key", help="API Key"),
    base_url: str = typer.Option("", "--base-url", help="Base URL"),
    output_max_tokens: int = typer.Option(128000, "--output-max-tokens", help="输出上限"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """新增模型"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    models = config.setdefault("models", [])
    new_id = f"model_{len(models) + 1:03d}"
    while any(m.get("id") == new_id for m in models):
        new_id = f"model_{int(new_id.split('_')[1]) + 1:03d}"
    models.append({
        "id": new_id,
        "name": name,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "output_max_tokens": output_max_tokens,
    })
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "id": new_id}, ensure_ascii=False))
    else:
        fmt.result(f"已添加模型：{new_id}（{name}）")


@models_app.command("update")
def models_update(
    model_id: str = typer.Argument(..., help="模型 ID"),
    name: str = typer.Option("", "--name", help="模型显示名称"),
    model: str = typer.Option("", "--model", help="模型 ID"),
    api_key: str = typer.Option("", "--api-key", help="API Key（留空保留原 Key）"),
    base_url: str = typer.Option("", "--base-url", help="Base URL"),
    output_max_tokens: int = typer.Option(0, "--output-max-tokens", help="输出上限"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """更新模型（未传字段保持不变）"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    models = config.get("models", [])
    for i, m in enumerate(models):
        if m.get("id") == model_id:
            updated = dict(m)
            if name:
                updated["name"] = name
            if model:
                updated["model"] = model
            if api_key:
                updated["api_key"] = api_key
            if base_url:
                updated["base_url"] = base_url
            if output_max_tokens > 0:
                updated["output_max_tokens"] = output_max_tokens
            models[i] = updated
            save_config(config)
            if json_mode:
                print(json.dumps({"status": "success", "id": model_id}, ensure_ascii=False))
            else:
                fmt.result(f"已更新模型：{model_id}")
            return
    fmt.error(f"模型不存在：{model_id}")
    raise typer.Exit(1)


@models_app.command("delete")
def models_delete(
    model_id: str = typer.Argument(..., help="模型 ID"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """删除模型"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    models = config.get("models", [])
    original_len = len(models)
    config["models"] = [m for m in models if m.get("id") != model_id]
    if len(config["models"]) == original_len:
        fmt.error(f"模型不存在：{model_id}")
        raise typer.Exit(1)
    save_config(config)
    if json_mode:
        print(json.dumps({"status": "success", "id": model_id}, ensure_ascii=False))
    else:
        fmt.result(f"已删除模型：{model_id}")


@models_app.command("test")
def models_test(
    model_id: str = typer.Argument(..., help="模型 ID"),
    json_mode: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """测试模型连通性"""
    config = load_config()
    fmt = OutputFormatter(json_mode=json_mode)
    model = _find_model(config, model_id)
    if model is None:
        fmt.error(f"模型不存在：{model_id}")
        raise typer.Exit(1)
    try:
        from api import LLMClient
        client = LLMClient({
            "url": model.get("base_url", ""),
            "key": model.get("api_key", ""),
            "model": model.get("model", ""),
            "output_max_tokens": 16,
        })
        client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        if json_mode:
            print(json.dumps({"status": "success", "message": "连接成功"}, ensure_ascii=False))
        else:
            fmt.result(f"[OK] 连接成功：{model_id}")
    except Exception as e:
        if json_mode:
            print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        else:
            fmt.error(f"[失败] 连接失败：{e}")
        raise typer.Exit(1)
