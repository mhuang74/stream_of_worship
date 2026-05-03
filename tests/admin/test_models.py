"""Tests for sow-admin database models."""

import json

import pytest

from stream_of_worship.admin.db.models import DatabaseStats, Recording, Song


class TestSong:
    """Tests for Song model."""

    @pytest.fixture
    def sample_song_row(self):
        """Return a sample database row tuple for a song."""
        return (
            "song_0001",  # id
            "將天敞開",  # title
            "jiang_tian_chang_kai",  # title_pinyin
            "作曲家",  # composer
            "作詞家",  # lyricist
            "敬拜讚美15",  # album_name
            "敬拜讚美系列",  # album_series
            "G",  # musical_key
            "第一行歌詞\n第二行歌詞",  # lyrics_raw
            '["第一行歌詞", "第二行歌詞"]',  # lyrics_lines
            '[{"label": "verse", "start": 0, "end": 30}]',  # sections
            "https://sop.org/song/123",  # source_url
            123,  # table_row_number
            "2024-01-15T10:30:00",  # scraped_at
            "2024-01-15T10:30:00",  # created_at
            "2024-01-15T10:30:00",  # updated_at
            None,  # deleted_at
        )

    @pytest.fixture
    def sample_song(self):
        """Return a sample Song instance."""
        return Song(
            id="song_0001",
            title="將天敞開",
            source_url="https://sop.org/song/123",
            scraped_at="2024-01-15T10:30:00",
            title_pinyin="jiang_tian_chang_kai",
            lyrics_lines='["第一行歌詞", "第二行歌詞"]',
            lyrics_raw="第一行歌詞\n第二行歌詞",
        )

    @pytest.fixture
    def sample_song_description(self):
        """Return column description for song test."""
        return [
            ("id", "", "", "", "", "", "",),
            ("title", "", "", "", "", "", "",),
            ("title_pinyin", "", "", "", "", "", "",),
            ("composer", "", "", "", "", "", "",),
            ("lyricist", "", "", "", "", "", "",),
            ("album_name", "", "", "", "", "", "",),
            ("album_series", "", "", "", "", "", "",),
            ("musical_key", "", "", "", "", "", "",),
            ("lyrics_raw", "", "", "", "", "", "",),
            ("lyrics_lines", "", "", "", "", "", "",),
            ("sections", "", "", "", "", "", "",),
            ("source_url", "", "", "", "", "", "",),
            ("table_row_number", "", "", "", "", "", "",),
            ("scraped_at", "", "", "", "", "", "",),
            ("created_at", "", "", "", "", "", "",),
            ("updated_at", "", "", "", "", "", "",),
            ("deleted_at", "", "", "", "", "", "",),
        ]

    def test_from_row(self, sample_song_row, sample_song_description):
        """Test creating Song from database row."""
        song = Song.from_row(sample_song_row, sample_song_description)

        assert song.id == "song_0001"
        assert song.title == "將天敞開"
        assert song.title_pinyin == "jiang_tian_chang_kai"
        assert song.musical_key == "G"

    def test_to_dict(self, sample_song):
        """Test converting Song to dictionary."""
        data = sample_song.to_dict()

        assert data["id"] == "song_0001"
        assert data["title"] == "將天敞開"
        assert data["source_url"] == "https://sop.org/song/123"

    def test_lyrics_list_from_lines(self, sample_song):
        """Test getting lyrics as list from lyrics_lines field."""
        lyrics = sample_song.lyrics_list

        assert lyrics == ["第一行歌詞", "第二行歌詞"]

    def test_lyrics_list_from_raw(self):
        """Test getting lyrics as list from lyrics_raw when lines is empty."""
        song = Song(
            id="song_0002",
            title="Test",
            source_url="https://example.com",
            scraped_at="2024-01-15T10:30:00",
            lyrics_raw="Line 1\nLine 2\nLine 3",
            lyrics_lines=None,
        )

        assert song.lyrics_list == ["Line 1", "Line 2", "Line 3"]

    def test_lyrics_list_empty(self):
        """Test getting lyrics as list when no lyrics exist."""
        song = Song(
            id="song_0003",
            title="Test",
            source_url="https://example.com",
            scraped_at="2024-01-15T10:30:00",
        )

        assert song.lyrics_list == []


