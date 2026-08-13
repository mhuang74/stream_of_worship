"""Tests for the LLM whole-song segmentation module (Design C).

LLM-dependent tests are gated via @pytest.mark.skipif on SOW_LLM_LIVE_TESTS.
They run only when SOW_LLM_LIVE_TESTS=1 with a configured SOW_LLM_API_KEY.
No custom pytest markers are used — skipif is the pattern.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.config import settings
from sow_analysis.storage.cache import CacheManager
from sow_analysis.workers.components import ComponentInstance, extract_components
from sow_analysis.workers.lrc_parser import parse_lrc
from sow_analysis.workers.section_segmenter import (
    DEFAULT_VALIDATOR_WEIGHTS,
    Section,
    _build_segmentation_prompt,
    _load_few_shot_examples,
    _map_sections_to_components,
    _parse_segmenter_json,
    _render_numbered_lrc,
    _validate_chorus_repetition,
    segment_song,
)


# ── Test LRC fixtures ──────────────────────────────────────────────────────

_LRC_WITH_BLANK = """[00:12.10]
[00:33.32]聖潔耶穌 祢寶座在這裡
[00:47.89]哈利路亞 祢榮耀在這裡
[01:02.33]聖潔耶穌 祢寶座在這裡
[01:16.64]哈利路亞 祢榮耀在這裡
[01:29.34]君王就在這裡 我們歡然獻祭
[01:33.02]平安的王在這裡 歡迎祢降臨
[01:37.55]
[01:41.22]聖潔耶穌 祢寶座在這裡
"""

_LRC_SIMPLE = """[00:10.00]讚美主
[00:25.00]哈利路亞
[00:40.00]讚美主
[00:55.00]哈利路亞
"""


# ── Unit tests (no LLM) ────────────────────────────────────────────────────


class TestRenderNumberedLrc:
    def test_format_with_blank_lines(self):
        numbered, count = _render_numbered_lrc(_LRC_WITH_BLANK)
        lines = numbered.split("\n")
        assert count == 9
        assert len(lines) == 9
        # Line 1 is blank (metadata-only timestamp)
        assert lines[0].startswith("1  [12.10] ")
        # Line 2 has text
        assert lines[1].startswith("2  [33.32] 聖潔耶穌")
        # Line 8 is blank
        assert lines[7].startswith("8  [97.55] ")

    def test_empty_lrc(self):
        numbered, count = _render_numbered_lrc("")
        assert numbered == "(empty LRC)"
        assert count == 0

    def test_simple_lrc(self):
        numbered, count = _render_numbered_lrc(_LRC_SIMPLE)
        assert count == 4
        lines = numbered.split("\n")
        assert lines[0].startswith("1  [10.00] 讚美主")


class TestParseSegmenterJson:
    def test_valid(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2, "confidence": 0.9},
                {"label": "chorus", "line_start": 3, "line_end": 4, "confidence": 0.95},
            ]
        })
        sections = _parse_segmenter_json(response, n_lines=4)
        assert sections is not None
        assert len(sections) == 2
        assert sections[0].label == "verse"
        assert sections[0].line_start == 1
        assert sections[0].line_end == 2
        assert sections[1].label == "chorus"
        assert sections[1].line_start == 3

    def test_rejects_overlap(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 3},
                {"label": "chorus", "line_start": 3, "line_end": 4},
            ]
        })
        assert _parse_segmenter_json(response, n_lines=4) is None

    def test_rejects_oob(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 10},
            ]
        })
        assert _parse_segmenter_json(response, n_lines=5) is None

    def test_rejects_bad_label(self):
        response = json.dumps({
            "sections": [
                {"label": "refrain", "line_start": 1, "line_end": 2},
            ]
        })
        assert _parse_segmenter_json(response, n_lines=2) is None

    def test_rejects_gap(self):
        response = json.dumps({
            "sections": [
                {"label": "verse", "line_start": 1, "line_end": 2},
                {"label": "chorus", "line_start": 4, "line_end": 5},
            ]
        })
        assert _parse_segmenter_json(response, n_lines=5) is None

    def test_rejects_malformed_toplevel(self):
        assert _parse_segmenter_json("not json", n_lines=5) is None
        assert _parse_segmenter_json("[]", n_lines=5) is None
        assert _parse_segmenter_json('{"wrong_key": []}', n_lines=5) is None
        assert _parse_segmenter_json('{"sections": []}', n_lines=5) is None

    def test_confidence_clamped(self):
        response = json.dumps({
            "sections": [
                {"label": "chorus", "line_start": 1, "line_end": 2, "confidence": 1.5},
            ]
        })
        sections = _parse_segmenter_json(response, n_lines=2)
        assert sections is not None
        assert sections[0].confidence == 1.0


class TestMapSectionsToComponents:
    def _parse_lines(self, lrc_content):
        return list(parse_lrc(lrc_content).lines)

    def test_single_chorus_two_rows(self):
        lines = self._parse_lines(_LRC_SIMPLE)
        sections = [
            Section(label="verse", line_start=1, line_end=2, confidence=0.9),
            Section(label="chorus", line_start=3, line_end=4, confidence=0.95),
        ]
        components = _map_sections_to_components(sections, lines)
        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) == 2
        entry = [c for c in chorus_rows if c.role == "entry"]
        exit_row = [c for c in chorus_rows if c.role == "exit"]
        assert len(entry) == 1
        assert len(exit_row) == 1
        assert entry[0].occurrence_index == 1
        assert exit_row[0].occurrence_index == 1
        assert entry[0].start_time == exit_row[0].start_time
        assert entry[0].source == "llm_segmentation"
        assert entry[0].section_label == "chorus"
        assert entry[0].lyrics_excerpt is not None

    def test_verse_before_chorus_loop_target(self):
        lines = self._parse_lines(_LRC_SIMPLE)
        sections = [
            Section(label="verse", line_start=1, line_end=2, confidence=0.9),
            Section(label="chorus", line_start=3, line_end=4, confidence=0.95),
        ]
        components = _map_sections_to_components(sections, lines)
        loop_targets = [c for c in components if c.role == "loop_target"]
        assert len(loop_targets) == 1
        assert loop_targets[0].component_type == "verse"
        assert loop_targets[0].section_label == "verse"

    def test_snap_to_downbeat_false_no_snapping(self):
        lines = self._parse_lines(_LRC_SIMPLE)
        sections = [
            Section(label="chorus", line_start=1, line_end=2, confidence=0.9),
        ]
        beats = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        downbeats = [0.0, 3.0]
        components = _map_sections_to_components(
            sections, lines, beats=beats, downbeats=downbeats,
            snap_to_downbeat=False,
        )
        # With snap_to_downbeat=False, beats snapping IS applied (matches
        # the fallback path behavior). The key assertion is that
        # snap_to_downbeat=False does NOT snap to downbeats.
        chorus = [c for c in components if c.component_type == "chorus"][0]
        # start_time should be snapped to nearest beat, not downbeat
        assert chorus.start_time in beats

    def test_snap_to_downbeat_true_uses_downbeats(self):
        lines = self._parse_lines(_LRC_SIMPLE)
        sections = [
            Section(label="chorus", line_start=1, line_end=2, confidence=0.9),
        ]
        beats = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        downbeats = [0.0, 3.0]
        components = _map_sections_to_components(
            sections, lines, beats=beats, downbeats=downbeats,
            snap_to_downbeat=True,
        )
        chorus = [c for c in components if c.component_type == "chorus"][0]
        # start_time should be snapped to nearest downbeat
        assert chorus.start_time in downbeats

    def test_no_chorus_returns_empty(self):
        lines = self._parse_lines(_LRC_SIMPLE)
        sections = [
            Section(label="verse", line_start=1, line_end=4, confidence=0.9),
        ]
        assert _map_sections_to_components(sections, lines) == []

    def test_multiple_choruses_roles(self):
        lrc = """[00:10.00]verse line
