"""Stats API endpoints: usage summary, daily breakdown, recent logs."""

from fastapi import APIRouter, Query
from typing import Optional

from usage import get_usage_db

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
