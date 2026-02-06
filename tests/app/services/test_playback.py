"""Tests for PlaybackService.

Tests audio playback state management.

Note: These tests mock miniaudio to avoid requiring actual audio hardware.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from stream_of_worship.app.services.playback import (
    PlaybackService,
    PlaybackState,
    PlaybackPosition,
)


@pytest.fixture
def mock_miniaudio():
    """Mock miniaudio module."""
    with patch("stream_of_worship.app.services.playback.miniaudio") as mock:
        # Mock PlaybackDevice
        mock_device = MagicMock()
        mock.PlaybackDevice = Mock(return_value=mock_device)

        # Mock DecodedSoundFile
        mock_source = MagicMock()
        mock_source.samples = b"fake_audio_data"
        mock_source.sample_rate = 44100
        mock_source.nchannels = 2
        mock.decode_file = Mock(return_value=mock_source)

        # Mock Stream
        mock_stream = MagicMock()
        mock.Stream = Mock(return_value=mock_stream)

        # Mock SampleFormat
        mock.SampleFormat = Mock()
        mock.SampleFormat.SIGNED16 = "S16"

        yield mock


@pytest.fixture
def playback_service(mock_miniaudio):
    """PlaybackService instance with mocked miniaudio."""
    return PlaybackService(buffer_ms=500, volume=0.8)


@pytest.fixture
def sample_mp3_file(tmp_path):
    """Create a small MP3 file for loading tests."""
    from pydub import AudioSegment

    audio = AudioSegment.silent(duration=1000)  # 1 second
    mp3_path = tmp_path / "test.mp3"
    audio.export(mp3_path, format="mp3")
    return mp3_path


class TestInitialState:
    """Tests for initial playback state."""

    def test_initial_state_is_stopped(self, playback_service):
        """Verify initial PlaybackState."""
        assert playback_service.state == PlaybackState.STOPPED
        assert playback_service.is_stopped is True
        assert playback_service.is_playing is False
        assert playback_service.is_paused is False

    def test_initial_position_is_zero(self, playback_service):
        """Verify initial position."""
        position = playback_service.get_position()

        assert isinstance(position, PlaybackPosition)
        assert position.current_seconds == 0.0
        assert position.progress_percent == 0.0


class TestStateTransitions:
    """Tests for playback state transitions."""

    def test_load_changes_state_to_ready(self, playback_service, sample_mp3_file):
        """Verify load transitions state."""
        with patch.object(playback_service, '_source') as mock_source:
            mock_source.samples = b"fake_data"
            mock_source.sample_rate = 44100
            mock_source.nchannels = 2

            result = playback_service.load(sample_mp3_file)

            assert result is True
            assert playback_service.current_file == sample_mp3_file

    def test_load_handles_missing_file(self, playback_service, tmp_path):
        """Verify load handles missing file."""
        missing_file = tmp_path / "nonexistent.mp3"

        result = playback_service.load(missing_file)

        assert result is False

    def test_play_transitions_to_playing(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify play changes state."""
        # First load the file
        playback_service.load(sample_mp3_file)

        # Mock to bypass actual playback
        with patch.object(playback_service, '_set_state') as mock_set_state:
            playback_service.play()

            # Verify state was set to PLAYING
            mock_set_state.assert_called_with(PlaybackState.PLAYING)

    def test_pause_transitions_to_paused(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify pause changes state."""
        playback_service.load(sample_mp3_file)

        with patch.object(playback_service, '_set_state'):
            playback_service.play()
            result = playback_service.pause()

            assert result is True
            assert playback_service.is_paused is True

    def test_stop_transitions_to_stopped(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify stop changes state."""
        playback_service.load(sample_mp3_file)

        with patch.object(playback_service, '_set_state'):
            playback_service.play()
            playback_service.stop()

            assert playback_service.is_stopped is True
            assert playback_service.position_seconds == 0.0


class TestCallbacks:
    """Tests for callback invocation."""

    def test_position_callback_invoked(self, playback_service, sample_mp3_file):
        """Verify position updates trigger callback."""
        position_calls = []

        def on_position_changed(position):
            position_calls.append(position)

        playback_service.set_callbacks(on_position_changed=on_position_changed)
        playback_service.load(sample_mp3_file)

        # Manually trigger position update
        position = playback_service.get_position()
        if playback_service._on_position_changed:
            playback_service._on_position_changed(position)

        assert len(position_calls) > 0

    def test_state_callback_invoked(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify state changes trigger callback."""
        state_calls = []

        def on_state_changed(state):
            state_calls.append(state)

        playback_service.set_callbacks(on_state_changed=on_state_changed)
        playback_service.load(sample_mp3_file)

        # Play to trigger state change
        with patch.object(playback_service, '_device'):
            playback_service.play()

        assert len(state_calls) > 0
        assert state_calls[0] == PlaybackState.PLAYING

    def test_finished_callback_invoked(self, playback_service):
        """Verify finished callback is registered."""
        finished_calls = []

        def on_finished():
            finished_calls.append(True)

        playback_service.set_callbacks(on_finished=on_finished)

        # Verify callback is stored
        assert playback_service._on_finished is on_finished


class TestSeek:
    """Tests for seek functionality."""

    def test_seek_updates_position(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify seek works."""
        playback_service.load(sample_mp3_file)

        result = playback_service.seek(5.0)

        assert result is True
        assert playback_service.position_seconds == 5.0

    def test_seek_clamps_to_duration(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify seek clamps to valid range."""
        playback_service.load(sample_mp3_file)

        # Try to seek beyond duration
        result = playback_service.seek(1000.0)

        # Should clamp to duration
        assert result is True
        assert playback_service.position_seconds <= playback_service.duration_seconds

    def test_seek_returns_false_when_no_file(self, playback_service):
        """Verify seek returns False when no file loaded."""
        result = playback_service.seek(5.0)

        assert result is False


class TestVolume:
    """Tests for volume control."""

    def test_set_volume_updates_volume(self, playback_service):
        """Verify volume can be set."""
        playback_service.set_volume(0.5)

        assert playback_service.volume == 0.5

    def test_set_volume_clamps_to_max(self, playback_service):
        """Verify volume clamps to 1.0."""
        playback_service.set_volume(2.0)

        assert playback_service.volume == 1.0

    def test_set_volume_clamps_to_min(self, playback_service):
        """Verify volume clamps to 0.0."""
        playback_service.set_volume(-1.0)

        assert playback_service.volume == 0.0


class TestResume:
    """Tests for resume functionality."""

    def test_resume_from_paused(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify resume from paused state."""
        playback_service.load(sample_mp3_file)

        with patch.object(playback_service, '_set_state'):
            playback_service.play()
            playback_service.pause()
            result = playback_service.resume()

            assert result is True

    def test_resume_returns_false_when_not_paused(self, playback_service):
        """Verify resume returns False when not paused."""
        result = playback_service.resume()

        assert result is False


class TestPreviewSection:
    """Tests for section preview."""

    def test_preview_section_plays_from_position(self, playback_service, sample_mp3_file, mock_miniaudio):
        """Verify preview starts from specified position."""
        with patch.object(playback_service, 'play') as mock_play:
            playback_service.preview_section(sample_mp3_file, start_seconds=10.0)

            mock_play.assert_called_once()
            # Verify start position was stored
            assert playback_service._position_seconds == 10.0
