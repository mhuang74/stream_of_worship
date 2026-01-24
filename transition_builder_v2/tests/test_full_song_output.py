"""Tests for full song output generation.

This test specifically reproduces the issue where pressing 'o' key
to generate full song output fails with:
    "'str' object has no attribute 'exists'"

Root cause: playback.py load() method expects Path but receives string from screen.
"""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import soundfile as sf
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.song import Song, Section
from app.services.generation import TransitionGenerationService
from app.services.playback import PlaybackService


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        output_dir = tmpdir / "output_transitions"
        output_songs_dir = tmpdir / "output_songs"
        stems_dir = tmpdir / "stems"
        audio_dir = tmpdir / "audio"

        output_dir.mkdir()
        output_songs_dir.mkdir()
        stems_dir.mkdir()
        audio_dir.mkdir()

        yield {
            "tmpdir": tmpdir,
            "output_dir": output_dir,
            "output_songs_dir": output_songs_dir,
            "stems_dir": stems_dir,
            "audio_dir": audio_dir,
        }


@pytest.fixture
def sample_songs(temp_dirs):
    """Create sample songs with audio files for testing."""
    audio_dir = temp_dirs["audio_dir"]
    sr = 44100
    duration = 10.0  # 10 seconds

    # Create sample audio files
    samples = int(sr * duration)
    audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

    song_a_path = audio_dir / "song_a.flac"
    song_b_path = audio_dir / "song_b.flac"

    sf.write(str(song_a_path), audio_data, sr, format='FLAC')
    sf.write(str(song_b_path), audio_data, sr, format='FLAC')

    # Create Song objects with Path filepath
    song_a = Song(
        filename="song_a.flac",
        filepath=song_a_path,  # This is a Path object
        duration=duration,
        tempo=120.0,
        key="C",
        mode="major",
        key_confidence=0.9,
        full_key="C major",
        loudness_db=-14.0,
        spectral_centroid=2000.0,
        sections=[
            Section(label="intro", start=0.0, end=3.0, duration=3.0),
            Section(label="verse", start=3.0, end=6.0, duration=3.0),
            Section(label="chorus", start=6.0, end=10.0, duration=4.0),
        ]
    )

    song_b = Song(
        filename="song_b.flac",
        filepath=song_b_path,  # This is a Path object
        duration=duration,
        tempo=120.0,
        key="G",
        mode="major",
        key_confidence=0.85,
        full_key="G major",
        loudness_db=-12.0,
        spectral_centroid=2500.0,
        sections=[
            Section(label="intro", start=0.0, end=2.0, duration=2.0),
            Section(label="verse", start=2.0, end=5.0, duration=3.0),
            Section(label="chorus", start=5.0, end=10.0, duration=5.0),
        ]
    )

    return song_a, song_b


@pytest.fixture
def generation_service(temp_dirs):
    """Create a TransitionGenerationService instance."""
    return TransitionGenerationService(
        output_dir=temp_dirs["output_dir"],
        output_songs_dir=temp_dirs["output_songs_dir"],
        stems_folder=temp_dirs["stems_dir"]
    )


def create_transition_file(temp_dirs, duration=5.0, sr=44100):
    """Create a sample transition audio file."""
    samples = int(sr * duration)
    audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

    transition_path = temp_dirs["output_dir"] / "test_transition.flac"
    sf.write(str(transition_path), audio_data, sr, format='FLAC')

    return transition_path


