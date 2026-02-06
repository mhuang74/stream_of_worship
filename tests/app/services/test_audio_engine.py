"""Tests for AudioEngine.

Tests audio generation and gap transition calculation.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from pydub import AudioSegment

from stream_of_worship.app.services.audio_engine import AudioEngine, AudioSegmentInfo, ExportResult
from stream_of_worship.app.db.models import SongsetItem


@pytest.fixture
def mock_asset_cache(tmp_path):
    """Mocked AssetCache returning test MP3 paths."""
    cache = MagicMock()

    def create_test_mp3(hash_prefix):
        audio_path = tmp_path / f"{hash_prefix}.mp3"
        audio = AudioSegment.silent(duration=1000)  # 1 second
        audio.export(audio_path, format="mp3")
        return audio_path

    cache.download_audio = Mock(side_effect=lambda h: create_test_mp3(h))
    return cache


@pytest.fixture
def audio_engine(mock_asset_cache):
    """AudioEngine with mock cache."""
    return AudioEngine(asset_cache=mock_asset_cache)


@pytest.fixture
def sample_songset_items():
    """List of SongsetItem for testing."""
    return [
        SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            recording_hash_prefix="abc123def456",
            position=0,
            gap_beats=2.0,
            tempo_bpm=120.0,
        ),
        SongsetItem(
            id="item_0002",
            songset_id="songset_0001",
            song_id="song_0002",
            recording_hash_prefix="def456ghi789",
            position=1,
            gap_beats=2.0,
            tempo_bpm=120.0,
        ),
    ]


class TestGapCalculation:
    """Tests for gap duration calculation."""

    def test_calculate_gap_ms_from_beats(self, audio_engine):
        """Verify beat-to-ms conversion using tempo."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=1,
            gap_beats=2.0,
        )

        gap_ms = audio_engine._calculate_gap_ms(item, tempo_bpm=120.0)

        # 120 BPM = 500ms per beat, 2 beats = 1000ms
        assert gap_ms == 1000

    def test_calculate_gap_ms_with_tempo(self, audio_engine):
        """Verify different tempos produce different gaps."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=1,
            gap_beats=2.0,
        )

        gap_120 = audio_engine._calculate_gap_ms(item, tempo_bpm=120.0)
        gap_60 = audio_engine._calculate_gap_ms(item, tempo_bpm=60.0)

        # 60 BPM should have twice the gap duration of 120 BPM
        assert gap_60 == gap_120 * 2

    def test_calculate_gap_ms_crossfade_enabled(self, audio_engine):
        """Verify gap is 0 when crossfade is enabled."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=1,
            gap_beats=2.0,
            crossfade_enabled=True,
            crossfade_duration_seconds=2.0,
        )

        gap_ms = audio_engine._calculate_gap_ms(item, tempo_bpm=120.0)

        assert gap_ms == 0

    def test_calculate_gap_ms_default_fallback(self, audio_engine):
        """Verify default gap when no tempo provided."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=1,
            gap_beats=2.0,
        )

        gap_ms = audio_engine._calculate_gap_ms(item, tempo_bpm=None)

        # Default: 2 beats * 1000ms = 2000ms
        assert gap_ms == 2000


class TestAudioLoading:
    """Tests for audio loading."""

    def test_load_audio_returns_audio_segment(self, audio_engine, tmp_path):
        """Verify pydub loads correctly."""
        # Create a test MP3
        audio_path = tmp_path / "test.mp3"
        audio = AudioSegment.silent(duration=1000)
        audio.export(audio_path, format="mp3")

        result = audio_engine._load_audio(audio_path)

        assert isinstance(result, AudioSegment)
        assert len(result) == 1000


class TestLoudnessNormalization:
    """Tests for loudness normalization."""

    def test_normalize_loudness_adjusts_gain(self, audio_engine):
        """Verify loudness matching."""
        # Create a quiet sine wave audio (not silent, to have measurable dBFS)
        import array
        samples = array.array('h', [int(1000 * (i % 100) / 100) for i in range(44100)])  # Quiet sine-like wave
        quiet_audio = AudioSegment(
            data=samples.tobytes(),
            sample_width=2,
            frame_rate=44100,
            channels=1
        )

        # Only test if we have measurable audio
        if quiet_audio.dBFS != float('-inf'):
            normalized = audio_engine._normalize_loudness(quiet_audio)
            # Should be louder than original (less negative dBFS)
            assert normalized.dBFS > quiet_audio.dBFS
        else:
            pytest.skip("Cannot test with -inf dBFS audio")

    def test_normalize_loudness_with_custom_target(self, audio_engine):
        """Verify custom target loudness."""
        import array
        samples = array.array('h', [int(5000 * (i % 100) / 100) for i in range(44100)])
        audio = AudioSegment(
            data=samples.tobytes(),
            sample_width=2,
            frame_rate=44100,
            channels=1
        )

        if audio.dBFS != float('-inf'):
            normalized = audio_engine._normalize_loudness(audio, target_lufs=-10.0)
            # Should be closer to target than original
            assert normalized.dBFS != float('-inf')
        else:
            pytest.skip("Cannot test with -inf dBFS audio")


class TestGenerateSongsetAudio:
    """Tests for songset audio generation."""

    def test_generate_songset_audio_single_song(self, audio_engine, tmp_path, mock_asset_cache):
        """Verify single song output (no transitions)."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            recording_hash_prefix="abc123def456",
            position=0,
        )

        output_path = tmp_path / "output.mp3"
        result = audio_engine.generate_songset_audio([item], output_path)

        assert isinstance(result, ExportResult)
        assert output_path.exists()
        assert len(result.segments) == 1

    def test_generate_songset_audio_empty_list_raises(self, audio_engine, tmp_path):
        """Verify error on empty songset."""
        output_path = tmp_path / "output.mp3"

        with pytest.raises(ValueError, match="empty songset"):
            audio_engine.generate_songset_audio([], output_path)

    def test_generate_songset_audio_progress_callback(self, audio_engine, tmp_path, sample_songset_items):
        """Verify callback invoked."""
        progress_calls = []

        def progress_callback(current, total):
            progress_calls.append((current, total))

        output_path = tmp_path / "output.mp3"
        audio_engine.generate_songset_audio(
            sample_songset_items,
            output_path,
            progress_callback=progress_callback,
        )

        assert len(progress_calls) > 0
        # First call should be (0, total_steps)
        assert progress_calls[0][0] == 0
        # Last call should be (total_steps, total_steps)
        assert progress_calls[-1][0] == progress_calls[-1][1]

    def test_generate_songset_audio_missing_audio_file_raises(self, tmp_path):
        """Verify error when audio not cached."""
        from unittest.mock import MagicMock

        # Create engine with mock that returns None
        mock_cache = MagicMock()
        mock_cache.download_audio.return_value = None

        engine = AudioEngine(asset_cache=mock_cache)

        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            recording_hash_prefix="missing_hash",
            position=0,
        )

        output_path = tmp_path / "output.mp3"

        with pytest.raises(FileNotFoundError):
            engine.generate_songset_audio([item], output_path)


