"""Tests for the LLM-based structured lyrics-to-LRC alignment module.

LLM-dependent tests are gated via @pytest.mark.skipif on SOW_LLM_LIVE_TESTS.
They run only when SOW_LLM_LIVE_TESTS=1 with a configured SOW_LLM_API_KEY.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.config import settings
from sow_analysis.workers.section_segmenter import Section
from sow_analysis.workers.structured_lyrics_aligner import (
    _build_alignment_prompt,
    _load_alignment_few_shot_examples,
    _parse_alignment_json,
    _render_structured_sections,
    _validate_section_content_alignment,
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

# The observed off-by-one case: structured Verse has 5 lines but the LRC merges
# Verse lines 4+5 into a single LRC line (4 LRC lines). The LLM extends the
# Verse to 5 LRC lines, swallowing Chorus line 1.
_LRC_OFF_BY_ONE = """[00:10.05] 主袢使卑微 轉為尊貴
[00:15.15] 使傷心流淚 轉為笑顏
[00:20.42] 患難生忍耐 忍耐生老練
[00:25.84] 老練生盼望 盼望不至羞愧 就沒有失望
[00:33.75] 心中充滿盼望 盼望使眼睛明亮
[00:40.00] 道路雖崎嶇 袢與我同行
[00:44.53] 心中充滿盼望 盼望使信心剛強
[00:50.55] 信靠每一句應許 生命充滿亮光
[00:58.46] 生命充滿亮光
"""

_STRUCTURED_LYRICS_OFF_BY_ONE = json.dumps({
    "sections": [
        {"label": "Verse", "raw_label": "[Verse]", "lines": [
            "主 袢使卑微轉為尊貴",
            "使傷心流淚轉為笑顏",
            "患難生忍耐 忍耐生老練",
            "老練生盼望 盼望不至羞愧",
            "就沒有失望",
        ]},
        {"label": "Chorus", "raw_label": "[Chorus]", "lines": [
            "心中充滿盼望 盼望使眼睛明亮",
            "道路雖崎嶇 袢與我同行",
            "心中充滿盼望 盼望使信心剛強",
            "信靠每一句應許 生命充滿亮光",
        ]},
    ],
    "preamble_lines": [],
})

# Distinct-line fixture for retry-loop tests (no repeated lines).
_LRC_RETRY = """[00:10.00] 第一行歌詞甲
[00:20.00] 第二行歌詞乙
[00:30.00] 第三行歌詞丙
[00:40.00] 第四行歌詞丁
[00:50.00] 第五行歌詞戊
[01:00.00] 第六行歌詞己
"""

_STRUCTURED_LYRICS_RETRY = json.dumps({
    "sections": [
        {"label": "Verse", "raw_label": "[Verse]", "lines": ["第一行歌詞甲", "第二行歌詞乙"]},
        {"label": "Chorus", "raw_label": "[Chorus]", "lines": ["第三行歌詞丙", "第四行歌詞丁"]},
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
        sections, breakdown = _parse_alignment_json(response, n_lines=4)
        assert sections is not None
        assert len(sections) == 2
        assert sections[0].label == "verse"
        assert sections[0].line_start == 1
        assert sections[1].label == "chorus"
        assert sections[1].line_start == 3

    def test_invalid_json_returns_none(self):
        sections, breakdown = _parse_alignment_json("not json", n_lines=5)
        assert sections is None
        assert "JSON decode failed" in breakdown

    def test_empty_sections_returns_none(self):
        sections, breakdown = _parse_alignment_json('{"sections": []}', n_lines=5)
        assert sections is None
        assert "empty" in breakdown

    def test_missing_sections_key_returns_none(self):
        sections, breakdown = _parse_alignment_json('{"wrong_key": []}', n_lines=5)
        assert sections is None
        assert "missing" in breakdown or "not a list" in breakdown

    def test_rejects_out_of_range(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 10},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=5)
        assert sections is None
        assert "out of range" in breakdown
        assert "n_lines=5" in breakdown

    def test_rejects_overlap(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 3},
                {"label": "chorus", "line_start": 3, "line_end": 4},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=4)
        assert sections is not None
        # Overlapping section (line 3) should be rejected; only verse kept.
        assert len(sections) == 1
        assert sections[0].label == "verse"
        assert "overlaps" in breakdown

    def test_allows_gaps(self):
        """[H4] Relaxed contiguity: gaps between sections are allowed."""
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 4, "line_end": 5},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=5)
        assert sections is not None
        assert len(sections) == 2

    def test_sorts_by_line_start(self):
        """[H4] Out-of-order sections are sorted by line_start."""
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 3, "line_end": 4},
                {"label": "verse", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=4)
        assert sections is not None
        assert sections[0].line_start == 1
        assert sections[1].line_start == 3

    def test_repeated_labels_different_ranges(self):
        """[H4] Three chorus sections with different line ranges are valid."""
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 3, "line_end": 4},
                {"label": "chorus", "line_start": 5, "line_end": 6},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=6)
        assert sections is not None
        assert len(sections) == 3
        assert all(s.label == "chorus" for s in sections)

    def test_label_normalization_verse_number(self):
        """[H5] 'verse 1' is normalized to 'verse'."""
        response = json.dumps({
            "sections": [
                {"label": "verse 1", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].label == "verse"

    def test_label_normalization_pre_chorus(self):
        """[H5] 'pre-chorus' is normalized to 'prechorus'."""
        response = json.dumps({
            "sections": [
                {"label": "pre-chorus", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].label == "prechorus"

    def test_label_normalization_hook(self):
        """[H5] 'hook' is normalized to 'chorus'."""
        response = json.dumps({
            "sections": [
                {"label": "hook", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].label == "chorus"

    def test_label_normalization_refrain(self):
        """[H5] 'refrain' is normalized to 'chorus'."""
        response = json.dumps({
            "sections": [
                {"label": "refrain", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].label == "chorus"

    def test_unknown_label_rejected(self):
        """Labels not in _VALID_LABELS or normalization map are rejected."""
        response = json.dumps({
            "sections": [
                {"label": "solo", "line_start": 1, "line_end": 2},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is None
        assert "invalid label" in breakdown
        assert "solo" in breakdown

    def test_confidence_clamped(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2, "confidence": 1.5},
            ]
        })
        sections, breakdown = _parse_alignment_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].confidence == 1.0

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
        sections, breakdown = _parse_alignment_json(response, n_lines=5)
        assert sections is not None
        # After sorting: 1-2 (verse), 2-3 (bridge overlaps with verse), 4-5 (chorus)
        # Bridge overlaps with verse (line 2), so it's rejected.
        assert len(sections) == 2
        labels = [s.label for s in sections]
        assert "verse" in labels
        assert "chorus" in labels
        assert "bridge" not in labels


# ── Unit tests for _parse_alignment_json() breakdown ──────────────────────────


class TestParseAlignmentJsonBreakdown:
    def test_json_decode_failure_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json("not json", 10)
        assert sections is None
        assert "JSON decode failed" in breakdown

    def test_empty_sections_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json('{"sections": []}', 10)
        assert sections is None
        assert "empty" in breakdown

    def test_invalid_label_rejected_with_reason(self):
        resp = json.dumps({"sections": [{"label": "solo", "line_start": 1, "line_end": 3}]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert sections is None
        assert "invalid label" in breakdown
        assert "solo" in breakdown

    def test_out_of_range_rejected_with_reason(self):
        resp = json.dumps({"sections": [{"label": "verse", "line_start": 5, "line_end": 15}]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert sections is None
        assert "out of range" in breakdown
        assert "n_lines=10" in breakdown

    def test_overlap_rejected_with_reason(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 5, "confidence": 0.9},
            {"label": "chorus", "line_start": 3, "line_end": 8, "confidence": 0.9},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert len(sections) == 1  # first accepted, second rejected
        assert "overlaps" in breakdown

    def test_successful_parse_returns_breakdown(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 3, "confidence": 0.9},
            {"label": "chorus", "line_start": 4, "line_end": 8, "confidence": 0.9},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert len(sections) == 2
        assert "Parsed 2 sections" in breakdown

    def test_not_a_dict_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json("[]", 10)
        assert sections is None
        assert "not a JSON object" in breakdown

    def test_sections_missing_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json("{}", 10)
        assert sections is None
        assert "missing" in breakdown or "not a list" in breakdown

    def test_all_sections_invalid_label_returns_breakdown(self):
        resp = json.dumps({"sections": [
            {"label": "solo", "line_start": 1, "line_end": 3},
            {"label": "duet", "line_start": 4, "line_end": 8},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert sections is None
        assert "All 2 sections rejected" in breakdown

    def test_duplicate_range_rejected_with_reason(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 3, "confidence": 0.9},
            {"label": "chorus", "line_start": 1, "line_end": 3, "confidence": 0.9},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert len(sections) == 1
        # Identical ranges are caught as overlaps (overlap check runs first)
        assert "overlaps" in breakdown


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

    def test_system_prompt_contains_line_merging_hint(self):
        """[Change 1] The system prompt warns that line-count mismatch is expected."""
        messages = _build_alignment_prompt(
            _LRC_SIMPLE, _STRUCTURED_LYRICS_SIMPLE, []
        )
        system = messages[0]["content"]
        assert "Line-count mismatch is expected" in system
        assert "match by lyric CONTENT" in system

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
    async def test_chorus_repetition_validation_skipped_for_structured_lyrics_llm(self, monkeypatch):
        """Chorus repetition cross-check is SKIPPED for structured_lyrics_llm.

        The LLM aligned authoritative structured-lyrics section labels and
        intentionally selected section boundaries (including lyrical
        variations on the last line). The deterministic repetition validator
        would trim line_end to the last line whose text repeats elsewhere,
        wrongly dropping the variation line. Regression: final Exit Chorus
        persisted one line short (26-32 instead of 26-33).
        """
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        # Chorus 1 (lines 1-2) ends on a non-repeating variation line. Line 1
        # ("祢就是唯一") repeats at line 3, so the repetition validator WOULD trim
        # chorus 1's line_end from 2 to 1. With the skip, line_end=2 is preserved.
        lrc = """[00:10.00]祢就是唯一
