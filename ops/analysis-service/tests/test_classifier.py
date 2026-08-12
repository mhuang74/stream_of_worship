"""Tests for ThemeClassifier LLM theme/posture classification."""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sow_analysis.config import settings
from sow_analysis.workers.classifier import ThemeClassifier
from sow_analysis.workers.components import ComponentInstance


def _make_component(occurrence=1, ctype="chorus", start=0.0, end=10.0):
    return ComponentInstance(
        component_type=ctype,
        occurrence_index=occurrence,
        role="none",
        start_time=start,
        end_time=end,
    )


@pytest.fixture
def classifier(monkeypatch):
    monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://test.example/v1")
    monkeypatch.setattr(settings, "SOW_LLM_MODEL", "test-model")
    with patch("sow_analysis.workers.classifier.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        clf = ThemeClassifier()
        clf._client = mock_client
        yield clf


def _mock_response(content: str, model="test-model", usage=None, finish="stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish
    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


def test_classify_components_logs_per_component(classifier, caplog):
    """Per-component start/completed logging fires for each component."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    components = [_make_component(1, "chorus"), _make_component(2, "verse")]

    parsed = {
        "theme": "讚美",
        "theme_confidence": 0.9,
        "theme_reasoning": "praise",
        "vocal_posture": "To God",
        "vocal_posture_confidence": 0.9,
        "posture_reasoning": "direct",
    }

    async def fake_call(sync_fn, *, description, loop=None):
        return parsed

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(classifier.classify_components(components))

    messages = [r.message for r in caplog.records]
    assert "LLM classification: 2 components to classify" in messages
    assert any("starting component 1/2" in m for m in messages)
    assert any("starting component 2/2" in m for m in messages)
    assert any("completed component 1/2" in m and "theme=讚美" in m for m in messages)
    assert any("completed component 2/2" in m for m in messages)
    assert components[0].theme == "讚美"
    assert components[1].vocal_posture == "To God"


def test_call_llm_with_retry_description(classifier):
    """call_llm_with_retry is invoked with a component-specific description."""
    component = _make_component(occurrence=3, ctype="bridge")
    parsed = {
        "theme": "祈禱",
        "theme_confidence": 0.8,
        "theme_reasoning": "",
        "vocal_posture": "To God",
        "vocal_posture_confidence": 0.8,
        "posture_reasoning": "",
    }
    captured = {}

    async def fake_call(sync_fn, *, description, loop=None):
        captured["description"] = description
        return parsed

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(classifier._classify_component_llm(component, "lyrics"))

    assert "comp 3" in captured["description"]
    assert "theme/posture classification" in captured["description"]


def test_retry_on_parse_failure(classifier):
    """A second call_llm_with_retry fires when first parse yields no theme."""
    component = _make_component(occurrence=1)
    calls = []

    async def fake_call(sync_fn, *, description, loop=None):
        calls.append(description)
        if len(calls) == 1:
            return {}  # unparseable -> theme None
        return {
            "theme": "敬拜",
            "theme_confidence": 0.8,
            "theme_reasoning": "",
            "vocal_posture": "To God",
            "vocal_posture_confidence": 0.8,
            "posture_reasoning": "",
        }

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(classifier._classify_component_llm(component, "lyrics"))

    assert len(calls) == 2
    assert "retry" in calls[1]
    assert component.theme == "敬拜"


def test_log_llm_diagnostics(classifier, caplog):
    """_log_llm_diagnostics extracts model/token usage from a mock response."""
    caplog.set_level(logging.DEBUG, logger="sow_analysis.workers.classifier")
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    usage.total_tokens = 150
    resp = _mock_response('{"theme": "讚美"}', model="gpt-test", usage=usage)
    component = _make_component(occurrence=7)

    classifier._log_llm_diagnostics(resp, component)

    messages = [r.message for r in caplog.records]
    assert any(
        "model=gpt-test" in m
        and "tokens=100/50/150" in m
        and "component=7" in m
        for m in messages
    )


def test_get_llm_semaphore_removed():
    """The removed _get_llm_semaphore helper is no longer referenced."""
    import sow_analysis.workers.classifier as mod

    assert not hasattr(mod, "_get_llm_semaphore")
    assert not hasattr(ThemeClassifier, "_retry_llm_call")
    assert not hasattr(ThemeClassifier, "_llm_semaphore")
