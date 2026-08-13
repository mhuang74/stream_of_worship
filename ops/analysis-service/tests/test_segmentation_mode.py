"""Tests for the --segmentation-mode mutual-exclusivity flag (v7).

Covers the four modes (llm, repetition, allin1, None) against the
extract_components identification block, asserting:
  - llm mode: no allin1 read, no repetition fallback; [] on LLM failure.
  - repetition mode: allin1 skipped, LRC block skipped, beat-grid cache hoist applies.
  - allin1 mode: LRC block + repetition block skipped; [] when no sections.
  - None mode: regression guard — current best-available priority preserved.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sow_analysis.storage.cache import BEAT_GRID_SCHEMA_VERSION, CacheManager
from sow_analysis.workers.components import (
    ComponentInstance,
    extract_components,
    identify_from_lyrics_repetition,
)


def _make_mock_global_features(sr: int = 22050, duration: float = 60.0):
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


_SAMPLE_LRC = """[00:00.00]讚美主
[00:05.00]哈利路亞
[00:10.00]讚美主
[00:15.00]哈利路亞
"""

_SECTIONS = [
    {"label": "verse", "start": 0.0, "end": 20.0},
    {"label": "chorus", "start": 20.0, "end": 40.0},
    {"label": "chorus", "start": 40.0, "end": 60.0},
]


class TestSegmentationModeLlm:
    """Mode 'llm' — runs ONLY segment_song; no allin1, no repetition fallback."""

    @pytest.mark.asyncio
    async def test_llm_empty_result_no_repetition_fallback(self):
        """segment_song returns [] → source='none', repetition NOT invoked."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                    return_value=[],
                ) as mock_segment,
                patch(
                    "sow_analysis.workers.components.identify_from_lyrics_repetition"
                ) as mock_repetition,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = False
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="llm_empty_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="llm",
                )

            assert components == []
            assert source == "none"
            mock_segment.assert_awaited_once()
            mock_repetition.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_no_lrc_returns_empty_with_warning(self):
        """lrc_content=None + mode='llm' → [] with warning, segment_song NOT called."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                ) as mock_segment,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = False
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="llm_nolrc_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=None,
                    force=True,
                    segmentation_mode="llm",
                )

            assert components == []
            assert source == "none"
            mock_segment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_success_sets_source(self):
        """segment_song returns components → source='llm_segmentation'."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()
            llm_components = [
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="entry",
                    start_time=10.0,
                    end_time=30.0,
                    confidence=0.9,
                    source="llm_segmentation",
                ),
            ]

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                    return_value=llm_components,
                ),
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = False
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="llm_ok_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="llm",
                )

            assert source == "llm_segmentation"
            assert len(components) == 1


class TestSegmentationModeRepetition:
    """Mode 'repetition' — runs ONLY identify_from_lyrics_repetition."""

    @pytest.mark.asyncio
    async def test_repetition_skips_allin1_even_with_sections(self):
        """Cached sections present but mode='repetition' → allin1 NOT called."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.components.identify_from_allin1_sections"
                ) as mock_allin1,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                ) as mock_segment,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = True
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="rep_skip_allin1_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="repetition",
                )

            assert source == "lyrics_repetition"
            assert len(components) > 0
            mock_allin1.assert_not_called()
            mock_segment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repetition_reads_beat_grid_cache(self):
        """beats=None + downbeats=None + populated beat-grid cache → cache IS read."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            content_hash = "rep_beatgrid_001"

            grid_payload = {
                "schema_version": BEAT_GRID_SCHEMA_VERSION,
                "source": "madmom",
                "content_hash": content_hash,
                "hash_prefix": content_hash[:12],
                "beats": [[0.0, 1], [1.0, 2], [2.0, 3], [3.0, 4], [4.0, 1]],
                "downbeats": [0.0, 4.0, 8.0, 12.0],
                "detected_at": "2026-08-12T12:00:00+00:00",
                "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
            }
            cache_manager.save_beat_grid(content_hash, grid_payload)

            mock_gf = _make_mock_global_features()

            captured_downbeats = {}

            def _spy_repetition(lrc_content, beats=None, downbeats=None, **kwargs):
                captured_downbeats["downbeats"] = downbeats
                return identify_from_lyrics_repetition(
                    lrc_content,
                    beats=beats,
                    downbeats=downbeats,
                    song_total_duration=kwargs.get("song_total_duration"),
                    snap_to_downbeat=kwargs.get("snap_to_downbeat", False),
                )

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.components.identify_from_lyrics_repetition",
                    side_effect=_spy_repetition,
                ) as mock_repetition,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = False
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash=content_hash,
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="repetition",
                )

            assert source == "lyrics_repetition"
            assert len(components) > 0
            mock_repetition.assert_called_once()
            assert captured_downbeats["downbeats"] == [0.0, 4.0, 8.0, 12.0]


