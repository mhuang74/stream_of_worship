from poc.songset_constructor.catalog import fetch_catalog
from poc.songset_constructor.models import ConstructorConfig


class FakeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return [
            {
                "song_id": "s1",
                "title": "赞美",
                "album_name": "A",
                "album_series": "PW",
                "composer": "C",
                "lyricist": None,
                "song_key": "C",
                "lyrics_raw": "赞美",
                "recording_hash_prefix": "abc123",
                "tempo_bpm": 120,
                "musical_key": "C",
                "musical_mode": "major",
                "key_confidence": 0.9,
            }
        ]


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_obj = cursor

    def cursor(self, row_factory=None):
        return self.cursor_obj


class FakeProvider:
    def __init__(self) -> None:
        self.cursor = FakeCursor()

    def get_connection(self):
        return FakeConnection(self.cursor)


def test_fetch_catalog_excludes_cpw_by_default() -> None:
    provider = FakeProvider()
    result = fetch_catalog(provider, ConstructorConfig(pool_limit=10))

    assert result[0].recording_hash_prefix == "abc123"
    assert "NOT ILIKE" in provider.cursor.query
    assert "%CPW%" in provider.cursor.params


def test_fetch_catalog_requires_lrc_review_or_published_and_active_rows() -> None:
    provider = FakeProvider()
    fetch_catalog(provider, ConstructorConfig(pool_limit=10))

    query = provider.cursor.query
    assert "s.deleted_at IS NULL" in query
    assert "r.deleted_at IS NULL" in query
    assert "r.lrc_status = 'completed'" in query
    assert "r.r2_lrc_url IS NOT NULL" in query
    assert "r.visibility_status IN ('review', 'published')" in query
    assert "r.analysis_status = 'completed'" not in query
