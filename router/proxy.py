import json as _json
import logging
import time
import uuid
from typing import Callable, Literal, Optional

import httpx
from fastapi.responses import StreamingResponse, JSONResponse

from providers import ProviderConfig, get_channel_for_model
from usage import get_usage_db

# Connect timeout 10s
# Stream responses need a longer read timeout (SSE keeps the connection open)
# Non-stream responses can be shorter for fast failure
_TIMEOUT_STREAM = httpx.Timeout(10.0, read=300.0)
_TIMEOUT_JSON = httpx.Timeout(10.0, read=120.0)
# Global connection pool: max 100 connections per host, idle connections recycled after 30s
# Reuse connections on reconnect, eliminating new TCP+TLS handshake overhead
_LIMITS = httpx.Limits(max_keepalive_connections=100, max_connections=100, keepalive_expiry=30.0)
_CLIENT: httpx.AsyncClient | None = None

# Supported output format literals
OutputFormat = Literal["openai", "anthropic"]


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
    output_format: OutputFormat = "openai",
) -> StreamingResponse | JSONResponse:
    """Forward request to the appropriate provider, streaming SSE or returning JSON.

    Args:
        output_format: "openai" for native passthrough, "anthropic" to convert to Anthropic SSE/JSON.
    """
    payload = build_forwarded_request(body, target_model)
    is_stream = payload.get("stream", False)
    start = time.monotonic()

    if is_stream:
        return await _stream_response(
            payload, target_model, channel, provider, start,
            on_error=on_error, role=role, output_format=output_format,
        )
    return await _json_response(
        payload, target_model, channel, provider, start,
        role=role, output_format=output_format,
    )


async def _stream_response(
    payload: dict,
    target_model: str,
    channel: str,
    provider: ProviderConfig,
    start: float,
    on_error: "Callable | None" = None,
    role: str = "unknown",
    output_format: OutputFormat = "openai",
) -> StreamingResponse:
    """Stream SSE response byte-by-byte, notify caller of upstream errors via on_error callback.

    Also extracts usage from the final SSE chunk and records it.

    When output_format is "anthropic", the upstream OpenAI SSE chunks are converted
    to Anthropic SSE format before yielding, and the response media type is
    "text/event-stream".
    """
    db = get_usage_db()
    accumulated_usage: dict | None = None

    async def generate():
        nonlocal accumulated_usage
        final_status = 200
        try:
            client = get_client()
            # Streaming requests need a longer read timeout
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
                    if output_format == "anthropic":
                        yield f"event: error\ndata: {err}\n\n".encode("utf-8")
                    else:
                        yield f"data: {err}\n\n".encode("utf-8")
                    return

                if output_format == "anthropic":
                    # Stateful converter: tracks whether message_start has been emitted
                    converter = AnthropicSSEConverter(target_model=target_model)
                    accumulated_usage = None
                    async for line in resp.aiter_lines():
                        usage = _parse_usage_from_chunk(line)
                        if usage is not None:
                            accumulated_usage = usage
                        for out_line in converter.feed(line):
                            yield (out_line + "\n").encode("utf-8")
                else:
                    # OpenAI native passthrough
                    async for line in resp.aiter_lines():
                        usage = _parse_usage_from_chunk(line)
                        if usage is not None:
                            accumulated_usage = usage
                        yield (line + "\n").encode("utf-8")
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

    media_type = "text/event-stream"
    return StreamingResponse(generate(), media_type=media_type)


