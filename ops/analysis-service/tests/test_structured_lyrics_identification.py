"""Tests for structured lyrics-based component identification (v8).

Covers identify_from_structured_lyrics() and the global _is_essential()
bridge expansion across components.py, classifier.py, and admin-cli
analysis.py.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.storage.cache import CacheManager
from sow_analysis.workers.components import (
    ComponentInstance,
    _is_essential,
    extract_components,
    identify_from_lyrics_repetition,
    identify_from_structured_lyrics,
)
from sow_analysis.workers.classifier import _is_essential as classifier_is_essential


# ---------------------------------------------------------------------------
# Test data: the worked example from the spec (cong_zao_chen_dao_ye_wan).
# ---------------------------------------------------------------------------

_SAMPLE_STRUCTURED_LYRICS = {
    "sections": [
        {
            "label": "verse 1",
            "raw_label": "Verse 1",
            "lines": [
                "早晨我睜開眼睛",
                "渴望聆聽祢聲音",
                "心中思想祢的好",
                "更多與祢來親近",
            ],
        },
        {
            "label": "verse 2",
            "raw_label": "Verse 2",
            "lines": [
                "夜晚我仍要歌唱",
                "向祢闡明我心意",
                "敬拜化成一首歌",
                "單單要唱給祢聽",
            ],
        },
        {
            "label": "chorus",
            "raw_label": "Chorus",
            "lines": [
                "從早晨到夜晚 從曠野到高山",
                "親愛主 我要稱頌祢美名",
                "從早晨到夜晚 祢愛永不止息",
                "親愛主 一生緊緊跟隨祢",
            ],
        },
        {
            "label": "bridge",
            "raw_label": "Bridge",
            "lines": [
                "我的主 我愛祢",
                "我要誇祢的愛無止盡",
                "我的主 我愛祢",
                "我要誇祢的愛無止盡",
            ],
        },
    ],
    "preamble_lines": [],
}


def _build_lrc_for_sample() -> str:
    """Build a 36-line LRC matching the sample structured lyrics.

    Layout (matching the spec's worked example):
      Lines 1-4:   Verse 1
      Lines 5-8:   Verse 2
      Lines 9-12:  Chorus (occ 1)
      Lines 13-16: Chorus (occ 2)
      Lines 17-20: Bridge (occ 1)
      Lines 21-24: Bridge (occ 2)
      Lines 25-28: Chorus (occ 3)
      Lines 29-32: Chorus (occ 4)
      Lines 33-36: Chorus (occ 5)
    """
    timestamps = [
        14.14, 20.00, 26.00, 53.13,       # V1
        53.13, 60.00, 70.00, 83.17,       # V2
        83.17, 100.00, 115.00, 135.45,    # C1
        135.45, 145.00, 155.00, 164.01,   # C2
        164.01, 175.00, 185.00, 192.07,  # B1
        192.07, 210.00, 230.00, 247.68,  # B2
        247.68, 255.00, 265.00, 275.07,  # C3
        275.07, 285.00, 295.00, 302.90,  # C4
        302.90, 310.00, 320.00, 328.00,  # C5
    ]
    texts = [
        "早晨我睜開眼睛", "渴望聆聽祢聲音", "心中思想祢的好", "更多與祢來親近",
        "夜晚我仍要歌唱", "向祢闡明我心意", "敬拜化成一首歌", "單單要唱給祢聽",
        "從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名",
        "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢",
        "從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名",
        "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢",
        "我的主 我愛祢", "我要誇祢的愛無止盡",
        "我的主 我愛祢", "我要誇祢的愛無止盡",
        "我的主 我愛祢", "我要誇祢的愛無止盡",
        "我的主 我愛祢", "我要誇祢的愛無止盡",
        "從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名",
        "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢",
        "從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名",
        "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢",
        "從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名",
        "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢",
    ]

    def _fmt(t: float) -> str:
        m = int(t // 60)
        s = t - m * 60
        return f"[{m:02d}:{s:05.2f}]"

    lines = []
    for t, text in zip(timestamps, texts):
        lines.append(f"{_fmt(t)}{text}")
    return "\n".join(lines)


def _sl_json() -> str:
    return json.dumps(_SAMPLE_STRUCTURED_LYRICS)


# ---------------------------------------------------------------------------
# Test 1: Basic matching — 9 components with correct types/occurrences/roles.
# ---------------------------------------------------------------------------


class TestBasicMatching:
    def test_nine_components_identified(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)

        # 4 verse + 5 chorus + 2 bridge = 11, but single-chorus rule doesn't
        # apply here (5 choruses). So: 2 verse + 5 chorus + 2 bridge = 9.
        # Wait: Verse 1 (1 occ) + Verse 2 (1 occ) = 2 verses.
        # Chorus (5 occ) + single-chorus rule doesn't apply (5 > 1).
        # Bridge (2 occ).
        # Total = 2 + 5 + 2 = 9.
        assert len(components) == 9

    def test_component_types(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        types = [c.component_type for c in components]
        assert types.count("verse") == 2
        assert types.count("chorus") == 5
        assert types.count("bridge") == 2

    def test_chorus_roles(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        choruses = [c for c in components if c.component_type == "chorus"]
        # Sort by start_time for role verification.
        choruses.sort(key=lambda c: c.start_time)
        assert choruses[0].role == "entry"
        assert choruses[-1].role == "exit"
        for c in choruses[1:-1]:
            assert c.role == "none"

    def test_verse_loop_target(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        verses = [c for c in components if c.component_type == "verse"]
        verses.sort(key=lambda c: c.start_time)
        # Verse 2 (last verse before first chorus) -> loop_target.
        assert verses[-1].role == "loop_target"
        # Verse 1 -> none.
        assert verses[0].role == "none"

    def test_source_field(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        for c in components:
            assert c.source == "structured_lyrics"

    def test_confidence_full_match(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        for c in components:
            assert c.confidence == 0.95

    def test_section_label_populated(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        for c in components:
            assert c.section_label is not None

    def test_lyrics_excerpt_populated(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(_sl_json(), lrc)
        for c in components:
            assert c.lyrics_excerpt is not None
            assert len(c.lyrics_excerpt) > 0


# ---------------------------------------------------------------------------
# Test 2: Single chorus -> two rows (entry + exit).
# ---------------------------------------------------------------------------


class TestSingleChorusTwoRows:
    def test_single_chorus_produces_entry_and_exit(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["哈利路亞", "讚美主"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]哈利路亞",
            "[00:15.00]讚美主",
            "[00:20.00]some other line",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        choruses = [c for c in components if c.component_type == "chorus"]
        assert len(choruses) == 2
        roles = {c.role for c in choruses}
        assert roles == {"entry", "exit"}


# ---------------------------------------------------------------------------
# Test 3: Multiple chorus occurrences — entry/exit on first/last.
# ---------------------------------------------------------------------------


class TestMultipleChorusOccurrences:
    def test_four_chorus_occurrences(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["哈利路亞", "讚美主"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]哈利路亞",
            "[00:15.00]讚美主",
            "[00:20.00]verse line one",
            "[00:25.00]verse line two",
            "[00:30.00]哈利路亞",
            "[00:35.00]讚美主",
            "[00:40.00]bridge line one",
            "[00:45.00]bridge line two",
            "[00:50.00]哈利路亞",
            "[00:55.00]讚美主",
            "[01:00.00]outro line one",
            "[01:05.00]outro line two",
            "[01:10.00]哈利路亞",
            "[01:15.00]讚美主",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        choruses = [c for c in components if c.component_type == "chorus"]
        assert len(choruses) == 4
        choruses.sort(key=lambda c: c.start_time)
        assert choruses[0].role == "entry"
        assert choruses[-1].role == "exit"
        for c in choruses[1:-1]:
            assert c.role == "none"


# ---------------------------------------------------------------------------
# Test 4: No match — structured lyrics text not in LRC.
# ---------------------------------------------------------------------------


class TestNoMatch:
    def test_no_match_returns_empty(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["completely different text", "not in lrc at all"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]some random line",
            "[00:15.00]another random line",
            "[00:20.00]yet another line",
            "[00:25.00]and one more",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        assert components == []


# ---------------------------------------------------------------------------
# Test 5: Traditional/simplified Chinese mismatch — zhconv handles it.
# ---------------------------------------------------------------------------


class TestTraditionalSimplifiedMismatch:
    def test_simplified_lrc_matches_traditional_structured(self):
        # Structured lyrics in traditional, LRC in simplified.
        # "早晨我睁开眼睛" (simplified) vs "早晨我睜開眼睛" (traditional).
        sl = json.dumps({
            "sections": [
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["早晨我睜開眼睛", "渴望聆聽祢聲音"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]早晨我睁开眼睛",
            "[00:15.00]渴望聆听祢声音",
            "[00:20.00]other line",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        choruses = [c for c in components if c.component_type == "chorus"]
        assert len(choruses) >= 1


# ---------------------------------------------------------------------------
# Test 6: Partial match — 4-line section, only 3 consecutive LRC lines match.
# ---------------------------------------------------------------------------


class TestPartialMatch:
    def test_partial_match_accepted_at_080_confidence(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": [
                        "line one exact",
                        "line two exact",
                        "line three exact",
                        "completely different fourth line",
                    ],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]line one exact",
            "[00:15.00]line two exact",
            "[00:20.00]line three exact",
            "[00:25.00]some other text here",
            "[00:30.00]trailing line",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        choruses = [c for c in components if c.component_type == "chorus"]
        assert len(choruses) >= 1
        # 3/4 = 75% -> partial match at 0.80 confidence.
        assert choruses[0].confidence == 0.80


# ---------------------------------------------------------------------------
# Test 7: Missing structured_lyrics.
# ---------------------------------------------------------------------------


class TestMissingStructuredLyrics:
    def test_none_structured_lyrics_returns_empty(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(None, lrc)
        assert components == []

    def test_empty_string_structured_lyrics_returns_empty(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics("", lrc)
        assert components == []

    def test_invalid_json_returns_empty(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics("not valid json{", lrc)
        assert components == []


# ---------------------------------------------------------------------------
# Test 8: Missing LRC.
# ---------------------------------------------------------------------------


class TestMissingLrc:
    def test_none_lrc_returns_empty(self):
        components = identify_from_structured_lyrics(_sl_json(), None)
        assert components == []

    def test_empty_lrc_returns_empty(self):
        components = identify_from_structured_lyrics(_sl_json(), "")
        assert components == []


# ---------------------------------------------------------------------------
# Test 9: Verse loop_target assignment.
# ---------------------------------------------------------------------------


class TestVerseLoopTarget:
    def test_last_verse_before_first_chorus_is_loop_target(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "verse 1",
                    "raw_label": "Verse 1",
                    "lines": ["verse one line a", "verse one line b"],
                },
                {
                    "label": "verse 2",
                    "raw_label": "Verse 2",
                    "lines": ["verse two line a", "verse two line b"],
                },
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["chorus line a", "chorus line b"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]verse one line a",
            "[00:15.00]verse one line b",
            "[00:20.00]verse two line a",
            "[00:25.00]verse two line b",
            "[00:30.00]chorus line a",
            "[00:35.00]chorus line b",
            "[00:40.00]outro line",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        verses = [c for c in components if c.component_type == "verse"]
        verses.sort(key=lambda c: c.start_time)
        # Verse 2 (last before chorus) -> loop_target.
        assert verses[-1].role == "loop_target"
        assert verses[0].role == "none"


# ---------------------------------------------------------------------------
# Test 10: Empty lines in section — section skipped.
# ---------------------------------------------------------------------------


class TestEmptyLinesInSection:
    def test_section_with_empty_lines_skipped(self):
        sl = json.dumps({
            "sections": [
                {
                    "label": "intro",
                    "raw_label": "Intro",
                    "lines": [],
                },
                {
                    "label": "chorus",
                    "raw_label": "Chorus",
                    "lines": ["chorus line a", "chorus line b"],
                },
            ],
        })
        lrc = "\n".join([
            "[00:10.00]chorus line a",
            "[00:15.00]chorus line b",
            "[00:20.00]outro",
        ])
        components = identify_from_structured_lyrics(sl, lrc)
        # Intro skipped (no lines), only chorus matched.
        types = [c.component_type for c in components]
        assert "intro" not in types
        assert "chorus" in types


# ---------------------------------------------------------------------------
# Test 11: Beat/downbeat snapping.
# ---------------------------------------------------------------------------


class TestBeatSnapping:
    def test_beats_snapped(self):
        lrc = _build_lrc_for_sample()
        beats = [14.0, 20.0, 53.0, 83.0, 100.0, 135.0, 164.0, 192.0, 247.0, 275.0, 302.0, 328.0]
        components = identify_from_structured_lyrics(
            _sl_json(), lrc, beats=beats
        )
        # All start/end times should be snapped to nearest beat.
        for c in components:
            assert c.start_time in beats or c.start_time in [b for b in beats]

    def test_downbeats_snapped(self):
        lrc = _build_lrc_for_sample()
        downbeats = [14.0, 53.0, 83.0, 135.0, 164.0, 192.0, 247.0, 275.0, 302.0, 328.0]
        components = identify_from_structured_lyrics(
            _sl_json(), lrc, downbeats=downbeats, snap_to_downbeat=True
        )
        for c in components:
            # Snapped times should be in downbeats (or very close).
            assert any(abs(c.start_time - d) < 0.01 for d in downbeats) or c.start_time in downbeats


# ---------------------------------------------------------------------------
# Test 12: First bridge is essential — in both components.py and classifier.py.
# ---------------------------------------------------------------------------


class TestFirstBridgeEssential:
    def test_components_is_essential_bridge_occ1(self):
        bridge = ComponentInstance(
            component_type="bridge",
            occurrence_index=1,
            role="none",
            start_time=10.0,
            end_time=20.0,
        )
        assert _is_essential(bridge) is True

    def test_components_is_essential_bridge_occ2(self):
        bridge = ComponentInstance(
            component_type="bridge",
            occurrence_index=2,
            role="none",
            start_time=30.0,
            end_time=40.0,
        )
        assert _is_essential(bridge) is False

    def test_classifier_is_essential_bridge_occ1(self):
        bridge = ComponentInstance(
            component_type="bridge",
            occurrence_index=1,
            role="none",
            start_time=10.0,
            end_time=20.0,
        )
        assert classifier_is_essential(bridge) is True

    def test_classifier_is_essential_bridge_occ2(self):
        bridge = ComponentInstance(
            component_type="bridge",
            occurrence_index=2,
            role="none",
            start_time=30.0,
            end_time=40.0,
        )
        assert classifier_is_essential(bridge) is False

    def test_essential_roles_still_essential(self):
        for role in ("entry", "exit", "loop_target", "entry_exit"):
            comp = ComponentInstance(
                component_type="chorus",
                occurrence_index=1,
                role=role,
                start_time=0.0,
                end_time=10.0,
            )
            assert _is_essential(comp) is True
            assert classifier_is_essential(comp) is True


# ---------------------------------------------------------------------------
# Test 13: Segmentation mode integration — extract_components with
# segmentation_mode="structured_lyrics".
# ---------------------------------------------------------------------------


def _make_mock_global_features(sr: int = 22050, duration: float = 400.0):
    import numpy as np
    from sow_analysis.workers.components import GlobalFeatures

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


class TestSegmentationModeIntegration:
    @pytest.mark.asyncio
    async def test_structured_lyrics_mode_returns_structured_lyrics_source(self):
        lrc = _build_lrc_for_sample()
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch("sow_analysis.workers.components.settings") as mock_settings,
                patch(
                    "sow_analysis.workers.structured_lyrics_aligner.align_structured_lyrics",
                    new=AsyncMock(
                        return_value=identify_from_structured_lyrics(_sl_json(), lrc)
                    ),
                ),
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="sl_mode_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    structured_lyrics=_sl_json(),
                    lrc_content=lrc,
                    force=True,
                    segmentation_mode="structured_lyrics",
                )

            assert source == "structured_lyrics_llm"
            assert len(components) == 9


# ---------------------------------------------------------------------------
# Test 14: End-time clamping — last LRC section end_time clamped to
# song_total_duration.
# ---------------------------------------------------------------------------


class TestEndTimeClamping:
    def test_last_section_end_time_clamped(self):
        lrc = _build_lrc_for_sample()
        # The last chorus (occ 5) starts at 302.90 and would extrapolate
        # to ~328+. Clamp to 310.0.
        components = identify_from_structured_lyrics(
            _sl_json(), lrc, song_total_duration=310.0
        )
        for c in components:
            assert c.end_time <= 310.0 + 0.01  # allow float tolerance

    def test_no_clamp_when_duration_unknown(self):
        lrc = _build_lrc_for_sample()
        components = identify_from_structured_lyrics(
            _sl_json(), lrc, song_total_duration=None
        )
        # Without clamping, last component end_time may exceed any bound.
        # Just verify it doesn't crash and produces results.
        assert len(components) == 9


# ---------------------------------------------------------------------------
# Test 15: End-time clamping (repetition path).
# ---------------------------------------------------------------------------


class TestRepetitionEndTimeClamping:
    def test_repetition_end_time_clamped(self):
        # LRC where the last repeated block is at the end of the song.
        lrc = "\n".join([
            "[00:10.00]哈利路亞",
            "[00:15.00]讚美主",
            "[00:20.00]some verse",
            "[00:25.00]another verse",
            "[00:30.00]哈利路亞",
            "[00:35.00]讚美主",
        ])
        components = identify_from_lyrics_repetition(
            lrc, song_total_duration=37.0
        )
        for c in components:
            assert c.end_time <= 37.0 + 0.01


# ---------------------------------------------------------------------------
# Test 16: rapidfuzz degraded path — _lines_match raises ImportError on
# non-exact comparison when rapidfuzz is unavailable.
# ---------------------------------------------------------------------------


class TestRapidfuzzDegradedPath:
    def test_lines_match_raises_on_non_exact_without_rapidfuzz(self):
        from sow_analysis.workers.components import _lines_match

        # Exact match should work without rapidfuzz.
        assert _lines_match("hello", "hello") is True

        # Non-exact match should raise ImportError when rapidfuzz is missing.
        with patch.dict("sys.modules", {"rapidfuzz": None, "rapidfuzz.fuzz": None}):
            with pytest.raises((ImportError, TypeError, AttributeError)):
                _lines_match("hello", "world")


# ---------------------------------------------------------------------------
# Test 17: allin1 guard fix — extract_components with both structured_lyrics
# and sections available (default mode) returns source="structured_lyrics".
# ---------------------------------------------------------------------------


class TestAllin1GuardFix:
    @pytest.mark.asyncio
    async def test_structured_lyrics_takes_priority_over_allin1(self):
        lrc = _build_lrc_for_sample()
        sections = [
            {"label": "verse", "start": 0.0, "end": 50.0},
            {"label": "chorus", "start": 50.0, "end": 100.0},
            {"label": "chorus", "start": 100.0, "end": 150.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch("sow_analysis.workers.components.settings") as mock_settings,
                patch(
                    "sow_analysis.workers.structured_lyrics_aligner.align_structured_lyrics",
                    new=AsyncMock(
                        return_value=identify_from_structured_lyrics(_sl_json(), lrc)
                    ),
                ),
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="guard_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=sections,
                    lrc_content=lrc,
                    structured_lyrics=_sl_json(),
                    force=True,
                )

            # structured_lyrics_llm should win over allin1.
            assert source == "structured_lyrics_llm"
            assert len(components) == 9


# ---------------------------------------------------------------------------
# Test 18: admin-cli _cached_components_have_llm_fields bridge.
# ---------------------------------------------------------------------------


class TestCachedComponentsHaveLlmFieldsBridge:
    def test_bridge_occ1_null_theme_returns_false(self):
        from stream_of_worship.admin.services.analysis import (
            _cached_components_have_llm_fields,
        )

        components = [
            {
                "component_type": "chorus",
                "occurrence_index": 1,
                "role": "entry",
                "theme": "讚美",
                "vocal_posture": "To God",
            },
            {
                "component_type": "bridge",
                "occurrence_index": 1,
                "role": "none",
                "theme": None,  # NULL theme
                "vocal_posture": None,
            },
        ]
        # Bridge occ=1 is now essential, so NULL theme -> cache not satisfied.
        result = _cached_components_have_llm_fields(
            components,
            classify_theme=True,
            classify_vocal_posture=False,
        )
        assert result is False

    def test_bridge_occ1_with_theme_returns_true(self):
        from stream_of_worship.admin.services.analysis import (
            _cached_components_have_llm_fields,
        )

        components = [
            {
                "component_type": "chorus",
                "occurrence_index": 1,
                "role": "entry",
                "theme": "讚美",
                "vocal_posture": "To God",
            },
            {
                "component_type": "bridge",
                "occurrence_index": 1,
                "role": "none",
                "theme": "敬拜",
                "vocal_posture": "To God",
            },
        ]
        result = _cached_components_have_llm_fields(
            components,
            classify_theme=True,
            classify_vocal_posture=True,
        )
        assert result is True

    def test_bridge_occ2_null_theme_returns_true(self):
        """Bridge occ=2 is NOT essential, so NULL theme is OK."""
        from stream_of_worship.admin.services.analysis import (
            _cached_components_have_llm_fields,
        )

        components = [
            {
                "component_type": "chorus",
                "occurrence_index": 1,
                "role": "entry",
                "theme": "讚美",
                "vocal_posture": "To God",
            },
            {
                "component_type": "bridge",
                "occurrence_index": 2,
                "role": "none",
                "theme": None,
                "vocal_posture": None,
            },
        ]
        result = _cached_components_have_llm_fields(
            components,
            classify_theme=True,
            classify_vocal_posture=True,
        )
        assert result is True
