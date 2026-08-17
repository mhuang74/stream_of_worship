"""Tests for ThemeClassifier LLM theme/posture classification."""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sow_analysis.config import settings
from sow_analysis.workers.classifier import ThemeClassifier, _lyric_hash, has_cached_llm_fields
from sow_analysis.workers.components import ComponentInstance


def _make_component(occurrence=1, ctype="chorus", start=0.0, end=10.0, role="none"):
    return ComponentInstance(
        component_type=ctype,
        occurrence_index=occurrence,
        role=role,
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


def _parsed_result(theme="讚美", posture="To God"):
    return {
        "theme": theme,
        "theme_confidence": 0.9,
        "theme_reasoning": "praise",
        "vocal_posture": posture,
        "vocal_posture_confidence": 0.9,
        "posture_reasoning": "direct",
    }


def test_classify_components_logs_per_component(classifier, caplog):
    """Per-component start/completed logging fires for each component."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    components = [_make_component(1, "chorus", start=0.0, end=5.0), _make_component(2, "verse", start=5.0, end=10.0)]
    # Distinct lyrics per component so dedup does not collapse them.
    lrc_content = (
        "[00:00.00]讚美主\n"
        "[00:05.00]感謝神\n"
    )

    async def fake_call(sync_fn, *, description, loop=None):
        return _parsed_result()

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=True
            )
        )

    messages = [r.message for r in caplog.records]
    assert any("2 to classify" in m and "2 total" in m for m in messages)
    assert any("starting component 1/2" in m for m in messages)
    assert any("starting component 2/2" in m for m in messages)
    assert any("lyric_hash=" in m for m in messages)
    assert any("lyrics=" in m for m in messages)
    assert any("completed component 1/2" in m and "theme=讚美" in m for m in messages)
    assert any("completed component 2/2" in m for m in messages)
    assert components[0].theme == "讚美"
    assert components[1].vocal_posture == "To God"


def test_truncate_lyrics():
    from sow_analysis.workers.classifier import _truncate_lyrics

    assert _truncate_lyrics(None) == "<empty>"
    assert _truncate_lyrics([]) == "<empty>"
    assert _truncate_lyrics([""]) == "<empty>"
    # Short text: no truncation.
    assert _truncate_lyrics(["讚美主"]) == "讚美主"
    # Long text: truncated with ellipsis.
    long = "X" * 100
    result = _truncate_lyrics([long])
    assert result.endswith("...")
    assert len(result) == 60 + 3  # 60 chars + "..."
    # Multiple lines joined with spaces.
    assert _truncate_lyrics(["讚美", "主"]) == "讚美 主"


def test_classify_components_logs_include_lyric_hash(classifier, caplog):
    """The completed log line includes lyric_hash for traceability."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    components = [_make_component(1, "chorus", start=0.0, end=5.0, role="entry")]
    lrc_content = "[00:00.00]讚美主\n"

    async def fake_call(sync_fn, *, description, loop=None):
        return _parsed_result()

    with patch("sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=False
            )
        )

    messages = [r.message for r in caplog.records]
    # Both starting and completed lines must carry lyric_hash.
    assert any("lyric_hash=" in m and "completed" in m for m in messages)


