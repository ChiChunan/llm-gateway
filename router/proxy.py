import json as _json
import logging
import time
from typing import Callable, Optional

import httpx
from fastapi.responses import StreamingResponse, JSONResponse

from providers import ProviderConfig, get_channel_for_model
from usage import get_usage_db

# connect timeout 10s
# 流式响应需要更长的 read timeout（SSE 保持连接）
# 非流式响应可以短一些，快速失败
_TIMEOUT_STREAM = httpx.Timeout(10.0, read=300.0)
_TIMEOUT_JSON = httpx.Timeout(10.0, read=120.0)
# 全局连接池：每 host 最多 100 个连接，空闲连接 30s 后回收
# 重连时复用已有连接，消除新建 TCP+TLS 握手开销
_LIMITS = httpx.Limits(max_keepalive_connections=100, max_connections=100, keepalive_expiry=30.0)
_CLIENT: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """返回全局共享的 httpx 客户端（延迟创建，确保在 async 上下文中调用）。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(timeout=_TIMEOUT_JSON, limits=_LIMITS)
    return _CLIENT


def build_forwarded_request(original: dict, target_model: str) -> dict:
    """Replace model and apply per-model thinking-disable and other adjustments."""
    # 模型名映射：gateway 路由名 -> 实际 API 名
    _model_name_map = {
        "deepseek-v4-flash-aliyun": "deepseek-v4-flash",
    }
    payload = {**original, "model": _model_name_map.get(target_model, target_model)}
    if target_model.startswith("doubao"):
        payload.setdefault("reasoning_effort", "minimal")
    elif target_model.startswith("glm") or get_channel_for_model(target_model) == "kimi":
        payload.setdefault("thinking", {"type": "disabled"})
    elif target_model.startswith("deepseek") or target_model == "deepseek-v4-flash-aliyun":
        # deepseek 模型需要 thinking disabled，不管属于哪个 channel
        if get_channel_for_model(target_model) == "aliyuncs":
            # 百炼用 enable_thinking 参数（通过 extra_body）
            extra_body = payload.setdefault("extra_body", {})
            extra_body["enable_thinking"] = False
        else:
            payload.setdefault("thinking", {"type": "disabled"})
        payload.pop("reasoning_effort", None)
    elif get_channel_for_model(target_model) == "xiaomi":
        payload.setdefault("thinking", {"type": "disabled"})
        payload.pop("reasoning_effort", None)
    elif get_channel_for_model(target_model) == "longcat":
        payload.pop("reasoning_effort", None)
        payload.pop("thinking", None)
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


def _normalize_usage(usage: dict) -> dict:
    """将 provider usage 字段统一为内部格式。

    MiniMax 的 prompt_tokens 只统计未命中部分，cached_tokens 是命中部分，
    与 OpenAI 规范（prompt_tokens 含缓存）不同，需要合并为总输入。
    """
    prompt = usage.get("prompt_tokens", 0) or 0
    completion = usage.get("completion_tokens", 0) or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    # MiniMax 非标准：cached > prompt 说明 prompt_tokens 仅为未命中部分
    if cached > prompt:
        prompt = prompt + cached
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
    }


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
    return _normalize_usage(usage)


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
            client = get_client()
            # 流式请求需要更长的 read timeout
            async with client.stream(
                "POST",
                f"{provider.base_url}/chat/completions",
                headers=provider.get_headers(),
                json=payload,
                timeout=_TIMEOUT_STREAM,
            ) as resp:
                if resp.status_code >= 400:
                    final_status = resp.status_code
                    body_bytes = await resp.aread()
                    if on_error is not None:
                        await on_error(resp.status_code, body_bytes.decode(errors="replace"))
                    logging.warning(f"PROXY stream upstream {resp.status_code}: model={target_model} channel={channel} body={body_bytes[:300]}")
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
    client = get_client()
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
        u = _normalize_usage(usage)
        db.record(
            target_model, channel, role=role,
            prompt_tokens=u["prompt_tokens"],
            completion_tokens=u["completion_tokens"],
            cached_tokens=u["cached_tokens"],
            status_code=resp.status_code, latency_ms=latency,
        )
    else:
        db.record(target_model, channel, role=role, status_code=resp.status_code, latency_ms=latency)

    return JSONResponse(content=content, status_code=resp.status_code)
