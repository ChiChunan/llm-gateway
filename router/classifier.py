import json
import time
from enum import Enum
import httpx

CLASSIFY_PROMPT = """\
你是一个任务分类器。不要推理，直接分类。

coordinator（满足任意一条）：
- 中等及以上规模的任务，涉及多步骤、多模块或较长链路（如开发系统、搭建平台、实现完整功能模块）
- 规划、拆解、制定方案、分配工作、协调多个子任务
- 检查、验证、review、分析某个文件/配置/代码/结果/方案
- 需求澄清、目标确认、边界梳理
- 适合多 Agent 并行处理的任务

writer（同时满足）：
- 写作、文档、总结、解释、翻译，或日常交流、闲聊、通识问答
- 不涉及系统/功能开发，不涉及代码实现

executor（以下情况）：
- 明确的单步代码任务（写一个函数、修一个 bug、实现一个接口）
- 工具调用、命令执行、文件读写等单一操作
- 无法归入以上两类

只返回 JSON：
{{"role": "coordinator"}} 或 {{"role": "writer"}} 或 {{"role": "executor"}}

用户请求：
{content} 不要思考，直接给结果"""


class Complexity(str, Enum):
    COORDINATOR = "coordinator"
    WRITER = "writer"
    EXECUTOR = "executor"


class Classifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 5.0, extra_body: dict | None = None):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._extra_body = extra_body or {}

    def extract_last_user_message(self, messages: list[dict]) -> str:
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        content = user_msgs[-1].get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content)[:1000]

    async def _call_llm(self, content: str) -> str:
        from usage import get_usage_db
        from providers import get_channel_for_model
        prompt = CLASSIFY_PROMPT.format(content=content)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0,
                    **self._extra_body,
                },
            )
            resp.raise_for_status()
            latency = int((time.monotonic() - start) * 1000)
            data = resp.json()
            usage = data.get("usage", {}) or {}
            try:
                get_usage_db().record(
                    model=self._model,
                    channel=get_channel_for_model(self._model),
                    role="classifier",
                    prompt_tokens=usage.get("prompt_tokens", 0) or 0,
                    completion_tokens=usage.get("completion_tokens", 0) or 0,
                    status_code=resp.status_code,
                    latency_ms=latency,
                )
            except Exception:
                pass
            return data["choices"][0]["message"]["content"]

    async def classify(self, last_user_message: str) -> Complexity:
        # 任何异常（超时、解析失败）均降级为 executor
        try:
            raw = await self._call_llm(last_user_message)
            data = json.loads(raw.strip())
            return Complexity(data["role"])
        except Exception:
            return Complexity.EXECUTOR