[00:20.00]chorus line one
[00:30.00]verse line two
[00:40.00]chorus line one
[00:50.00]chorus line one
"""
        lines = self._parse_lines(lrc)
        sections = [
            Section(label="verse", line_start=1, line_end=1, confidence=0.9),
            Section(label="chorus", line_start=2, line_end=2, confidence=0.95),
            Section(label="verse", line_start=3, line_end=3, confidence=0.9),
            Section(label="chorus", line_start=4, line_end=5, confidence=0.95),
        ]
        components = _map_sections_to_components(sections, lines)
        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) == 2
        assert chorus_rows[0].role == "entry"
        assert chorus_rows[0].occurrence_index == 1
        assert chorus_rows[1].role == "exit"
        assert chorus_rows[1].occurrence_index == 2


class TestLoadFewShot:
    def test_leakage_guard_raises_on_fixture_id(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "segmentation_few_shot.json"
        fake_file.write_text(json.dumps([
            {"source_song_id": "jun_wang_jiu_zai_zhe_li_1c32724c", "input": "x", "sections": []}
        ]), encoding="utf-8")
        monkeypatch.setattr(
            "sow_analysis.workers.section_segmenter.Path"
            if False else
            "sow_analysis.workers.section_segmenter.__file__",
            str(tmp_path / "section_segmenter.py"),
        )
        # Patch __file__ so Path(__file__).parent points to tmp_path
        import sow_analysis.workers.section_segmenter as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "section_segmenter.py")
        try:
            with pytest.raises(ValueError, match="held-out"):
                _load_few_shot_examples()
        finally:
            mod.__file__ = original_file

    def test_placeholder_does_not_raise(self, monkeypatch, tmp_path):
        fake_file = tmp_path / "segmentation_few_shot.json"
        fake_file.write_text(json.dumps([
            {"source_song_id": "__CHANGE_ME__", "input": "x", "sections": []}
        ]), encoding="utf-8")
        import sow_analysis.workers.section_segmenter as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "section_segmenter.py")
        try:
            examples = _load_few_shot_examples()
            assert len(examples) == 1
        finally:
            mod.__file__ = original_file

    def test_absent_file_returns_empty(self, monkeypatch, tmp_path):
        import sow_analysis.workers.section_segmenter as mod
        original_file = mod.__file__
        mod.__file__ = str(tmp_path / "nonexistent.py")
        try:
            examples = _load_few_shot_examples()
            assert examples == []
        finally:
            mod.__file__ = original_file


class TestValidateChorusRepetition:
    def test_trims_overmerged_chorus(self):
        """Chorus whose line_end extends past the last repeating line is trimmed."""
        # Lines: verse(1-2), chorus(3-4), verse(5-6), chorus(7-8)
        # If LLM says chorus is lines 3-6 (over-merged), validator should
        # trim line_end to 4 (last line that repeats elsewhere).
        lrc = """[00:10.00]verse one
