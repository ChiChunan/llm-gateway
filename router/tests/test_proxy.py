import pytest
from proxy import build_forwarded_request, extract_channel_from_model


def test_build_forwarded_request_replaces_model():
    original = {
        "model": "auto",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    result = build_forwarded_request(original, target_model="doubao-seed-2-0-pro")
    assert result["model"] == "doubao-seed-2-0-pro"
    assert result["messages"] == original["messages"]
    assert result["stream"] is True


def test_build_forwarded_request_preserves_other_fields():
    original = {
        "model": "auto",
        "messages": [],
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    result = build_forwarded_request(original, target_model="MiniMax-M2.7-highspeed")
    assert result["temperature"] == 0.7
    assert result["max_tokens"] == 1000


def test_extract_channel_from_model_ark_lite():
    assert extract_channel_from_model("doubao-seed-2-0-lite") == "ark"


def test_extract_channel_from_model_ark_pro():
    assert extract_channel_from_model("doubao-seed-2-0-pro") == "ark"


def test_extract_channel_from_model_ark_glm():
    assert extract_channel_from_model("glm-5-1") == "ark"


def test_extract_channel_from_model_kimi():
    assert extract_channel_from_model("kimi-for-coding") == "kimi"


def test_extract_channel_from_model_minimax():
    assert extract_channel_from_model("MiniMax-M2.7-highspeed") == "minimax"


def test_extract_channel_from_model_unknown():
    assert extract_channel_from_model("unknown-model") == "unknown"
