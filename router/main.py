import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from channel import ChannelManager
from classifier import Classifier, Complexity
from proxy import extract_channel_from_model, forward_request
from providers import ProviderConfig, init_providers, get_provider_for_model
from usage import init_usage_db
from api_stats import router as stats_router
from dashboard import router as dashboard_router

CST = timezone(timedelta(hours=8))


def load_config(path: str = "routing.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


cfg = load_config()

# Initialize providers from environment variables
providers = init_providers()

if not providers:
    raise RuntimeError("No LLM providers configured. Set at least one of: ARK_API_KEY, KIMI_API_KEY, MINIMAX_API_KEY")

channel_mgr = ChannelManager(
    channels=list(providers.keys())
)

# Initialize usage tracking DB
usage_db = init_usage_db()

# Classifier uses ARK provider (doubao-seed-2-0-lite)
ark_provider = providers.get("ark")
if ark_provider:
    classifier = Classifier(
        base_url=ark_provider.base_url,
        api_key=ark_provider.api_key,
        model=cfg["routing"]["classifier"],
    )
else:
    # Fallback: try to use any available provider for classification
    first_provider = next(iter(providers.values()))
    classifier = Classifier(
        base_url=first_provider.base_url,
        api_key=first_provider.api_key,
        model=cfg["routing"]["classifier"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

# Mount stats API and dashboard
app.include_router(stats_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    available = channel_mgr.available_channels()
    earliest = channel_mgr.earliest_recovery()
    return {
        "available_channels": available,
        "providers": list(providers.keys()),
        "earliest_recovery": earliest.isoformat() if earliest else None,
    }


@app.get("/v1/models")
async def list_models():
    routing = cfg["routing"]
    model_ids = [
        "auto",
        routing["simple"],
        routing["complex"],
        routing["complex_fallback"],
        routing["simple_fallback"],
    ]
    # Deduplicate while preserving order
    seen = set()
    unique_models = []
    for m in model_ids:
        if m not in seen:
            seen.add(m)
            unique_models.append(m)
    return {
        "object": "list",
        "data": [{"id": m, "object": "model"} for m in unique_models],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    requested_model = body.get("model", "auto")
    messages = body.get("messages", [])

    routing = cfg["routing"]

    # If model is explicitly specified (not "auto"), route directly
    if requested_model != "auto":
        target_model = requested_model
        channel = extract_channel_from_model(target_model)
        if channel == "unknown" or channel not in providers:
            return JSONResponse(
                status_code=400,
                content={"error": f"unknown model: {target_model}"},
            )
        if not channel_mgr.is_available(channel):
            return JSONResponse(
                status_code=503,
                content={"error": f"channel {channel} is rate-limited"},
            )
        provider = providers[channel]
    else:
        # Smart routing: classify complexity and route accordingly
        last_msg = classifier.extract_last_user_message(messages)
        complexity = await classifier.classify(last_msg)

        if complexity == Complexity.SIMPLE:
            # Simple: MiniMax → ARK fallback
            target_model = routing["simple"]
            channel = extract_channel_from_model(target_model)
            if channel not in providers or not channel_mgr.is_available(channel):
                target_model = routing["simple_fallback"]
                channel = extract_channel_from_model(target_model)
        else:
            # Complex: glm-5-1 (ARK) → kimi-for-coding fallback → doubao fallback
            target_model = routing["complex"]
            channel = extract_channel_from_model(target_model)
            if channel not in providers or not channel_mgr.is_available(channel):
                target_model = routing["complex_fallback"]
                channel = extract_channel_from_model(target_model)
                if channel not in providers or not channel_mgr.is_available(channel):
                    target_model = routing["fallback"]
                    channel = extract_channel_from_model(target_model)

        # Final check: all channels exhausted
        if channel not in providers:
            return JSONResponse(
                status_code=503,
                content={"error": "no provider available for target model"},
            )
        if not channel_mgr.is_available(channel):
            earliest = channel_mgr.earliest_recovery()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "all channels unavailable",
                    "earliest_recovery": earliest.isoformat() if earliest else None,
                },
            )
        provider = providers[channel]

    async def _on_upstream_error(status_code: int, body: str):
        """Stream upstream error callback: mark channel on 429."""
        if status_code == 429:
            import json as _json
            try:
                err_msg = _json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            channel_mgr.handle_429(channel, err_msg)

    try:
        return await forward_request(
            body, target_model, channel, provider,
            on_error=_on_upstream_error,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            err_body = e.response.json()
            err_msg = err_body.get("error", {}).get("message", "")
            channel_mgr.handle_429(channel, err_msg)
            return JSONResponse(
                status_code=503,
                content={"error": "rate limited", "detail": err_msg},
            )
        return JSONResponse(status_code=502, content={"error": "upstream error"})
