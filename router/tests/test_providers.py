import pytest
import os
from unittest.mock import patch
from providers import (
    ProviderConfig,
    MODEL_TO_CHANNEL,
    init_providers,
    get_channel_for_model,
    get_provider_for_model,
)


def test_model_to_channel_mapping():
    assert MODEL_TO_CHANNEL["doubao-seed-2-0-lite"] == "ark"
    assert MODEL_TO_CHANNEL["doubao-seed-2-0-pro"] == "ark"
    assert MODEL_TO_CHANNEL["glm-5-1"] == "ark"
    assert MODEL_TO_CHANNEL["kimi-for-coding"] == "kimi"
    assert MODEL_TO_CHANNEL["MiniMax-M2.7-highspeed"] == "minimax"


def test_get_channel_for_model():
    assert get_channel_for_model("glm-5-1") == "ark"
    assert get_channel_for_model("unknown") == "unknown"


def test_provider_config_headers():
    pc = ProviderConfig(base_url="http://x", api_key="test-key")
    headers = pc.get_headers()
    assert headers["Authorization"] == "Bearer test-key"


def test_provider_config_extra_headers():
    pc = ProviderConfig(
        base_url="http://x",
        api_key="test-key",
        extra_headers={"anthropic-version": "2023-06-01"},
    )
    headers = pc.get_headers()
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["anthropic-version"] == "2023-06-01"


@patch.dict(os.environ, {
    "ARK_API_KEY": "ark-key",
    "KIMI_API_KEY": "kimi-key",
    "MINIMAX_API_KEY": "minimax-key",
    "MINIMAX_BASE_URL": "https://minimax.example.com/v1",
})
def test_init_providers_all():
    providers = init_providers()
    assert "ark" in providers
    assert "kimi" in providers
    assert "minimax" in providers
    assert providers["ark"].api_key == "ark-key"
    assert providers["kimi"].api_key == "kimi-key"
    assert providers["kimi"].extra_headers == {"anthropic-version": "2023-06-01"}
    assert providers["minimax"].api_key == "minimax-key"
    assert providers["minimax"].base_url == "https://minimax.example.com/v1"


@patch.dict(os.environ, {"ARK_API_KEY": "ark-key"}, clear=False)
def test_init_providers_partial():
    # Clear other keys for this test
    env = os.environ.copy()
    for k in ["KIMI_API_KEY", "MINIMAX_API_KEY"]:
        env.pop(k, None)
    with patch.dict(os.environ, env, clear=True):
        providers = init_providers()
    assert "ark" in providers
    assert "kimi" not in providers
    assert "minimax" not in providers


def test_get_provider_for_model():
    providers = {
        "ark": ProviderConfig(base_url="http://ark", api_key="k"),
        "kimi": ProviderConfig(base_url="http://kimi", api_key="k"),
    }
    assert get_provider_for_model("glm-5-1", providers) is providers["ark"]
    assert get_provider_for_model("kimi-for-coding", providers) is providers["kimi"]
    assert get_provider_for_model("unknown-model", providers) is None
