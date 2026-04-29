import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import httpx

CLASSIFY_PROMPT = """\
你是一个任务分类器。不要推理，直接分类。

coordinator（满足任意一条）：
- 中等及以上规模的任务，涉及多步骤、多模块或较长链路（如开发系统、搭建平台、实现完整功能模块）
- 规划、拆解、制定方案、分配工作、协调多个子任务
- 跨文件/跨模块的分析、review、验证（单文件分析归 executor）
- 需求澄清、目标确认、边界梳理
- 适合多 Agent 并行处理的任务
- 子 Agent 结果回收、分析汇总、下一步行动规划

writer（同时满足）：
- 写作、文档、总结、解释、翻译，或日常交流、闲聊、通识问答
- 不涉及系统/功能开发，不涉及代码实现

executor（以下情况）：
- 明确的单步代码任务（写一个函数、修一个 bug、实现一个接口）
- 单文件的分析、检查、review
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


@dataclass
class ClassifyResult:
    complexity: Complexity
    model: str          # 实际执行分类的模型
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


_COORDINATOR_KEYWORDS = [
    # 规划/拆解
    "规划", "拆解", "方案", "计划", "设计", "架构",
    # 分析/检查/验证
    "分析", "检查", "验证", "review", "审查", "审计", "评估",
    # 协调/多步骤
    "协调", "调度", "多步", "流程", "pipeline",
    # 需求/目标
    "需求", "目标", "边界", "澄清",
    # 汇总/总结大型任务
    "汇总", "整合", "梳理",
]

# 移除 "kimi" 关键字：用户提及模型名不等于需要复杂模型
# 注意：COORDINATOR 和 WRITER 关键字有重叠时，优先 WRITER（因为先检查 WRITER）
_WRITER_KEYWORDS = [
    # 写作/文档
    "写", "写作", "文档", "文章", "博客", "报告", "总结", "概括", "摘要",
    # 翻译
    "翻译", "译成", "译为",
]


def keyword_classify(text: str) -> ClassifyResult:
    """ark 不可用时的关键字降级分类。优先级：COORDINATOR > WRITER > EXECUTOR。"""
    lower = text.lower()
    if any(kw in lower for kw in _COORDINATOR_KEYWORDS):
        complexity = Complexity.COORDINATOR
    elif any(kw in lower for kw in _WRITER_KEYWORDS):
        complexity = Complexity.WRITER
    else:
        complexity = Complexity.EXECUTOR
    return ClassifyResult(complexity=complexity, model="keyword", prompt_tokens=0, completion_tokens=0, latency_ms=0)


class Classifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 10.0, extra_body: dict | None = None):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._extra_body = extra_body or {}
        # 复用全局连接池，避免每次分类都新建 TCP+TLS 握手
        from proxy import get_client
        self._client = get_client()

    def extract_last_user_message(self, messages: list[dict]) -> str:
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        content = user_msgs[-1].get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content)[:1000]

    async def _call_llm(self, content: str, on_429=None) -> tuple[str, dict, int]:
        """返回 (content, usage, latency_ms)"""
        prompt = CLASSIFY_PROMPT.format(content=content)
        start = time.monotonic()
        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0,
                "reasoning_effort": "minimal",
                **self._extra_body,
            },
            timeout=self._timeout,
        )
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 429 and on_429:
            err_msg = ""
            try:
                err_msg = resp.json().get("error", {}).get("message", "")
            except Exception:
                pass
            on_429(self._model, err_msg)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {}) or {}
        return data["choices"][0]["message"]["content"], usage, latency

    async def classify(self, last_user_message: str, on_429=None) -> Optional[ClassifyResult]:
        """分类成功返回 ClassifyResult，失败返回 None（调用方负责降级）"""
        try:
            raw, usage, latency = await self._call_llm(last_user_message, on_429=on_429)
            data = json.loads(raw.strip())
            return ClassifyResult(
                complexity=Complexity(data["role"]),
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0) or 0,
                completion_tokens=usage.get("completion_tokens", 0) or 0,
                latency_ms=latency,
            )
        except Exception as e:
            logging.warning(f"classifier failed: {type(e).__name__}: {e}")
            return None
