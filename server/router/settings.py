"""设置 HTTP API（配置/模型管理）"""

import copy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from commands.common import load_config, save_config

router = APIRouter()


# ─── Pydantic 请求模型 ────────────────────────────────────────────

class SettingsReq(BaseModel):
    """保存配置的请求体（完整配置对象）"""
    __pydantic_config__ = {"extra": "allow"}


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
    __pydantic_config__ = {"extra": "allow"}


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
    except Exception:
        return {}


@router.put("/api/settings")
async def save_settings(req: SettingsReq) -> dict:
    """保存配置"""
    try:
        save_config(req.model_dump())
        return {"ok": True}
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
    except Exception:
        return []


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
    """更新模型"""
    try:
        config = load_config()
        models = config.get("models", [])
        for i, m in enumerate(models):
            if m.get("id") == model_id:
                updated = {**m, **req.model_dump(exclude_unset=True), "id": model_id}
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


# ─── 模型分配 ──────────────────────────────────────────────────────

@router.get("/api/settings/assignments")
async def get_model_assignments() -> dict:
    """获取模型分配"""
    try:
        config = load_config()
        return config.get("assignments", {})
    except Exception:
        return {}


@router.put("/api/settings/assignments/{role}")
async def set_model_assignment(role: str, req: AssignmentReq) -> dict:
    """设置模型分配"""
    try:
        config = load_config()
        assignments = config.setdefault("assignments", {})
        assignments[role] = req.model_id
        save_config(config)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))
