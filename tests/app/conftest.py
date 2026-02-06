"""Shared fixtures for app tests."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_db_path(tmp_path):
    """Temporary SQLite database path."""
    return tmp_path / "test.db"


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Temporary cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary output directory."""
    output = tmp_path / "output"
    output.mkdir()
    return output


@pytest.fixture
def sample_mp3_file(tmp_path):
    """Create a minimal valid MP3 file for testing using pydub."""
    from pydub import AudioSegment

    audio = AudioSegment.silent(duration=1000)  # 1 second
    mp3_path = tmp_path / "test.mp3"
    audio.export(mp3_path, format="mp3")
    return mp3_path


@pytest.fixture
def sample_songset_row():
    """Sample database row for Songset.from_row()."""
    return ("songset_20240101120000", "Test Songset", "A test description", "2024-01-01T12:00:00", "2024-01-01T12:00:00")


@pytest.fixture
def sample_songset_item_row():
    """Sample database row for SongsetItem.from_row() (basic)."""
    return ("item_20240101120000000000", "songset_20240101120000", "song_0001", "abc123def456", 0, 2.0, 0, None, 0, 1.0, "2024-01-01T12:00:00")


@pytest.fixture
def sample_songset_item_detailed_row():
    """Sample database row for SongsetItem.from_row() (detailed with joined fields)."""
    return (
        "item_20240101120000000000",  # id
        "songset_20240101120000",  # songset_id
        "song_0001",  # song_id
        "abc123def456",  # recording_hash_prefix
        0,  # position
        2.0,  # gap_beats
        0,  # crossfade_enabled
        None,  # crossfade_duration_seconds
        0,  # key_shift_semitones
        1.0,  # tempo_ratio
        "2024-01-01T12:00:00",  # created_at
        "Test Song Title",  # song_title
        "G",  # song_key
        180.5,  # duration_seconds
        120.0,  # tempo_bpm
        "G Major",  # recording_key
        -14.0,  # loudness_db
    )
