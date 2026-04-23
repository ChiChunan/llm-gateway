import httpx
from fastapi.responses import StreamingResponse, JSONResponse

# 模型名到渠道标识的映射表
MODEL_TO_CHANNEL: dict[str, str] = {
    "doubao-lite-32k": "volc_lite",
    "doubao-pro-128k": "volc_pro",
    "moonshot-v1-8k": "kimi_8k",
    "moonshot-v1-128k": "kimi_128k",
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
) -> StreamingResponse | JSONResponse:
    """转发请求到 new-api，流式请求透传 SSE，非流式返回 JSON。"""
    payload = build_forwarded_request(body, target_model)
    is_stream = payload.get("stream", False)
    if is_stream:
        return await _stream_response(payload, new_api_base, new_api_key)
    return await _json_response(payload, new_api_base, new_api_key)


async def _stream_response(payload: dict, base: str, key: str) -> StreamingResponse:
    """逐字节透传 SSE 流式响应。"""
    async def generate():
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _json_response(payload: dict, base: str, key: str) -> JSONResponse:
    """发送普通请求并返回 JSON 响应。"""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
