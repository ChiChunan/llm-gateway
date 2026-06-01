import pytest
from unittest.mock import AsyncMock, patch
from classifier import Classifier, Complexity


@pytest.mark.asyncio
async def test_classify_returns_coordinator():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=('{"role": "coordinator"}', {"prompt_tokens": 10, "completion_tokens": 3}, 50))):
        result = await clf.classify("帮我拆解这个项目的任务")
    assert result.complexity == Complexity.COORDINATOR


@pytest.mark.asyncio
async def test_classify_returns_writer():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=('{"role": "writer"}', {"prompt_tokens": 10, "completion_tokens": 3}, 50))):
        result = await clf.classify("帮我写一篇技术文档")
    assert result.complexity == Complexity.WRITER


@pytest.mark.asyncio
async def test_classify_returns_executor():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=('{"role": "executor"}', {"prompt_tokens": 10, "completion_tokens": 3}, 50))):
        result = await clf.classify("实现一个二分查找算法")
    assert result.complexity == Complexity.EXECUTOR


@pytest.mark.asyncio
async def test_classify_fallback_on_invalid_json():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=('not json', {"prompt_tokens": 10, "completion_tokens": 3}, 50))):
        result = await clf.classify("anything")
    # classify() 在解析失败时返回 None（调用方 main.py 负责降级）
    assert result is None


@pytest.mark.asyncio
async def test_classify_fallback_on_timeout():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(side_effect=Exception("timeout"))):
        result = await clf.classify("anything")
    # classify() 在异常时返回 None（调用方 main.py 负责降级）
    assert result is None


def test_extract_last_user_message_basic():
    clf = Classifier(base_url="http://x", api_key="k", model="m")
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Write me a complex algorithm."},
    ]
    assert clf.extract_last_user_message(messages) == "Write me a complex algorithm."


def test_extract_last_user_message_truncates_at_1000():
    clf = Classifier(base_url="http://x", api_key="k", model="m")
    long_msg = "x" * 2000
    messages = [{"role": "user", "content": long_msg}]
    result = clf.extract_last_user_message(messages)
    assert len(result) == 1000


def test_extract_last_user_message_no_user_returns_empty():
    clf = Classifier(base_url="http://x", api_key="k", model="m")
    messages = [{"role": "system", "content": "only system"}]
    assert clf.extract_last_user_message(messages) == ""
