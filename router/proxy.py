import httpx
from fastapi.responses import StreamingResponse, JSONResponse

# connect 超时 10s，read 超时 120s（兼顾快速失败与长流式响应）
_TIMEOUT = httpx.Timeout(10.0, read=120.0)

# 模型名到渠道标识的映射表
MODEL_TO_CHANNEL: dict[str, str] = {
    "doubao-seed-2-0-lite": "ark_lite",
    "doubao-seed-2-0-pro": "ark_pro",
    "glm-5-1": "ark_glm",
}


def build_forwarded_request(original: dict, target_model: str) -> dict:
    """将原始请求体中的 model 替换为目标模型，其余字段原样透传。"""
    return {**original, "model": target_model}


def extract_channel_from_model(model: str) -> str:
    """根据模型名返回对应渠道标识，未命中时返回 'unknown'。"""
    return MODEL_TO_CHANNEL.get(model, "unknown")


async def forward_request(
    body: dict,
    target_model: str,
    new_api_base: str,
    new_api_key: str,
    on_error: "callable | None" = None,
) -> StreamingResponse | JSONResponse:
    """转发请求到 new-api，流式请求透传 SSE，非流式返回 JSON。"""
    payload = build_forwarded_request(body, target_model)
    is_stream = payload.get("stream", False)
    if is_stream:
        return await _stream_response(payload, new_api_base, new_api_key, on_error=on_error)
    return await _json_response(payload, new_api_base, new_api_key)


async def _stream_response(
    payload: dict,
    base: str,
    key: str,
    on_error: "callable | None" = None,
) -> StreamingResponse:
    """逐字节透传 SSE 流式响应，上游错误通过 on_error callback 通知调用方。"""
    import json as _json

    async def generate():
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            ) as resp:
                # 上游返回 4xx/5xx 时，通知调用方并透传错误事件
                if resp.status_code >= 400:
                    body_bytes = await resp.aread()
                    if on_error is not None:
                        await on_error(resp.status_code, body_bytes.decode(errors="replace"))
                    err = _json.dumps({"error": "upstream error", "status": resp.status_code})
                    yield f"data: {err}\n\n".encode()
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _json_response(payload: dict, base: str, key: str) -> JSONResponse:
    """发送普通请求并返回 JSON 响应。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
    # 防御上游返回非 JSON 内容导致解析异常
    try:
        content = resp.json()
    except Exception:
        content = {"error": "invalid upstream response", "raw": resp.text[:500]}
    return JSONResponse(content=content, status_code=resp.status_code)