class TestFullSongOutputStringPathBug:
    """Tests to reproduce and verify the fix for the 'str' object has no attribute 'exists' bug."""

    def test_generate_full_song_output_with_path_object(
        self, temp_dirs, sample_songs, generation_service
    ):
        """Test generate_full_song_output when transition_audio_path is a Path object."""
        song_a, song_b = sample_songs
        transition_path = create_transition_file(temp_dirs)

        # Call with Path object - should work
        output_path, metadata = generation_service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,  # verse
            section_b_index=1,  # verse
            transition_audio_path=transition_path,  # Path object
            sr=44100
        )

        assert output_path.exists()
        assert metadata["output_type"] == "full_song"

    def test_generate_full_song_output_with_string_path(
        self, temp_dirs, sample_songs, generation_service
    ):
        """Test generate_full_song_output when transition_audio_path is a string.

        This test reproduces the bug: "'str' object has no attribute 'exists'"
        The bug occurs because the service might not properly convert string paths to Path objects.
        """
        song_a, song_b = sample_songs
        transition_path = create_transition_file(temp_dirs)

        # Call with string path - this is how the screen calls it
        # This should work but might fail with the bug
        output_path, metadata = generation_service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,  # verse
            section_b_index=1,  # verse
            transition_audio_path=str(transition_path),  # String!
            sr=44100
        )

        assert output_path.exists()
        assert metadata["output_type"] == "full_song"

    def test_generate_full_song_output_with_string_filepath_in_song(
        self, temp_dirs, generation_service
    ):
        """Test when song.filepath is a string instead of a Path.

        This is another potential source of the bug - if song.filepath is somehow
        stored as a string instead of a Path object.
        """
        audio_dir = temp_dirs["audio_dir"]
        sr = 44100
        duration = 10.0

        # Create audio files
        samples = int(sr * duration)
        audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

        song_a_path = audio_dir / "song_a_str.flac"
        song_b_path = audio_dir / "song_b_str.flac"

        sf.write(str(song_a_path), audio_data, sr, format='FLAC')
        sf.write(str(song_b_path), audio_data, sr, format='FLAC')

        # Create Song objects with STRING filepath (simulating the bug)
        song_a = Song(
            filename="song_a_str.flac",
            filepath=str(song_a_path),  # String instead of Path!
            duration=duration,
            tempo=120.0,
            key="C",
            mode="major",
            key_confidence=0.9,
            full_key="C major",
            loudness_db=-14.0,
            spectral_centroid=2000.0,
            sections=[
                Section(label="intro", start=0.0, end=3.0, duration=3.0),
                Section(label="verse", start=3.0, end=6.0, duration=3.0),
                Section(label="chorus", start=6.0, end=10.0, duration=4.0),
            ]
        )

        song_b = Song(
            filename="song_b_str.flac",
            filepath=str(song_b_path),  # String instead of Path!
            duration=duration,
            tempo=120.0,
            key="G",
            mode="major",
            key_confidence=0.85,
            full_key="G major",
            loudness_db=-12.0,
            spectral_centroid=2500.0,
            sections=[
                Section(label="intro", start=0.0, end=2.0, duration=2.0),
                Section(label="verse", start=2.0, end=5.0, duration=3.0),
                Section(label="chorus", start=5.0, end=10.0, duration=5.0),
            ]
        )

        transition_path = create_transition_file(temp_dirs)

        # This might fail with "'str' object has no attribute 'exists'"
        # if the service doesn't handle string filepaths correctly
        output_path, metadata = generation_service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,
            section_b_index=1,
            transition_audio_path=str(transition_path),
            sr=44100
        )

        assert output_path.exists()
        assert metadata["output_type"] == "full_song"

    def test_generate_full_song_output_first_section(
        self, temp_dirs, sample_songs, generation_service
    ):
        """Test when section_a_index is 0 (no sections before)."""
        song_a, song_b = sample_songs
        transition_path = create_transition_file(temp_dirs)

        output_path, metadata = generation_service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=0,  # First section - no sections before
            section_b_index=1,
            transition_audio_path=str(transition_path),
            sr=44100
        )

        assert output_path.exists()
        assert metadata["num_song_a_sections_before"] == 0

    def test_generate_full_song_output_last_section(
        self, temp_dirs, sample_songs, generation_service
    ):
        """Test when section_b_index is the last section (no sections after)."""
        song_a, song_b = sample_songs
        transition_path = create_transition_file(temp_dirs)

        output_path, metadata = generation_service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,
            section_b_index=2,  # Last section - no sections after
            transition_audio_path=str(transition_path),
            sr=44100
        )

        assert output_path.exists()
        assert metadata["num_song_b_sections_after"] == 0


class TestPlaybackServiceStringPathBug:
    """Tests for the PlaybackService bug where load() fails with string paths.

    The actual bug: line 949 in generation.py screen calls:
        self.playback.load(str(output_path))

    But playback.load() at line 68 does:
        if not audio_path.exists():

    This fails because audio_path is a string, not a Path object.
    """

    def test_playback_load_with_path_object(self, temp_dirs):
        """Test PlaybackService.load() with a Path object - should work."""
        # Create sample audio file
        audio_dir = temp_dirs["audio_dir"]
        sr = 44100
        duration = 1.0
        samples = int(sr * duration)
        audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

        audio_path = audio_dir / "test_audio.flac"
        sf.write(str(audio_path), audio_data, sr, format='FLAC')

        # Test with Path object
        playback = PlaybackService()
        result = playback.load(audio_path)  # Path object

        assert result is True
        playback.stop()

    def test_playback_load_with_string_path_reproduces_bug(self, temp_dirs):
        """Test that PlaybackService.load() FAILS with string path (reproduces the bug).

        This test will FAIL until the bug is fixed, demonstrating the issue.
        """
        # Create sample audio file
        audio_dir = temp_dirs["audio_dir"]
        sr = 44100
        duration = 1.0
        samples = int(sr * duration)
        audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

        audio_path = audio_dir / "test_audio_str.flac"
        sf.write(str(audio_path), audio_data, sr, format='FLAC')

        # Test with string path - this is how the screen calls it
        playback = PlaybackService()

        # This should work but currently raises:
        # AttributeError: 'str' object has no attribute 'exists'
        result = playback.load(str(audio_path))  # String!

        assert result is True
        playback.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
