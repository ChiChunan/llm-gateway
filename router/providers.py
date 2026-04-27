"""Provider configuration: maps models to their LLM provider endpoints."""

import os
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    base_url: str
    api_key: str
    extra_headers: dict = field(default_factory=dict)

    def get_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        headers.update(self.extra_headers)
        return headers


# Model name -> channel identifier
MODEL_TO_CHANNEL: dict[str, str] = {
    "ark-code-latest": "ark",
    "doubao-seed-2-0-lite": "ark",
    "doubao-seed-2-0-pro": "ark",
    "glm-5-1": "ark",
    "kimi-for-coding": "kimi",
    "MiniMax-M2.7-highspeed": "minimax",
}

# Channel -> default model (for /v1/models listing)
CHANNEL_TO_DEFAULT_MODEL: dict[str, str] = {
    "ark": "doubao-seed-2-0-pro",
    "kimi": "kimi-for-coding",
    "minimax": "MiniMax-M2.7-highspeed",
}


def init_providers() -> dict[str, ProviderConfig]:
    """Initialize provider configs from environment variables."""
    providers = {}

    # ARK (火山引擎)
    ark_key = os.environ.get("ARK_API_KEY", "")
    ark_base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
    if ark_key:
        providers["ark"] = ProviderConfig(
            base_url=ark_base,
            api_key=ark_key,
        )

    # Kimi Coding
    kimi_key = os.environ.get("KIMI_API_KEY", "")
    kimi_base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
    if kimi_key:
        providers["kimi"] = ProviderConfig(
            base_url=kimi_base,
            api_key=kimi_key,
            extra_headers={
                "anthropic-version": "2023-06-01",
                "User-Agent": "claude-code/1.0",
            },
        )

    # MiniMax
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    minimax_base = os.environ.get("MINIMAX_BASE_URL", "https://v2.aicodee.com/v1")
    if minimax_key:
        providers["minimax"] = ProviderConfig(
            base_url=minimax_base,
            api_key=minimax_key,
        )

    return providers


def get_channel_for_model(model: str) -> str:
    """Return the channel identifier for a model name."""
    return MODEL_TO_CHANNEL.get(model, "unknown")


def get_provider_for_model(model: str, providers: dict[str, ProviderConfig]) -> ProviderConfig | None:
    """Return the provider config for a model, or None if not found."""
    channel = get_channel_for_model(model)
    return providers.get(channel)
