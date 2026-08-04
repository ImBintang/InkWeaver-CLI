"""设置 HTTP API（配置/模型管理）"""

import copy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from commands.common import load_config, save_config, get_workspaces_dir

router = APIRouter()


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class SettingsReq(BaseModel):
    """保存配置的请求体（完整配置对象）"""
    model_config = ConfigDict(extra="allow")


class ModelReq(BaseModel):
    """模型定义请求"""
    name: str = ""
    provider: str = "deepseek"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    output_max_tokens: int = 128000


class ModelUpdateReq(BaseModel):
    """模型更新请求（允许部分字段）"""
    model_config = ConfigDict(extra="allow")


class AssignmentReq(BaseModel):
    model_id: str


# ─── 配置 ──────────────────────────────────────────────────────────

def _mask_api_key(config: dict) -> dict:
    """脱敏 api_key"""
    safe = copy.deepcopy(config)
    for m in safe.get("models", []):
        key = m.get("api_key", "")
        if len(key) > 8:
            m["api_key"] = key[:4] + "***" + key[-4:]
    safe.pop("api", None)
    return safe


@router.get("/api/settings")
async def get_settings() -> dict:
    """获取配置（脱敏）"""
    try:
        config = load_config()
        return _mask_api_key(config)
    except Exception as e:
        # 不静默：配置读取失败必须返回 500，前端才能向用户展示明确错误
        raise HTTPException(500, detail=f"读取配置失败：{e}")


@router.put("/api/settings")
async def save_settings(req: SettingsReq) -> dict:
    """保存配置"""
    try:
        data = req.model_dump()
        if not data:
            # 防御：空配置说明请求体解析异常，禁止用空配置覆盖整个 config.yaml
            raise HTTPException(400, detail="请求体为空，已拒绝保存（防止清空配置）")
        save_config(data)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ─── 模型管理 ──────────────────────────────────────────────────────

@router.get("/api/settings/models")
async def list_models() -> list[dict]:
    """列出所有模型（脱敏）"""
    try:
        config = load_config()
        models = copy.deepcopy(config.get("models", []))
        for m in models:
            key = m.get("api_key", "")
            if len(key) > 8:
                m["api_key"] = key[:4] + "***" + key[-4:]
        return models
    except Exception as e:
        # 不静默：返回 500 而非空列表，避免前端误以为"没有模型"
        raise HTTPException(500, detail=f"读取模型列表失败：{e}")


@router.post("/api/settings/models")
async def add_model(req: ModelReq) -> dict:
    """添加模型"""
    try:
        config = load_config()
        models = config.setdefault("models", [])
        new_id = f"model_{len(models) + 1:03d}"
        while any(m.get("id") == new_id for m in models):
            new_id = f"model_{int(new_id.split('_')[1]) + 1:03d}"
        new_model = {"id": new_id, **req.model_dump()}
        models.append(new_model)
        save_config(config)
        return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.put("/api/settings/models/{model_id}")
async def update_model(model_id: str, req: ModelUpdateReq) -> dict:
    """更新模型

    P1-39：api_key 传空串/缺失时保留原 Key（“留空则保留原 Key”的 UI 承诺），
    避免前端编辑时置空的 api_key 覆盖已保存密钥。
    """
    try:
        config = load_config()
        models = config.get("models", [])
        updates = req.model_dump(exclude_unset=True)
        if "api_key" in updates and not (updates["api_key"] or "").strip():
            updates.pop("api_key")  # 留空则保留原 Key
        for i, m in enumerate(models):
            if m.get("id") == model_id:
                updated = {**m, **updates, "id": model_id}
                models[i] = updated
                save_config(config)
                return {"ok": True}
        raise HTTPException(404, detail=f"模型 {model_id} 不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.delete("/api/settings/models/{model_id}")
async def delete_model(model_id: str) -> dict:
    """删除模型"""
    try:
        config = load_config()
        models = config.get("models", [])
        original_len = len(models)
        config["models"] = [m for m in models if m.get("id") != model_id]
        if len(config["models"]) == original_len:
            raise HTTPException(404, detail=f"模型 {model_id} 不存在")
        save_config(config)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/api/settings/models/{model_id}/test")
def test_model(model_id: str) -> dict:
    """模型连通性测试：用该模型配置发起一次最小请求。

    使用同步 def 合 FastAPI 线程池执行，避免阻塞事件循环（LLM 调用可能耗时）。
    """
    config = load_config()
    models = config.get("models", [])
    model = next((m for m in models if m.get("id") == model_id), None)
    if model is None:
        raise HTTPException(404, detail=f"模型 {model_id} 不存在")
    api_cfg = {
        "url": model.get("base_url", ""),
        "key": model.get("api_key", ""),
        "model": model.get("model", ""),
        "output_max_tokens": 16,
    }
    try:
        from api import LLMClient
        client = LLMClient(api_cfg)
        try:
            # 最小请求（不带 thinking 参数，兼容所有 OpenAI 兼容供应商）
            client.client.chat.completions.create(
                model=client.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return {"ok": True, "message": "连接成功", "model": client.model}
        finally:
            client.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 模型分配 ──────────────────────────────────────────────────────

@router.get("/api/settings/assignments")
async def get_model_assignments() -> dict:
    """获取模型分配（v6.5.8：extract 已并入 chat，读取时过滤旧 extract 键）"""
    try:
        config = load_config()
        assignments = config.get("assignments", {})
        # 鉴知统一：剔除旧 extract 分配，前端只展示 chat/write/review 三角色
        assignments.pop("extract", None)
        return assignments
    except Exception as e:
        # 不静默：返回 500 而非空对象，避免前端误以为"没有分配"
        raise HTTPException(500, detail=f"读取模型分配失败：{e}")


@router.put("/api/settings/assignments/{role}")
async def set_model_assignment(role: str, req: AssignmentReq) -> dict:
    """设置模型分配（v6.5.8：extract 映射到 chat，与 resolve_api_config 一致）"""
    try:
        config = load_config()
        if role == "extract":
            role = "chat"
        assignments = config.setdefault("assignments", {})
        assignments.pop("extract", None)  # 写入时顺带清理旧 extract 键
        assignments[role] = req.model_id
        save_config(config)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ─── 工作区存储 ────────────────────────────────────────────────────

@router.get("/api/settings/workspace/size")
def workspace_size() -> dict:
    """计算工作区根目录总大小（字节）

    使用同步 def 合 FastAPI 线程池执行，避免阻塞事件循环（大目录 rglob 耗时）。
    """
    try:
        config = load_config()
        ws_dir = get_workspaces_dir(config)
        total = sum(p.stat().st_size for p in ws_dir.rglob("*") if p.is_file())
        return {"ok": True, "bytes": total, "dir": str(ws_dir)}
    except Exception as e:
        raise HTTPException(500, detail=f"计算工作区大小失败：{e}")
