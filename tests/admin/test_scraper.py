"""Tests for catalog scraper service."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song
from stream_of_worship.admin.services.scraper import CatalogScraper


class TestCatalogScraper:
    """Tests for CatalogScraper class."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary database with schema."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()
        return client

    @pytest.fixture
    def scraper(self, temp_db):
        """Create a scraper with database client."""
        return CatalogScraper(db_client=temp_db)

    @pytest.fixture
    def scraper_no_db(self):
        """Create a scraper without database client."""
        return CatalogScraper(db_client=None)

    def test_init_with_db(self, temp_db):
        """Test initialization with database client."""
        scraper = CatalogScraper(db_client=temp_db)
        assert scraper.db_client == temp_db
        assert scraper.url == "https://www.sop.org/songs/"

    def test_init_without_db(self):
        """Test initialization without database client."""
        scraper = CatalogScraper(db_client=None)
        assert scraper.db_client is None
        assert scraper.url == "https://www.sop.org/songs/"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_all_songs(self, mock_get, scraper_no_db):
        """Test scraping songs from HTML table."""
        html_content = """
        <table id="tablepress-3">
            <tr>
                <th>曲名</th>
                <th>作曲</th>
                <th>作詞</th>
                <th>專輯名稱</th>
                <th>專輯系列</th>
                <th>調性</th>
                <th>歌詞</th>
            </tr>
            <tr>
                <td>Test Song</td>
                <td>Test Composer</td>
                <td>Test Lyricist</td>
                <td>Test Album</td>
                <td>Test Series</td>
                <td>G</td>
                <td>Line 1<br/>Line 2<br/>Line 3</td>
            </tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        songs = scraper_no_db.scrape_all_songs()

        assert len(songs) == 1
        song = songs[0]
        assert song.title == "Test Song"
        assert song.composer == "Test Composer"
        assert song.lyricist == "Test Lyricist"
        assert song.album_name == "Test Album"
        assert song.album_series == "Test Series"
        assert song.musical_key == "G"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_with_limit(self, mock_get, scraper_no_db):
        """Test scraping with limit."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>Song 1</td><td>Comp 1</td><td>Lyric 1</td>
                <td>Album 1</td><td>Series</td><td>C</td><td>Lyrics 1</td></tr>
            <tr><td>Song 2</td><td>Comp 2</td><td>Lyric 2</td>
                <td>Album 2</td><td>Series</td><td>D</td><td>Lyrics 2</td></tr>
            <tr><td>Song 3</td><td>Comp 3</td><td>Lyric 3</td>
                <td>Album 3</td><td>Series</td><td>E</td><td>Lyrics 3</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        songs = scraper_no_db.scrape_all_songs(limit=2)

        assert len(songs) == 2
        assert songs[0].title == "Song 1"
        assert songs[1].title == "Song 2"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_parses_lyrics(self, mock_get, scraper_no_db):
        """Test that lyrics are parsed correctly."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>Song</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>G</td>
                <td>First line<br/>Second line<br/>Third line</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        songs = scraper_no_db.scrape_all_songs()

        assert len(songs) == 1
        song = songs[0]
        lyrics_list = song.lyrics_list
        assert len(lyrics_list) == 3
        assert lyrics_list[0] == "First line"
        assert lyrics_list[1] == "Second line"
        assert lyrics_list[2] == "Third line"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_generates_song_id(self, mock_get, scraper_no_db):
        """Test that song IDs are generated correctly."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>將天敞開</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>G</td><td>Lyrics</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        songs = scraper_no_db.scrape_all_songs()

        assert len(songs) == 1
        assert "jiang_tian_chang_kai" in songs[0].id

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_request_error(self, mock_get, scraper_no_db):
        """Test handling of request errors."""
        from requests.exceptions import RequestException

        mock_get.side_effect = RequestException("Connection error")

        with pytest.raises(RequestException):
            scraper_no_db.scrape_all_songs()

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_table_not_found(self, mock_get, scraper_no_db):
        """Test error when table is not found."""
        html_content = "<html><body>No table here</body></html>"
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="Table 'tablepress-3' not found"):
            scraper_no_db.scrape_all_songs()