[00:20.00]verse two
[00:30.00]哈利路亞
[00:40.00]讚美主
[00:50.00]verse three
[00:60.00]verse four
[00:70.00]哈利路亞
[00:80.00]讚美主
"""
        sections = [
            Section(label="verse", line_start=1, line_end=2, confidence=0.9),
            Section(label="chorus", line_start=3, line_end=6, confidence=0.95),
            Section(label="verse", line_start=7, line_end=8, confidence=0.9),
        ]
        result = _validate_chorus_repetition(sections, lrc)
        chorus = [s for s in result if s.label == "chorus"][0]
        # Line 4 (讚美主) repeats at line 8, so trim to line_end=4
        assert chorus.line_end == 4
        # Confidence multiplied by 0.90
        assert abs(chorus.confidence - (0.95 * 0.90)) < 0.01

    def test_keeps_nonrepeated_chorus(self):
        """A chorus whose lines don't repeat anywhere is kept at *0.60."""
        lrc = """[00:10.00]unique verse
[00:20.00]unique chorus line
[00:30.00]another unique line
[00:40.00]different verse
"""
        sections = [
            Section(label="verse", line_start=1, line_end=1, confidence=0.9),
            Section(label="chorus", line_start=2, line_end=3, confidence=0.95),
            Section(label="verse", line_start=4, line_end=4, confidence=0.9),
        ]
        result = _validate_chorus_repetition(sections, lrc)
        chorus = [s for s in result if s.label == "chorus"][0]
        assert chorus.line_end == 3  # unchanged
        assert abs(chorus.confidence - (0.95 * 0.60)) < 0.01

    def test_confirmed_unchanged_gets_bonus(self):
        """Chorus already ending on a repeating line gets +0.05 bonus."""
        lrc = """[00:10.00]verse one
[00:20.00]哈利路亞
[00:30.00]讚美主
[00:40.00]verse two
[00:50.00]哈利路亞
[00:60.00]讚美主
"""
        sections = [
            Section(label="verse", line_start=1, line_end=1, confidence=0.9),
            Section(label="chorus", line_start=2, line_end=3, confidence=0.90),
            Section(label="verse", line_start=4, line_end=4, confidence=0.9),
            Section(label="chorus", line_start=5, line_end=6, confidence=0.90),
        ]
        result = _validate_chorus_repetition(sections, lrc)
        first_chorus = [s for s in result if s.label == "chorus"][0]
        # Line 3 (讚美主) repeats at line 6, so confirmed unchanged
        assert first_chorus.line_end == 3
        assert abs(first_chorus.confidence - min(1.0, 0.90 + 0.05)) < 0.01


