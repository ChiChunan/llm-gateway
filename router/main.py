import asyncio
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

CST = timezone(timedelta(hours=8))


def load_config(path: str = "routing.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _require_env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(f"环境变量 {name} 未设置")
    return val


cfg = load_config()

NEW_API_BASE = _require_env("NEW_API_BASE")
NEW_API_KEY = _require_env("NEW_API_KEY")

channel_mgr = ChannelManager(
    channels=["ark_lite", "ark_pro", "ark_glm"]
)

classifier = Classifier(
    base_url=NEW_API_BASE,
    api_key=NEW_API_KEY,
    model=cfg["routing"]["classifier"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    available = channel_mgr.available_channels()
    earliest = channel_mgr.earliest_recovery()
    return {
        "available_channels": available,
        "earliest_recovery": earliest.isoformat() if earliest else None,
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "auto", "object": "model"},
            {"id": cfg["routing"]["simple"], "object": "model"},
            {"id": cfg["routing"]["complex"], "object": "model"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    last_msg = classifier.extract_last_user_message(messages)
    complexity = await classifier.classify(last_msg)

    routing = cfg["routing"]
    target_model = (
        routing["simple"] if complexity == Complexity.SIMPLE else routing["complex"]
    )
    channel = extract_channel_from_model(target_model)

    if not channel_mgr.is_available(channel):
        fallback_model = routing["fallback"]
        fallback_channel = extract_channel_from_model(fallback_model)
        if not channel_mgr.is_available(fallback_channel):
            earliest = channel_mgr.earliest_recovery()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "all channels unavailable",
                    "earliest_recovery": earliest.isoformat() if earliest else None,
                },
            )
        target_model = fallback_model
        channel = fallback_channel

    async def _on_upstream_error(status_code: int, body: str):
        """流式请求上游错误回调：429 时触发渠道标记。"""
        if status_code == 429:
            import json as _json
            try:
                err_msg = _json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            channel_mgr.handle_429(channel, err_msg)

    try:
        return await forward_request(
            body, target_model, NEW_API_BASE, NEW_API_KEY,
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
