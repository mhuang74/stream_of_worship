"""Unit tests for the ``model:effort`` LLM notation and its builder wiring.

Mirrors the splitting semantics of
ops/analysis-service/src/sow_analysis/services/llm_models.py.
"""

from unittest.mock import patch

import pytest

from stream_of_worship.admin.services.llm_models import (
    KNOWN_REASONING_EFFORTS,
    chat_model_kwargs,
    split_llm_model_effort,
)
from stream_of_worship.admin.services.structured_lyrics import build_chat_model_for_lyrics
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.graph.llm import build_chat_model


class TestSplitLlmModelEffort:
    """Tests for split_llm_model_effort."""

    @pytest.mark.parametrize(
        ("llm_model", "expected"),
        [
            ("deepseek-v4.1-flash:low", ("deepseek-v4.1-flash", "low")),
            ("m:xhigh", ("m", "xhigh")),
            ("m:none", ("m", "none")),
            ("m", ("m", None)),
            ("m:free", ("m:free", None)),
            ("m:LOW", ("m:LOW", None)),
            ("m:high:low", ("m:high", "low")),
            ("some/provider:free", ("some/provider:free", None)),
        ],
    )
    def test_splits_only_known_effort_suffixes(self, llm_model, expected):
        assert split_llm_model_effort(llm_model) == expected

    def test_every_documented_effort_is_recognized(self):
        for effort in KNOWN_REASONING_EFFORTS:
            assert split_llm_model_effort(f"m:{effort}") == ("m", effort)


class TestChatModelKwargs:
    """Tests for chat_model_kwargs."""

    def test_effort_becomes_extra_body_reasoning_effort(self):
        assert chat_model_kwargs("deepseek-v4.1-flash:low") == {
            "model": "deepseek-v4.1-flash",
            "extra_body": {"reasoning_effort": "low"},
        }

    def test_plain_model_id_has_no_extra_body(self):
        assert chat_model_kwargs("qwen3.6-35b-fast") == {"model": "qwen3.6-35b-fast"}

    def test_unknown_suffix_is_kept_in_model_id(self):
        assert chat_model_kwargs("some/provider:free") == {"model": "some/provider:free"}


class TestBuilderWiring:
    """Both local chat builders must strip the suffix into extra_body."""

    def test_constructor_builder_splits_suffix(self, monkeypatch):
        monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
        config = RunConfig(llm_enabled=True, llm_model="deepseek-v4.1-flash:low")

        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            build_chat_model(config)

        kwargs = mock_chat.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4.1-flash"
        assert kwargs["extra_body"] == {"reasoning_effort": "low"}

    def test_constructor_builder_keeps_plain_model_id(self, monkeypatch):
        monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
        config = RunConfig(llm_enabled=True, llm_model="qwen3.6-35b-fast")

        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            build_chat_model(config)

        kwargs = mock_chat.call_args.kwargs
        assert kwargs["model"] == "qwen3.6-35b-fast"
        assert "extra_body" not in kwargs

    def test_constructor_builder_none_when_llm_disabled(self):
        assert build_chat_model(RunConfig(llm_enabled=False)) is None

    def test_lyrics_builder_splits_suffix(self, monkeypatch):
        monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
        monkeypatch.setenv("SOW_LLM_MODEL", "deepseek-v4.1-flash:low")

        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            build_chat_model_for_lyrics()

        kwargs = mock_chat.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4.1-flash"
        assert kwargs["extra_body"] == {"reasoning_effort": "low"}

    def test_lyrics_builder_keeps_plain_model_id(self, monkeypatch):
        monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
        monkeypatch.setenv("SOW_LLM_MODEL", "qwen3.6-35b-fast")

        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            build_chat_model_for_lyrics()

        kwargs = mock_chat.call_args.kwargs
        assert kwargs["model"] == "qwen3.6-35b-fast"
        assert "extra_body" not in kwargs

    def test_lyrics_builder_requires_env(self, monkeypatch):
        monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
        monkeypatch.delenv("SOW_LLM_MODEL", raising=False)

        with pytest.raises(RuntimeError, match="SOW_LLM_API_KEY"):
            build_chat_model_for_lyrics()
