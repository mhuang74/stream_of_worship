"""Tests for the LLM-based structured lyrics-to-LRC alignment module.

LLM-dependent tests are gated via @pytest.mark.skipif on SOW_LLM_LIVE_TESTS.
They run only when SOW_LLM_LIVE_TESTS=1 with a configured SOW_LLM_API_KEY.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.config import settings
from sow_analysis.workers.structured_lyrics_aligner import (
    _build_alignment_prompt,
    _load_alignment_few_shot_examples,
    _parse_alignment_json,
    _render_structured_sections,
    align_structured_lyrics,
)

# ── Test fixtures ───────────────────────────────────────────────────────────

_LRC_SIMPLE = """[00:10.00]讚美主
[00:25.00]哈利路亞
[00:40.00]讚美主
[00:55.00]哈利路亞
"""

_STRUCTURED_LYRICS_SIMPLE = json.dumps({
    "sections": [
        {"label": "Verse", "raw_label": "[Verse]", "lines": ["讚美主", "哈利路亞"]},
        {"label": "Chorus", "raw_label": "[Chorus]", "lines": ["讚美主", "哈利路亞"]},
    ],
    "preamble_lines": [],
})


# ── Unit tests for _render_structured_sections() ────────────────────────────


class TestRenderStructuredSections:
    def test_renders_sections_with_labels(self):
        text = _render_structured_sections(_STRUCTURED_LYRICS_SIMPLE)
        assert "[Verse]" in text
        assert "[Chorus]" in text
        assert "讚美主" in text
        assert "哈利路亞" in text

    def test_sections_separated_by_blank_line(self):
        text = _render_structured_sections(_STRUCTURED_LYRICS_SIMPLE)
        blocks = text.split("\n\n")
        assert len(blocks) == 2
        assert blocks[0].startswith("[Verse]")
        assert blocks[1].startswith("[Chorus]")

    def test_invalid_json_returns_placeholder(self):
        text = _render_structured_sections("not json")
        assert "invalid" in text.lower()

    def test_no_sections_returns_placeholder(self):
        text = _render_structured_sections(json.dumps({"sections": []}))
        assert "no sections" in text.lower()

    def test_uses_raw_label_fallback(self):
        structured = json.dumps({
            "sections": [
                {"raw_label": "[Custom]", "lines": ["line1"]},
            ]
        })
        text = _render_structured_sections(structured)
        assert "[Custom]" in text


# ── Unit tests for _parse_alignment_json() ──────────────────────────────────


class TestParseAlignmentJson:
    def test_valid_json(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })
        sections = _parse_alignment_json(response, n_lines=4)
        assert sections is not None
        assert len(sections) == 2
        assert sections[0].label == "verse"
        assert sections[0].line_start == 1
        assert sections[1].label == "chorus"
        assert sections[1].line_start == 3

    def test_invalid_json_returns_none(self):
        assert _parse_alignment_json("not json", n_lines=5) is None

    def test_empty_sections_returns_none(self):
        assert _parse_alignment_json('{"sections": []}', n_lines=5) is None

    def test_missing_sections_key_returns_none(self):
        assert _parse_alignment_json('{"wrong_key": []}', n_lines=5) is None

    def test_rejects_out_of_range(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 10},
            ]
        })
        assert _parse_alignment_json(response, n_lines=5) is None

    def test_rejects_overlap(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 3},
                {"label": "chorus", "line_start": 3, "line_end": 4},
            ]
        })
        result = _parse_alignment_json(response, n_lines=4)
        assert result is not None
        # Overlapping section (line 3) should be rejected; only verse kept.
        assert len(result) == 1
        assert result[0].label == "verse"

    def test_allows_gaps(self):
        """[H4] Relaxed contiguity: gaps between sections are allowed."""
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 4, "line_end": 5},
            ]
        })
        result = _parse_alignment_json(response, n_lines=5)
        assert result is not None
        assert len(result) == 2

    def test_sorts_by_line_start(self):
        """[H4] Out-of-order sections are sorted by line_start."""
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 3, "line_end": 4},
                {"label": "verse", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=4)
        assert result is not None
        assert result[0].line_start == 1
        assert result[1].line_start == 3

    def test_repeated_labels_different_ranges(self):
        """[H4] Three chorus sections with different line ranges are valid."""
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 3, "line_end": 4},
                {"label": "chorus", "line_start": 5, "line_end": 6},
            ]
        })
        result = _parse_alignment_json(response, n_lines=6)
        assert result is not None
        assert len(result) == 3
        assert all(s.label == "chorus" for s in result)

    def test_label_normalization_verse_number(self):
        """[H5] 'verse 1' is normalized to 'verse'."""
        response = json.dumps({
            "sections": [
                {"label": "verse 1", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is not None
        assert result[0].label == "verse"

    def test_label_normalization_pre_chorus(self):
        """[H5] 'pre-chorus' is normalized to 'prechorus'."""
        response = json.dumps({
            "sections": [
                {"label": "pre-chorus", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is not None
        assert result[0].label == "prechorus"

    def test_label_normalization_hook(self):
        """[H5] 'hook' is normalized to 'chorus'."""
        response = json.dumps({
            "sections": [
                {"label": "hook", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is not None
        assert result[0].label == "chorus"

    def test_label_normalization_refrain(self):
        """[H5] 'refrain' is normalized to 'chorus'."""
        response = json.dumps({
            "sections": [
                {"label": "refrain", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is not None
        assert result[0].label == "chorus"

    def test_unknown_label_rejected(self):
        """Labels not in _VALID_LABELS or normalization map are rejected."""
        response = json.dumps({
            "sections": [
                {"label": "solo", "line_start": 1, "line_end": 2},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is None

    def test_confidence_clamped(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2, "confidence": 1.5},
            ]
        })
        result = _parse_alignment_json(response, n_lines=2)
        assert result is not None
        assert result[0].confidence == 1.0

    def test_overlap_against_all_accepted(self):
        """[H4] Overlap check compares against ALL accepted sections, not just previous."""
        # Sections 1-2, 4-5 (gap at 3), then 2-3 overlaps with 1-2.
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 4, "line_end": 5},
                {"label": "bridge", "line_start": 2, "line_end": 3},
            ]
        })
        result = _parse_alignment_json(response, n_lines=5)
        assert result is not None
        # After sorting: 1-2 (verse), 2-3 (bridge overlaps with verse), 4-5 (chorus)
        # Bridge overlaps with verse (line 2), so it's rejected.
        assert len(result) == 2
        labels = [s.label for s in result]
        assert "verse" in labels
        assert "chorus" in labels
        assert "bridge" not in labels


# ── Unit tests for _build_alignment_prompt() ──────────────────────────────


class TestBuildAlignmentPrompt:
    def test_returns_system_and_user_messages(self):
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, []
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_prompt_contains_normalization_instruction(self):
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, []
        )
        system = messages[0]["content"]
        assert "Normalize all labels" in system
        assert "intro, verse, prechorus, chorus, bridge, outro, instrumental" in system

    def test_user_message_contains_numbered_lrc(self):
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, []
        )
        user = messages[1]["content"]
        assert "1  [10.00] 讚美主" in user

    def test_user_message_contains_structured_sections(self):
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, []
        )
        user = messages[1]["content"]
        assert "[Verse]" in user
        assert "[Chorus]" in user

    def test_few_shot_examples_included(self):
        examples = [
            {
                "source_song_id": "test_song",
                "input": "Numbered LRC:\n1  [00:10.00] test line",
                "sections": [
                    {"label": "verse", "line_start": 1, "line_end": 1, "confidence": 0.9}
                ],
            }
        ]
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, examples
        )
        user = messages[1]["content"]
        assert "test line" in user
        assert "Expected output:" in user


# ── Unit tests for _load_alignment_few_shot_examples() ─────────────────────


class TestLoadAlignmentFewShot:
    def test_leakage_guard_raises_on_fixture_id(self, tmp_path):
        """[H3] Loading a few-shot file with a held-out source_song_id raises."""
        fake_file = tmp_path / "structured_lyrics_alignment_few_shot.json"
        fake_file.write_text(json.dumps([
            {
                "source_song_id": "jun_wang_jiu_zai_zhe_li_1c32724c",
                "input": "x",
                "sections": [],
            }
        ]), encoding="utf-8")
        import sow_analysis.workers.structured_lyrics_aligner as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "structured_lyrics_aligner.py")
        try:
            with pytest.raises(ValueError, match="held-out"):
                _load_alignment_few_shot_examples()
        finally:
            mod.__file__ = original_file

    def test_non_held_out_id_does_not_raise(self, tmp_path):
        fake_file = tmp_path / "structured_lyrics_alignment_few_shot.json"
        fake_file.write_text(json.dumps([
            {"source_song_id": "safe_song_id", "input": "x", "sections": []}
        ]), encoding="utf-8")
        import sow_analysis.workers.structured_lyrics_aligner as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "structured_lyrics_aligner.py")
        try:
            examples = _load_alignment_few_shot_examples()
            assert len(examples) == 1
        finally:
            mod.__file__ = original_file

    def test_absent_file_returns_empty(self, tmp_path):
        import sow_analysis.workers.structured_lyrics_aligner as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "nonexistent.py")
        try:
            examples = _load_alignment_few_shot_examples()
            assert examples == []
        finally:
            mod.__file__ = original_file

    def test_committed_file_loads_successfully(self):
        """The committed few-shot file loads without error and has 3 examples."""
        examples = _load_alignment_few_shot_examples()
        assert len(examples) == 3
        for ex in examples:
            assert "source_song_id" in ex
            assert "input" in ex
            assert "sections" in ex

    def test_committed_file_no_held_out_ids(self):
        """[H3] No committed example has a held-out source_song_id."""
        examples = _load_alignment_few_shot_examples()
        held_out = {
            "jun_wang_jiu_zai_zhe_li_1c32724c",
            "yi_sheng_jing_bai_mi_da2173d0",
            "zhu_a__wo_yao_gen_sui_mi_83163301",
        }
        for ex in examples:
            assert ex["source_song_id"] not in held_out

    def test_committed_file_labels_normalized(self):
        """[H5] All labels in committed examples use normalized forms."""
        examples = _load_alignment_few_shot_examples()
        valid_labels = {
            "intro", "verse", "prechorus", "chorus",
            "bridge", "outro", "instrumental",
        }
        for ex in examples:
            for sec in ex["sections"]:
                assert sec["label"] in valid_labels, (
                    f"Label '{sec['label']}' is not normalized"
                )


# ── Unit tests for align_structured_lyrics() (mocked LLM) ──────────────────


class TestAlignStructuredLyrics:
    @pytest.mark.asyncio
    async def test_returns_components_with_correct_source(self, monkeypatch):
        """[C1] Components have source='structured_lyrics_llm'."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        llm_response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_SIMPLE,
                _LRC_SIMPLE,
            )

        assert len(components) > 0
        assert all(c.source == "structured_lyrics_llm" for c in components)

    @pytest.mark.asyncio
    async def test_chorus_role_assignment(self, monkeypatch):
        """Single chorus produces entry + exit rows."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        llm_response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_SIMPLE,
                _LRC_SIMPLE,
            )

        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) == 2
        roles = {c.role for c in chorus_rows}
        assert "entry" in roles
        assert "exit" in roles

    @pytest.mark.asyncio
    async def test_returns_empty_on_parse_failure(self, monkeypatch):
        """When LLM returns invalid JSON, returns empty list."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value="not json at all",
        ):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_SIMPLE,
                _LRC_SIMPLE,
            )

        assert components == []

    @pytest.mark.asyncio
    async def test_duration_clamping(self, monkeypatch):
        """[H2] end_time is clamped to song_total_duration."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        llm_response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_SIMPLE,
                _LRC_SIMPLE,
                song_total_duration=20.0,
            )

        for c in components:
            assert c.end_time <= 20.0

    @pytest.mark.asyncio
    async def test_non_chorus_only_sections(self, monkeypatch):
        """[H1] Songs without chorus still produce components with entry/exit roles."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        structured_no_chorus = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["讚美主", "哈利路亞"]},
                {"label": "Bridge", "lines": ["讚美主", "哈利路亞"]},
            ]
        })

        llm_response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "bridge", "line_start": 3, "line_end": 4, "confidence": 0.85},
            ]
        })

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            components = await align_structured_lyrics(
                structured_no_chorus,
                _LRC_SIMPLE,
            )

        assert len(components) == 2
        assert components[0].role == "entry"
        assert components[1].role == "exit"

    @pytest.mark.asyncio
    async def test_chorus_repetition_validation_applied(self, monkeypatch):
        """Defensive post-processing: _validate_chorus_repetition is applied."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        # LRC where chorus lines repeat (lines 3-4 match lines 1-2)
        lrc = """[00:10.00]哈利路亞
[00:20.00]讚美主
[00:30.00]哈利路亞
[00:40.00]讚美主
"""
        llm_response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2, "confidence": 0.95},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })

        with patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_SIMPLE,
                lrc,
            )

        # Should produce components (chorus repetition validation is defensive,
        # doesn't remove sections, only adjusts confidence/boundaries)
        assert len(components) > 0
        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) >= 2


# ── Live LLM test (gated) ──────────────────────────────────────────────────

_LIVE_TEST_SKIP = os.environ.get("SOW_LLM_LIVE_TESTS") != "1"


@pytest.mark.skipif(_LIVE_TEST_SKIP, reason="Set SOW_LLM_LIVE_TESTS=1 to run live LLM tests")
class TestAlignStructuredLyricsLive:
    @pytest.mark.asyncio
    async def test_align_live(self):
        """Live LLM alignment on a simple LRC + structured lyrics."""
        if not settings.SOW_LLM_API_KEY:
            pytest.skip("SOW_LLM_API_KEY not set")

        components = await align_structured_lyrics(
            _STRUCTURED_LYRICS_SIMPLE,
            _LRC_SIMPLE,
        )
        assert len(components) > 0
        assert all(c.source == "structured_lyrics_llm" for c in components)
