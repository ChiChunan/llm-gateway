import pytest
from datetime import datetime, timedelta, timezone
from channel import ChannelManager

CST = timezone(timedelta(hours=8))


def make_manager():
    return ChannelManager(channels=["ark", "kimi", "minimax"])


def test_all_channels_available_initially():
    mgr = make_manager()
    assert mgr.is_available("ark") is True
    assert mgr.is_available("kimi") is True
    assert mgr.is_available("minimax") is True


def test_mark_unavailable_then_check():
    mgr = make_manager()
    future = datetime.now(CST) + timedelta(hours=5)
    mgr.mark_unavailable("ark", until=future)
    assert mgr.is_available("ark") is False


def test_channel_recovers_after_reset_time():
    mgr = make_manager()
    past = datetime.now(CST) - timedelta(seconds=1)
    mgr.mark_unavailable("ark", until=past)
    assert mgr.is_available("ark") is True


def test_parse_ark_429_message_5h():
    mgr = make_manager()
    msg = "You have exceeded the 5-hour usage quota. It will reset at 2026-04-23 17:01:14 +0800 CST."
    reset = mgr.parse_reset_time(msg)
    assert reset is not None
    assert reset.year == 2026
    assert reset.hour == 17


def test_parse_429_message_unknown_format():
    mgr = make_manager()
    msg = "Some unknown error message without reset time."
    reset = mgr.parse_reset_time(msg)
    assert reset is None


def test_available_channels_filters_unavailable():
    mgr = make_manager()
    future = datetime.now(CST) + timedelta(hours=5)
    mgr.mark_unavailable("ark", until=future)
    mgr.mark_unavailable("kimi", until=future)
    available = mgr.available_channels()
    assert "ark" not in available
    assert "kimi" not in available
    assert "minimax" in available


def test_earliest_recovery_time():
    mgr = make_manager()
    t1 = datetime.now(CST) + timedelta(hours=1)
    t2 = datetime.now(CST) + timedelta(hours=3)
    mgr.mark_unavailable("ark", until=t1)
    mgr.mark_unavailable("kimi", until=t2)
    earliest = mgr.earliest_recovery()
    assert earliest == t1


def test_handle_429_unknown_channel():
    mgr = make_manager()
    # Should not crash on unknown channel
    mgr.handle_429("nonexistent", "error message")


def test_handle_429_with_default_ban():
    mgr = make_manager()
    mgr.handle_429("ark", "Rate limit exceeded without reset time")
    assert mgr.is_available("ark") is False