class TestSegmentationModeAllin1:
    """Mode 'allin1' — runs ONLY identify_from_allin1_sections."""

    @pytest.mark.asyncio
    async def test_allin1_no_sections_returns_empty(self):
        """No sections + mode='allin1' → [], LRC path untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                ) as mock_segment,
                patch(
                    "sow_analysis.workers.components.identify_from_lyrics_repetition"
                ) as mock_repetition,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = True
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="allin1_nosec_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=None,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="allin1",
                )

            assert components == []
            assert source == "none"
            mock_segment.assert_not_awaited()
            mock_repetition.assert_not_called()

    @pytest.mark.asyncio
    async def test_allin1_success_sets_source(self):
        """Sections present + mode='allin1' → source='allin1_sections'."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                ) as mock_segment,
                patch(
                    "sow_analysis.workers.components.identify_from_lyrics_repetition"
                ) as mock_repetition,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = True
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="allin1_ok_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                    segmentation_mode="allin1",
                )

            assert source == "allin1_sections"
            assert len(components) >= 2
            mock_segment.assert_not_awaited()
            mock_repetition.assert_not_called()


class TestSegmentationModeNone:
    """Mode None (default) — regression guard for current best-available priority."""

    @pytest.mark.asyncio
    async def test_none_mode_uses_allin1_first(self):
        """Sections present + no mode → allin1 path (current behavior)."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))

            mock_gf = _make_mock_global_features()

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features"
                ) as mock_precompute,
                patch(
                    "sow_analysis.workers.section_segmenter.segment_song",
                    new_callable=AsyncMock,
                ) as mock_segment,
                patch("sow_analysis.workers.components.settings") as mock_settings,
            ):
                mock_precompute.return_value = mock_gf
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION = False
                mock_settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS = 999

                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash="none_allin1_001",
                    cache_manager=cache_manager,
                    r2_client=None,
                    sections=_SECTIONS,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                )

            assert source == "allin1_sections"
            assert len(components) >= 2
            mock_segment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_mode_beat_grid_cache_fallback_runs(self):
        """None mode + no beats/downbeats + populated beat-grid cache → cache read."""
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_text("dummy")
            cache_manager = CacheManager(Path(tmp))
            content_hash = "none_beatgrid_001"

            grid_payload = {
                "schema_version": BEAT_GRID_SCHEMA_VERSION,
                "source": "madmom",
                "content_hash": content_hash,
                "hash_prefix": content_hash[:12],
                "beats": [[0.0, 1], [1.0, 2], [2.0, 3], [3.0, 4], [4.0, 1]],
                "downbeats": [0.0, 4.0, 8.0, 12.0],
                "detected_at": "2026-08-12T12:00:00+00:00",
                "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
            }
            cache_manager.save_beat_grid(content_hash, grid_payload)

            with (
                patch(
                    "sow_analysis.workers.components._precompute_global_features",
                    side_effect=RuntimeError("simulated failure"),
                ),
            ):
                components, source = await extract_components(
                    audio_path=audio_path,
                    content_hash=content_hash,
                    cache_manager=cache_manager,
                    r2_client=None,
                    lrc_content=_SAMPLE_LRC,
                    force=True,
                )

            assert source == "lyrics_repetition"
            assert len(components) > 0
            for c in components:
                assert c.confidence == 0.7
