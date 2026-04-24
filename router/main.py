import json as _json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

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
    cfg["routing"].get("coordinator_candidates", [])
    + cfg["routing"].get("writer_candidates", [])
    + cfg["routing"].get("executor_candidates", [])
    + cfg["routing"].get("coordinator_fallback", [])
    + cfg["routing"].get("writer_fallback", [])
    + cfg["routing"].get("executor_fallback", [])
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


def _pick_model(candidates: list[str], session_key: str | None = None) -> tuple[str, object] | tuple[None, None]:
    """遍历候选列表，返回第一个可用且有 provider 的 (model, provider)。

    session_key 非空时做一致性哈希：同一 session 固定起始模型，不可用时顺移。
    """
    if not candidates:
        return None, None
    if session_key:
        import hashlib
        start = int(hashlib.md5(session_key.encode()).hexdigest(), 16) % len(candidates)
        ordered = candidates[start:] + candidates[:start]
    else:
        ordered = candidates
    for model in ordered:
        if not channel_mgr.is_available(model):
            continue
        provider = get_provider_for_model(model, providers)
        if provider is not None:
            return model, provider
    return None, None


def _get_session_key(messages: list[dict]) -> str | None:
    """取 messages[0] 的内容前 200 字符作为 session 标识。"""
    if not messages:
        return None
    content = messages[0].get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)[:200] or None


GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "V.A.L.O.R.")

_PUBLIC_PATHS = {"/health", "/dashboard"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/static") or request.url.path.startswith("/api/stats"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {GATEWAY_API_KEY}":
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)

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
        routing.get("coordinator_candidates", [])
        + routing.get("writer_candidates", [])
        + routing.get("executor_candidates", [])
    )
    # fallback candidates 与主候选重叠，不单独列出
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
    import logging
    logging.warning(f"HEADERS: {dict(request.headers)}")
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
        session_key = _get_session_key(messages)

        if complexity == Complexity.COORDINATOR:
            target_model, target_provider = _pick_model(routing.get("coordinator_candidates", []), session_key)
            if target_model is None:
                target_model, target_provider = _pick_model(routing.get("coordinator_fallback", []), session_key)
        elif complexity == Complexity.WRITER:
            target_model, target_provider = _pick_model(routing.get("writer_candidates", []), session_key)
            if target_model is None:
                target_model, target_provider = _pick_model(routing.get("writer_fallback", []), session_key)
        else:  # EXECUTOR
            target_model, target_provider = _pick_model(routing.get("executor_candidates", []), session_key)
            if target_model is None:
                target_model, target_provider = _pick_model(routing.get("executor_fallback", []), session_key)

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
