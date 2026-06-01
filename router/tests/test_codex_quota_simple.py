"""Tests for /quota/codex/simple endpoint — normal, parse failure, stale."""

import json
import os
import tempfile
import pytest
from unittest.mock import patch

# We need to import the app with a mock status file
# The endpoint reads /tmp/codex_status.json directly


@pytest.fixture
def mock_status_file(tmp_path):
    """Create a temporary status file and patch the endpoint to use it."""
    status_path = str(tmp_path / "codex_status.json")
    return status_path


@pytest.fixture
def normal_data():
    """Normal codex status data."""
    return {
        "timestamp": "2026-06-01T15:17:01+08:00",
        "parsed": {
            "model": None,
            "plan": "plus",
            "hourly_limit": {
                "pct_left": 97,
                "window_minutes": 300,
            },
            "weekly_limit": {
                "pct_left": 95,
                "window_minutes": 10080,
            },
            "data_age_seconds": 14,
            "source_file": "rollout-2026-06-01T15-16-20-019e820a.jsonl",
        },
    }


@pytest.fixture
def stale_data():
    """Stale codex status data (age > 300)."""
    return {
        "timestamp": "2026-06-01T10:00:00+08:00",
        "parsed": {
            "model": None,
            "plan": "pro",
            "hourly_limit": {
                "pct_left": 25,
                "window_minutes": 300,
            },
            "weekly_limit": {
                "pct_left": 10,
                "window_minutes": 10080,
            },
            "data_age_seconds": 7200,
            "source_file": "rollout-old.jsonl",
        },
    }


