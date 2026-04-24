"""Tests for usage tracking module."""

import os
import tempfile
import pytest

from usage import UsageDB


@pytest.fixture
def db():
    """Create a temp DB for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = UsageDB(path)
    yield database
    os.unlink(path)


def test_record_and_totals(db):
    db.record("glm-5-1", "ark", prompt_tokens=100, completion_tokens=50, cached_tokens=20, latency_ms=500)
    db.record("glm-5-1", "ark", prompt_tokens=200, completion_tokens=80, cached_tokens=10, latency_ms=300)
    db.record("MiniMax-M2.7-highspeed", "minimax", prompt_tokens=30, completion_tokens=10, latency_ms=100)

    totals = db.get_total_stats()
    assert totals["total_requests"] == 3
    assert totals["total_prompt_tokens"] == 330
    assert totals["total_completion_tokens"] == 140
    assert totals["total_cached_tokens"] == 30


def test_summary_group_by_model(db):
    db.record("glm-5-1", "ark", prompt_tokens=100, completion_tokens=50)
    db.record("MiniMax-M2.7-highspeed", "minimax", prompt_tokens=30, completion_tokens=10)

    summary = db.get_summary(group_by="model")
    assert len(summary) == 2
    models = {r["grp"] for r in summary}
    assert "glm-5-1" in models
    assert "MiniMax-M2.7-highspeed" in models


def test_summary_group_by_channel(db):
    db.record("glm-5-1", "ark", prompt_tokens=100)
    db.record("doubao-seed-2-0-pro", "ark", prompt_tokens=50)
    db.record("MiniMax-M2.7-highspeed", "minimax", prompt_tokens=30)

    summary = db.get_summary(group_by="channel")
    channels = {r["grp"] for r in summary}
    assert "ark" in channels
    assert "minimax" in channels


def test_summary_includes_channel_info(db):
    db.record("glm-5-1", "ark", prompt_tokens=100)
    summary = db.get_summary(group_by="model")
    assert summary[0]["grp_channel"] == "ark"


def test_daily_breakdown(db):
    db.record("glm-5-1", "ark", prompt_tokens=100)
    db.record("glm-5-1", "ark", prompt_tokens=50)
    db.record("MiniMax-M2.7-highspeed", "minimax", prompt_tokens=30)

    daily = db.get_daily()
    assert len(daily) >= 1
    # Both models should appear in today's date
    today_models = {r["model"] for r in daily}
    assert "glm-5-1" in today_models
    assert "MiniMax-M2.7-highspeed" in today_models


def test_recent_logs(db):
    db.record("glm-5-1", "ark", prompt_tokens=100, completion_tokens=50, status_code=200)
    logs = db.get_recent_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["model"] == "glm-5-1"
    assert logs[0]["channel"] == "ark"
    assert logs[0]["status_code"] == 200


def test_recent_logs_with_offset(db):
    for i in range(5):
        db.record("model-a", "ark", prompt_tokens=i * 10)
    logs = db.get_recent_logs(limit=2, offset=0)
    assert len(logs) == 2
    # Should be newest first
    assert logs[0]["id"] > logs[1]["id"]


def test_record_failed_request(db):
    db.record("glm-5-1", "ark", status_code=429, latency_ms=100)
    logs = db.get_recent_logs()
    assert logs[0]["status_code"] == 429


def test_empty_db(db):
    assert db.get_total_stats()["total_requests"] == 0
    assert db.get_summary() == []
    assert db.get_daily() == []
    assert db.get_recent_logs() == []


def test_parse_usage_from_sse_chunk():
    from proxy import _parse_usage_from_chunk

    # Normal usage chunk
    line = 'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":50}}'
    usage = _parse_usage_from_chunk(line)
    assert usage is not None
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 50
    assert usage["cached_tokens"] == 0


def test_parse_usage_with_cached_tokens():
    from proxy import _parse_usage_from_chunk

    line = 'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":50,"prompt_tokens_details":{"cached_tokens":30}}}'
    usage = _parse_usage_from_chunk(line)
    assert usage is not None
    assert usage["cached_tokens"] == 30


def test_parse_usage_done_chunk():
    from proxy import _parse_usage_from_chunk

    assert _parse_usage_from_chunk("data: [DONE]") is None
    assert _parse_usage_from_chunk("data: {}") is None
    assert _parse_usage_from_chunk("") is None