class TestCatalogScraperWithDatabase:
    """Tests for CatalogScraper database operations."""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary database with schema."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()
        return client

    @pytest.fixture
    def scraper(self, temp_db):
        """Create a scraper with database client."""
        return CatalogScraper(db_client=temp_db)

    @pytest.fixture
    def sample_song(self):
        """Return a sample song."""
        return Song(
            id="test_song_001",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
            composer="Test Composer",
            album_name="Test Album",
            musical_key="G",
        )

    def test_save_songs(self, scraper, temp_db, sample_song):
        """Test saving songs to database."""
        songs = [sample_song]
        count = scraper.save_songs(songs)

        assert count == 1

        # Verify song was saved
        retrieved = temp_db.get_song("test_song_001")
        assert retrieved is not None
        assert retrieved.title == "Test Song"

    def test_save_songs_empty_list(self, scraper):
        """Test saving empty list."""
        count = scraper.save_songs([])
        assert count == 0

    def test_save_songs_without_db(self, sample_song):
        """Test saving without database client."""
        scraper_no_db = CatalogScraper(db_client=None)
        count = scraper_no_db.save_songs([sample_song])
        assert count == 0

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_with_incremental(self, mock_get, temp_db):
        """Test incremental scraping skips existing songs."""
        # Insert an existing song
        existing_song = Song(
            id="jiang_tian_chang_kai_1",
            title="將天敞開",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        temp_db.insert_song(existing_song)

        # Mock HTML with the same song
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>將天敞開</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>G</td><td>Lyrics</td></tr>
            <tr><td>New Song</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>C</td><td>Lyrics</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        scraper = CatalogScraper(db_client=temp_db)
        songs = scraper.scrape_all_songs(incremental=True, force=False)

        # Should only return the new song
        assert len(songs) == 1
        assert songs[0].title == "New Song"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_scrape_with_force(self, mock_get, temp_db):
        """Test force re-scrapes all songs."""
        # Insert an existing song
        existing_song = Song(
            id="jiang_tian_chang_kai_1",
            title="將天敞開",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        temp_db.insert_song(existing_song)

        # Mock HTML with the same song plus a new one
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>將天敞開</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>G</td><td>Lyrics</td></tr>
            <tr><td>New Song</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>C</td><td>Lyrics</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        scraper = CatalogScraper(db_client=temp_db)
        songs = scraper.scrape_all_songs(incremental=True, force=True)

        # Should return both songs when force=True
        assert len(songs) == 2


class TestCatalogScraperValidation:
    """Tests for CatalogScraper validation."""

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_validate_test_song(self, mock_get):
        """Test validation of the test song."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
                <td>Album</td><td>Series</td><td>G</td>
                <td>這是歌詞<br/>第二行</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        scraper = CatalogScraper(db_client=None)
        song = scraper.validate_test_song()

        assert song.title == "將天敞開"
        assert song.composer == "游智婷"
        assert song.lyricist == "鄭懋柔"
        assert song.musical_key == "G"

    @patch("stream_of_worship.admin.services.scraper.requests.get")
    def test_validate_test_song_not_found(self, mock_get):
        """Test validation fails when test song not found."""
        html_content = """
        <table id="tablepress-3">
            <tr><th>曲名</th><th>作曲</th><th>作詞</th><th>專輯名稱</th>
                <th>專輯系列</th><th>調性</th><th>歌詞</th></tr>
            <tr><td>Other Song</td><td>Composer</td><td>Lyricist</td>
                <td>Album</td><td>Series</td><td>C</td><td>Lyrics</td></tr>
        </table>
        """
        mock_response = Mock()
        mock_response.text = html_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        scraper = CatalogScraper(db_client=None)
        with pytest.raises(AssertionError, match="Test song '將天敞開' not found"):
            scraper.validate_test_song()


class TestCatalogScraperLyricsParsing:
    """Tests for lyrics parsing."""

    @pytest.fixture
    def scraper_no_db(self):
        """Create a scraper without database client."""
        return CatalogScraper(db_client=None)

    def test_parse_lyrics_cell_with_br(self, scraper_no_db):
        """Test parsing lyrics cell with <br/> tags."""
        from bs4 import BeautifulSoup

        html = "Line 1<br/>Line 2<br/>Line 3"
        cell = BeautifulSoup(html, "html.parser")

        result = scraper_no_db._parse_lyrics_cell(cell)

        assert result["lyrics_lines"] == ["Line 1", "Line 2", "Line 3"]
        assert "Line 1\nLine 2\nLine 3" in result["lyrics_raw"]

    def test_parse_lyrics_cell_empty_lines(self, scraper_no_db):
        """Test parsing lyrics cell with empty lines."""
        from bs4 import BeautifulSoup

        html = "Line 1<br/><br/>Line 2"
        cell = BeautifulSoup(html, "html.parser")

        result = scraper_no_db._parse_lyrics_cell(cell)

        # Empty lines should be filtered out
        assert result["lyrics_lines"] == ["Line 1", "Line 2"]


class TestCatalogScraperHelpers:
    """Tests for helper methods."""

    @pytest.fixture
    def scraper_no_db(self):
        """Create a scraper without database client."""
        return CatalogScraper(db_client=None)

    def test_find_header_index(self, scraper_no_db):
        """Test finding header index by keywords."""
        headers = ["曲名", "作曲", "作詞", "專輯名稱", "調性", "歌詞"]

        assert scraper_no_db._find_header_index(headers, ["曲名", "title"]) == 0
        assert scraper_no_db._find_header_index(headers, ["作曲", "composer"]) == 1
        assert scraper_no_db._find_header_index(headers, ["調性", "key"]) == 4
        assert scraper_no_db._find_header_index(headers, ["nonexistent"]) is None

    def test_normalize_song_id_chinese(self, scraper_no_db):
        """Test normalizing Chinese song title to ID."""
        song_id = scraper_no_db._normalize_song_id("將天敞開", 42)

        assert "jiang_tian_chang_kai" in song_id
        assert song_id.endswith("_42")

    def test_normalize_song_id_english(self, scraper_no_db):
        """Test normalizing English song title to ID."""
        song_id = scraper_no_db._normalize_song_id("Amazing Grace", 1)

        assert "amazing" in song_id
        assert song_id.endswith("_1")

    def test_normalize_song_id_length_limit(self, scraper_no_db):
        """Test that long titles are truncated."""
        long_title = "A" * 200
        song_id = scraper_no_db._normalize_song_id(long_title, 5)

        assert len(song_id) <= 100

    def test_detect_sections(self, scraper_no_db):
        """Test section detection."""
        lines = ["Line 1", "Line 2", "Line 3"]
        sections = scraper_no_db._detect_sections(lines)

        assert len(sections) == 1
        assert sections[0]["section_type"] == "unknown"
        assert sections[0]["lines"] == lines
