import json as _json
import json
import logging
import os
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import random

import httpx
import yaml
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

import provider_state as _pstate
from channel import ChannelManager
from classifier import Classifier, ClassifyResult, Complexity, keyword_classify
from proxy import forward_request, convert_openai_to_anthropic_response, convert_openai_sse_to_anthropic_sse, AnthropicSSEConverter
from providers import init_providers, get_channel_for_model, get_provider_for_model
from usage import init_usage_db
from api_stats import router as stats_router, config_router
from dashboard import router as dashboard_router

CST = timezone(timedelta(hours=8))

# 5xx/超时失败计数，用于指数退避（重启后清零）
_failure_counts: dict[str, int] = {}

def _backoff_seconds(model: str) -> int:
    """指数退避：60s → 120s → 300s → 3600s 上限。"""
    n = _failure_counts.get(model, 0)
    return min(60 * (2 ** n), 3600)

def _record_failure(model: str):
    _failure_counts[model] = _failure_counts.get(model, 0) + 1

def _clear_failure(model: str):
    _failure_counts.pop(model, None)


def load_config(path: str = "routing.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


cfg = load_config()

providers = init_providers()

if not providers:
    raise RuntimeError("No LLM providers configured. Set at least one of: ARK_API_KEY, KIMI_API_KEY, MINIMAX_API_KEY")

# ChannelManager 用 model name 粒度，支持 plan 联动
# 分类器模型也纳入管理，确保 plan 429 时能被联动封禁
_classifier_primary = cfg["routing"].get("classifier_primary", {})
_classifier_fallback_cfg = cfg["routing"].get("classifier_fallback", {})
_classifier_models = list(dict.fromkeys([
    _classifier_primary.get("model"),
    _classifier_fallback_cfg.get("model"),
]))
# 过滤掉空字符串和 None
_classifier_models = [m for m in _classifier_models if m]
_all_models = list(dict.fromkeys(
    [m for m in _classifier_models if m]
    + cfg["routing"].get("complex_candidates", [])
    + cfg["routing"].get("complex_fallback", [])
    + cfg["routing"].get("simple_candidates", [])
    + cfg["routing"].get("simple_fallback", [])
    + cfg["routing"].get("multimodal_candidates", [])  # 加入多模态候选
))

channel_mgr = ChannelManager(
    channels=_all_models,
    plans=cfg.get("plans", {}),
)

# 从配置加载 model 初始开关状态
for model, enabled in cfg.get("model_switches", {}).items():
    channel_mgr.set_model_enabled(model, bool(enabled))
_pstate.set_channel_mgr(channel_mgr, "routing.yaml")

usage_db = init_usage_db()


def _make_classifier(primary_cfg: dict) -> Classifier | None:
    """根据 {provider, model} 配置创建 Classifier 实例。"""
    if not primary_cfg:
        return None
    provider_name = primary_cfg.get("provider", "")
    model_name = primary_cfg.get("model", "")
    prov = providers.get(provider_name)
    if not prov:
        return None
    # 百炼的 API 模型名与 gateway 路由名不同，通过 extra_body 传入实际模型名
    extra_body = {}
    if provider_name == "aliyuncs":
        extra_body["model"] = "deepseek-v4-flash"
    return Classifier(base_url=prov.base_url, api_key=prov.api_key, model=model_name, extra_body=extra_body)


classifier_primary = _make_classifier(_classifier_primary)
classifier_fallback = _make_classifier(_classifier_fallback_cfg)

# 用于分类器不可用判断的字段
_classifier_primary_model = _classifier_primary.get("model", "") if _classifier_primary else ""
_classifier_fallback_model = _classifier_fallback_cfg.get("model", "")


def _pick_model(candidates: list[str], session_key: str | None = None) -> list[tuple[str, object]]:
    """返回按 session_key 打乱后的可用候选列表 [(model, provider), ...]。

    - 会预先过滤掉不可用、被禁用、或无 provider 的模型
    - 有 session_key 时：用 seeded shuffle 打乱列表，同 session 固定顺序，不同 session 均匀随机
    - 无 session_key 时：按过滤后原始顺序
    - 返回完整有序列表，调用方取 [0] 做首选，后续元素做降级重试
    """
    if not candidates:
        return []

    # 预过滤：只保留真正可用的模型
    available = []
    for model in candidates:
        provider = get_provider_for_model(model, providers)
        if provider is None:
            continue
        if not channel_mgr.is_model_enabled(model):
            continue
        if not channel_mgr.is_available(model):
            continue
        available.append(model)

    if not available:
        return []

    # 有 session_key：用 seeded shuffle 打乱列表，同 session 固定顺序，不同 session 均匀随机
    if session_key:
        seed = int(hashlib.sha256(session_key.encode()).hexdigest(), 16)
        ordered = available[:]
        random.Random(seed).shuffle(ordered)
        logging.debug(f"pick_model session_key={session_key!r} available={available} ordered={ordered}")
    else:
        ordered = available
        logging.debug(f"pick_model no_session_key available={available} ordered={ordered}")

    # 返回 (model, provider) 有序列表
    result = []
    for model in ordered:
        provider = get_provider_for_model(model, providers)
        if provider is not None:
            result.append((model, provider))
    return result


def _has_image(messages: list[dict]) -> bool:
    """检测 messages 中是否包含图片内容。

    只检查结构化的 list 格式 content（多模态消息格式），
    不做字符串关键词匹配，避免对话历史中提到图片文件名导致误判。
    """
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
    return False


def _get_session_key(messages: list[dict]) -> str | None:
    """取第一条 user 消息前200字符作为 session 标识，同一对话路由固定。"""
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = str(content)
        if content.startswith("User:"):
            content = content[5:].split("\nAssistant:")[0].strip()
        return content[:200] or None
    return None


_raw_gateway_key = os.environ.get("GATEWAY_API_KEY")
if not _raw_gateway_key:
    logging.critical(
        "GATEWAY_API_KEY is not set! Refusing to start without authentication. "
        "Set it in .env or environment."
    )
    raise SystemExit("GATEWAY_API_KEY is required")
GATEWAY_API_KEY = _raw_gateway_key

_PUBLIC_PATHS = {"/health", "/dashboard", "/v1/models"}

# 统计 API 只读端点白名单（公开，Dashboard 需要访问）
_STATS_READONLY_PATHS = {
    "/api/stats/summary", "/api/stats/daily", "/api/stats/hourly",
    "/api/stats/totals", "/api/stats/classifier", "/api/stats/roles",
}

# 需要认证的统计端点（含敏感日志数据）
_STATS_AUTH_PATHS = {"/api/stats/logs"}

# 配置 API 只读端点白名单（公开，Dashboard 需要读取模型状态）
_CONFIG_READONLY_GET_PATHS = {"/api/config/providers", "/api/config/models"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 1. 无条件公开路径
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)

        # 2. 统计 API：只读端点公开，含日志的端点需认证
        if path in _STATS_READONLY_PATHS:
            return await call_next(request)
        if path in _STATS_AUTH_PATHS:
            pass  # 走下方认证逻辑

        # 3. 配置 API：GET 只读公开，POST/PUT/DELETE 需认证
        elif path in _CONFIG_READONLY_GET_PATHS and method == "GET":
            return await call_next(request)

        # 4. 其他路径或写操作 → 需要认证
        auth = request.headers.get("Authorization", "")
        x_api_key = request.headers.get("x-api-key", "")
        if auth == f"Bearer {GATEWAY_API_KEY}" or x_api_key == GATEWAY_API_KEY:
            return await call_next(request)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 预热连接池：对每个 provider 发一个轻量请求，真正建立 TCP+TLS 连接
    from proxy import get_client
    client = get_client()
    _warmup_tasks = []
    for _name, _prov in providers.items():
        try:
            # 用一个极短的超时发 GET 到 base_url，只为了触发 TCP 握手
            _warmup_tasks.append(
                client.get(
                    _prov.base_url.rstrip("/") + "/models",
                    timeout=httpx.Timeout(5.0, connect=3.0),
                )
            )
        except Exception:
            pass
    if _warmup_tasks:
        import asyncio
        # 启动时等待一次预热，确保连接池真的建立起来
        await asyncio.gather(*_warmup_tasks, return_exceptions=True)
    logging.info(f"connection pool warmup: {len(_warmup_tasks)} providers")
    yield
    # 关闭时关闭全局 httpx client，释放连接池
    from proxy import _CLIENT
    if _CLIENT is not None:
        await _CLIENT.aclose()
        # 重置，以便下次 startup 可重新初始化
        import proxy as _p
        _p._CLIENT = None


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)