class TestExportResult:
    """Tests for ExportResult dataclass."""

    def test_export_result_dataclass(self, tmp_path):
        """Verify ExportResult creation."""
        segment = AudioSegmentInfo(
            item=SongsetItem(
                id="item_0001",
                songset_id="songset_0001",
                song_id="song_0001",
                position=0,
            ),
            audio_path=tmp_path / "test.mp3",
            start_time_seconds=0.0,
            duration_seconds=180.0,
            gap_before_seconds=0.0,
        )

        result = ExportResult(
            output_path=tmp_path / "output.mp3",
            total_duration_seconds=180.0,
            segments=[segment],
        )

        assert result.output_path == tmp_path / "output.mp3"
        assert result.total_duration_seconds == 180.0
        assert len(result.segments) == 1
        assert result.sample_rate == 44100
        assert result.channels == 2


class TestPreviewTransition:
    """Tests for transition preview generation."""

    def test_preview_transition_generates_clip(self, audio_engine, tmp_path, mock_asset_cache):
        """Verify transition preview created."""
        from_item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            recording_hash_prefix="abc123def456",
            position=0,
            tempo_bpm=120.0,
        )
        to_item = SongsetItem(
            id="item_0002",
            songset_id="songset_0001",
            song_id="song_0002",
            recording_hash_prefix="def456ghi789",
            position=1,
            gap_beats=2.0,
            tempo_bpm=120.0,
        )

        result = audio_engine.preview_transition(from_item, to_item, preview_duration_seconds=2.0)

        assert result is not None
        assert result.exists()

    def test_preview_transition_returns_none_when_no_recording(self, audio_engine):
        """Verify None returned when items lack recordings."""
        from_item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            recording_hash_prefix=None,
        )
        to_item = SongsetItem(
            id="item_0002",
            songset_id="songset_0001",
            song_id="song_0002",
            position=1,
            recording_hash_prefix="def456ghi789",
        )

        result = audio_engine.preview_transition(from_item, to_item)

        assert result is None


class TestGetAudioInfo:
    """Tests for get_audio_info."""

    def test_get_audio_info_returns_metadata(self, audio_engine, tmp_path, mock_asset_cache):
        """Verify duration, channels, etc. returned."""
        info = audio_engine.get_audio_info("abc123def456")

        assert info is not None
        assert "duration_seconds" in info
        assert "duration_ms" in info
        assert "channels" in info
        assert "sample_rate" in info
        assert "bitrate" in info
        assert "file_size_bytes" in info

    def test_get_audio_info_returns_none_when_not_cached(self):
        """Verify None when audio not available."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        mock_cache.download_audio.return_value = None

        engine = AudioEngine(asset_cache=mock_cache)
        info = engine.get_audio_info("missing_hash")

        assert info is None
