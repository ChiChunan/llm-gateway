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


def _optional_env(name: str, default: str) -> str:
    return os.environ.get(name, default) or default


cfg = load_config()

channel_mgr = ChannelManager(
    channels=["volc_lite", "volc_pro", "kimi_8k", "kimi_128k"]
)

NEW_API_BASE = _require_env("NEW_API_BASE")
NEW_API_KEY = _require_env("NEW_API_KEY")

KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
KIMI_BASE_URL = _optional_env("KIMI_BASE_URL", "https://api.moonshot.cn/v1")

classifier = Classifier(
    base_url=NEW_API_BASE,
    api_key=NEW_API_KEY,
    model=cfg["routing"]["classifier"],
)


async def _kimi_balance_watcher():
    interval = cfg.get("kimi", {}).get("balance_check_interval", 300)
    warn_threshold = cfg.get("thresholds", {}).get("kimi_balance_warn", 10.0)

    while True:
        await asyncio.sleep(interval)
        if not KIMI_API_KEY:
            continue
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{KIMI_BASE_URL}/users/me/balance",
                    headers={"Authorization": f"Bearer {KIMI_API_KEY}"},
                )
                data = resp.json()
                balance = float(data.get("balance", 9999))
                for ch in ["kimi_8k", "kimi_128k"]:
                    if balance < warn_threshold:
                        channel_mgr.mark_unavailable(
                            ch, until=datetime.now(CST) + timedelta(hours=24)
                        )
                    else:
                        channel_mgr.mark_unavailable(
                            ch, until=datetime.now(CST) - timedelta(seconds=1)
                        )
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_kimi_balance_watcher())
    yield
    task.cancel()


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

    try:
        return await forward_request(body, target_model, NEW_API_BASE, NEW_API_KEY)
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
