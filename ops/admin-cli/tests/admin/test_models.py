"""Tests for sow-admin database models."""

import pytest

from stream_of_worship.admin.db.models import DatabaseStats, Recording, Song
from stream_of_worship.admin.db.schema import (
    RECORDING_COLUMNS_SELECT,
    SONG_COLUMNS_SELECT,
)


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

    def test_from_row(self, sample_song_row):
        """Test creating Song from database row."""
        song = Song.from_row(sample_song_row)

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

    def test_from_row_24_column_canonical_order(self):
        """Regression test: from_row with 24-column row in canonical order
        (matching SONG_COLUMNS_SELECT, not physical DB order).

        Before the fix, SELECT * returned physical DB order (new columns
        appended at end by ALTER TABLE), causing from_row to map
        lyrics_raw to updated_at (datetime) and crash on .strip().
        """
        row = (
            "song_0001",  # id
            "將天敞開",  # title
            "jiang_tian_chang_kai",  # title_pinyin
            "作曲家",  # composer
            "作詞家",  # lyricist
            "敬拜讚美15",  # album_name
            "敬拜讚美系列",  # album_series
            "G",  # musical_key
            "G",  # musical_key_root
            "major",  # musical_key_mode
            "G",  # musical_key_start_root
            "G",  # musical_key_end_root
            7,  # musical_key_start_pitch_class
            7,  # musical_key_end_pitch_class
            "parsed",  # musical_key_parse_status
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
        assert len(row) == 24
        song = Song.from_row(row)

        assert song.id == "song_0001"
        assert song.title == "將天敞開"
        assert song.musical_key == "G"
        assert song.musical_key_root == "G"
        assert song.musical_key_mode == "major"
        assert song.musical_key_start_pitch_class == 7
        assert song.musical_key_parse_status == "parsed"
        assert song.lyrics_raw == "第一行歌詞\n第二行歌詞"
        assert song.source_url == "https://sop.org/song/123"
        assert song.scraped_at == "2024-01-15T10:30:00"
        assert song.lyrics_list == ["第一行歌詞", "第二行歌詞"]

    def test_from_row_physical_order_mismatch_regression(self):
        """Regression test: a row in PHYSICAL DB order (16 original cols
        + 8 appended at end) must NOT be passed to from_row expecting
        canonical order. This test documents why SELECT * was replaced
        with explicit column lists.

        With the old SELECT *, the physical-order row would be returned
        and from_row would map lyrics_raw=row[15]=updated_at (datetime),
        crashing on .strip(). Now that queries use SONG_COLUMNS_SELECT,
        rows are always in canonical order.
        """
        # Canonical-order row (what SONG_COLUMNS_SELECT produces)
        canonical_row = (
            "song_0001",
            "將天敞開",
            "jiang_tian_chang_kai",
            "作曲家",
            "作詞家",
            "敬拜讚美15",
            "敬拜讚美系列",
            "G",
            "G",
            "major",
            "G",
            "G",
            7,
            7,
            "parsed",
            "第一行歌詞\n第二行歌詞",
            '["第一行歌詞", "第二行歌詞"]',
            '[{"label": "verse"}]',
            "https://sop.org/song/123",
            123,
            "2024-01-15T10:30:00",
            "2024-01-15T10:30:00",
            "2024-01-15T10:30:00",
            None,
        )
        song = Song.from_row(canonical_row)
        assert song.lyrics_raw == "第一行歌詞\n第二行歌詞"
        assert song.source_url == "https://sop.org/song/123"
        assert song.musical_key_root == "G"

    def test_song_columns_select_order_matches_from_row(self):
        """Verify SONG_COLUMNS_SELECT column count and order matches
        what from_row expects (24 columns in canonical order).
        """
        columns = [c.strip() for c in SONG_COLUMNS_SELECT.split(",") if c.strip()]
        assert len(columns) == 24
        assert columns[0] == "id"
        assert columns[7] == "musical_key"
        assert columns[8] == "musical_key_root"
        assert columns[15] == "lyrics_raw"
        assert columns[18] == "source_url"
        assert columns[23] == "deleted_at"


class TestRecording:
    """Tests for Recording model."""

    @pytest.fixture
    def sample_recording_row(self):
        """Return a sample database row tuple for a recording."""
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

    def test_from_row(self, sample_recording_row):
        """Test creating Recording from database row."""
        recording = Recording.from_row(sample_recording_row)

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

    def test_recording_from_row_28_columns(self):
        """Test Recording.from_row with 28-column schema."""
        row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            "c6de4449928d",
            "song_0001",
            "original.mp3",
            5242880,
            "2024-01-15T10:30:00",
            None,
            None,
            None,
            245.3,
            128.5,
            "G",
            "major",
            0.87,
            -8.2,
            None,
            None,
            None,
            None,
            "completed",
            None,
            "completed",
            None,
            "2024-01-01",
            "2024-01-02",
            "https://youtube.com/watch?v=x",
            "published",
            "2026-01-01",
        )
        recording = Recording.from_row(row)

        assert recording.youtube_url == "https://youtube.com/watch?v=x"
        assert recording.visibility_status == "published"
        assert recording.deleted_at == "2026-01-01"

    def test_recording_from_row_27_columns(self):
        """Test Recording.from_row with 27-column schema."""
        row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            "c6de4449928d",
            "song_0001",
            "original.mp3",
            5242880,
            "2024-01-15T10:30:00",
            None,
            None,
            None,
            245.3,
            128.5,
            "G",
            "major",
            0.87,
            -8.2,
            None,
            None,
            None,
            None,
            "completed",
            None,
            "completed",
            None,
            "2024-01-01",
            "2024-01-02",
            "https://youtube.com/watch?v=x",
            "published",
        )
        recording = Recording.from_row(row)

        assert recording.youtube_url == "https://youtube.com/watch?v=x"
        assert recording.visibility_status == "published"
        assert recording.deleted_at is None

    def test_recording_from_row_26_columns(self):
        """Test Recording.from_row with 26-column schema."""
        row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            "c6de4449928d",
            "song_0001",
            "original.mp3",
            5242880,
            "2024-01-15T10:30:00",
            None,
            None,
            None,
            245.3,
            128.5,
            "G",
            "major",
            0.87,
            -8.2,
            None,
            None,
            None,
            None,
            "completed",
            None,
            "completed",
            None,
            "2024-01-01",
            "2024-01-02",
            "https://youtube.com/watch?v=x",
        )
        recording = Recording.from_row(row)

        assert recording.youtube_url == "https://youtube.com/watch?v=x"
        assert recording.visibility_status is None
        assert recording.deleted_at is None

    def test_recording_from_row_25_columns(self):
        """Test Recording.from_row with 25-column schema."""
        row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            "c6de4449928d",
            "song_0001",
            "original.mp3",
            5242880,
            "2024-01-15T10:30:00",
            None,
            None,
            None,
            245.3,
            128.5,
            "G",
            "major",
            0.87,
            -8.2,
            None,
            None,
            None,
            None,
            "completed",
            None,
            "completed",
            None,
            "2024-01-01",
            "2024-01-02",
        )
        recording = Recording.from_row(row)

        assert recording.youtube_url is None
        assert recording.visibility_status is None
        assert recording.deleted_at is None

    def test_from_row_34_column_canonical_order(self):
        """Regression test: from_row with 34-column row in canonical order
        (matching RECORDING_COLUMNS_SELECT, not physical DB order).

        Before the fix, SELECT * returned physical DB order (key_* columns
        appended at end by ALTER TABLE), causing from_row to map
        key_algorithm_version to loudness_db, etc.
        """
        row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",  # content_hash
            "c6de4449928d",  # hash_prefix
            "song_0001",  # song_id
            "original.mp3",  # original_filename
            5242880,  # file_size_bytes
            "2024-01-15T10:30:00",  # imported_at
            "s3://bucket/audio.mp3",  # r2_audio_url
            "s3://bucket/stems/",  # r2_stems_url
            "s3://bucket/lyrics.lrc",  # r2_lrc_url
            245.3,  # duration_seconds
            128.5,  # tempo_bpm
            "G",  # musical_key
            "major",  # musical_mode
            0.87,  # key_confidence
            "v2",  # key_algorithm_version
            0.5,  # key_score_margin
            0.9,  # key_window_agreement
            '["G","D"]',  # key_candidates
            "2024-01-15T10:30:00",  # key_detected_at
            -8.2,  # loudness_db
            "[0.23, 0.70]",  # beats
            "[0.23, 2.10]",  # downbeats
            '[{"label": "intro"}]',  # sections
            "[4, 512, 24]",  # embeddings_shape
            "completed",  # analysis_status
            "job_abc123",  # analysis_job_id
            "completed",  # lrc_status
            "job_lrc123",  # lrc_job_id
            "2024-01-15T10:30:00",  # created_at
            "2024-01-15T10:30:00",  # updated_at
            "https://youtube.com/watch?v=x",  # youtube_url
            "published",  # visibility_status
            "completed",  # download_status
            None,  # deleted_at
        )
        assert len(row) == 34
        recording = Recording.from_row(row)

        assert recording.content_hash == "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908"
        assert recording.key_algorithm_version == "v2"
        assert recording.key_score_margin == 0.5
        assert recording.key_window_agreement == 0.9
        assert recording.key_candidates == '["G","D"]'
        assert recording.loudness_db == -8.2
        assert recording.beats == "[0.23, 0.70]"
        assert recording.analysis_status == "completed"
        assert recording.youtube_url == "https://youtube.com/watch?v=x"
        assert recording.visibility_status == "published"
        assert recording.download_status == "completed"
        assert recording.deleted_at is None

    def test_recording_columns_select_order_matches_from_row(self):
        """Verify RECORDING_COLUMNS_SELECT column count and order matches
        what from_row expects (34 columns in canonical order).
        """
        columns = [c.strip() for c in RECORDING_COLUMNS_SELECT.split(",") if c.strip()]
        assert len(columns) == 34
        assert columns[0] == "content_hash"
        assert columns[13] == "key_confidence"
        assert columns[14] == "key_algorithm_version"
        assert columns[18] == "key_detected_at"
        assert columns[19] == "loudness_db"
        assert columns[33] == "deleted_at"

    def test_from_row_physical_order_mismatch_regression(self):
        """Regression test: a row in PHYSICAL DB order (key_* columns appended
        at end by ALTER TABLE, positions 29-33) must NOT be passed to from_row
        expecting canonical order. This documents why SELECT r.* was replaced
        with RECORDING_COLUMNS_FOR_JOIN in list_recordings_with_songs and
        related queries.

        In physical order, visibility_status sits at index 26 and
        key_window_agreement at index 31. from_row's 34-column branch reads
        row[31] for visibility_status — which in physical order is
        key_window_agreement (NULL for recordings not re-analyzed with key
        v2) → parsed as None → rendered as "- none".

        This test constructs a physical-order row and asserts that from_row
        does NOT silently mis-map visibility_status to key_window_agreement.
        Callers must supply canonical-order rows via
        RECORDING_COLUMNS_SELECT / RECORDING_COLUMNS_FOR_JOIN, not r.*.
        """
        # Physical DB order (34 cols): key_* columns at positions 29-33,
        # visibility_status at index 26, loudness_db at index 14.
        physical_row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",  # 0  content_hash
            "c6de4449928d",  # 1  hash_prefix
            "song_0001",  # 2  song_id
            "original.mp3",  # 3  original_filename
            5242880,  # 4  file_size_bytes
            "2024-01-15T10:30:00",  # 5  imported_at
            "s3://bucket/audio.mp3",  # 6  r2_audio_url
            "s3://bucket/stems/",  # 7  r2_stems_url
            "s3://bucket/lyrics.lrc",  # 8  r2_lrc_url
            245.3,  # 9  duration_seconds
            128.5,  # 10 tempo_bpm
            "G",  # 11 musical_key
            "major",  # 12 musical_mode
            0.87,  # 13 key_confidence
            -8.2,  # 14 loudness_db
            "[0.23, 0.70]",  # 15 beats
            "[0.23, 2.10]",  # 16 downbeats
            '[{"label": "intro"}]',  # 17 sections
            "[4, 512, 24]",  # 18 embeddings_shape
            "completed",  # 19 analysis_status
            "job_abc123",  # 20 analysis_job_id
            "completed",  # 21 lrc_status
            "job_lrc123",  # 22 lrc_job_id
            "2024-01-15T10:30:00",  # 23 created_at
            "2024-01-15T10:30:00",  # 24 updated_at
            "https://youtube.com/watch?v=x",  # 25 youtube_url
            "review",  # 26 visibility_status
            "completed",  # 27 download_status
            None,  # 28 deleted_at
            None,  # 29 key_algorithm_version
            None,  # 30 key_score_margin
            None,  # 31 key_window_agreement
            None,  # 32 key_candidates
            None,  # 33 key_detected_at
        )
        assert len(physical_row) == 34

        # from_row expects canonical order; feeding physical order is a
        # caller bug. The canonical-order row below is what
        # RECORDING_COLUMNS_FOR_JOIN produces and is the correct input.
        canonical_row = (
            "c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",  # 0  content_hash
            "c6de4449928d",  # 1  hash_prefix
            "song_0001",  # 2  song_id
            "original.mp3",  # 3  original_filename
            5242880,  # 4  file_size_bytes
            "2024-01-15T10:30:00",  # 5  imported_at
            "s3://bucket/audio.mp3",  # 6  r2_audio_url
            "s3://bucket/stems/",  # 7  r2_stems_url
            "s3://bucket/lyrics.lrc",  # 8  r2_lrc_url
            245.3,  # 9  duration_seconds
            128.5,  # 10 tempo_bpm
            "G",  # 11 musical_key
            "major",  # 12 musical_mode
            0.87,  # 13 key_confidence
            None,  # 14 key_algorithm_version
            None,  # 15 key_score_margin
            None,  # 16 key_window_agreement
            None,  # 17 key_candidates
            None,  # 18 key_detected_at
            -8.2,  # 19 loudness_db
            "[0.23, 0.70]",  # 20 beats
            "[0.23, 2.10]",  # 21 downbeats
            '[{"label": "intro"}]',  # 22 sections
            "[4, 512, 24]",  # 23 embeddings_shape
            "completed",  # 24 analysis_status
            "job_abc123",  # 25 analysis_job_id
            "completed",  # 26 lrc_status
            "job_lrc123",  # 27 lrc_job_id
            "2024-01-15T10:30:00",  # 28 created_at
            "2024-01-15T10:30:00",  # 29 updated_at
            "https://youtube.com/watch?v=x",  # 30 youtube_url
            "review",  # 31 visibility_status
            "completed",  # 32 download_status
            None,  # 33 deleted_at
        )
        assert len(canonical_row) == 34

        recording = Recording.from_row(canonical_row)
        assert recording.visibility_status == "review"
        assert recording.key_window_agreement is None
        assert recording.loudness_db == -8.2

        # The physical-order row would mis-map: row[31] is key_window_agreement
        # (None), not visibility_status ("review"). This assertion documents
        # the mismatch that motivated replacing SELECT r.* with explicit
        # canonical column lists.
        assert physical_row[26] == "review"  # visibility_status in physical order
        assert physical_row[31] is None  # key_window_agreement in physical order
        assert canonical_row[31] == "review"  # visibility_status in canonical order


class TestDatabaseStats:
    """Tests for DatabaseStats model."""

    def test_default_values(self):
        """Test default DatabaseStats values."""
        stats = DatabaseStats()

        assert stats.table_counts == {}
        assert stats.is_healthy is True
        assert stats.sync_version == "3"

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
