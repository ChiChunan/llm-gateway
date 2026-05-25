import json as _json
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
        logging.warning(f"pick_model session_key={session_key!r} available={available} ordered={ordered}")
    else:
        ordered = available
        logging.warning(f"pick_model no_session_key available={available} ordered={ordered}")

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
        routing.get("complex_candidates", [])
        + routing.get("simple_candidates", [])
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
                logging.warning(
                    f"primary classifier ({_classifier_primary_model}) unavailable, "
                    f"trying fallback ({_classifier_fallback_model})"
                )
                classify_result = await classifier_fallback.classify(
                    last_msg, context=history_context, on_429=channel_mgr.handle_429
                )
                classifier_model = _classifier_fallback_model
            else:
                logging.warning("all classifiers unavailable, fallback to keyword")
                classifier_model = None
            if classify_result is None:
                if classifier_model:
                    logging.warning("classifier returned None (failed), fallback to keyword")
                classify_result = keyword_classify(last_msg)
            complexity = classify_result.complexity
            logging.warning(f"classify complexity={complexity.value} model={classify_result.model} msg={last_msg[:50]!r}")

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
            last_error = str(e)
            logging.warning(f"FAIL {attempt_model}: {last_error}")
            if diagnosis is not None:
                diagnosis["attempted_models"].append({
                    "model": attempt_model,
                    "error_type": type(e).__name__,
                    "error": str(e),
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
