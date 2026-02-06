"""Tests for app database models.

Tests Songset and SongsetItem dataclasses with their serialization methods.
"""

import re

import pytest

from stream_of_worship.app.db.models import Songset, SongsetItem


class TestSongset:
    """Tests for Songset dataclass."""

    def test_songset_from_row_basic(self, sample_songset_row):
        """Verify from_row() with minimal tuple."""
        songset = Songset.from_row(sample_songset_row)

        assert songset.id == "songset_20240101120000"
        assert songset.name == "Test Songset"
        assert songset.description == "A test description"
        assert songset.created_at == "2024-01-01T12:00:00"
        assert songset.updated_at == "2024-01-01T12:00:00"

    def test_songset_from_row_full(self):
        """Verify from_row() with all fields including None."""
        row = ("songset_0001", "Name", None, "2024-01-01T00:00:00", "2024-01-01T00:00:00")
        songset = Songset.from_row(row)

        assert songset.id == "songset_0001"
        assert songset.name == "Name"
        assert songset.description is None

    def test_songset_to_dict(self, sample_songset_row):
        """Verify to_dict() returns correct dict."""
        songset = Songset.from_row(sample_songset_row)
        d = songset.to_dict()

        assert d["id"] == "songset_20240101120000"
        assert d["name"] == "Test Songset"
        assert d["description"] == "A test description"
        assert d["created_at"] == "2024-01-01T12:00:00"
        assert d["updated_at"] == "2024-01-01T12:00:00"

    def test_songset_to_dict_roundtrip(self, sample_songset_row):
        """Verify from_row(songset.to_dict().values()) works."""
        songset = Songset.from_row(sample_songset_row)
        d = songset.to_dict()
        values = tuple(d.values())

        songset2 = Songset.from_row(values)

        assert songset2.id == songset.id
        assert songset2.name == songset.name
        assert songset2.description == songset.description

    def test_songset_generate_id_format(self):
        """Verify ID format matches pattern."""
        songset_id = Songset.generate_id()

        # Should match pattern: songset_YYYYMMDDHHMMSS
        assert songset_id.startswith("songset_")
        assert len(songset_id) == len("songset_") + 14  # 14 chars for timestamp

        # Verify timestamp portion is all digits
        timestamp_part = songset_id.split("_")[1]
        assert timestamp_part.isdigit()
        assert len(timestamp_part) == 14


class TestSongsetItem:
    """Tests for SongsetItem dataclass."""

    def test_songset_item_from_row_basic(self, sample_songset_item_row):
        """Verify from_row() with minimal tuple."""
        item = SongsetItem.from_row(sample_songset_item_row)

        assert item.id == "item_20240101120000000000"
        assert item.songset_id == "songset_20240101120000"
        assert item.song_id == "song_0001"
        assert item.recording_hash_prefix == "abc123def456"
        assert item.position == 0
        assert item.gap_beats == 2.0
        assert item.crossfade_enabled is False
        assert item.crossfade_duration_seconds is None
        assert item.key_shift_semitones == 0
        assert item.tempo_ratio == 1.0

    def test_songset_item_from_row_detailed(self, sample_songset_item_detailed_row):
        """Verify from_row() with detailed=True."""
        item = SongsetItem.from_row(sample_songset_item_detailed_row, detailed=True)

        assert item.id == "item_20240101120000000000"
        assert item.song_title == "Test Song Title"
        assert item.song_key == "G"
        assert item.duration_seconds == 180.5
        assert item.tempo_bpm == 120.0
        assert item.recording_key == "G Major"
        assert item.loudness_db == -14.0

    def test_songset_item_to_dict(self, sample_songset_item_detailed_row):
        """Verify to_dict() returns correct dict."""
        item = SongsetItem.from_row(sample_songset_item_detailed_row, detailed=True)
        d = item.to_dict()

        assert d["id"] == item.id
        assert d["songset_id"] == item.songset_id
        assert d["song_id"] == item.song_id
        assert d["recording_hash_prefix"] == item.recording_hash_prefix
        assert d["position"] == item.position
        assert d["gap_beats"] == item.gap_beats
        assert d["crossfade_enabled"] == item.crossfade_enabled
        assert d["song_title"] == item.song_title
        assert d["duration_seconds"] == item.duration_seconds

    def test_songset_item_formatted_duration_with_value(self):
        """Verify formatted_duration property with valid duration."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            duration_seconds=185.5,
        )

        assert item.formatted_duration == "3:05"

    def test_songset_item_formatted_duration_none(self):
        """Verify formatted_duration returns --:-- when None."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            duration_seconds=None,
        )

        assert item.formatted_duration == "--:--"

    def test_songset_item_display_key_recording_priority(self):
        """Verify display_key property prioritizes recording_key."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            recording_key="D Major",
            song_key="G",
        )

        assert item.display_key == "D Major"

    def test_songset_item_display_key_fallback_to_song(self):
        """Verify display_key falls back to song_key."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            recording_key=None,
            song_key="G",
        )

        assert item.display_key == "G"

    def test_songset_item_display_key_unknown(self):
        """Verify display_key returns ? when no key available."""
        item = SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            position=0,
            recording_key=None,
            song_key=None,
        )

        assert item.display_key == "?"

    def test_songset_item_generate_id_format(self):
        """Verify ID format matches pattern."""
        item_id = SongsetItem.generate_id()

        # Should match pattern: item_YYYYMMDDHHMMSSffffff
        assert item_id.startswith("item_")

        # Verify timestamp portion is all digits
        timestamp_part = item_id.split("_")[1]
        assert timestamp_part.isdigit()
        assert len(timestamp_part) == 20  # 14 for datetime + 6 for microseconds
