import pytest
from proxy import build_forwarded_request, extract_channel_from_model


def test_build_forwarded_request_replaces_model():
    original = {
        "model": "auto",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    result = build_forwarded_request(original, target_model="ark-code-latest")
    assert result["model"] == "ark-code-latest"
    assert result["messages"] == original["messages"]
    assert result["stream"] is True


def test_build_forwarded_request_preserves_other_fields():
    original = {
        "model": "auto",
        "messages": [],
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    # 非 MiniMax 模型，max_tokens 原样保留
    result = build_forwarded_request(original, target_model="ark-code-latest")
    assert result["temperature"] == 0.7
    assert result["max_tokens"] == 1000


def test_build_forwarded_request_minimax_bumps_max_tokens():
    original = {"model": "auto", "messages": [], "max_tokens": 1000}
    result = build_forwarded_request(original, target_model="MiniMax-M3")
    assert result["max_tokens"] == 8192


def test_extract_channel_from_model_ark_lite():
    assert extract_channel_from_model("doubao-seed-2-0-lite") == "ark"


def test_extract_channel_from_model_ark_pro():
    assert extract_channel_from_model("ark-code-latest") == "ark"


def test_extract_channel_from_model_ark_glm():
    assert extract_channel_from_model("glm-5-1") == "ark"


def test_extract_channel_from_model_kimi():
    assert extract_channel_from_model("kimi-for-coding") == "kimi"


def test_extract_channel_from_model_minimax():
    assert extract_channel_from_model("MiniMax-M3") == "minimax"


def test_extract_channel_from_model_unknown():
    assert extract_channel_from_model("unknown-model") == "unknown"