class TestRecording:
    """Tests for Recording model."""

    @pytest.fixture
    def sample_recording_row(self):
        """Return a sample database row tuple for a recording (30 columns)."""
        return (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",  # content_hash
            "c6de4449928d",  # hash_prefix
            "song_0001",  # song_id
            "original.mp3",  # original_filename
            5242880,  # file_size_bytes
            "2024-01-15T10:30:00",  # imported_at
            "s3://bucket/c6de4449928d/audio.mp3",  # r2_audio_url
            "s3://bucket/c6de4449928d/stems/",  # r2_stems_url
            "s3://bucket/c6de4449928d/lyrics.lrc",  # r2_lrc_url
            245.3,  # duration_seconds
            128.5,  # tempo_bpm
            "G",  # musical_key
            "major",  # musical_mode
            0.87,  # key_confidence
            -8.2,  # loudness_db
            "[0.23, 0.70, 1.17]",  # beats
            "[0.23, 2.10]",  # downbeats
            '[{"label": "intro", "start": 0.0, "end": 15.2}]',  # sections
            "[4, 512, 24]",  # embeddings_shape
            "completed",  # analysis_status
            "job_abc123",  # analysis_job_id
            "pending",  # lrc_status
            None,  # lrc_job_id
            "2024-01-15T10:30:00",  # created_at
            "2024-01-15T10:30:00",  # updated_at
            "https://youtube.com/watch?v=x",  # youtube_url
            "published",  # visibility_status
            None,  # deleted_at
            "completed",  # download_status
        )

    @pytest.fixture
    def sample_recording(self):
        """Return a sample Recording instance."""
        return Recording(
            content_hash="c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            hash_prefix="c6de4449928d",
            original_filename="original.mp3",
            file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            analysis_status="completed",
        )

    @pytest.fixture
    def sample_recording_description(self):
        """Return column description for recording test (30 columns)."""
        return [
            ("content_hash", "", "", "", "", "", "",),
            ("hash_prefix", "", "", "", "", "", "",),
            ("song_id", "", "", "", "", "", "",),
            ("original_filename", "", "", "", "", "", "",),
            ("file_size_bytes", "", "", "", "", "", "",),
            ("imported_at", "", "", "", "", "", "",),
            ("r2_audio_url", "", "", "", "", "", "",),
            ("r2_stems_url", "", "", "", "", "", "",),
            ("r2_lrc_url", "", "", "", "", "", "",),
            ("duration_seconds", "", "", "", "", "", "",),
            ("tempo_bpm", "", "", "", "", "", "",),
            ("musical_key", "", "", "", "", "", "",),
            ("musical_mode", "", "", "", "", "", "",),
            ("key_confidence", "", "", "", "", "", "",),
            ("loudness_db", "", "", "", "", "", "",),
            ("beats", "", "", "", "", "", "",),
            ("downbeats", "", "", "", "", "", "",),
            ("sections", "", "", "", "", "", "",),
            ("embeddings_shape", "", "", "", "", "", "",),
            ("analysis_status", "", "", "", "", "", "",),
            ("analysis_job_id", "", "", "", "", "", "",),
            ("lrc_status", "", "", "", "", "", "",),
            ("lrc_job_id", "", "", "", "", "", "",),
            ("created_at", "", "", "", "", "", "",),
            ("updated_at", "", "", "", "", "", "",),
            ("youtube_url", "", "", "", "", "", "",),
            ("visibility_status", "", "", "", "", "", "",),
            ("deleted_at", "", "", "", "", "", "",),
            ("download_status", "", "", "", "", "", "",),
        ]

    def test_from_row(self, sample_recording_row, sample_recording_description):
        """Test creating Recording from database row."""
        recording = Recording.from_row(sample_recording_row, sample_recording_description)

        assert recording.content_hash == "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908"
        assert recording.hash_prefix == "c6de4449928d"
        assert recording.tempo_bpm == 128.5
        assert recording.analysis_status == "completed"

    def test_to_dict(self, sample_recording):
        """Test converting Recording to dictionary."""
        data = sample_recording.to_dict()

        assert data["content_hash"] == "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908"
        assert data["tempo_bpm"] == 128.5

    def test_has_analysis_true(self, sample_recording):
        """Test has_analysis property when completed."""
        assert sample_recording.has_analysis is True

    def test_has_analysis_false(self):
        """Test has_analysis property when not completed."""
        recording = Recording(
            content_hash="abc123",
            hash_prefix="abc123",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            analysis_status="pending",
        )

        assert recording.has_analysis is False

    def test_has_lrc_true(self):
        """Test has_lrc property when completed."""
        recording = Recording(
            content_hash="abc123",
            hash_prefix="abc123",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            lrc_status="completed",
        )

        assert recording.has_lrc is True

    def test_has_lrc_false(self, sample_recording):
        """Test has_lrc property when not completed."""
        assert sample_recording.has_lrc is False

    def test_beats_list(self):
        """Test getting beats as a list."""
        recording = Recording(
            content_hash="abc123",
            hash_prefix="abc123",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            beats="[0.23, 0.70, 1.17]",
        )

        assert recording.beats_list == [0.23, 0.70, 1.17]

    def test_formatted_duration(self, sample_recording):
        """Test duration formatting."""
        assert sample_recording.formatted_duration == "4:05"

    def test_formatted_duration_none(self):
        """Test duration formatting when None."""
        recording = Recording(
            content_hash="abc123",
            hash_prefix="abc123",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            duration_seconds=None,
        )

        assert recording.formatted_duration == "--:--"

    def test_from_row_dict_mapping(self, sample_recording_description):
        """Test Recording.from_row uses dict-based mapping, independent of column order."""
        # Create a row with columns in a different order than the dataclass definition
        # but with the same values and description
        row_with_reordered_columns = (
            "completed",  # analysis_status (at position 19 normally, now first)
            "2024-01-15T10:30:00",  # updated_at (at position 24 normally, now second)
            "completed",  # lrc_status (at position 21 normally, now third)
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",  # content_hash (at position 0 normally, now fourth)
            "c6de4449928d",  # hash_prefix
            "song_0001",
            "original.mp3",
            5242880,
            "2024-01-15T10:30:00",
            "s3://bucket/c6de4449928d/audio.mp3",
            "s3://bucket/c6de4449928d/stems/",
            "s3://bucket/c6de4449928d/lyrics.lrc",
            245.3,
            128.5,
            "G",
            "major",
            0.87,
            -8.2,
            "[0.23, 0.70, 1.17]",
            "[0.23, 2.10]",
            '[{"label": "intro", "start": 0.0, "end": 15.2}]',
            "[4, 512, 24]",
            "job_abc123",
            None,
            None,
            "2024-01-15T10:30:00",
            "https://youtube.com/watch?v=x",
            "published",
            None,
            "pending",
        )

        # Create a description that reflects the reordered columns
        reordered_description = [
            ("analysis_status", "", "", "", "", "", "",),
            ("updated_at", "", "", "", "", "", "",),
            ("lrc_status", "", "", "", "", "", "",),
            ("content_hash", "", "", "", "", "", "",),
            ("hash_prefix", "", "", "", "", "", "",),
            ("song_id", "", "", "", "", "", "",),
            ("original_filename", "", "", "", "", "", "",),
            ("file_size_bytes", "", "", "", "", "", "",),
            ("imported_at", "", "", "", "", "", "",),
            ("r2_audio_url", "", "", "", "", "", "",),
            ("r2_stems_url", "", "", "", "", "", "",),
            ("r2_lrc_url", "", "", "", "", "", "",),
            ("duration_seconds", "", "", "", "", "", "",),
            ("tempo_bpm", "", "", "", "", "", "",),
            ("musical_key", "", "", "", "", "", "",),
            ("musical_mode", "", "", "", "", "", "",),
            ("key_confidence", "", "", "", "", "", "",),
            ("loudness_db", "", "", "", "", "", "",),
            ("beats", "", "", "", "", "", "",),
            ("downbeats", "", "", "", "", "", "",),
            ("sections", "", "", "", "", "", "",),
            ("embeddings_shape", "", "", "", "", "", "",),
            ("analysis_job_id", "", "", "", "", "", "",),
            ("lrc_job_id", "", "", "", "", "", "",),
            ("created_at", "", "", "", "", "", "",),
            ("youtube_url", "", "", "", "", "", "",),
            ("visibility_status", "", "", "", "", "", "",),
            ("deleted_at", "", "", "", "", "", "",),
            ("download_status", "", "", "", "", "", "",),
        ]

        # Since dict-based mapping is used, the recording should be created correctly
        # with values mapped by column name, not position
        recording = Recording.from_row(row_with_reordered_columns, reordered_description)

        # Verify that values are correctly mapped despite the reorder
        assert recording.content_hash == "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908"
        assert recording.hash_prefix == "c6de4449928d"
        assert recording.analysis_status == "completed"
        assert recording.lrc_status == "completed"
        assert recording.youtube_url == "https://youtube.com/watch?v/x"


class TestDatabaseStats:
    """Tests for DatabaseStats model."""

    def test_default_values(self):
        """Test default DatabaseStats values."""
        stats = DatabaseStats()

        assert stats.table_counts == {}
        assert stats.integrity_ok is True
        assert stats.foreign_keys_enabled is False

    def test_total_songs(self):
        """Test total_songs property."""
        stats = DatabaseStats(table_counts={"songs": 42, "recordings": 10})

        assert stats.total_songs == 42

    def test_total_recordings(self):
        """Test total_recordings property."""
        stats = DatabaseStats(table_counts={"songs": 42, "recordings": 10})

        assert stats.total_recordings == 10

    def test_missing_tables(self):
        """Test properties when tables don't exist in counts."""
        stats = DatabaseStats(table_counts={})

        assert stats.total_songs == 0
        assert stats.total_recordings == 0