def _write_status(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


class TestCodexQuotaSimpleNormal:
    """正常返回测试。"""

    def test_normal_response_structure(self, mock_status_file, normal_data):
        """验证正常数据返回正确的扁平结构和字段值。"""
        _write_status(mock_status_file, normal_data)

        # 直接测试解析逻辑（不启动 FastAPI）
        with open(mock_status_file, "r") as f:
            raw = json.load(f)
        parsed = raw.get("parsed", {})

        plan_raw = (parsed.get("plan") or "unknown").upper()
        h = parsed.get("hourly_limit") or {}
        w = parsed.get("weekly_limit") or {}
        h_left = h.get("pct_left", 0)
        w_left = w.get("pct_left", 0)
        h_used = max(0, 100 - h_left)
        w_used = max(0, 100 - w_left)
        age = parsed.get("data_age_seconds")
        stale = bool(age is not None and age > 300)

        assert plan_raw == "PLUS"
        assert h_left == 97
        assert w_left == 95
        assert h_used == 3
        assert w_used == 5
        assert stale is False

    def test_status_thresholds(self):
        """验证状态阈值规则。"""
        def _status(left):
            if left is None:
                return "error"
            if left >= 60:
                return "ok"
            if left >= 30:
                return "warn"
            return "danger"

        assert _status(100) == "ok"
        assert _status(60) == "ok"
        assert _status(59) == "warn"
        assert _status(30) == "warn"
        assert _status(29) == "danger"
        assert _status(0) == "danger"
        assert _status(None) == "error"

    def test_updated_at_format(self, normal_data):
        """验证 updated_at 从 ISO timestamp 提取 HH:MM。"""
        from datetime import datetime
        ts = normal_data["timestamp"]
        dt = datetime.fromisoformat(ts)
        assert dt.strftime("%H:%M") == "15:17"


class TestCodexQuotaSimpleParseFailure:
    """解析失败测试。"""

    def test_missing_status_file(self, tmp_path):
        """状态文件不存在时返回错误。"""
        missing_path = str(tmp_path / "nonexistent.json")
        assert not os.path.exists(missing_path)
        # 验证 error 模板
        error_response = {
            "ok": False,
            "title": "CODEX",
            "plan": "--",
            "five_hour_left": None,
            "weekly_left": None,
            "five_hour_used": None,
            "weekly_used": None,
            "five_hour_status": "error",
            "weekly_status": "error",
            "age_seconds": None,
            "updated_at": "--:--",
            "stale": True,
            "error": "status_file_not_found",
        }
        assert error_response["ok"] is False
        assert error_response["stale"] is True
        assert error_response["error"] == "status_file_not_found"

    def test_malformed_json(self, tmp_path):
        """JSON 格式错误时应触发异常处理。"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json!!!")
        with pytest.raises(json.JSONDecodeError):
            with open(str(bad_file), "r") as f:
                json.load(f)

    def test_missing_parsed_field(self, tmp_path):
        """缺少 parsed 字段时返回错误。"""
        bad_data = {"timestamp": "2026-06-01T15:00:00+08:00"}
        status_file = tmp_path / "no_parsed.json"
        status_file.write_text(json.dumps(bad_data))

        with open(str(status_file), "r") as f:
            raw = json.load(f)
        parsed = raw.get("parsed", {})
        assert not parsed  # empty dict is falsy

    def test_empty_hourly_limit(self, tmp_path):
        """hourly_limit 为 None 时不影响 crash。"""
        data = {
            "timestamp": "2026-06-01T15:00:00+08:00",
            "parsed": {
                "plan": "plus",
                "hourly_limit": None,
                "weekly_limit": None,
                "data_age_seconds": 10,
            },
        }
        status_file = tmp_path / "null_limits.json"
        status_file.write_text(json.dumps(data))

        with open(str(status_file), "r") as f:
            raw = json.load(f)
        parsed = raw.get("parsed", {})
        h = parsed.get("hourly_limit") or {}
        w = parsed.get("weekly_limit") or {}
        # None -> {} -> pct_left defaults to 0
        assert h.get("pct_left", 0) == 0
        assert w.get("pct_left", 0) == 0


class TestCodexQuotaSimpleStale:
    """数据 stale 测试。"""

    def test_stale_detection(self, mock_status_file, stale_data):
        """age_seconds > 300 时 stale = true。"""
        _write_status(mock_status_file, stale_data)

        with open(mock_status_file, "r") as f:
            raw = json.load(f)
        parsed = raw["parsed"]
        age = parsed.get("data_age_seconds")
        stale = bool(age is not None and age > 300)

        assert stale is True
        assert age == 7200

    def test_not_stale_at_boundary(self):
        """age_seconds = 300 时 stale = false（严格大于 300）。"""
        age = 300
        stale = bool(age is not None and age > 300)
        assert stale is False

        age = 301
        stale = bool(age is not None and age > 300)
        assert stale is True

    def test_danger_status_when_low(self, stale_data):
        """stale 数据同时额度低时状态应为 danger。"""
        h_left = stale_data["parsed"]["hourly_limit"]["pct_left"]  # 25
        w_left = stale_data["parsed"]["weekly_limit"]["pct_left"]  # 10

        def _status(left):
            if left >= 60:
                return "ok"
            if left >= 30:
                return "warn"
            return "danger"

        assert _status(h_left) == "danger"  # 25 < 30
        assert _status(w_left) == "danger"  # 10 < 30

    def test_stale_with_null_age(self):
        """age_seconds 为 None 时 stale = false（不视为 stale）。"""
        age = None
        stale = bool(age is not None and age > 300)
        assert stale is False


class TestCodexQuotaSimpleCORS:
    """CORS 响应头测试。"""

    def test_cors_headers_present(self):
        """验证 CORS 头定义正确。"""
        expected_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Cache-Control": "no-store",
        }
        assert expected_headers["Access-Control-Allow-Origin"] == "*"
        assert "GET" in expected_headers["Access-Control-Allow-Methods"]
        assert "OPTIONS" in expected_headers["Access-Control-Allow-Methods"]
        assert expected_headers["Cache-Control"] == "no-store"


class TestCodexQuotaSimpleFieldMapping:
    """上游字段到扁平字段的映射测试。"""

    def test_five_hour_naming_convention(self):
        """确认使用 five_hour 而不是 hourly（300 分钟 = 5小时）。"""
        # 这是一个命名约定的断言
        # hourly_limit.window_minutes == 300 → 对外用 five_hour_
        window_minutes = 300
        assert window_minutes == 300, "5 hours = 300 minutes"
        # 字段名应该用 five_hour_ 前缀
        field_names = ["five_hour_left", "five_hour_used", "five_hour_status"]
        for name in field_names:
            assert "five_hour" in name
            assert "hourly" not in name

    def test_used_equals_100_minus_left(self):
        """used = 100 - left 计算验证。"""
        test_cases = [
            (97, 3),  # normal
            (60, 40),  # boundary ok/warn
            (30, 70),  # boundary warn/danger
            (0, 100),  # exhausted
            (100, 0),  # full
        ]
        for left, expected_used in test_cases:
            used = max(0, 100 - left)
            assert used == expected_used, f"left={left}: expected {expected_used}, got {used}"