def test_classify_components_skips_non_essential(classifier, caplog):
    """With all_components=False, only essential-role components get LLM calls."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    # 5 components: 2 with role='none', 3 essential. Each has distinct lyrics
    # so dedup does not collapse them.
    components = [
        _make_component(1, "chorus", start=0.0, end=5.0, role="entry"),
        _make_component(2, "chorus", start=5.0, end=10.0, role="none"),
        _make_component(3, "chorus", start=10.0, end=15.0, role="exit"),
        _make_component(4, "verse", start=15.0, end=20.0, role="loop_target"),
        _make_component(5, "chorus", start=20.0, end=25.0, role="none"),
    ]
    lrc_content = (
        "[00:00.00]讚美主\n"
        "[00:05.00]感謝神\n"
        "[00:10.00]敬拜你\n"
        "[00:15.00]祈禱\n"
        "[00:20.00]信心\n"
    )

    call_count = 0

    async def fake_call(sync_fn, *, description, loop=None):
        nonlocal call_count
        call_count += 1
        return _parsed_result()

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=False
            )
        )

    # Only 3 essential components get LLM calls.
    assert call_count == 3
    # Essential components have theme populated.
    assert components[0].theme == "讚美"  # entry
    assert components[2].theme == "讚美"  # exit
    assert components[3].theme == "讚美"  # loop_target
    # Non-essential components retain theme=None.
    assert components[1].theme is None
    assert components[4].theme is None

    messages = [r.message for r in caplog.records]
    assert any("3 to classify" in m and "2 skipped" in m for m in messages)
    assert any("skipped component 2/5" in m for m in messages)
    assert any("skipped component 5/5" in m for m in messages)


def test_classify_components_all_components_flag(classifier):
    """With all_components=True, all components get LLM calls."""
    components = [
        _make_component(1, "chorus", start=0.0, end=5.0, role="entry"),
        _make_component(2, "chorus", start=5.0, end=10.0, role="none"),
        _make_component(3, "chorus", start=10.0, end=15.0, role="exit"),
        _make_component(4, "verse", start=15.0, end=20.0, role="loop_target"),
        _make_component(5, "chorus", start=20.0, end=25.0, role="none"),
    ]
    # Distinct lyrics per component so dedup does not collapse them.
    lrc_content = (
        "[00:00.00]讚美主\n"
        "[00:05.00]感謝神\n"
        "[00:10.00]敬拜你\n"
        "[00:15.00]祈禱\n"
        "[00:20.00]信心\n"
    )

    call_count = 0

    async def fake_call(sync_fn, *, description, loop=None):
        nonlocal call_count
        call_count += 1
        return _parsed_result()

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=True
            )
        )

    # All 5 components get LLM calls.
    assert call_count == 5
    # All components have theme populated.
    for comp in components:
        assert comp.theme == "讚美"


def test_classify_components_lyric_dedup(classifier, caplog):
    """3 essential chorus components with identical lyrics → 1 LLM call."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    # 3 essential chorus components with overlapping time ranges that map
    # to the same LRC lyrics.
    lrc_content = (
        "[00:00.00]讚美主\n"
        "[00:05.00]哈利路亞\n"
        "[00:10.00]讚美主\n"
        "[00:15.00]哈利路亞\n"
        "[00:20.00]讚美主\n"
        "[00:25.00]哈利路亞\n"
    )
    components = [
        _make_component(1, "chorus", start=0.0, end=15.0, role="entry"),
        _make_component(2, "chorus", start=0.0, end=15.0, role="exit"),
        _make_component(3, "chorus", start=0.0, end=15.0, role="entry_exit"),
    ]

    call_count = 0

    async def fake_call(sync_fn, *, description, loop=None):
        nonlocal call_count
        call_count += 1
        return _parsed_result(theme="敬拜", posture="To God")

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=True
            )
        )

    # Only 1 LLM call fires (all 3 have identical lyrics).
    assert call_count == 1
    # All 3 components inherit the same theme/posture via copy.
    for comp in components:
        assert comp.theme == "敬拜"
        assert comp.vocal_posture == "To God"
        assert comp.theme_confidence == 0.9
        assert comp.vocal_posture_confidence == 0.9
        assert comp.theme_reasoning == "praise"
        assert comp.posture_reasoning == "direct"

    messages = [r.message for r in caplog.records]
    assert any("1 unique lyric groups" in m and "deduped from 3" in m for m in messages)
    assert any("dedup hit" in m and "copied from component 1/3" in m for m in messages)


def test_classify_components_dedup_different_lyrics(classifier):
    """2 essential components with different lyrics → 2 distinct LLM calls."""
    lrc_content = (
        "[00:00.00]讚美主\n"
        "[00:05.00]哈利路亞\n"
        "[00:10.00]感謝神\n"
        "[00:15.00]恩典滿溢\n"
    )
    components = [
        _make_component(1, "chorus", start=0.0, end=8.0, role="entry"),
        _make_component(2, "chorus", start=8.0, end=16.0, role="exit"),
    ]

    call_count = 0

    async def fake_call(sync_fn, *, description, loop=None):
        nonlocal call_count
        call_count += 1
        return _parsed_result()

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=True
            )
        )

    # 2 distinct LLM calls (different lyrics).
    assert call_count == 2


def test_lyric_hash_normalizes_whitespace_and_case():
    """Whitespace and case differences produce the same hash."""
    h1 = _lyric_hash(["  Hello  World "])
    h2 = _lyric_hash(["hello world"])
    h3 = _lyric_hash(["HELLO", "world"])
    assert h1 == h2 == h3
    assert h1 != "EMPTY"


def test_lyric_hash_empty_inputs():
    """None/empty inputs return the EMPTY sentinel."""
    assert _lyric_hash(None) == "EMPTY"
    assert _lyric_hash([]) == "EMPTY"
    assert _lyric_hash([""]) == "EMPTY"
    assert _lyric_hash(["   ", "\t"]) == "EMPTY"


