import pytest
from unittest.mock import AsyncMock, patch
from classifier import Classifier, Complexity


@pytest.mark.asyncio
async def test_classify_returns_simple():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    mock_response = '{"complexity": "simple"}'
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=mock_response)):
        result = await clf.classify("What is 2+2?")
    assert result == Complexity.SIMPLE


@pytest.mark.asyncio
async def test_classify_returns_complex():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    mock_response = '{"complexity": "complex"}'
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value=mock_response)):
        result = await clf.classify("Design a distributed cache system.")
    assert result == Complexity.COMPLEX


@pytest.mark.asyncio
async def test_classify_fallback_on_invalid_json():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(return_value="not json")):
        result = await clf.classify("anything")
    assert result == Complexity.COMPLEX


@pytest.mark.asyncio
async def test_classify_fallback_on_timeout():
    clf = Classifier(base_url="http://localhost:3000/v1", api_key="test", model="doubao-seed-2-0-lite")
    with patch.object(clf, "_call_llm", new=AsyncMock(side_effect=Exception("timeout"))):
        result = await clf.classify("anything")
    assert result == Complexity.COMPLEX


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
