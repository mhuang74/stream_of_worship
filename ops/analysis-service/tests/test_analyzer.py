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


_SAMPLE_LRC = "[00:00.00]神愛你\n[00:10.00]我活著稱頌祢\n[00:20.00]哈利路亞"


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
        tempo_calls = [call for call in mock_librosa.beat.tempo.call_args_list]
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
        """When primary estimate < 60 and alt ≈ 2×primary in fast range [110,180], return alt."""
        # First call (start_bpm=80) returns 55.0; second call (start_bpm=120) returns 110.0
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([55.0]),  # primary with start_bpm=80 (< 60, triggers guard)
            np.array([110.0]),  # alt with start_bpm=120 (≈ 2×55, in [110,180])
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
        assert result["tempo_bpm"] == 110.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_half_time_guard_not_fired_on_legitimate_64_bpm_tempo(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """Regression: a ~65 BPM primary must NOT be doubled by the half-time guard.

        Song cc8f923fa60d ("我活著要稱頌祢") has a true tempo of ~65 BPM. librosa
        returns primary=64.6 with start_bpm=80. The v3 guard threshold (< 65)
        misfired and doubled it to 129.2. With the threshold lowered to < 60,
        the guard no longer fires and the correct primary is returned.
        """
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([64.6])
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 64.6
        assert mock_librosa.beat.tempo.call_count == 1

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_half_time_guard_rejects_doubling_below_110(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When alt ≈ 2×primary but alt < 110, keep primary (50 vs 100).

        A primary of 50 doubled to 100 is below the v4 floor of 110, so the
        guard rejects the doubling. This prevents false-doubling of legitimate
        very-slow tempos (though such tempos are rare in worship music).
        """
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([50.0]),  # primary with start_bpm=80 (< 60, triggers guard)
            np.array([100.0]),  # alt with start_bpm=120 (≈ 2×50, but < 110)
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 50.0
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
            np.array([50.0]),  # primary with start_bpm=80
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

        # Should keep primary since 90 is not ≈ 2×50
        assert result["tempo_bpm"] == 50.0
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
    async def test_no_octave_guard_when_primary_in_worship_range(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When primary is in the worship-plausible range (65-120), no second call."""
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

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_half_time_guard_not_fired_on_legitimate_slow_tempo(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """Regression: a ~70 BPM primary must NOT be doubled by the half-time guard.

        Song 863331f713b5 ("當祢走進我們當中") has a true tempo of ~70 BPM. librosa
        returns primary=69.837 with start_bpm=80. The v2 guard threshold (< 70)
        misfired and doubled it to 135.999. With the threshold lowered to < 65,
        the guard no longer fires and the correct primary is returned.
        """
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([69.837])
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

        assert result["tempo_bpm"] == 69.837
        assert mock_librosa.beat.tempo.call_count == 1

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_double_time_guard_selects_half_time(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When primary > 120 and alt ≈ primary/2 in worship range, return alt."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([136.0]),  # primary with start_bpm=80
            np.array([70.0]),  # alt with start_bpm=60
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 70.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_double_time_guard_ignores_non_half_time(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When alt is not ≈ primary/2, keep primary (137 vs 90)."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([137.0]),  # primary with start_bpm=80
            np.array([90.0]),  # alt with start_bpm=60 — not half-time
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 137.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_double_time_guard_rejects_half_time_outside_worship_range(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When alt ≈ primary/2 but outside 65-100 range, keep primary (140 vs 50)."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.side_effect = [
            np.array([140.0]),  # primary with start_bpm=80
            np.array([50.0]),  # alt with start_bpm=60 — outside worship range
        ]
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 140.0
        assert mock_librosa.beat.tempo.call_count == 2

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_double_time_guard_not_triggered_at_or_below_120(
        self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When primary <= 120, no second tempo call is made."""
        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([110.0])
        mock_detect_key.return_value = _stub_key_result()
        mock_compute_loudness.return_value = -20.0

        cache_manager = MagicMock()
        cache_manager.get_fast_analyze_result.return_value = None

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_text("dummy")

        result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

        assert result["tempo_bpm"] == 110.0
        assert mock_librosa.beat.tempo.call_count == 1


class TestAnalyzeAudioFastV5CpsPrior:
    """Tests for the prod-v5 CPS-derived lognormal prior path."""

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @patch("sow_analysis.workers.analyzer.settings")
    @pytest.mark.asyncio
    async def test_v5_uses_cps_prior_when_lrc_present(
        self, mock_settings, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When v5 + lrc_content, librosa.beat.tempo is called with prior=... (no octave guard)."""
        mock_settings.BPM_ALGORITHM_VERSION = "v5_cps_prior"
        mock_settings.KEY_ALGORITHM_VERSION = "ks_segment_vote_v1"

        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([68.0])
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
            lrc_content=_SAMPLE_LRC,
        )

        assert result["tempo_bpm"] == 68.0
        # Only one tempo call (no octave guard)
        assert mock_librosa.beat.tempo.call_count == 1
        # The call must use prior=, not start_bpm=
        tempo_call = mock_librosa.beat.tempo.call_args
        assert tempo_call.kwargs.get("prior") is not None
        assert "start_bpm" not in tempo_call.kwargs

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @patch("sow_analysis.workers.analyzer.settings")
    @pytest.mark.asyncio
    async def test_v5_falls_back_to_v4_when_lrc_missing(
        self, mock_settings, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When v5 + lrc_content=None, the v4 path runs (start_bpm + octave guard)."""
        mock_settings.BPM_ALGORITHM_VERSION = "v5_cps_prior"
        mock_settings.KEY_ALGORITHM_VERSION = "ks_segment_vote_v1"

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
            lrc_content=None,
        )

        assert result["tempo_bpm"] == 85.0
        # v4 path uses start_bpm, not prior
        tempo_call = mock_librosa.beat.tempo.call_args
        assert tempo_call.kwargs.get("start_bpm") == 80.0
        assert "prior" not in tempo_call.kwargs

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @patch("sow_analysis.workers.analyzer.settings")
    @pytest.mark.asyncio
    async def test_v5_falls_back_to_v4_when_cps_unparseable(
        self, mock_settings, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When v5 + malformed LRC (no valid lines), fall through to v4."""
        mock_settings.BPM_ALGORITHM_VERSION = "v5_cps_prior"
        mock_settings.KEY_ALGORITHM_VERSION = "ks_segment_vote_v1"

        mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
        mock_librosa.get_duration.return_value = 3.0
        mock_librosa.onset.onset_strength.return_value = np.zeros(258)
        mock_librosa.beat.tempo.return_value = np.array([90.0])
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
            lrc_content="just plain text\nno timestamps here",
        )

        assert result["tempo_bpm"] == 90.0
        # v4 fallback uses start_bpm
        tempo_call = mock_librosa.beat.tempo.call_args
        assert tempo_call.kwargs.get("start_bpm") == 80.0
        assert "prior" not in tempo_call.kwargs

    @patch("sow_analysis.workers.analyzer.compute_loudness")
    @patch("sow_analysis.workers.analyzer.detect_key")
    @patch("sow_analysis.workers.analyzer.librosa")
    @patch("sow_analysis.workers.analyzer.settings")
    @pytest.mark.asyncio
    async def test_v4_default_ignores_lrc_content(
        self, mock_settings, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
    ):
        """When BPM_ALGORITHM_VERSION=v4_octave_guard, lrc_content is ignored."""
        mock_settings.BPM_ALGORITHM_VERSION = "v4_octave_guard"
        mock_settings.KEY_ALGORITHM_VERSION = "ks_segment_vote_v1"

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
            lrc_content=_SAMPLE_LRC,
        )

        assert result["tempo_bpm"] == 80.0
        # v4 path uses start_bpm, not prior
        tempo_call = mock_librosa.beat.tempo.call_args
        assert tempo_call.kwargs.get("start_bpm") == 80.0
        assert "prior" not in tempo_call.kwargs
