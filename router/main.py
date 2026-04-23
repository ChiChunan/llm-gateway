import json as _json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from channel import ChannelManager
from classifier import Classifier, Complexity
from proxy import forward_request
from providers import init_providers, get_channel_for_model, get_provider_for_model
from usage import init_usage_db
from api_stats import router as stats_router
from dashboard import router as dashboard_router

CST = timezone(timedelta(hours=8))


def load_config(path: str = "routing.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


cfg = load_config()

providers = init_providers()

if not providers:
    raise RuntimeError("No LLM providers configured. Set at least one of: ARK_API_KEY, KIMI_API_KEY, MINIMAX_API_KEY")

# ChannelManager 用 model name 粒度，支持 plan 联动
_all_models = list(dict.fromkeys(
    cfg["routing"].get("simple_candidates", [])
    + cfg["routing"].get("complex_candidates", [])
    + cfg["routing"].get("simple_fallback_candidates", [])
))

channel_mgr = ChannelManager(
    channels=_all_models,
    plans=cfg.get("plans", {}),
)

usage_db = init_usage_db()

ark_provider = providers.get("ark")
if ark_provider:
    classifier = Classifier(
        base_url=ark_provider.base_url,
        api_key=ark_provider.api_key,
        model=cfg["routing"]["classifier"],
    )
else:
    first_provider = next(iter(providers.values()))
    classifier = Classifier(
        base_url=first_provider.base_url,
        api_key=first_provider.api_key,
        model=cfg["routing"]["classifier"],
    )


def _pick_model(candidates: list[str]) -> tuple[str, object] | tuple[None, None]:
    """遍历候选列表，返回第一个可用且有 provider 的 (model, provider)。"""
    for model in candidates:
        if not channel_mgr.is_available(model):
            continue
        provider = get_provider_for_model(model, providers)
        if provider is not None:
            return model, provider
    return None, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

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
    all_candidates = (
        routing.get("simple_candidates", [])
        + routing.get("complex_candidates", [])
    )
    # simple_fallback_candidates 与 complex_candidates 重叠，不单独列出
    seen = set()
    models = [{"id": "auto", "object": "model"}]
    for m in all_candidates:
        if m not in seen:
            models.append({"id": m, "object": "model"})
            seen.add(m)
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    requested_model = body.get("model", "auto")
    messages = body.get("messages", [])
    routing = cfg["routing"]

    # 指定具体模型时直接路由，不走分类器
    if requested_model != "auto":
        provider = get_provider_for_model(requested_model, providers)
        if provider is None:
            return JSONResponse(
                status_code=400,
                content={"error": f"unknown model: {requested_model}"},
            )
        if not channel_mgr.is_available(requested_model):
            return JSONResponse(
                status_code=503,
                content={"error": f"model {requested_model} is rate-limited"},
            )
        target_model, target_provider = requested_model, provider
    else:
        last_msg = classifier.extract_last_user_message(messages)
        complexity = await classifier.classify(last_msg)

        if complexity == Complexity.SIMPLE:
            target_model, target_provider = _pick_model(routing.get("simple_candidates", []))
            if target_model is None:
                # ARK 超限，降级到 S 档非 ARK 候选
                target_model, target_provider = _pick_model(routing.get("simple_fallback_candidates", []))
        else:
            target_model, target_provider = _pick_model(routing.get("complex_candidates", []))

        if target_model is None:
            earliest = channel_mgr.earliest_recovery()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "all channels unavailable",
                    "earliest_recovery": earliest.isoformat() if earliest else None,
                },
            )

    # 流式请求的 429 通过回调触发；非流式请求的 429 通过下方 except 捕获
    async def _on_upstream_error(status_code: int, body: str):
        if status_code == 429:
            try:
                err_msg = _json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            channel_mgr.handle_429(target_model, err_msg)

    try:
        return await forward_request(
            body, target_model, get_channel_for_model(target_model), target_provider,
            on_error=_on_upstream_error,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            err_body = e.response.json()
            err_msg = err_body.get("error", {}).get("message", "")
            channel_mgr.handle_429(target_model, err_msg)
            return JSONResponse(
                status_code=503,
                content={"error": "rate limited", "detail": err_msg},
            )
        return JSONResponse(status_code=502, content={"error": "upstream error"})