async def _json_response(
    payload: dict,
    target_model: str,
    channel: str,
    provider: ProviderConfig,
    start: float,
    role: str = "unknown",
    output_format: OutputFormat = "openai",
) -> JSONResponse:
    """Send a non-streaming request and return the JSON response.

    Also extracts usage from the response and records it.

    When output_format is "anthropic", the upstream OpenAI JSON response is
    converted to Anthropic message format before returning.
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

    if output_format == "anthropic":
        content = convert_openai_to_anthropic_response(content)

    return JSONResponse(content=content, status_code=resp.status_code)


class AnthropicSSEConverter:
    """Converts OpenAI SSE stream chunks into Anthropic SSE event sequence.

    The Anthropic streaming protocol requires a strictly ordered sequence of events:

        1. event: ping                          (optional, sent every ~5s by upstream)
        2. event: message_start   + data: {...}
        3. event: content_block_start + data: {"index":0,"type":"content_block_start",...}
        4. event: content_block_delta (x N) + data: {"index":0,"type":"content_block_delta",...}
        5. event: content_block_stop  + data: {"index":0,"type":"content_block_stop"}
        6. event: message_delta      + data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},...}
        7. event: message_stop        + data: {"type":"message_stop"}

    OpenAI SSE chunk examples:
        data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}
        data: {"choices":[{"index":0,"delta":{"content":" there"},"finish_reason":null}]}
        data: {"choices":[{"index":0,"finish_reason":"stop"}]}
        data: [DONE]

    This converter is stateful — call feed() for each raw SSE line in order.
    It buffers the first chunk (the role+initial-content chunk) so that the
    message_start event can include the model name extracted from the chunk.
    """

    _State = Literal[
        "idle",       # waiting for first data line
        "started",    # message_start emitted, awaiting content chunks
        "done",       # message_stop emitted, no more output
    ]

    def __init__(self, target_model: str = "unknown-model"):
        self._state: AnthropicSSEConverter._State = "idle"
        self._model = target_model
        # Buffer: (delta_obj, finish_reason, usage_dict)
        self._first_chunk: dict | None = None
        self._finish_reason: str | None = None
        self._usage: dict | None = None

    def feed(self, line: str) -> list[str]:
        """Process one raw SSE line from upstream.

        Returns a list of Anthropic SSE output lines (no trailing newline).
        Empty list means no output for this input line.
        """
        if self._state == "done":
            return []

        # Non-data lines (comments, empty lines) are passed through as-is
        if not line.startswith("data:"):
            return [line]

        raw = line[5:].strip()

        # OpenAI SSE terminator — convert to Anthropic message_stop
        if raw == "[DONE]":
            self._state = "done"
            # If we never got a role chunk, emit a minimal message sequence now
            if self._state != "started":
                return [
                    "event: message_start",
                    f"data: {_json.dumps({'type':'message_start','message':{'id':f'msg_{uuid.uuid4().hex[:8]}','type':'message','role':'assistant','content':[],'model':self._model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':0}}})}",
                    "",
                    "event: content_block_start",
                    f"data: {_json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}",
                    "",
                    "event: content_block_stop",
                    "data: {\"type\":\"content_block_stop\",\"index\":0}",
                    "",
                    "event: message_delta",
                    f"data: {_json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':0}})}",
                    "",
                    "event: message_stop",
                    "data: {\"type\":\"message_stop\"}",
                ]
            return [
                "event: content_block_stop",
                "data: {\"type\":\"content_block_stop\",\"index\":0}",
                "",
                "event: message_delta",
                f"data: {_json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':self._usage or {}})}",
                "",
                "event: message_stop",
                "data: {\"type\":\"message_stop\"}",
            ]

        try:
            obj = _json.loads(raw)
        except Exception:
            return [line]

        choices: list = obj.get("choices", [])
        if not choices or not isinstance(choices, list):
            return [line]

        first = choices[0]
        delta: dict = first.get("delta", {})
        finish_reason = first.get("finish_reason")
        self._usage = obj.get("usage")

        # -- Extract message metadata from first chunk --
        if self._first_chunk is None:
            # First chunk: captures role, initial content, finish_reason
            self._first_chunk = delta
            self._finish_reason = finish_reason
            if finish_reason:
                # No content — finish_reason alone means an empty response
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                self._state = "done"
                return [
                    "event: message_start",
                    f"data: {_json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':self._model,'stop_reason':_finish_reason_to_anthropic(finish_reason),'stop_sequence':None,'usage':self._usage or {'input_tokens':0,'output_tokens':0}}})}",
                    "",
                    "event: content_block_start",
                    f"data: {_json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}",
                    "",
                    "event: content_block_stop",
                    "data: {\"type\":\"content_block_stop\",\"index\":0}",
                    "",
                    "event: message_delta",
                    f"data: {_json.dumps({'type':'message_delta','delta':{'stop_reason':_finish_reason_to_anthropic(finish_reason)},'usage':self._usage or {}})}",
                    "",
                    "event: message_stop",
                    "data: {\"type\":\"message_stop\"}",
                ]

            # Defer message_start until we know if there will be actual content
            # Store role and emit nothing yet — will emit on next content chunk
            role = delta.get("role", "")
            content = delta.get("content", "")
            if content:
                # role+content in same chunk
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                self._state = "started"
                return [
                    "event: message_start",
                    f"data: {_json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':role,'content':[],'model':self._model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':0}}})}",
                    "",
                    "event: content_block_start",
                    f"data: {_json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}",
                    "",
                    "event: content_block_delta",
                    f"data: {_json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':content}})}",
                ]
            # role only — wait for content in next chunk
            return []

        # -- Subsequent chunks --
        if self._state != "started":
            # We have a buffered role but no content block started yet
            role = self._first_chunk.get("role", "assistant")
            msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            self._state = "started"
            out = [
                "event: message_start",
                f"data: {_json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':role,'content':[],'model':self._model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':0}}})}",
                "",
                "event: content_block_start",
                f"data: {_json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}",
            ]
        else:
            out = []

        content = delta.get("content", "")

        if finish_reason:
            self._state = "done"
            if content:
                out.extend([
                    "",
                    "event: content_block_delta",
                    f"data: {_json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':content}})}",
                    "",
                    "event: content_block_stop",
                    "data: {\"type\":\"content_block_stop\",\"index\":0}",
                    "",
                    "event: message_delta",
                    f"data: {_json.dumps({'type':'message_delta','delta':{'stop_reason':_finish_reason_to_anthropic(finish_reason),'stop_sequence':None},'usage':self._usage or {}})}",
                    "",
                    "event: message_stop",
                    "data: {\"type\":\"message_stop\"}",
                ])
            else:
                out.extend([
                    "",
                    "event: content_block_stop",
                    "data: {\"type\":\"content_block_stop\",\"index\":0}",
                    "",
                    "event: message_delta",
                    f"data: {_json.dumps({'type':'message_delta','delta':{'stop_reason':_finish_reason_to_anthropic(finish_reason),'stop_sequence':None},'usage':self._usage or {}})}",
                    "",
                    "event: message_stop",
                    "data: {\"type\":\"message_stop\"}",
                ])
        elif content:
            out.extend([
                "",
                "event: content_block_delta",
                f"data: {_json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':content}})}",
            ])

        return out


def _finish_reason_to_anthropic(reason: str | None) -> str:
    """Map OpenAI finish_reason to Anthropic stop_reason."""
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "content_filter",
    }
    if not reason:
        return "end_turn"
    return mapping.get(reason, reason)


def convert_openai_to_anthropic_response(openai_response: dict) -> dict:
    """将 OpenAI 格式的非流式响应转换为 Anthropic 格式。

    OpenAI:  choices[0].message.content -> Anthropic: content[0].text
    """
    anthropic_response = {
        "id": openai_response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": openai_response.get("model", ""),
        "usage": openai_response.get("usage", {}),
    }
    choices = openai_response.get("choices", [])
    if choices and len(choices) > 0:
        message = choices[0].get("message", {})
        content_text = message.get("content", "")
        anthropic_response["content"] = [{"type": "text", "text": content_text}]
        if message.get("reasoning_content"):
            anthropic_response["content"].insert(
                0, {"type": "text", "text": message["reasoning_content"]}
            )
        stop_reason = choices[0].get("finish_reason", "")
        anthropic_response["stop_reason"] = _finish_reason_to_anthropic(stop_reason)
    else:
        anthropic_response["content"] = []
        anthropic_response["stop_reason"] = None
    stop = openai_response.get("stop")
    if stop:
        anthropic_response["stop_sequence"] = stop if isinstance(stop, str) else (stop[0] if stop else None)
    else:
        anthropic_response["stop_sequence"] = None
    return anthropic_response


def convert_openai_sse_to_anthropic_sse(line: str, converter: AnthropicSSEConverter | None = None) -> list[str]:
    """Convert a single OpenAI SSE line to Anthropic SSE lines.

    If converter is None, a new instance is created (stateless single-line usage).
    For streaming, pass the same converter instance to preserve state across lines.
    """
    if converter is None:
        converter = AnthropicSSEConverter()
    return converter.feed(line)