def test_call_llm_with_retry_description(classifier):
    """call_llm_with_retry is invoked with a component-specific description."""
    component = _make_component(occurrence=3, ctype="bridge")
    captured = {}

    async def fake_call(sync_fn, *, description, loop=None):
        captured["description"] = description
        return _parsed_result()

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
        return _parsed_result(theme="敬拜")

    with patch(
        "sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call
    ):
        asyncio.run(classifier._classify_component_llm(component, "lyrics"))

    assert len(calls) == 2
    assert "retry" in calls[1]
    assert component.theme == "敬拜"


def test_apply_llm_result_drops_non_canonical_theme(classifier):
    """A theme outside the 12-value enum is dropped to None, not persisted.

    Regression: the LLM sometimes returns a near-miss value (e.g. '十字架構'
    instead of the canonical '十字架'). Persisting it would violate the
    song_components_theme_check DB CHECK constraint and fail the whole
    component upsert. _apply_llm_result must coerce it to None (which the
    constraint allows) and keep reasoning/confidence for diagnostics.
    """
    component = _make_component(occurrence=1, role="entry")
    parsed = _parsed_result(theme="十字架構", posture="To God")
    parsed["theme_reasoning"] = "lyrics center on the cross"

    classifier._apply_llm_result(component, parsed, heuristic_posture=None)

    assert component.theme is None
    assert component.theme_reasoning == "lyrics center on the cross"
    assert component.vocal_posture == "To God"


def test_apply_llm_result_keeps_canonical_theme(classifier):
    """A valid enum theme is preserved verbatim."""
    component = _make_component(occurrence=1, role="entry")
    parsed = _parsed_result(theme="十字架", posture="About God")

    classifier._apply_llm_result(component, parsed, heuristic_posture=None)

    assert component.theme == "十字架"
    assert component.vocal_posture == "About God"


def test_apply_llm_result_drops_non_canonical_posture(classifier):
    """A vocal_posture outside the 3-value enum is dropped to None."""
    component = _make_component(occurrence=1, role="entry")
    parsed = _parsed_result(theme="讚美", posture="To Everyone")

    classifier._apply_llm_result(component, parsed, heuristic_posture=None)

    assert component.theme == "讚美"
    assert component.vocal_posture is None


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


def test_has_cached_llm_fields_all_populated():
    """Returns True when all essential components have theme + posture set."""
    components = [
        ComponentInstance(
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=0,
            end_time=10,
            theme="讚美",
            vocal_posture="To God",
            theme_confidence=0.9,
            vocal_posture_confidence=0.9,
        ),
    ]
    assert has_cached_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True
    )


def test_has_cached_llm_fields_missing_theme():
    """Returns False when a candidate component has theme=None."""
    components = [
        ComponentInstance(
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=0,
            end_time=10,
            theme=None,
            vocal_posture="To God",
        ),
    ]
    assert not has_cached_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True
    )


def test_has_cached_llm_fields_skips_non_essential():
    """Non-essential components are ignored (essential-only mode)."""
    components = [
        ComponentInstance(
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=0,
            end_time=10,
            theme="讚美",
            vocal_posture="To God",
        ),
        ComponentInstance(
            component_type="verse",
            occurrence_index=1,
            role="none",
            start_time=10,
            end_time=20,
            theme=None,
            vocal_posture=None,
        ),
    ]
    assert has_cached_llm_fields(
        components,
        classify_theme=True,
        classify_vocal_posture=True,
        all_components=False,
    )


def test_has_cached_llm_fields_all_components_mode():
    """all_components=True requires ALL components to have fields."""
    components = [
        ComponentInstance(
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=0,
            end_time=10,
            theme="讚美",
            vocal_posture="To God",
        ),
        ComponentInstance(
            component_type="verse",
            occurrence_index=1,
            role="none",
            start_time=10,
            end_time=20,
            theme=None,
            vocal_posture=None,
        ),
    ]
    assert not has_cached_llm_fields(
        components,
        classify_theme=True,
        classify_vocal_posture=True,
        all_components=True,
    )


def test_has_cached_llm_fields_theme_only():
    """When classify_vocal_posture=False, missing posture is OK."""
    components = [
        ComponentInstance(
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=0,
            end_time=10,
            theme="讚美",
            vocal_posture=None,
        ),
    ]
    assert has_cached_llm_fields(
        components, classify_theme=True, classify_vocal_posture=False
    )


def test_has_cached_llm_fields_empty():
    """Empty component list returns True (nothing to classify)."""
    assert has_cached_llm_fields(
        [], classify_theme=True, classify_vocal_posture=True
    )
