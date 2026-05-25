import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import httpx

CLASSIFY_PROMPT = """\
你是一个任务分类器。不要推理，直接分类。

complex（满足任意一条即为复杂任务）：
- 中等及以上规模的任务，涉及多步骤、多模块或较长链路（如开发系统、搭建平台、实现完整功能模块）
- 规划、拆解、制定方案、分配工作、协调多个子任务
- 跨文件/跨模块的分析、review、验证（单文件分析归 simple）
- 需求澄清、目标确认、边界梳理、架构设计
- 适合多 Agent 并行处理的任务
- 子 Agent 结果回收、分析汇总、下一步行动规划

simple（除 complex 以外的所有任务）：
- 写作、文档、总结、解释、翻译
- 日常交流、闲聊、通识问答
- 明确的单步代码任务（写一个函数、修一个 bug、实现一个接口）
- 单文件的分析、检查、review
- 工具调用、命令执行、文件读写等单一操作
- 无法归入 complex 的其他任务

只返回 JSON：
{{"role": "complex"}} 或 {{"role": "simple"}}

{context}
用户请求：
{content} 不要思考，直接给结果"""

CLASSIFY_PROMPT_CONTEXTED = """\
你是一个任务分类器。不要推理，直接分类。

complex（满足任意一条即为复杂任务）：
- 中等及以上规模的任务，涉及多步骤、多模块或较长链路（如开发系统、搭建平台、实现完整功能模块）
- 规划、拆解、制定方案、分配工作、协调多个子任务
- 跨文件/跨模块的分析、review、验证（单文件分析归 simple）
- 需求澄清、目标确认、边界梳理、架构设计
- 适合多 Agent 并行处理的任务
- 子 Agent 结果回收、分析汇总、下一步行动规划

simple（除 complex 以外的所有任务）：
- 写作、文档、总结、解释、翻译
- 日常交流、闲聊、通识问答
- 明确的单步代码任务（写一个函数、修一个 bug、实现一个接口）
- 单文件的分析、检查、review
- 工具调用、命令执行、文件读写等单一操作
- 无法归入 complex 的其他任务

只返回 JSON：
{{"role": "complex"}} 或 {{"role": "simple"}}

历史对话摘要（供上下文参考，不是需要分类的内容）：
{context}

当前需要分类的用户请求：
{content}

不要思考，直接给结果"""


class Complexity(str, Enum):
    COMPLEX = "complex"
    SIMPLE = "simple"


@dataclass
class ClassifyResult:
    complexity: Complexity
    model: str          # 实际执行分类的模型
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


# 移除了 WRITER 关键字列表（writer + executor 合并为 simple）
# 移除了 EXECUTOR 关键字列表
# 只保留 COORDINATOR -> COMPLEX 的判断，其余全部归 SIMPLE
_COMPLEX_KEYWORDS = [
    # 规划/拆解/方案
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


def keyword_classify(text: str) -> ClassifyResult:
    """simple 不可用时的关键字降级分类。complex 有关键字则 complex，否则 simple。"""
    lower = text.lower()
    if any(kw in lower for kw in _COMPLEX_KEYWORDS):
        complexity = Complexity.COMPLEX
    else:
        complexity = Complexity.SIMPLE
    return ClassifyResult(complexity=complexity, model="keyword", prompt_tokens=0, completion_tokens=0, latency_ms=0)


class Classifier:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 15.0, extra_body: dict | None = None):
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

    def extract_context_for_classify(self, messages: list[dict], max_context_chars: int = 3000) -> tuple[str, str]:
        """从 messages 中提取 (历史摘要, 最后一条user消息)，用于带上下文的分类。

        设计原则：
        - last_message 优先保证完整性（前 2000 字符），分类器需要看到当前请求的完整内容
        - context 历史只取摘要，最多取最近 5 条，控制在 max_context_chars 以内
        """
        # 1. 找到最后一条 user 消息（当前请求），优先保证它的完整性
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx == -1:
            return "", ""

        last_msg = messages[last_user_idx]
        last_content = last_msg.get("content", "")
        if isinstance(last_content, list):
            last_content = " ".join(p.get("text", "") for p in last_content if isinstance(p, dict))
        last_message = str(last_content)[:2000]

        # 2. 提取 last_user_idx 之前的消息做历史摘要（最多 5 条，来自不同轮次）
        history_msgs = messages[:last_user_idx]
        summary_lines = []
        for msg in history_msgs:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            content = str(content)
            # 每条历史只保留第一行，最多截 300 字符
            first_line = content.split("\n")[0][:300]
            summary_lines.append(f"{role}: {first_line}")

        # 3. 只保留最近 5 条，从后往前
        summary_lines = summary_lines[-5:]

        # 4. 控制总长度
        context = "\n".join(summary_lines)
        if len(context) > max_context_chars:
            context = context[:max_context_chars] + "\n...(truncated)"

        return context, last_message

    async def _call_llm(self, content: str, context: str = "", on_429=None) -> tuple[str, dict, int]:
        """返回 (content, usage, latency_ms)。提供了 context 时使用带上下文的 prompt。"""
        if context:
            prompt = CLASSIFY_PROMPT_CONTEXTED.format(context=context, content=content)
        else:
            prompt = CLASSIFY_PROMPT.format(context="", content=content)
        start = time.monotonic()
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 30,
            "temperature": 0,
            **self._extra_body,
        }
        # reasoning_effort 只有 ARK (doubao) 支持，其他 provider 不能传
        if self._base_url and "ark.cn-beijing.volces.com" in self._base_url:
            body["reasoning_effort"] = "minimal"
        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
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

    async def classify(self, last_user_message: str, context: str = "", on_429=None) -> Optional[ClassifyResult]:
        """分类成功返回 ClassifyResult，失败返回 None（调用方负责降级）。context 非空时带历史上下文。"""
        try:
            raw, usage, latency = await self._call_llm(last_user_message, context=context, on_429=on_429)
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
