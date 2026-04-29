import json as _json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import provider_state as _pstate
from channel import ChannelManager
from classifier import Classifier, ClassifyResult, Complexity, keyword_classify
from proxy import forward_request
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
# 分类器模型也纳入管理，确保 ark plan 429 时能被联动封禁
_classifier_models = [m for m in [cfg["routing"].get("classifier")] if m]
_all_models = list(dict.fromkeys(
    _classifier_models
    + cfg["routing"].get("coordinator_candidates", [])
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

# 从配置加载 provider 初始开关状态
for provider, enabled in cfg.get("provider_switches", {}).items():
    channel_mgr.set_provider_state(provider, bool(enabled))
for model, enabled in cfg.get("model_switches", {}).items():
    channel_mgr.set_model_enabled(model, bool(enabled))
_pstate.set_channel_mgr(channel_mgr, "routing.yaml")

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

# ARK 不可用时降级为关键字匹配，不再使用 MiniMax 分类器


def _pick_model(candidates: list[str], session_key: str | None = None) -> tuple[str, object] | tuple[None, None]:
    """遍历候选列表，返回第一个可用且有 provider 的 (model, provider)。

    - 会预先过滤掉不可用、被禁用、或无 provider 的模型
    - 有 session_key 时：用一致性哈希打乱过滤后的列表，同 session 固定，不同 session 随机
    - 无 session_key 时：按过滤后原始顺序遍历
    """
    if not candidates:
        return None, None

    # 预过滤：只保留真正可用的模型
    available = []
    for model in candidates:
        provider = get_provider_for_model(model, providers)
        if provider is None:
            continue
        provider_name = get_channel_for_model(model)
        if not channel_mgr.is_provider_available(provider_name):
            continue
        if not channel_mgr.is_model_enabled(model):
            continue
        if not channel_mgr.is_available(model):
            continue
        available.append(model)

    if not available:
        return None, None

    # 有 session_key：hash 取余选模型，同 session 固定，不同 session 均匀随机
    if session_key:
        import hashlib
        idx = int(hashlib.sha256(session_key.encode()).hexdigest(), 16) % len(available)
        ordered = available[idx:] + available[:idx]
        logging.warning(f"pick_model session_key={session_key!r} idx={idx} available={available} ordered={ordered}")
    else:
        ordered = available
        logging.warning(f"pick_model no_session_key available={available} ordered={ordered}")

    # 按打乱后顺序返回第一个
    for model in ordered:
        provider = get_provider_for_model(model, providers)
        if provider is not None:
            return model, provider
    return None, None


def _has_image(messages: list[dict]) -> bool:
    """检测 messages 中是否包含图片内容。"""
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, list):
            continue
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


GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "V.A.L.O.R.")

_PUBLIC_PATHS = {"/health", "/dashboard"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        is_readonly_config = request.method == "GET" and request.url.path in {"/api/config/providers", "/api/config/models"}
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/static") or request.url.path.startswith("/api/stats") or is_readonly_config:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {GATEWAY_API_KEY}":
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 预热连接池（提前建立到各 provider 的连接）
    from proxy import get_client
    get_client()
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
    requested_model = body.get("model", "auto")
    messages = body.get("messages", [])
    routing = cfg["routing"]
    logging.warning(f"chat_completions requested_model={requested_model!r}")

    # 指定具体模型时直接路由，不走分类器
    if requested_model != "auto":
        provider = get_provider_for_model(requested_model, providers)
        if provider is None:
            return JSONResponse(
                status_code=400,
                content={"error": f"unknown model: {requested_model}"},
            )
        provider_name = get_channel_for_model(requested_model)
        if not channel_mgr.is_provider_available(provider_name):
            return JSONResponse(
                status_code=503,
                content={"error": f"provider for {requested_model} is disabled"},
            )
        if not channel_mgr.is_available(requested_model):
            return JSONResponse(
                status_code=503,
                content={"error": f"model {requested_model} is rate-limited"},
            )
        target_model, target_provider = requested_model, provider
    else:
        session_key = _get_session_key(messages)
        complexity = None
        classify_result = None
        # 含图片时跳过分类器，直接路由到多模态候选列表
        if _has_image(messages):
            target_model, target_provider = _pick_model(routing.get("multimodal_candidates", []), session_key)
        else:
            last_msg = classifier.extract_last_user_message(messages)
            classifier_model = cfg["routing"]["classifier"]
            ark_available = (
                channel_mgr.is_provider_available("ark")
                and channel_mgr.is_model_enabled(classifier_model)
                and channel_mgr.is_available(classifier_model)
            )
            if ark_available:
                classify_result = await classifier.classify(last_msg, on_429=channel_mgr.handle_429)
            else:
                logging.warning(f"classifier skipped: ark unavailable, fallback to keyword")
                classify_result = None
            if classify_result is None:
                if ark_available:
                    logging.warning("classifier returned None (failed), fallback to keyword")
                classify_result = keyword_classify(last_msg)
            complexity = classify_result.complexity
            logging.warning(f"classify complexity={complexity.value} model={classify_result.model} msg={last_msg[:50]!r}")

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


    # 构建完整候选列表用于降级重试
    if requested_model != "auto":
        retry_candidates = [(target_model, target_provider)]
    else:
        if complexity is None:
            # 多模态路径（图片请求），只在 multimodal_candidates 内重试
            all_candidates = routing.get("multimodal_candidates", [])
        elif complexity == Complexity.COORDINATOR:
            all_candidates = routing.get("coordinator_candidates", []) + routing.get("coordinator_fallback", [])
        elif complexity == Complexity.WRITER:
            all_candidates = routing.get("writer_candidates", []) + routing.get("writer_fallback", [])
        else:
            all_candidates = routing.get("executor_candidates", []) + routing.get("executor_fallback", [])
        # 去重保序，只保留当前可用的
        seen = set()
        retry_candidates = []
        for m in all_candidates:
            if m in seen:
                continue
            seen.add(m)
            p = get_provider_for_model(m, providers)
            provider_name = get_channel_for_model(m)
            if p and channel_mgr.is_provider_available(provider_name) and channel_mgr.is_model_enabled(m) and channel_mgr.is_available(m):
                retry_candidates.append((m, p))

    last_error = None
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
            else:
                _record_failure(_m)
                channel_mgr.mark_unavailable(_m, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(_m)))

        role_str = "direct" if requested_model != "auto" else complexity.value
        # 获取 provider 名称（即 channel 名称，如 "ark"/"kimi"）
        attempt_provider_name = get_channel_for_model(attempt_model)
        try:
            channel_mgr.acquire_provider_request(attempt_provider_name)
            try:
                resp = await forward_request(
                    body, attempt_model, get_channel_for_model(attempt_model), attempt_provider,
                    on_error=_on_upstream_error,
                    role=role_str,
                )
            finally:
                channel_mgr.release_provider_request(attempt_provider_name)
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
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            _record_failure(attempt_model)
            channel_mgr.mark_unavailable(attempt_model, until=datetime.now(CST) + timedelta(seconds=_backoff_seconds(attempt_model)))
            last_error = str(e)
            logging.warning(f"FAIL {attempt_model}: {last_error}")

    earliest = channel_mgr.earliest_recovery()
    return JSONResponse(
        status_code=503,
        content={
            "error": "all candidates failed",
            "last_error": last_error,
            "earliest_recovery": earliest.isoformat() if earliest else None,
        },
    )
