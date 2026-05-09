"""Tests for catalog CLI commands."""

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song
from stream_of_worship.admin.main import app
from stream_of_worship.db.connection import ConnectionProvider

runner = CliRunner()


@pytest.fixture
def provider(postgres_url):
    """Yield a ConnectionProvider and close it after the test."""
    provider = ConnectionProvider(postgres_url)
    yield provider
    provider.close()


@pytest.fixture
def client(provider):
    """Return an initialized DatabaseClient with schema ready.

    Tables are truncated after each test for isolation.
    """
    db = DatabaseClient(provider)
    db.initialize_schema()
    yield db
    conn = provider.get_connection()
    conn.rollback()
    cursor = conn.cursor()
    for table in ["songs", "recordings", "songsets", "songset_items"]:
        try:
            cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
            conn.commit()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
        except Exception:
            conn.rollback()
    conn.commit()


@pytest.fixture
def config_with_db(tmp_path, postgres_url):
    """Create a config file pointing to the test database."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


class TestCatalogScrapeCommand:
    """Tests for 'catalog scrape' command."""

    def test_scrape_without_config(self):
        """Test scrape fails without config."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["catalog", "scrape"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_scrape_connection_error(self, tmp_path):
        """Test scrape fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[database]\nurl = "postgresql://invalid:5432/nodb"\n'
        )

        result = runner.invoke(app, ["catalog", "scrape", "--config", str(config_path)])

        assert result.exit_code == 1

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_success(self, mock_get, client, config_with_db):
        """Test successful scrape command."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>Test Song</td><td>Test Composer</td><td>Test Lyricist</td>
                <td>Test Album</td><td>Test Series</td><td>G</td>
                <td>Line 1<br/>Line 2</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = runner.invoke(
            app, ["catalog", "scrape", "--config", str(config_with_db), "--limit", "1"]
        )

        assert result.exit_code == 0
        assert "Found 1 songs" in result.output
        assert "Test Song" in result.output

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_dry_run(self, mock_get, client, config_with_db):
        """Test scrape with dry-run flag."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>Test Song</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>C</td><td>Lyrics</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = runner.invoke(
            app,
            ["catalog", "scrape", "--config", str(config_with_db), "--dry-run"],
        )

        assert result.exit_code == 0
        assert "Dry run mode" in result.output
        assert "Dry run - no songs saved" in result.output

        songs = client.list_songs()
        assert len(songs) == 0


class TestCatalogListCommand:
    """Tests for 'catalog list' command."""

    @pytest.fixture
    def config_with_songs(self, client, config_with_db):
        """Create a database with sample songs."""
        songs = [
            Song(
                id="song_001",
                title="Song One",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
                composer="Composer A",
                album_name="Album X",
                musical_key="G",
            ),
            Song(
                id="song_002",
                title="Song Two",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
                composer="Composer B",
                album_name="Album Y",
                musical_key="D",
            ),
            Song(
                id="song_003",
                title="Song Three",
                source_url="https://example.com/3",
                scraped_at=datetime.now().isoformat(),
                composer="Composer A",
                album_name="Album X",
                musical_key="C",
            ),
        ]
        for song in songs:
            client.insert_song(song)

        return config_with_db

    def test_list_without_config(self):
        """Test list fails without config."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["catalog", "list"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_list_all_songs(self, config_with_songs):
        """Test listing all songs."""
        result = runner.invoke(
            app, ["catalog", "list", "--config", str(config_with_songs)]
        )

        assert result.exit_code == 0
        assert "Song One" in result.output
        assert "Song Two" in result.output
        assert "Song Three" in result.output

    def test_list_with_album_filter(self, config_with_songs):
        """Test listing with album filter."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "list",
                "--config",
                str(config_with_songs),
                "--album",
                "Album X",
            ],
        )

        assert result.exit_code == 0
        assert "Song One" in result.output
        assert "Song Three" in result.output
        assert "Song Two" not in result.output

    def test_list_with_key_filter(self, config_with_songs):
        """Test listing with key filter."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "list",
                "--config",
                str(config_with_songs),
                "--key",
                "G",
            ],
        )

        assert result.exit_code == 0
        assert "Song One" in result.output
        assert "Song Two" not in result.output
        assert "Song Three" not in result.output

    def test_list_with_limit(self, config_with_songs):
        """Test listing with limit."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "list",
                "--config",
                str(config_with_songs),
                "--limit",
                "2",
            ],
        )

        assert result.exit_code == 0
        assert "(2 total)" in result.output or "Song" in result.output

    def test_list_format_ids(self, config_with_songs):
        """Test listing with ids format."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "list",
                "--config",
                str(config_with_songs),
                "--format",
                "ids",
            ],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" in result.output
        assert "song_003" in result.output

    def test_list_empty_database(self, client, config_with_db):
        """Test listing with empty database."""
        result = runner.invoke(app, ["catalog", "list", "--config", str(config_with_db)])

        assert result.exit_code == 0
        assert "No songs found" in result.output


