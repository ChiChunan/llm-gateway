"""Stats API endpoints: usage summary, daily breakdown, recent logs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import threading
import yaml
import time

from usage import get_usage_db
from provider_state import get_channel_mgr, get_config_path
from providers import get_channel_for_model

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 读-改-写操作锁，防止并发写入 routing.yaml 导致状态被覆盖
_config_lock = threading.Lock()

# 缓存 _load_model_names 的结果
# 配置只在 POST 变更时才失效，缓存有效期用时间兜底
_config_cache: dict[str, tuple[float, any]] = {}
_CONFIG_CACHE_TTL = 30.0  # 秒


def _get_cached(key: str, loader: callable) -> any:
    """带 TTL 的配置缓存读。"""
    import time as _t
    now = _t.time()
    if key in _config_cache:
        ts, val = _config_cache[key]
        if now - ts < _CONFIG_CACHE_TTL:
            return val
    val = loader()
    _config_cache[key] = (now, val)
    return val


def _invalidate_config_cache():
    """配置变更时清除缓存。"""
    _config_cache.clear()


@router.get("/summary")
async def stats_summary(
    since: Optional[str] = None,
    until: Optional[str] = None,
    group_by: str = Query("model", regex="^(model|channel)$"),
    role_filter: Optional[str] = Query(None, description="筛选特定角色，如 'request' 排除分类器，'classifier' 只看分类器，留空则包含全部"),
):
    """Aggregated usage summary grouped by model or channel."""
    db = get_usage_db()
    return {"data": db.get_summary(since=since, until=until, group_by=group_by, role_filter=role_filter)}


@router.get("/daily")
async def stats_daily(
    since: Optional[str] = None,
    until: Optional[str] = None,
    role_filter: Optional[str] = Query("request", description="筛选角色：'request' 排除分类器(默认)，'classifier' 只看分类器，留空同默认"),
):
    """Daily usage breakdown per model (默认排除分类器请求)."""
    db = get_usage_db()
    return {"data": db.get_daily(since=since, until=until, role_filter=role_filter)}


@router.get("/logs")
async def stats_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Recent raw usage log entries."""
    db = get_usage_db()
    return {"data": db.get_recent_logs(limit=limit, offset=offset)}


@router.get("/hourly")
async def stats_hourly(
    date: Optional[str] = None,
    role_filter: Optional[str] = Query("request", description="筛选角色：'request' 排除分类器(默认)，'classifier' 只看分类器，留空同默认"),
):
    """Hourly usage breakdown for a given date (default: today, 默认排除分类器请求)."""
    db = get_usage_db()
    return {"data": db.get_hourly(date=date, role_filter=role_filter)}


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
async def stats_totals(days: int | None = None, date: str | None = None):
    """Lifetime totals, last N days if days is set, or a single date if date is set (YYYY-MM-DD)."""
    db = get_usage_db()
    if date:
        return {"data": db.get_total_stats(date=date)}
    return {"data": db.get_total_stats(days=days)}


# ─── Config Router (model switches only) ─────────────────────────────────────

config_router = APIRouter(prefix="/api/config", tags=["config"])


class ModelSwitchRequest(BaseModel):
    enabled: bool


def _load_model_names() -> list[str]:
    """从 routing.yaml 的 model_switches 取 model 名称列表（带缓存）。"""
    def _read():
        config_path = get_config_path()
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return list(cfg.get("model_switches", {}).keys())
    return _get_cached("model_names", _read)


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
async def update_model(model_name: str, body: ModelSwitchRequest):
    """切换指定 model 的开关，并持久化到 routing.yaml。"""
    known = _load_model_names()
    if model_name not in known:
        raise HTTPException(status_code=400, detail=f"未知 model: {model_name}")

    channel_mgr = get_channel_mgr()
    original_enabled = channel_mgr.is_model_enabled(model_name)

    # 锁保护整个读-改-写过程，防止并发请求相互覆盖
    with _config_lock:
        channel_mgr.set_model_enabled(model_name, body.enabled)
        if body.enabled:
            channel_mgr.mark_available(model_name)

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

    _invalidate_config_cache()
    return _build_model_info(model_name)
