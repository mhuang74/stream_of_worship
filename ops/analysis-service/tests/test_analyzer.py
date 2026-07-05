"""Tests for analyze_audio_fast tempo parameters and octave guard."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sow_analysis.workers.analyzer import KeyDetectionResult, analyze_audio_fast


def _stub_key_result() -> KeyDetectionResult:
    return KeyDetectionResult(
        key="C",
        mode="major",
        confidence=0.9,
        score_margin=None,
        window_agreement=None,
        candidates=[],
        algorithm_version="ks_fulltrack_v1",
        detected_at="2026-01-01T00:00:00+00:00",
    )


class TestAnalyzeAudioFastTempoParams:
    """Tests that tempo estimation uses correct hop_length and start_bpm."""

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_default_params_are_hop512_start80(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """Verify default hop_length=512 and start_bpm=80 are passed through."""
        # Setup mocks
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([80.0])
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(
            audio_path,
            cache_manager,
            "abc123",
        )

        assert result["tempo_bpm"] == 80.0

        # Assert librosa.beat.tempo was called with start_bpm=80 and hop_length=512
        tempo_calls = [
            call
            for call in mock_librosa.beat.tempo.call_args_list
        ]
        assert len(tempo_calls) >= 1
        assert tempo_calls[0].kwargs.get("start_bpm") == 80.0
        assert tempo_calls[0].kwargs.get("hop_length") == 512

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_octave_guard_selects_double_time(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When primary estimate < 70 and alt ≈ 2×primary, return alt."""
        # First call (start_bpm=80) returns 65.0; second call (start_bpm=120) returns 130.0
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([65.0]),  # primary with start_bpm=80
            np.array([130.0]),  # alt with start_bpm=120
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(
            audio_path,
            cache_manager,
            "abc123",
        )

        # The octave guard should select the double-time alternative
        assert result["tempo_bpm"] == 130.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_octave_guard_ignores_non_double_time(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When alt is not ≈ 2×primary, keep primary."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([65.0]),  # primary with start_bpm=80
            np.array([90.0]),  # alt with start_bpm=120 — not double
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(
            audio_path,
            cache_manager,
            "abc123",
        )

        # Should keep primary since 90 is not ≈ 2×65
        assert result["tempo_bpm"] == 65.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_custom_start_bpm_passed_through(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """Verify custom start_bpm overrides the default."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([100.0])
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(
            audio_path,
            cache_manager,
            "abc123",
            start_bpm=100.0,
        )

        assert result["tempo_bpm"] == 100.0
        tempo_calls = mock_librosa.beat.tempo.call_args_list
        assert len(tempo_calls) == 1
        assert tempo_calls[0].kwargs.get("start_bpm") == 100.0

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_no_octave_guard_when_primary_above_70(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When primary >= 70, no second tempo call is made."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([85.0])
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(
            audio_path,
            cache_manager,
            "abc123",
        )

        assert result["tempo_bpm"] == 85.0
        assert mock_librosa.beat.tempo.call_count == 1