[00:20.00]我心永遠屬祢
[00:30.00]祢就是唯一
[00:40.00]我心永遠棲息
"""
        # Structured lyrics whose chorus first line matches LRC line 1 so the
        # content-alignment validator is a no-op (no corrective retries).
        structured = json.dumps({
            "sections": [
                {"label": "Chorus", "raw_label": "[Chorus]", "lines": ["祢就是唯一", "我願永遠棲息"]},
            ],
            "preamble_lines": [],
        })
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
                structured,
                lrc,
            )

        # LLM-aligned boundaries are preserved; nothing is trimmed.
        assert len(components) > 0
        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) >= 2
        # Chorus 1 keeps its intentional variation line (line_end 2, not 1).
        first = chorus_rows[0]
        assert first.line_end == 2


# ── Unit tests for _validate_section_content_alignment() ───────────────────


class TestValidateSectionContentAlignment:
    def test_all_sections_aligned_correctly(self):
        """All line_start values match structured first lines -> no repairs/flags."""
        sections = [
            Section("verse", 1, 4, 0.9),
            Section("chorus", 5, 8, 0.95),
        ]
        repaired, diagnostics = _validate_section_content_alignment(
            sections, _STRUCTURED_LYRICS_OFF_BY_ONE, _LRC_OFF_BY_ONE
        )
        assert diagnostics == []
        assert repaired[0].line_start == 1
        assert repaired[1].line_start == 5

    def test_line_start_off_by_one_repaired(self):
        """Chorus line_start 6->5 repaired; Verse line_end trimmed to 4 by overlap."""
        sections = [
            Section("verse", 1, 5, 0.9),
            Section("chorus", 6, 9, 0.95),
        ]
        repaired, diagnostics = _validate_section_content_alignment(
            sections, _STRUCTURED_LYRICS_OFF_BY_ONE, _LRC_OFF_BY_ONE
        )
        chorus = repaired[1]
        assert chorus.line_start == 5
        assert chorus.line_end == 8
        verse = repaired[0]
        assert verse.line_end == 4
        assert any("line_end trimmed" in d for d in diagnostics)

    def test_line_start_mismatch_unrepairable_flagged(self):
        """line_start points to a line that doesn't match within ±2 -> flagged."""
        structured = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["完全不同的開頭歌詞", "第二行"]},
                {"label": "Chorus", "lines": ["心中充滿盼望 盼望使眼睛明亮", "道路雖崎嶇 袢與我同行"]},
            ]
        })
        sections = [
            Section("verse", 1, 4, 0.9),
            Section("chorus", 5, 8, 0.95),
        ]
        repaired, diagnostics = _validate_section_content_alignment(
            sections, structured, _LRC_OFF_BY_ONE
        )
        assert any("may be misaligned" in d for d in diagnostics)
        # Unrepairable section is kept (not dropped).
        assert repaired[0].line_start == 1

    def test_repair_reverted_on_new_overlap(self):
        """Repairing chorus line_start would overlap bridge -> repair reverted."""
        structured = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["lineA", "lineB"]},
                {"label": "Chorus", "lines": ["lineC", "lineD"]},
                {"label": "Bridge", "lines": ["lineE", "lineF"]},
            ]
        })
        lrc = """[00:10.00] lineA
[00:20.00] lineB
[00:30.00] lineX
[00:40.00] lineC
[00:50.00] lineE
[01:00.00] lineF
"""
        sections = [
            Section("verse", 1, 2, 0.9),
            Section("chorus", 3, 4, 0.9),
            Section("bridge", 5, 6, 0.9),
        ]
        repaired, diagnostics = _validate_section_content_alignment(
            sections, structured, lrc
        )
        chorus = repaired[1]
        assert chorus.line_start == 3
        assert chorus.line_end == 4
        assert any("repair reverted" in d for d in diagnostics)

    def test_simplified_traditional_mismatch_handled(self):
        """Structured traditional vs LRC simplified -> zhconv normalizes, no flag."""
        structured = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["讚美主", "哈利路亞"]},
            ]
        })
        lrc = """[00:10.00] 赞美主
[00:20.00] 哈利路亚
"""
        sections = [Section("verse", 1, 2, 0.9)]
        _repaired, diagnostics = _validate_section_content_alignment(
            sections, structured, lrc
        )
        assert diagnostics == []

    def test_fuzzy_match_minor_wording_variation(self):
        """Space/whitespace variation handled by normalization + fuzzy match."""
        structured = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["主 袢使卑微轉為尊貴", "使傷心流淚轉為笑顏"]},
            ]
        })
        lrc = """[00:10.05] 主袢使卑微 轉為尊貴
[00:15.15] 使傷心流淚 轉為笑顏
"""
        sections = [Section("verse", 1, 2, 0.9)]
        _repaired, diagnostics = _validate_section_content_alignment(
            sections, structured, lrc
        )
        assert diagnostics == []

    def test_structured_section_count_mismatch(self):
        """Structured has 4 sections, LLM returned 5 -> matches by fuzzy, no crash."""
        structured = json.dumps({
            "sections": [
                {"label": "Verse", "lines": ["第一行歌詞甲", "第二行歌詞乙"]},
                {"label": "Chorus", "lines": ["第三行歌詞丙", "第四行歌詞丁"]},
                {"label": "Verse", "lines": ["第五行歌詞戊", "第六行歌詞己"]},
                {"label": "Chorus", "lines": ["第一行歌詞甲", "第二行歌詞乙"]},
            ]
        })
        sections = [
            Section("verse", 1, 2, 0.9),
            Section("chorus", 3, 4, 0.9),
            Section("verse", 5, 6, 0.9),
            Section("chorus", 1, 2, 0.9),
            Section("chorus", 3, 4, 0.9),
        ]
        repaired, _diagnostics = _validate_section_content_alignment(
            sections, structured, _LRC_RETRY
        )
        assert len(repaired) == 5


