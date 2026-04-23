import pytest
from proxy import build_forwarded_request, extract_channel_from_model

def test_build_forwarded_request_replaces_model():
    original = {
        "model": "auto",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    result = build_forwarded_request(original, target_model="doubao-pro-128k")
    assert result["model"] == "doubao-pro-128k"
    assert result["messages"] == original["messages"]
    assert result["stream"] is True

def test_build_forwarded_request_preserves_other_fields():
    original = {
        "model": "auto",
        "messages": [],
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    result = build_forwarded_request(original, target_model="doubao-lite-32k")
    assert result["temperature"] == 0.7
    assert result["max_tokens"] == 1000

def test_extract_channel_from_model_volc_lite():
    assert extract_channel_from_model("doubao-lite-32k") == "volc_lite"

def test_extract_channel_from_model_volc_pro():
    assert extract_channel_from_model("doubao-pro-128k") == "volc_pro"

def test_extract_channel_from_model_kimi():
    assert extract_channel_from_model("moonshot-v1-8k") == "kimi_8k"
    assert extract_channel_from_model("moonshot-v1-128k") == "kimi_128k"

def test_extract_channel_from_model_unknown():
    assert extract_channel_from_model("unknown-model") == "unknown"
