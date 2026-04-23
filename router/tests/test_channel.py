import pytest
from datetime import datetime, timedelta, timezone
from channel import ChannelManager

CST = timezone(timedelta(hours=8))

def make_manager():
    return ChannelManager(channels=["volc_lite", "volc_pro", "kimi_8k", "kimi_128k"])

def test_all_channels_available_initially():
    mgr = make_manager()
    assert mgr.is_available("volc_lite") is True
    assert mgr.is_available("volc_pro") is True

def test_mark_unavailable_then_check():
    mgr = make_manager()
    future = datetime.now(CST) + timedelta(hours=5)
    mgr.mark_unavailable("volc_lite", until=future)
    assert mgr.is_available("volc_lite") is False

def test_channel_recovers_after_reset_time():
    mgr = make_manager()
    past = datetime.now(CST) - timedelta(seconds=1)
    mgr.mark_unavailable("volc_lite", until=past)
    assert mgr.is_available("volc_lite") is True

def test_parse_volc_429_message_5h():
    mgr = make_manager()
    msg = "You have exceeded the 5-hour usage quota. It will reset at 2026-04-23 17:01:14 +0800 CST."
    reset = mgr.parse_reset_time(msg)
    assert reset is not None
    assert reset.year == 2026
    assert reset.hour == 17

def test_parse_volc_429_message_unknown_format():
    mgr = make_manager()
    msg = "Some unknown error message without reset time."
    reset = mgr.parse_reset_time(msg)
    assert reset is None

def test_available_channels_filters_unavailable():
    mgr = make_manager()
    future = datetime.now(CST) + timedelta(hours=5)
    mgr.mark_unavailable("volc_lite", until=future)
    mgr.mark_unavailable("volc_pro", until=future)
    available = mgr.available_channels()
    assert "volc_lite" not in available
    assert "volc_pro" not in available
    assert "kimi_8k" in available

def test_earliest_recovery_time():
    mgr = make_manager()
    t1 = datetime.now(CST) + timedelta(hours=1)
    t2 = datetime.now(CST) + timedelta(hours=3)
    mgr.mark_unavailable("volc_lite", until=t1)
    mgr.mark_unavailable("volc_pro", until=t2)
    earliest = mgr.earliest_recovery()
    assert earliest == t1