# ── Fake OpenAI client for retry-loop tests ────────────────────────────────


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 10
    total_tokens = 20


class _FakeResponse:
    def __init__(self, content):
        self.model = "fake-model"
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()
        self.id = "fake-id"


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.responses.pop(0))


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


# ── Unit tests for align_structured_lyrics() retry loop ────────────────────


class TestAlignStructuredLyricsRetry:
    def _patch_llm(self, responses):
        from unittest.mock import patch as _patch

        fake_client = _FakeClient(responses)
        return fake_client, _patch(
            "sow_analysis.workers.structured_lyrics_aligner._build_client",
            return_value=fake_client,
        ), _patch(
            "sow_analysis.workers.structured_lyrics_aligner.call_llm_with_retry",
            new_callable=AsyncMock,
            side_effect=lambda fn, **kwargs: fn(),
        )

    @pytest.mark.asyncio
    async def test_retry_on_validation_flag(self, monkeypatch):
        """Wrong alignment on attempt 1 -> corrective message -> attempt 2 used."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        wrong = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
            {"label": "chorus", "line_start": 6, "line_end": 6, "confidence": 0.9},
        ]})
        correct = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
            {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
        ]})
        fake_client, p_client, p_retry = self._patch_llm([wrong, correct])

        with p_client, p_retry:
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_RETRY, _LRC_RETRY
            )

        assert len(fake_client.chat.completions.calls) == 2
        second_messages = fake_client.chat.completions.calls[1]["messages"]
        assert any(
            m["role"] == "user" and "Your previous alignment had issues" in m["content"]
            for m in second_messages
        )
        chorus = next(c for c in components if c.component_type == "chorus")
        assert "第三行歌詞丙" in chorus.lyrics_excerpt

    @pytest.mark.asyncio
    async def test_no_retry_when_all_repaired(self, monkeypatch):
        """Repairable off-by-one -> validator repairs, only 1 LLM call."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        repairable = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 5, "confidence": 0.9},
            {"label": "chorus", "line_start": 6, "line_end": 9, "confidence": 0.95},
        ]})
        fake_client, p_client, p_retry = self._patch_llm([repairable])

        with p_client, p_retry:
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_OFF_BY_ONE, _LRC_OFF_BY_ONE
            )

        assert len(fake_client.chat.completions.calls) == 1
        chorus = next(c for c in components if c.component_type == "chorus")
        assert "心中充滿盼望 盼望使眼睛明亮" in chorus.lyrics_excerpt

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted_uses_best_effort(self, monkeypatch, caplog):
        """Always-wrong alignment -> 2 calls, best-effort repaired result, warnings."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        wrong = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
            {"label": "chorus", "line_start": 6, "line_end": 6, "confidence": 0.9},
        ]})
        fake_client, p_client, p_retry = self._patch_llm([wrong, wrong])

        with p_client, p_retry, caplog.at_level("WARNING"):
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_RETRY, _LRC_RETRY
            )

        assert len(fake_client.chat.completions.calls) == 2
        assert any("may be misaligned" in r.message for r in caplog.records)
        chorus = next(c for c in components if c.component_type == "chorus")
        assert "第六行歌詞己" in chorus.lyrics_excerpt

    @pytest.mark.asyncio
    async def test_retry_disabled_when_max_attempts_1(self, monkeypatch):
        """max_attempts=1 -> 1 call, no retry, validator still repairs."""
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")
        monkeypatch.setattr(settings, "SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS", 1)

        repairable = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 5, "confidence": 0.9},
            {"label": "chorus", "line_start": 6, "line_end": 9, "confidence": 0.95},
        ]})
        fake_client, p_client, p_retry = self._patch_llm([repairable])

        with p_client, p_retry:
            components = await align_structured_lyrics(
                _STRUCTURED_LYRICS_OFF_BY_ONE, _LRC_OFF_BY_ONE
            )

        assert len(fake_client.chat.completions.calls) == 1
        chorus = next(c for c in components if c.component_type == "chorus")
        assert "心中充滿盼望 盼望使眼睛明亮" in chorus.lyrics_excerpt


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
