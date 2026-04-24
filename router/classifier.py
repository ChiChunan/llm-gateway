import json
from enum import Enum
import httpx

CLASSIFY_PROMPT = """\
你是一个任务分类器。判断以下用户请求属于哪个角色。

coordinator：需要规划、拆解任务、制定方案、分配工作，或需要检查结果、review 内容、验证质量、提出修改意见，或需要澄清用户需求、确认目标和边界
writer：需要写作、生成文档、总结、解释说明、翻译，或日常交流、通识问答、闲聊，且不涉及代码实现
executor：需要写代码、调用工具、具体实现、技术问答，或无法归入以上两类

只返回 JSON，不要其他内容：
{{"role": "coordinator"}} 或 {{"role": "writer"}} 或 {{"role": "executor"}}

用户请求：
{content}"""


class Complexity(str, Enum):
    COORDINATOR = "coordinator"
    WRITER = "writer"
    EXECUTOR = "executor"


class Classifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 5.0):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def extract_last_user_message(self, messages: list[dict]) -> str:
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        content = user_msgs[-1].get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content)[:1000]

    async def _call_llm(self, content: str) -> str:
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
        # 任何异常（超时、解析失败）均降级为 executor
        try:
            raw = await self._call_llm(last_user_message)
            data = json.loads(raw.strip())
            return Complexity(data["role"])
        except Exception:
            return Complexity.EXECUTOR
