"""Tests for ``ReadOnlyClient.list_active_recordings_by_song_id``.

Uses the ``FakeCursor`` / ``FakeConnection`` / ``FakeProvider`` pattern from
``test_audio_soft_delete_maintenance.py``.
"""

from stream_of_worship.db.app.read_client import ReadOnlyClient


class FakeCursor:
    def __init__(self, fetchall_rows=None, fetchone_rows=None):
        self._fetchall_rows = list(fetchall_rows or [])
        self._fetchone_rows = list(fetchone_rows or [])
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._fetchall_rows

    def fetchone(self):
        return self._fetchone_rows.pop(0) if self._fetchone_rows else None


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class FakeProvider:
    def __init__(self, connection):
        self.connection = connection

    def get_connection(self):
        return self.connection

    def invalidate(self):
        pass

    def close(self):
        pass


def _recording_row(hash_prefix: str, song_id: str, imported_at: str) -> tuple:
    """Build a 34-column recording row tuple for the new schema."""
    return (
        "a" * 64,              # content_hash
        hash_prefix,           # hash_prefix
        song_id,               # song_id
        "song.mp3",            # original_filename
        100,                   # file_size_bytes
        imported_at,           # imported_at
        "s3://bucket/audio",   # r2_audio_url
        None,                  # r2_stems_url
        None,                  # r2_lrc_url
        240.0,                 # duration_seconds
        72.0,                  # tempo_bpm
        "G",                   # musical_key
        "major",               # musical_mode
        0.8,                   # key_confidence
        "ks_segment_vote_v1",  # key_algorithm_version
        0.1,                   # key_score_margin
        0.75,                  # key_window_agreement
        '[{"key": "G"}]',      # key_candidates
        "2024-01-01T00:00:00", # key_detected_at
        -8.2,                  # loudness_db
        "[0.1]",               # beats
        "[0.1]",               # downbeats
        "[]",                  # sections
        "[4, 512, 24]",        # embeddings_shape
        "completed",           # analysis_status
        "analysis_1",          # analysis_job_id
        "completed",           # lrc_status
        "lrc_1",               # lrc_job_id
        "2024-01-01T00:00:00", # created_at
        "2024-01-02T00:00:00", # updated_at
        "https://youtu.be/x", # youtube_url
        "published",           # visibility_status
        "completed",           # download_status
        None,                  # deleted_at
    )


def test_list_active_recordings_orders_by_imported_at_desc():
    # FakeCursor returns rows as-is; the SQL ORDER BY is verified separately.
    # Data is pre-sorted DESC to simulate what the DB would return.
    rows = [
        _recording_row("hash000000003", "song_1", "2024-03-01T00:00:00"),
        _recording_row("hash000000002", "song_1", "2024-02-01T00:00:00"),
        _recording_row("hash000000001", "song_1", "2024-01-01T00:00:00"),
    ]
    cursor = FakeCursor(fetchall_rows=rows)
    client = ReadOnlyClient(FakeProvider(FakeConnection(cursor)))

    recordings = client.list_active_recordings_by_song_id("song_1")

    assert len(recordings) == 3
    assert recordings[0].hash_prefix == "hash000000003"
    assert recordings[1].hash_prefix == "hash000000002"
    assert recordings[2].hash_prefix == "hash000000001"

    sql = cursor.executed[0][0]
    assert "ORDER BY imported_at DESC" in sql
    assert "deleted_at IS NULL" in sql


def test_list_active_recordings_excludes_soft_deleted():
    rows = [_recording_row("hash000000001", "song_1", "2024-01-01T00:00:00")]
    cursor = FakeCursor(fetchall_rows=rows)
    client = ReadOnlyClient(FakeProvider(FakeConnection(cursor)))

    recordings = client.list_active_recordings_by_song_id("song_1", include_deleted=False)

    assert len(recordings) == 1
    sql = cursor.executed[0][0]
    assert "deleted_at IS NULL" in sql


def test_list_active_recordings_include_deleted_true():
    rows = [
        _recording_row("hash000000001", "song_1", "2024-01-01T00:00:00"),
        _recording_row("hash000000002", "song_1", "2024-02-01T00:00:00"),
    ]
    cursor = FakeCursor(fetchall_rows=rows)
    client = ReadOnlyClient(FakeProvider(FakeConnection(cursor)))

    recordings = client.list_active_recordings_by_song_id("song_1", include_deleted=True)

    assert len(recordings) == 2
    sql = cursor.executed[0][0]
    assert "deleted_at" not in sql.split("WHERE")[1].split("ORDER")[0]


def test_list_active_recordings_empty():
    cursor = FakeCursor(fetchall_rows=[])
    client = ReadOnlyClient(FakeProvider(FakeConnection(cursor)))

    recordings = client.list_active_recordings_by_song_id("song_nonexistent")

    assert recordings == []