# ── 请求体大小限制（M6: 防止超大 body 耗尽内存）──────────────────
_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    """拒绝超过 10MB 的请求体，防止内存耗尽攻击。

    防御 #15 (CC 审计发现)：检查 Content-Length 和 Transfer-Encoding。
    - Content-Length 超限 → 413
    - Transfer-Encoding: chunked → 拒绝（无法预知大小，会被 Starlette 全量读入内存）
    """
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": "request body too large", "max_bytes": _MAX_BODY_BYTES},
        )
    # 拒绝 chunked 传输：Starlette 会把整个 body 读进内存，无法中间拦截
    transfer_encoding = request.headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        return JSONResponse(
            status_code=411,
            content={"error": "chunked transfer not allowed, use Content-Length"},
        )
    return await call_next(request)


# ── 简易速率限制（M5: 令牌桶，防止滥用）────────────────────────
import time as _time
from collections import defaultdict as _ddict
import threading as _threading

_rate_lock = _threading.Lock()
# {client_ip: [last_refill_ts, tokens_remaining]}
_rate_buckets: dict[str, list] = _ddict(lambda: [_time.monotonic(), 60.0])
_RATE_LIMIT_RPS = 10.0  # 每客户端每秒最大请求数
_RATE_LIMIT_BURST = 60.0  # 令牌桶容量（允许突发）


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """简易令牌桶速率限制，按客户端 IP 限流。

    - 公开路径（/health, /dashboard, /api/stats/*）不计入限流
    - 认证通过后对 LLM 请求限流：10 req/s，突发上限 60
    """
    path = request.url.path
    # 公开路径不限流
    if (
        path in _PUBLIC_PATHS
        or path in _STATS_READONLY_PATHS
        or path.startswith("/static")
    ):
        return await call_next(request)

    # 获取客户端 IP（支持反向代理 X-Forwarded-For）
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )

    now = _time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[client_ip]
        elapsed = now - bucket[0]
        bucket[0] = now
        # 补充令牌
        bucket[1] = min(_RATE_LIMIT_BURST, bucket[1] + elapsed * _RATE_LIMIT_RPS)
        if bucket[1] < 1.0:
            retry_after = max(1, int((1.0 - bucket[1]) / _RATE_LIMIT_RPS) + 1)
            return JSONResponse(
                status_code=429,
                content={"error": "rate limit exceeded", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        bucket[1] -= 1.0

    return await call_next(request)


app.include_router(stats_router)
app.include_router(config_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    available = channel_mgr.available_channels()
    # 过滤掉 model_enabled=False 的模型，与 _pick_model 行为保持一致
    truly_available = [m for m in available if channel_mgr.is_model_enabled(m)]
    earliest = channel_mgr.earliest_recovery()
    return {
        "available_channels": truly_available,
        "providers": list(providers.keys()),
        "earliest_recovery": earliest.isoformat() if earliest else None,
    }


@app.get("/v1/models")
async def list_models():
    routing = cfg["routing"]
    all_candidates = (
        routing.get("complex_candidates", [])
        + routing.get("simple_candidates", [])
        + routing.get("multimodal_candidates", [])
    )
    # fallback candidates 与主候选重叠，不单独列出
    seen = set()
    models = [{"id": "auto", "object": "model"}]
    for m in all_candidates:
        if m not in seen:
            models.append({"id": m, "object": "model"})
            seen.add(m)
    return {"object": "list", "data": models}


def _is_openai_format(body) -> bool:
    """检测请求 body 是 OpenAI /v1/chat/completions 格式而非 Anthropic /v1/messages 格式。

    关键区分：Anthropic 的 messages[*].content 必须是 list（可能含图片块），
    OpenAI 的 messages[*].content 通常是字符串。系统提示也用 messages 列表而非
    顶层 system 字段。

    保守判断：所有非空 messages 的 content 都是字符串（且没有顶层 system 字段）
    → 视为 OpenAI 格式。
    """
    if not isinstance(body, dict):
        return False
    if body.get("system") is not None:
        return False  # 顶层 system 字段是 Anthropic 特征
    if body.get("stop_sequences") is not None:
        return False  # Anthropic 字段
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    string_count = 0
    list_count = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            string_count += 1
        elif isinstance(content, list):
            list_count += 1
    # 至少有一条 string content，且没有 list content → OpenAI 格式
    return string_count > 0 and list_count == 0


def _convert_anthropic_to_openai(anthropic_body: dict) -> dict:
    """将 Anthropic /v1/messages 请求转换为 OpenAI /v1/chat/completions 格式。

    转换内容：
    - system 字段提取为独立 system role 消息
    - messages 中 content 字符串转为 OpenAI 格式
    - messages 中 content 数组（多模态）转为 OpenAI content 数组
    - max_tokens 映射到 max_tokens
    - stream 参数保留
    - tools 映射为 OpenAI tools 格式
    """
    openai_messages = []

    # 处理 system 字段
    system = anthropic_body.get("system", "")
    if system:
        if isinstance(system, str):
            openai_messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # system 为数组时，提取所有 text 内容合并
            system_text = ""
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    system_text += block.get("text", "")
            if system_text:
                openai_messages.append({"role": "system", "content": system_text})

    # 处理 messages
    for msg in anthropic_body.get("messages", []):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, str):
            # 字符串内容直接转换
            openai_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            # 数组内容（可能包含多模态）
            openai_content = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        openai_content.append({"type": "text", "text": block.get("text", "")})
                    elif block_type == "image":
                        # Anthropic image 格式转换
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            media_type = source.get("media_type", "image/jpeg")
                            data = source.get("data", "")
                            openai_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{data}"}
                            })
                        elif source.get("type") == "url":
                            openai_content.append({
                                "type": "image_url",
                                "image_url": {"url": source.get("url", "")}
                            })
                    elif block_type == "tool_use":
                        # tool_use 转换为 OpenAI tool_calls 格式
                        if "tool_calls" not in openai_messages[-1] if openai_messages else True:
                            openai_messages.append({
                                "role": role,
                                "content": "",
                                "tool_calls": []
                            })
                            # 修正：需要找到最后一条消息并添加 tool_calls
                        # 简化处理：直接在消息中添加 tool_calls
                        if openai_messages and openai_messages[-1].get("role") == role:
                            if "tool_calls" not in openai_messages[-1]:
                                openai_messages[-1]["tool_calls"] = []
                            openai_messages[-1]["tool_calls"].append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": _json.dumps(block.get("input", {}))
                                }
                            })
                            continue
                    elif block_type == "tool_result":
                        # tool_result 转换为 OpenAI tool role 消息
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": block.get("content", "")
                        })
                        continue

            # 如果没有特殊处理（如 tool_use），添加普通内容
            if openai_content:
                # 如果只有一个 text 块，简化为字符串
                if len(openai_content) == 1 and openai_content[0]["type"] == "text":
                    openai_messages.append({"role": role, "content": openai_content[0]["text"]})
                else:
                    openai_messages.append({"role": role, "content": openai_content})

    # 构建 OpenAI 请求
    openai_body = {
        "model": anthropic_body.get("model", "auto"),
        "messages": openai_messages,
        "max_tokens": anthropic_body.get("max_tokens", 16384),
    }

    # 可选参数
    if "temperature" in anthropic_body:
        openai_body["temperature"] = anthropic_body["temperature"]
    if "top_p" in anthropic_body:
        openai_body["top_p"] = anthropic_body["top_p"]
    if "stream" in anthropic_body:
        openai_body["stream"] = anthropic_body["stream"]
    if "stop_sequences" in anthropic_body:
        openai_body["stop"] = anthropic_body["stop_sequences"]

    # 处理 tools
    tools = anthropic_body.get("tools", [])
    if tools:
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {})
                }
            })
        openai_body["tools"] = openai_tools

    # 处理 tool_choice
    tool_choice = anthropic_body.get("tool_choice")
    if tool_choice:
        if isinstance(tool_choice, dict):
            choice_type = tool_choice.get("type", "")
            if choice_type == "auto":
                openai_body["tool_choice"] = "auto"
            elif choice_type == "any":
                openai_body["tool_choice"] = "required"
            elif choice_type == "tool":
                openai_body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")}
                }
        elif isinstance(tool_choice, str):
            # 防御 #21 (CC 审计发现)：tool_choice 字符串白名单校验
            _ALLOWED_TOOL_CHOICE_STR = {"auto", "none", "required"}
            if tool_choice not in _ALLOWED_TOOL_CHOICE_STR:
                raise HTTPException(status_code=400, detail=f"invalid tool_choice: {tool_choice!r}")
            openai_body["tool_choice"] = tool_choice

    return openai_body


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic /v1/messages 端点，转换为 OpenAI 格式后内部转发。

    接收 Anthropic Messages API 格式请求，转换为 OpenAI chat/completions 格式，
    通过内部路由逻辑选择模型，转发请求，并将响应转换回 Anthropic 格式。
    """
    body = await request.json()
    requested_model = body.get("model", "auto")
    stream = body.get("stream", False)

    logging.debug(f"anthropic_messages requested_model={requested_model!r} stream={stream}")

    # 转换 Anthropic 请求为 OpenAI 格式
    openai_body = _convert_anthropic_to_openai(body)
    messages = openai_body.get("messages", [])

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
        retry_candidates = [(requested_model, provider)]
    else:
        session_key = _get_session_key(messages)
        complexity = None
        classify_result = None
        classifier_model = None

        # 含图片时跳过分类器，直接路由到多模态候选列表
        if _has_image(messages):
            retry_candidates = _pick_model(routing.get("multimodal_candidates", []), session_key)
        else:
            # 提取分类上下文
            if classifier_primary:
                history_context, last_msg = classifier_primary.extract_context_for_classify(messages)
            elif classifier_fallback:
                history_context, last_msg = classifier_fallback.extract_context_for_classify(messages)
            else:
                last_msg = ""
                for msg in messages[::-1]:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                        last_msg = str(content)[:2000]
                        break
                history_context = ""

            classify_result = None

            # 分类器二级降级
            if classifier_primary and (
                channel_mgr.is_model_enabled(_classifier_primary_model)
                and channel_mgr.is_available(_classifier_primary_model)
            ):
                classify_result = await classifier_primary.classify(
                    last_msg, context=history_context, on_429=channel_mgr.handle_429
                )
                classifier_model = _classifier_primary_model
            elif classifier_fallback and (
                channel_mgr.is_model_enabled(_classifier_fallback_model)
                and channel_mgr.is_available(_classifier_fallback_model)
            ):
                classify_result = await classifier_fallback.classify(
                    last_msg, context=history_context, on_429=channel_mgr.handle_429
                )
                classifier_model = _classifier_fallback_model
            else:
                logging.debug("all classifiers unavailable, fallback to keyword")
                classifier_model = None

            if classify_result is None:
                if classifier_model:
                    logging.debug("classifier returned None (failed), fallback to keyword")
                classify_result = keyword_classify(last_msg)

            complexity = classify_result.complexity
            logging.warning(f"classify complexity={complexity.value} model={classify_result.model} msg_len={len(last_msg)}")

            if complexity == Complexity.COMPLEX:
                retry_candidates = _pick_model(routing.get("complex_candidates", []), session_key)
                if not retry_candidates:
                    retry_candidates = _pick_model(routing.get("complex_fallback", []), session_key)
            else:
                retry_candidates = _pick_model(routing.get("simple_candidates", []), session_key)
                if not retry_candidates:
                    retry_candidates = _pick_model(routing.get("simple_fallback", []), session_key)

        if not retry_candidates:
            earliest = channel_mgr.earliest_recovery()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "all channels unavailable",
                    "earliest_recovery": earliest.isoformat() if earliest else None,
                },
            )

    last_error = None
    diagnosis = None
    if requested_model == "auto":
        _classifier_errored = (
            classify_result is None
            and classifier_model is not None
        )
        diagnosis = {
            "classify_result": classify_result.complexity.value if classify_result else None,
            "classifier_model": classify_result.model if classify_result else None,
            "classifier_errored": _classifier_errored,
            "attempted_models": [],
        }

    for i, (attempt_model, attempt_provider) in enumerate(retry_candidates):
        if i > 0:
            logging.warning(f"FALLBACK attempt {i}: {attempt_model} (prev error: {last_error})")

        async def _on_upstream_error(status_code: int, resp_body: str, _m=attempt_model):
            try:
                err_msg = _json.loads(resp_body).get("error", {}).get("message", resp_body)
            except Exception:
                err_msg = resp_body
            if status_code == 429:
                channel_mgr.handle_429(_m, err_msg)
                logging.warning(f"STREAM 429 ban: {_m} msg={err_msg[:200]}")
            else:
                _record_failure(_m)
                channel_mgr.mark_unavailable(_m, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(_m)))
                logging.warning(f"STREAM {status_code} ban: {_m} backoff={_backoff_seconds(_m)}s msg={err_msg[:200]}")

        role_str = "direct" if requested_model != "auto" else (complexity.value if complexity else "multimodal")
        try:
            resp = await forward_request(
                openai_body, attempt_model, get_channel_for_model(attempt_model), attempt_provider,
                on_error=_on_upstream_error,
                role=role_str,
            )
            _clear_failure(attempt_model)

            # 请求成功后才记录分类器用量
            if requested_model == "auto" and classify_result:
                from providers import get_channel_for_model as _gcfm
                usage_db.record(
                    model=classify_result.model,
                    channel=_gcfm(classify_result.model),
                    role="classifier",
                    prompt_tokens=classify_result.prompt_tokens,
                    completion_tokens=classify_result.completion_tokens,
                    latency_ms=classify_result.latency_ms,
                )

            # 转换响应为 Anthropic 格式
            if stream:
                # 流式响应：转换 SSE 格式
                async def generate_anthropic_sse():
                    converter = AnthropicSSEConverter(target_model=attempt_model)
                    async for line in resp.body_iterator:
                        line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                        converted_lines = convert_openai_sse_to_anthropic_sse(line_str, converter)
                        for converted_line in converted_lines:
                            yield (converted_line + "\n").encode("utf-8")

                return StreamingResponse(
                    generate_anthropic_sse(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    }
                )
            else:
                # 非流式响应：转换 JSON 格式
                openai_json = json.loads(resp.body) if isinstance(resp.body, bytes) else resp.body
                anthropic_response = convert_openai_to_anthropic_response(openai_json)
                return JSONResponse(content=anthropic_response)

        except httpx.HTTPStatusError as e:
            err_msg = ""
            try:
                err_msg = e.response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            if e.response.status_code == 429:
                channel_mgr.handle_429(attempt_model, err_msg)
            else:
                _record_failure(attempt_model)
                channel_mgr.mark_unavailable(attempt_model, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(attempt_model)))
            last_error = f"{e.response.status_code}: {err_msg}"
            logging.warning(f"FAIL {attempt_model}: {last_error}")
            if diagnosis is not None:
                diagnosis["attempted_models"].append({
                    "model": attempt_model,
                    "status_code": e.response.status_code,
                    "error": err_msg,
                })
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            _record_failure(attempt_model)
            channel_mgr.mark_unavailable(attempt_model, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(attempt_model)))
            # 防御 #24 (CC 审计发现)：str(e) 可能包含内部 IP:port，仅在日志保留完整，响应里只暴露类型
            last_error = f"{type(e).__name__}: <redacted>"
            logging.warning(f"FAIL {attempt_model}: {type(e).__name__}: {e}")
            if diagnosis is not None:
                diagnosis["attempted_models"].append({
                    "model": attempt_model,
                    "error_type": type(e).__name__,
                    "error": f"{type(e).__name__}: <redacted>",
                })

    earliest = channel_mgr.earliest_recovery()
    error_content = {
        "error": "all candidates failed",
        "last_error": last_error,
        "earliest_recovery": earliest.isoformat() if earliest else None,
    }
    if diagnosis is not None:
        error_content["diagnosis"] = diagnosis
    return JSONResponse(
        status_code=503,
        content=error_content,
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, _body: dict | None = None):
    body = _body if _body is not None else await request.json()
    requested_model = body.get("model", "auto")
    messages = body.get("messages", [])
    routing = cfg["routing"]
    logging.debug(f"chat_completions requested_model={requested_model!r}")

    # 指定具体模型时直接路由，不走分类器
    if requested_model != "auto":
        try:
            provider = get_provider_for_model(requested_model, providers)
            logging.warning(f"DBG provider_lookup model={requested_model!r} provider={provider}")
        except Exception as e:
            import traceback
            logging.error(f"DBG provider_lookup EXC: {e!r}\n{traceback.format_exc()}")
            return JSONResponse(status_code=500, content={"error": f"provider lookup failed: {e!r}"})
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
        retry_candidates = [(requested_model, provider)]
    else:
        session_key = _get_session_key(messages)
        complexity = None
        classify_result = None
        classifier_model = None  # 初始化，避免有图片时未定义
        # 含图片时跳过分类器，直接路由到多模态候选列表
        if _has_image(messages):
            retry_candidates = _pick_model(routing.get("multimodal_candidates", []), session_key)
        else:
            # 提取分类上下文和last_msg用任意classifier实例就行，方法是static的
            if classifier_primary:
                history_context, last_msg = classifier_primary.extract_context_for_classify(messages)
            elif classifier_fallback:
                history_context, last_msg = classifier_fallback.extract_context_for_classify(messages)
            else:
                # 没有classifier实例，降级为只提取last_msg
                last_msg = ""
                for msg in messages[::-1]:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                        last_msg = str(content)[:2000]
                        break
                history_context = ""
            classify_result = None

            # 分类器二级降级：primary (LongCat) → fallback (ARK doubao-lite) → keyword
            if classifier_primary and (
                channel_mgr.is_model_enabled(_classifier_primary_model)
                and channel_mgr.is_available(_classifier_primary_model)
            ):
                classify_result = await classifier_primary.classify(
                    last_msg, context=history_context, on_429=channel_mgr.handle_429
                )
                classifier_model = _classifier_primary_model
            elif classifier_fallback and (
                channel_mgr.is_model_enabled(_classifier_fallback_model)
                and channel_mgr.is_available(_classifier_fallback_model)
            ):
                logging.debug(
                    f"primary classifier ({_classifier_primary_model}) unavailable, "
                    f"trying fallback ({_classifier_fallback_model})"
                )
                classify_result = await classifier_fallback.classify(
                    last_msg, context=history_context, on_429=channel_mgr.handle_429
                )
                classifier_model = _classifier_fallback_model
            else:
                logging.debug("all classifiers unavailable, fallback to keyword")
                classifier_model = None
            if classify_result is None:
                if classifier_model:
                    logging.debug("classifier returned None (failed), fallback to keyword")
                classify_result = keyword_classify(last_msg)
            complexity = classify_result.complexity
            logging.warning(f"classify complexity={complexity.value} model={classify_result.model} msg_len={len(last_msg)}")

            if complexity == Complexity.COMPLEX:
                retry_candidates = _pick_model(routing.get("complex_candidates", []), session_key)
                if not retry_candidates:
                    retry_candidates = _pick_model(routing.get("complex_fallback", []), session_key)
            else:  # SIMPLE = writer + executor 合并
                retry_candidates = _pick_model(routing.get("simple_candidates", []), session_key)
                if not retry_candidates:
                    retry_candidates = _pick_model(routing.get("simple_fallback", []), session_key)

        if not retry_candidates:
            earliest = channel_mgr.earliest_recovery()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "all channels unavailable",
                    "earliest_recovery": earliest.isoformat() if earliest else None,
                },
            )

    last_error = None
    diagnosis = None
    if requested_model == "auto":
        # classifier 是否出错：classify_result 为 None 且不是因为 classifier_model 本身不可用
        _classifier_errored = (
            classify_result is None
            and classifier_model is not None
        )
        diagnosis = {
            "classify_result": classify_result.complexity.value if classify_result else None,
            "classifier_model": classify_result.model if classify_result else None,
            "classifier_errored": _classifier_errored,
            "attempted_models": [],
        }

    for i, (attempt_model, attempt_provider) in enumerate(retry_candidates):
        if i > 0:
            logging.warning(f"FALLBACK attempt {i}: {attempt_model} (prev error: {last_error})")
        # 流式请求的 429 通过回调触发；非流式请求的 429 通过下方 except 捕获
        async def _on_upstream_error(status_code: int, body: str, _m=attempt_model):
            try:
                err_msg = _json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            if status_code == 429:
                channel_mgr.handle_429(_m, err_msg)
                logging.warning(f"STREAM 429 ban: {_m} msg={err_msg[:200]}")
            else:
                _record_failure(_m)
                channel_mgr.mark_unavailable(_m, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(_m)))
                logging.warning(f"STREAM {status_code} ban: {_m} backoff={_backoff_seconds(_m)}s msg={err_msg[:200]}")

        role_str = "direct" if requested_model != "auto" else (complexity.value if complexity else "multimodal")
        try:
            resp = await forward_request(
                body, attempt_model, get_channel_for_model(attempt_model), attempt_provider,
                on_error=_on_upstream_error,
                role=role_str,
            )
            _clear_failure(attempt_model)
            # 请求成功后才记录分类器用量，确保统计的是真正完成路由的次数
            if requested_model == "auto" and classify_result:
                from providers import get_channel_for_model as _gcfm
                usage_db.record(
                    model=classify_result.model,
                    channel=_gcfm(classify_result.model),
                    role="classifier",
                    prompt_tokens=classify_result.prompt_tokens,
                    completion_tokens=classify_result.completion_tokens,
                    latency_ms=classify_result.latency_ms,
                )
            return resp
        except httpx.HTTPStatusError as e:
            err_msg = ""
            try:
                err_msg = e.response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            if e.response.status_code == 429:
                channel_mgr.handle_429(attempt_model, err_msg)
            else:
                _record_failure(attempt_model)
                channel_mgr.mark_unavailable(attempt_model, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(attempt_model)))
            last_error = f"{e.response.status_code}: {err_msg}"
            logging.warning(f"FAIL {attempt_model}: {last_error}")
            if diagnosis is not None:
                diagnosis["attempted_models"].append({
                    "model": attempt_model,
                    "status_code": e.response.status_code,
                    "error": err_msg,
                })
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            _record_failure(attempt_model)
            channel_mgr.mark_unavailable(attempt_model, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(attempt_model)))
            # 防御 #24 (CC 审计发现)：str(e) 可能包含内部 IP:port，仅在日志保留完整，响应里只暴露类型
            last_error = f"{type(e).__name__}: <redacted>"
            logging.warning(f"FAIL {attempt_model}: {type(e).__name__}: {e}")
            if diagnosis is not None:
                diagnosis["attempted_models"].append({
                    "model": attempt_model,
                    "error_type": type(e).__name__,
                    "error": f"{type(e).__name__}: <redacted>",
                })

    earliest = channel_mgr.earliest_recovery()
    error_content = {
        "error": "all candidates failed",
        "last_error": last_error,
        "earliest_recovery": earliest.isoformat() if earliest else None,
    }
    if diagnosis is not None:
        error_content["diagnosis"] = diagnosis
    return JSONResponse(
        status_code=503,
        content=error_content,
    )
