"""Unit tests for _is_openai_format() in main.py.

The bug being guarded against: /v1/chat/completions was double-registered
(two handlers on the same path). The first one (anthropic_messages) processed
ALL requests as Anthropic format, converting the upstream OpenAI response into
Anthropic shape and breaking OpenAI SDK clients (choices=None, created=None,
object=None).

The fix routes OpenAI-shaped requests through to the real chat_completions
handler. These tests verify the detector is correct.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import _is_openai_format  # noqa: E402


class TestIsOpenAIFormat:
    # --- Positive cases: should be detected as OpenAI ---

    def test_simple_string_content(self):
        body = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        }
        assert _is_openai_format(body) is True

    def test_multi_turn_string_content(self):
        body = {
            "model": "auto",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello!"},
            ],
        }
        assert _is_openai_format(body) is True

    def test_title_generation_shape(self):
        """Hermes auxiliary title generation: short prompt, string content."""
        body = {
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Summarize in 5 words."},
            ],
            "max_tokens": 30,
        }
        assert _is_openai_format(body) is True

    def test_with_temperature(self):
        body = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
        }
        assert _is_openai_format(body) is True

    # --- Negative cases: should NOT be detected as OpenAI ---

    def test_anthropic_top_level_system(self):
        """Anthropic: system is a top-level field, not a messages entry."""
        body = {
            "model": "mimo-v2.5-pro",
            "system": "you are helpful",
            "messages": [{"role": "user", "content": "hi"}],
        }
        assert _is_openai_format(body) is False

    def test_anthropic_list_content(self):
        """Anthropic: content is a list of content blocks (text/image)."""
        body = {
            "model": "mimo-v2.5-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "what's in this image?"}],
                }
            ],
        }
        assert _is_openai_format(body) is False

    def test_anthropic_image_content(self):
        body = {
            "model": "mimo-v2.5-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "data": "..."}},
                        {"type": "text", "text": "describe"},
                    ],
                }
            ],
        }
        assert _is_openai_format(body) is False

    def test_anthropic_stop_sequences(self):
        body = {
            "model": "mimo-v2.5-pro",
            "stop_sequences": ["END"],
            "messages": [{"role": "user", "content": "hi"}],
        }
        assert _is_openai_format(body) is False

    def test_empty_messages(self):
        body = {"model": "auto", "messages": []}
        assert _is_openai_format(body) is False

    def test_missing_messages(self):
        body = {"model": "auto"}
        assert _is_openai_format(body) is False

    def test_non_dict_body(self):
        assert _is_openai_format([1, 2, 3]) is False
        assert _is_openai_format("string") is False
        assert _is_openai_format(None) is False

    def test_mixed_string_and_list_content_is_conservative(self):
        """If a request has mixed content types, lean toward Anthropic (no conversion)."""
        body = {
            "model": "auto",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "user", "content": [{"type": "text", "text": "world"}]},
            ],
        }
        # list_count > 0 → not classified as OpenAI → goes through Anthropic path
        # This is a conservative choice; if a user actually sends mixed content
        # they should split or use a clear format.
        assert _is_openai_format(body) is False