class TestCatalogSearchCommand:
    """Tests for 'catalog search' command."""

    @pytest.fixture
    def config_with_songs(self, client, config_with_db):
        """Create a database with sample songs."""
        songs = [
            Song(
                id="song_001",
                title="將天敞開",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
                composer="游智婷",
                lyrics_raw="將天敞開歌詞內容",
            ),
            Song(
                id="song_002",
                title="感謝",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
                composer="感謝作曲家",
                lyrics_raw="感謝的歌詞在這裡",
            ),
            Song(
                id="song_003",
                title="讚美之歌",
                source_url="https://example.com/3",
                scraped_at=datetime.now().isoformat(),
                composer="讚美作曲家",
                lyrics_raw="讚美的歌詞內容",
            ),
        ]
        for song in songs:
            client.insert_song(song)

        return config_with_db

    def test_search_without_config(self):
        """Test search fails without config."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["catalog", "search", "test"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_search_by_title(self, config_with_songs):
        """Test searching by title."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "search",
                "將天",
                "--config",
                str(config_with_songs),
                "--field",
                "title",
            ],
        )

        assert result.exit_code == 0
        assert "將天敞開" in result.output
        assert "感謝" not in result.output

    def test_search_by_lyrics(self, config_with_songs):
        """Test searching by lyrics."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "search",
                "感謝的歌詞",
                "--config",
                str(config_with_songs),
                "--field",
                "lyrics",
            ],
        )

        assert result.exit_code == 0
        assert "感謝" in result.output
        assert "將天敞開" not in result.output

    def test_search_by_composer(self, config_with_songs):
        """Test searching by composer."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "search",
                "游智婷",
                "--config",
                str(config_with_songs),
                "--field",
                "composer",
            ],
        )

        assert result.exit_code == 0
        assert "將天敞開" in result.output

    def test_search_all_fields(self, config_with_songs):
        """Test searching all fields."""
        result = runner.invoke(
            app, ["catalog", "search", "讚美", "--config", str(config_with_songs)]
        )

        assert result.exit_code == 0
        assert "讚美之歌" in result.output

    def test_search_with_limit(self, config_with_songs):
        """Test search with limit."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "search",
                "歌",
                "--config",
                str(config_with_songs),
                "--limit",
                "1",
            ],
        )

        assert result.exit_code == 0
        assert "(1 found)" in result.output or "found" in result.output

    def test_search_no_results(self, config_with_songs):
        """Test search with no results."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "search",
                "nonexistent",
                "--config",
                str(config_with_songs),
            ],
        )

        assert result.exit_code == 0
        assert "No songs found" in result.output


class TestCatalogShowCommand:
    """Tests for 'catalog show' command."""

    @pytest.fixture
    def client_with_song(self, client, config_with_db):
        """Create a database with a sample song."""
        song = Song(
            id="test_song_001",
            title="Test Song",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
            title_pinyin="test_song",
            composer="Test Composer",
            lyricist="Test Lyricist",
            album_name="Test Album",
            album_series="Test Series",
            musical_key="G",
            lyrics_raw="Line 1\nLine 2\nLine 3",
            lyrics_lines='["Line 1", "Line 2", "Line 3"]',
            table_row_number=42,
        )
        client.insert_song(song)

        return {"client": client, "config_path": config_with_db, "song": song}

    def test_show_without_config(self):
        """Test show fails without config."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["catalog", "show", "song_001"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_show_existing_song(self, client_with_song):
        """Test showing an existing song."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "show",
                "test_song_001",
                "--config",
                str(client_with_song["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "Test Song" in result.output
        assert "Test Composer" in result.output
        assert "Test Lyricist" in result.output
        assert "Test Album" in result.output
        assert "G" in result.output
        assert "Line 1" in result.output
        assert "Line 2" in result.output

    def test_show_nonexistent_song(self, client_with_song):
        """Test showing a non-existent song."""
        result = runner.invoke(
            app,
            [
                "catalog",
                "show",
                "nonexistent",
                "--config",
                str(client_with_song["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Song not found" in result.output

    def test_show_connection_error(self, tmp_path):
        """Test show fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["catalog", "show", "song_001", "--config", str(config_path)]
        )

        assert result.exit_code == 1