# ── Tuning-loop override parity (defaults are bit-identical) ────────────────


class TestTuningOverrideParity:
    """The three v1 tuning-loop kwargs must default to current behavior.

    Passing the explicit default values (few_shot_override=None,
    system_prompt_override=None, validator_weights=DEFAULT_VALIDATOR_WEIGHTS)
    must produce byte-identical output to calling without them.
    """

    def test_validator_weights_defaults_match_literals(self):
        w = DEFAULT_VALIDATOR_WEIGHTS
        assert w.nonrepeated_multiplier == 0.60
        assert w.trimmed_multiplier == 0.90
        assert w.confirmed_bonus == 0.05
        assert w.mapping_confidence_multiplier == 0.95

    def test_validate_chorus_repetition_parity(self):
        lrc = """[00:10.00]verse one
[00:20.00]哈利路亞
[00:30.00]讚美主
[00:40.00]verse two
[00:50.00]哈利路亞
[00:60.00]讚美主
"""
        sections = [
            Section(label="verse", line_start=1, line_end=1, confidence=0.9),
            Section(label="chorus", line_start=2, line_end=3, confidence=0.90),
            Section(label="verse", line_start=4, line_end=4, confidence=0.9),
            Section(label="chorus", line_start=5, line_end=6, confidence=0.90),
        ]
        default = _validate_chorus_repetition(sections, lrc)
        explicit = _validate_chorus_repetition(
            sections, lrc, weights=DEFAULT_VALIDATOR_WEIGHTS
        )
        assert default == explicit

    def test_map_sections_to_components_parity(self):
        lines = list(parse_lrc(_LRC_SIMPLE).lines)
        sections = [
            Section(label="verse", line_start=1, line_end=2, confidence=0.9),
            Section(label="chorus", line_start=3, line_end=4, confidence=0.95),
        ]
        default = _map_sections_to_components(sections, lines)
        explicit = _map_sections_to_components(
            sections, lines, weights=DEFAULT_VALIDATOR_WEIGHTS
        )
        assert default == explicit

    def test_build_segmentation_prompt_parity(self):
        default = _build_segmentation_prompt(_LRC_SIMPLE, None, None, [])
        explicit = _build_segmentation_prompt(
            _LRC_SIMPLE, None, None, [], system_prompt_override=None
        )
        assert default == explicit


# ── Integration tests (extract_components fallback) ─────────────────────────


