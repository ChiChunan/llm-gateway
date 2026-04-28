"""Stats API endpoints: usage summary, daily breakdown, recent logs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import yaml

from usage import get_usage_db
from provider_state import get_channel_mgr, get_config_path

router = APIRouter(prefix="/api/stats", tags=["stats"])


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
    # 保存原始状态，用于写文件失败时回滚
    original_state = channel_mgr.get_provider_state(provider_name)

    channel_mgr.set_provider_state(provider_name, body.enabled)

    # 持久化到 routing.yaml
    config_path = get_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("provider_switches", {})[provider_name] = body.enabled
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
    except Exception as exc:
        # 写文件失败：回滚内存状态
        channel_mgr.set_provider_state(provider_name, original_state.value == "enabled")
        raise HTTPException(status_code=500, detail=f"写入配置失败: {exc}") from exc

    return _build_provider_info(provider_name)
