"""Shared fixtures for admin CLI tests.

Provides a Postgres-backed ``setup_db`` fixture that seeds a fresh schema
with one song and writes a config TOML pointing at the testcontainers DB.
This avoids duplicating the helper across test_audio_commands.py,
test_catalog_commands.py, and test_scraper.py.
"""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song


@pytest.fixture
def setup_db(make_test_provider, postgres_url):
    """Create a Postgres schema seeded with one song and return paths.

    Returns a dict with keys:
        - ``config_path``: Path to a TOML config with ``[database].url``.
        - ``song``: The seeded ``Song`` instance.
        - ``db_client``: A ``DatabaseClient`` connected to the test DB.
    """
    provider = make_test_provider()
    client = DatabaseClient(provider)
    client.initialize_schema()

    song = Song(
        id="song_001",
        title="測試歌曲",
        source_url="https://example.com/1",
        scraped_at="2024-01-01T00:00:00",
        composer="測試作曲家",
        album_name="測試專輯",
        musical_key="G",
    )
    client.insert_song(song)

    import tempfile
    from pathlib import Path

    config_path = Path(tempfile.mktemp(suffix=".toml"))
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')

    yield {"config_path": config_path, "song": song, "db_client": client}

    # Cleanup: drop all tables
    try:
        client.connection_provider.invalidate()
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass
    finally:
        config_path.unlink(missing_ok=True)
