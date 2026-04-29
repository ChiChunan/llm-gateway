"""Stats API endpoints: usage summary, daily breakdown, recent logs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import threading
import yaml

from usage import get_usage_db
from provider_state import get_channel_mgr, get_config_path
from providers import get_channel_for_model

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 读-改-写操作锁，防止并发写入 routing.yaml 导致状态被覆盖
_config_lock = threading.Lock()


@router.get("/summary")
async def stats_summary(
    since: Optional[str] = None,
    until: Optional[str] = None,
    group_by: str = Query("model", regex="^(model|channel)$"),
):
    """Aggregated usage summary grouped by model or channel."""
    db = get_usage_db()
    return {"data": db.get_summary(since=since, until=until, group_by=group_by)}


@router.get("/daily")
async def stats_daily(
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Daily usage breakdown per model."""
    db = get_usage_db()
    return {"data": db.get_daily(since=since, until=until)}


@router.get("/logs")
async def stats_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Recent raw usage log entries."""
    db = get_usage_db()
    return {"data": db.get_recent_logs(limit=limit, offset=offset)}


@router.get("/hourly")
async def stats_hourly(date: Optional[str] = None):
    """Hourly usage breakdown for a given date (default: today)."""
    db = get_usage_db()
    return {"data": db.get_hourly(date=date)}


@router.get("/classifier")
async def stats_classifier(since: Optional[str] = None, until: Optional[str] = None):
    """Classifier model usage summary."""
    db = get_usage_db()
    return {"data": db.get_summary(since=since, until=until, group_by="model", role_filter="classifier")}


@router.get("/roles")
async def stats_roles(since: Optional[str] = None, until: Optional[str] = None):
    """Request count grouped by routing role."""
    db = get_usage_db()
    return {"data": db.get_role_stats(since=since, until=until)}


@router.get("/totals")
async def stats_totals():
    """Lifetime totals."""
    db = get_usage_db()
    return {"data": db.get_total_stats()}


# ─── Config Router ────────────────────────────────────────────────────────────

config_router = APIRouter(prefix="/api/config", tags=["config"])


class ProviderSwitchRequest(BaseModel):
    enabled: bool


def _load_provider_names() -> list[str]:
    """从 routing.yaml 的 provider_switches 取 provider 名称列表。"""
    config_path = get_config_path()
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("provider_switches", {}).keys())


def _build_provider_info(name: str) -> dict:
    """组装单个 provider 的状态字典。"""
    channel_mgr = get_channel_mgr()
    state = channel_mgr.get_provider_state(name)
    active = channel_mgr.get_active_requests(name)
    return {
        "name": name,
        "state": state.value,
        "active_requests": active,
        "enabled": channel_mgr.is_provider_available(name),
    }


def _load_model_names() -> list[str]:
    """从 routing.yaml 的 model_switches 取 model 名称列表。"""
    config_path = get_config_path()
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("model_switches", {}).keys())


def _build_model_info(name: str) -> dict:
    """组装单个 model 的状态字典。"""
    channel_mgr = get_channel_mgr()
    enabled = channel_mgr.is_model_enabled(name)
    provider = get_channel_for_model(name)
    return {
        "name": name,
        "provider": provider,
        "enabled": enabled,
    }


@config_router.get("/providers")
async def get_providers():
    """返回所有 provider 的当前运行状态。"""
    names = _load_provider_names()
    return {"data": [_build_provider_info(n) for n in names]}


@config_router.post("/providers/{provider_name}")
async def update_provider(provider_name: str, body: ProviderSwitchRequest):
    """切换指定 provider 的开关，并持久化到 routing.yaml。"""
    known = _load_provider_names()
    if provider_name not in known:
        raise HTTPException(status_code=400, detail=f"未知 provider: {provider_name}")

    channel_mgr = get_channel_mgr()
    original_state = channel_mgr.get_provider_state(provider_name)

    # 锁保护整个读-改-写过程，防止并发请求相互覆盖
    with _config_lock:
        channel_mgr.set_provider_state(provider_name, body.enabled)

        config_path = get_config_path()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault("provider_switches", {})[provider_name] = body.enabled
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            channel_mgr.set_provider_state(provider_name, original_state.value == "enabled")
            raise HTTPException(status_code=500, detail=f"写入配置失败: {exc}") from exc

    return _build_provider_info(provider_name)


@config_router.get("/models")
async def get_models():
    """返回所有 model 的当前开关状态，按 provider 分组。"""
    names = _load_model_names()
    models = [_build_model_info(n) for n in names]
    grouped: dict[str, list] = {}
    for m in models:
        grouped.setdefault(m["provider"], []).append(m)
    return {"data": grouped}


@config_router.post("/models/{model_name}")
async def update_model(model_name: str, body: ProviderSwitchRequest):
    """切换指定 model 的开关，并持久化到 routing.yaml。"""
    known = _load_model_names()
    if model_name not in known:
        raise HTTPException(status_code=400, detail=f"未知 model: {model_name}")

    channel_mgr = get_channel_mgr()
    original_enabled = channel_mgr.is_model_enabled(model_name)

    # 锁保护整个读-改-写过程，防止并发请求相互覆盖
    with _config_lock:
        channel_mgr.set_model_enabled(model_name, body.enabled)

        config_path = get_config_path()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault("model_switches", {})[model_name] = body.enabled
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            channel_mgr.set_model_enabled(model_name, original_enabled)
            raise HTTPException(status_code=500, detail=f"写入配置失败: {exc}") from exc

    return _build_model_info(model_name)
