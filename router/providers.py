"""Provider configuration: maps models to their LLM provider endpoints."""

import os
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    base_url: str
    api_key: str
    extra_headers: dict = field(default_factory=dict)
    # 自定义认证 header 名，None 时使用默认的 Authorization: Bearer
    auth_header: str | None = None

    def get_headers(self) -> dict[str, str]:
        if self.auth_header:
            headers = {self.auth_header: self.api_key}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}"}
        headers.update(self.extra_headers)
        return headers


# Model name -> channel identifier
MODEL_TO_CHANNEL: dict[str, str] = {
    "doubao-seed-2-0-lite": "ark",
    "doubao-seed-2-0-pro": "ark",
    "deepseek-v4-pro": "ark",
    "deepseek-v4-flash": "ark",
    "MiniMax-M2.7-highspeed": "minimax",
    "mimo-v2.5-pro": "xiaomi",
    "mimo-v2.5": "xiaomi",
    "LongCat-2.0-Preview": "longcat",
    "deepseek-v4-flash-aliyun": "aliyuncs",
    "deepseek-v4-flash": "ark",
}

# Channel -> default model (for /v1/models listing)
CHANNEL_TO_DEFAULT_MODEL: dict[str, str] = {
    "ark": "deepseek-v4-pro",
    "minimax": "MiniMax-M2.7-highspeed",
    "xiaomi": "mimo-v2.5-pro",
    "longcat": "LongCat-2.0-Preview",
    "aliyuncs": "deepseek-v4-flash-aliyun",
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

    # DeepSeek
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    if deepseek_key:
        providers["deepseek"] = ProviderConfig(
            base_url=deepseek_base,
            api_key=deepseek_key,
        )

    # Xiaomi Mimo
    mimo_key = os.environ.get("MIMO_API_KEY", "")
    mimo_base = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
    if mimo_key:
        providers["xiaomi"] = ProviderConfig(
            base_url=mimo_base,
            api_key=mimo_key,
            auth_header="api-key",
        )

    # LongCat
    longcat_key = os.environ.get("LONGCAT_API_KEY", "")
    longcat_base = os.environ.get("LONGCAT_BASE_URL", "https://api.longcat.chat/openai")
    if longcat_key:
        providers["longcat"] = ProviderConfig(
            base_url=longcat_base,
            api_key=longcat_key,
        )

    # DashScope (阿里云百炼)
    dashscope_key = os.environ.get("ALIYUNCS_API_KEY", "")
    dashscope_base = os.environ.get("ALIYUNCS_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if dashscope_key:
        providers["aliyuncs"] = ProviderConfig(
            base_url=dashscope_base,
            api_key=dashscope_key,
        )

    return providers


def get_channel_for_model(model: str) -> str:
    """Return the channel identifier for a model name."""
    return MODEL_TO_CHANNEL.get(model, "unknown")


def get_provider_for_model(model: str, providers: dict[str, ProviderConfig]) -> ProviderConfig | None:
    """Return the provider config for a model, or None if not found."""
    channel = get_channel_for_model(model)
    return providers.get(channel)
