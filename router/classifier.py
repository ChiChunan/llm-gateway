import json
from enum import Enum
import httpx

CLASSIFY_PROMPT = """\
你是一个任务分类器。判断以下用户请求的复杂度。

简单（simple）：单轮问答、翻译、格式转换、简单代码补全、解释单个概念
复杂（complex）：多步推理、长文档分析、复杂代码生成、架构设计、调试、需要上下文记忆的多轮任务

只返回 JSON，不要其他内容：
{{"complexity": "simple"}} 或 {{"complexity": "complex"}}

用户请求：
{content}"""


class Complexity(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


class Classifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 5.0):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def extract_last_user_message(self, messages: list[dict]) -> str:
        # 取所有 user 角色消息，返回最后一条前 1000 字符
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        content = user_msgs[-1].get("content", "")
        # 兼容多模态消息格式（content 为 list）
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content)[:1000]

    async def _call_llm(self, content: str) -> str:
        # 调用轻量 LLM 判断复杂度，返回原始响应字符串
        prompt = CLASSIFY_PROMPT.format(content=content)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def classify(self, last_user_message: str) -> Complexity:
        # 任何异常（超时、解析失败）均降级为 complex，保证安全性
        try:
            raw = await self._call_llm(last_user_message)
            data = json.loads(raw.strip())
            return Complexity(data["complexity"])
        except Exception:
            return Complexity.COMPLEX