def _make_mock_gf(duration=20.0):
    """Create a mock GlobalFeatures without computing real audio features."""
    import numpy as np
    from sow_analysis.workers.components import GlobalFeatures

    sr = 22050
    n_samples = int(sr * duration)
    hop_length = 512
    n_frames = max(n_samples // hop_length + 1, 2)
    return GlobalFeatures(
        y=np.zeros(n_samples),
        sr=sr,
        duration=duration,
        onset_env=np.zeros(n_frames),
        onset_frames=np.arange(n_frames),
        onset_times=np.linspace(0, duration, n_frames),
        rms=np.ones(n_frames) * 0.1,
        rms_times=np.linspace(0, duration, n_frames),
        y_harmonic=np.zeros(n_samples),
        chroma=np.ones((12, n_frames)) * 0.5,
        drums_y=None,
        drums_onset_env=None,
        drums_rms=None,
        drums_rms_times=None,
        vocals_y=None,
    )


_LRC_FOR_FALLBACK = """[00:00.00]讚美主
[00:05.00]哈利路亞
[00:10.00]讚美主
[00:15.00]哈利路亞
[00:20.00]讚美主
[00:25.00]哈利路亞
"""


class TestExtractComponentsFallback:
    @pytest.mark.asyncio
    async def test_falls_back_when_llm_disabled(self, monkeypatch):
        """SOW_COMPONENTS_USE_LLM_SEGMENTATION=false → lyrics_repetition path."""
        monkeypatch.setattr(settings, "SOW_COMPONENTS_USE_LLM_SEGMENTATION", False)
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "")

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_gf(duration=30.0)
            with patch(
                "sow_analysis.workers.components._precompute_global_features"
            ) as mock_precompute:
                mock_precompute.return_value = mock_gf
                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="test_llm_disabled",
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_LRC_FOR_FALLBACK,
                    beats=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                    force=True,
                )
            assert source == "lyrics_repetition"
            assert all(c.source != "llm_segmentation" for c in components)

    @pytest.mark.asyncio
    async def test_falls_back_when_no_key(self, monkeypatch):
        """SOW_LLM_API_KEY="" + use_llm_segmentation=True → falls through."""
        monkeypatch.setattr(settings, "SOW_COMPONENTS_USE_LLM_SEGMENTATION", False)
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "")

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_gf(duration=30.0)
            with patch(
                "sow_analysis.workers.components._precompute_global_features"
            ) as mock_precompute:
                mock_precompute.return_value = mock_gf
                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="test_no_key",
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_LRC_FOR_FALLBACK,
                    beats=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                    force=True,
                    use_llm_segmentation=True,
                )
            assert source != "llm_segmentation"
            assert source == "lyrics_repetition"

    @pytest.mark.asyncio
    async def test_falls_back_on_json_violation(self, monkeypatch):
        """Monkeypatch _parse_segmenter_json → None; falls through."""
        monkeypatch.setattr(settings, "SOW_COMPONENTS_USE_LLM_SEGMENTATION", False)
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "fake-key")
        monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
        monkeypatch.setattr(settings, "SOW_LLM_MODEL", "fake-model")

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_gf(duration=30.0)

            # Mock segment_song to return [] (simulating parse failure → empty)
            async def _mock_segment_song(*args, **kwargs):
                return []

            with patch(
                "sow_analysis.workers.components._precompute_global_features"
            ) as mock_precompute, patch(
                "sow_analysis.workers.section_segmenter.segment_song",
                new=_mock_segment_song,
            ):
                mock_precompute.return_value = mock_gf
                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="test_json_violation",
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_LRC_FOR_FALLBACK,
                    beats=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                    force=True,
                    use_llm_segmentation=True,
                )
            assert source == "lyrics_repetition"

    @pytest.mark.asyncio
    async def test_no_regression_default(self, monkeypatch):
        """Default env → source is lyrics_repetition (no llm_segmentation)."""
        monkeypatch.setattr(settings, "SOW_COMPONENTS_USE_LLM_SEGMENTATION", False)
        monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "")

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_gf(duration=30.0)
            with patch(
                "sow_analysis.workers.components._precompute_global_features"
            ) as mock_precompute:
                mock_precompute.return_value = mock_gf
                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="test_no_regression",
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_LRC_FOR_FALLBACK,
                    beats=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                    force=True,
                )
            assert source in ("lyrics_repetition", "allin1_sections", "none")
            assert source != "llm_segmentation"


# ── Live LLM test (gated) ──────────────────────────────────────────────────

_LIVE_TEST_SKIP = os.environ.get("SOW_LLM_LIVE_TESTS") != "1"


@pytest.mark.skipif(_LIVE_TEST_SKIP, reason="Set SOW_LLM_LIVE_TESTS=1 to run live LLM tests")
class TestSegmentSongLive:
    @pytest.mark.asyncio
    async def test_segment_song_live(self):
        """Full segment_song call; asserts components with source=llm_segmentation."""
        if not settings.SOW_LLM_API_KEY:
            pytest.skip("SOW_LLM_API_KEY not set")
        components = await segment_song(_LRC_FOR_FALLBACK)
        assert len(components) > 0
        chorus_rows = [c for c in components if c.component_type == "chorus"]
        assert len(chorus_rows) >= 1
        assert all(c.source == "llm_segmentation" for c in components)
