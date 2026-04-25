import json as _json
import time
from typing import Callable, Optional

import httpx
from fastapi.responses import StreamingResponse, JSONResponse

from providers import ProviderConfig, get_channel_for_model
from usage import get_usage_db

# connect timeout 10s, read timeout 120s (balance fast-fail with long streaming)
_TIMEOUT = httpx.Timeout(10.0, read=120.0)


def build_forwarded_request(original: dict, target_model: str) -> dict:
    """Replace model and apply per-model thinking-disable and other adjustments."""
    payload = {**original, "model": target_model}
    if target_model.startswith("doubao"):
        payload.setdefault("reasoning_effort", "minimal")
    elif target_model.startswith("glm") or get_channel_for_model(target_model) == "kimi":
        payload.setdefault("thinking", {"type": "disabled"})
    elif get_channel_for_model(target_model) == "minimax":
        # MiniMax: no thinking-disable param; ensure budget isn't exhausted before content
        if payload.get("max_tokens", 8192) < 8192:
            payload["max_tokens"] = 8192
    # ARK 流式响应默认不返回 usage，需显式开启
    if payload.get("stream") and get_channel_for_model(target_model) == "ark":
        payload.setdefault("stream_options", {"include_usage": True})
    return payload


def extract_channel_from_model(model: str) -> str:
    """Return the channel identifier for a model name, or 'unknown'."""
    return get_channel_for_model(model)


def _parse_usage_from_chunk(line: str) -> dict | None:
    """Extract usage from an SSE data line like: data: {"choices":[],"usage":{...}}

    Returns {"prompt_tokens": N, "completion_tokens": N, "cached_tokens": N} or None.
    """
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return None
    try:
        obj = _json.loads(payload)
    except Exception:
        return None
    usage = obj.get("usage")
    if not usage or not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0,
    }


async def forward_request(
    body: dict,
    target_model: str,
    channel: str,
    provider: ProviderConfig,
    on_error: "Callable | None" = None,
    role: str = "unknown",
) -> StreamingResponse | JSONResponse:
    """Forward request to the appropriate provider, streaming SSE or returning JSON."""
    payload = build_forwarded_request(body, target_model)
    is_stream = payload.get("stream", False)
    start = time.monotonic()

    if is_stream:
        return await _stream_response(payload, target_model, channel, provider, start, on_error=on_error, role=role)
    return await _json_response(payload, target_model, channel, provider, start, role=role)


async def _stream_response(
    payload: dict,
    target_model: str,
    channel: str,
    provider: ProviderConfig,
    start: float,
    on_error: "Callable | None" = None,
    role: str = "unknown",
) -> StreamingResponse:
    """Stream SSE response byte-by-byte, notify caller of upstream errors via on_error callback.

    Also extracts usage from the final SSE chunk and records it.
    """
    db = get_usage_db()
    accumulated_usage: dict | None = None

    async def generate():
        nonlocal accumulated_usage
        final_status = 200
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{provider.base_url}/chat/completions",
                    headers=provider.get_headers(),
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        final_status = resp.status_code
                        body_bytes = await resp.aread()
                        if on_error is not None:
                            await on_error(resp.status_code, body_bytes.decode(errors="replace"))
                        err = _json.dumps({"error": "upstream error", "status": resp.status_code})
                        yield f"data: {err}\n\n".encode()
                        return
                    async for line in resp.aiter_lines():
                        usage = _parse_usage_from_chunk(line)
                        if usage is not None:
                            accumulated_usage = usage
                        yield (line + "\n\n").encode("utf-8")
        except Exception:
            final_status = 500
            raise
        finally:
            latency = int((time.monotonic() - start) * 1000)
            if accumulated_usage:
                db.record(
                    target_model, channel, role=role,
                    prompt_tokens=accumulated_usage["prompt_tokens"],
                    completion_tokens=accumulated_usage["completion_tokens"],
                    cached_tokens=accumulated_usage["cached_tokens"],
                    status_code=final_status, latency_ms=latency,
                )
            else:
                db.record(target_model, channel, role=role, status_code=final_status, latency_ms=latency)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _json_response(
    payload: dict,
    target_model: str,
    channel: str,
    provider: ProviderConfig,
    start: float,
    role: str = "unknown",
) -> JSONResponse:
    """Send a non-streaming request and return the JSON response.

    Also extracts usage from the response and records it.
    """
    db = get_usage_db()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{provider.base_url}/chat/completions",
            headers=provider.get_headers(),
            json=payload,
        )
    latency = int((time.monotonic() - start) * 1000)

    try:
        content = resp.json()
    except Exception:
        content = {"error": "invalid upstream response", "raw": resp.text[:500]}
        db.record(target_model, channel, role=role, status_code=resp.status_code, latency_ms=latency)
        return JSONResponse(content=content, status_code=resp.status_code)

    usage = content.get("usage")
    if usage and isinstance(usage, dict):
        db.record(
            target_model, channel, role=role,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            cached_tokens=usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0,
            status_code=resp.status_code, latency_ms=latency,
        )
    else:
        db.record(target_model, channel, role=role, status_code=resp.status_code, latency_ms=latency)

    return JSONResponse(content=content, status_code=resp.status_code)
