"""Tests for migrate commands."""

import sqlite3

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands import migrate as migrate_commands


@pytest.fixture
def temp_db_with_old_song_ids(tmp_path):
    """Create temporary database with old-format song IDs."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)

    # Create admin tables
    conn.execute("""
        CREATE TABLE songs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            composer TEXT,
            lyricist TEXT,
            source_url TEXT NOT NULL,
            scraped_at TEXT NOT NULL,
            table_row_number INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE recordings (
            content_hash TEXT PRIMARY KEY,
            hash_prefix TEXT NOT NULL,
            song_id TEXT,
            original_filename TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Insert old-format songs
    songs_data = [
        (
            "song_0001",
            "Test Song 1",
            "Composer 1",
            "Lyricist 1",
            "http://test1.com",
            "2024-01-01",
            1,
        ),
        (
            "song_0002",
            "Test Song 2",
            "Composer 2",
            "Lyricist 2",
            "http://test2.com",
            "2024-01-01",
            2,
        ),
    ]
    conn.executemany(
        """INSERT INTO songs (id, title, composer, lyricist, source_url, scraped_at, table_row_number)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        songs_data,
    )

    # Insert recordings referencing old IDs
    recordings_data = [
        ("hash1" * 8, "abc123def456", "song_0001", "test1.mp3", 1000, "2024-01-01"),
        ("hash2" * 8, "def456ghi789", "song_0002", "test2.mp3", 1000, "2024-01-01"),
    ]
    conn.executemany(
        """INSERT INTO recordings (content_hash, hash_prefix, song_id, original_filename, file_size_bytes, imported_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        recordings_data,
    )
    conn.commit()
    conn.close()

    # Create a config file
    config_path = tmp_path / "config.toml"
    config_path.write_text(f"""
[service]
analysis_url = "http://localhost:8000"

[database]
path = "{db_path}"

[r2]
bucket = "test-bucket"
endpoint_url = ""
region = "auto"

[turso]
database_url = ""
""")

    return tmp_path


class TestMigrateSongIdsCommand:
    """Tests for migrate-song-ids command."""

    def test_migrate_song_ids_dry_run(self, temp_db_with_old_song_ids):
        """Test dry-run shows mappings without changes."""
        runner = CliRunner()
        result = runner.invoke(
            migrate_commands.app,
            ["song-ids", "--config", str(temp_db_with_old_song_ids), "--dry-run"],
        )

        assert result.exit_code == 0
        assert "Dry run" in result.output

    def test_migrate_song_ids_without_config(self, tmp_path):
        """Test command fails without config."""
        runner = CliRunner()
        result = runner.invoke(
            migrate_commands.app, ["song-ids", "--config", str(tmp_path / "nonexistent")]
        )

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_migrate_song_ids_idempotent(self, temp_db_with_old_song_ids):
        """Test running migration twice is idempotent."""
        runner = CliRunner()

        # First migration
        result1 = runner.invoke(
            migrate_commands.app, ["song-ids", "--config", str(temp_db_with_old_song_ids)]
        )
        assert result1.exit_code == 0
        # After first migration, IDs should be in new format

        # Second migration
        result2 = runner.invoke(
            migrate_commands.app, ["song-ids", "--config", str(temp_db_with_old_song_ids)]
        )
        assert result2.exit_code == 0
        assert "No migration needed" in result2.output

    def test_migrate_song_ids_with_empty_database(self, tmp_path):
        """Test command with empty songs table."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE songs (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE recordings (content_hash TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        # Create a config file
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[service]
analysis_url = "http://localhost:8000"

[database]
path = "{db_path}"

[r2]
bucket = "test-bucket"
endpoint_url = ""
region = "auto"

[turso]
database_url = ""
""")

        runner = CliRunner()
        result = runner.invoke(migrate_commands.app, ["song-ids", "--config", str(tmp_path)])

        assert result.exit_code == 0
        assert "No songs found" in result.output
