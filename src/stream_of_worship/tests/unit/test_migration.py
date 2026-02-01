"""Tests for data migration script."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Import migration functions directly
import sys

# Add parent directories to path for importing migration script
_test_root = Path(__file__).parent.parent.parent.parent.parent
if str(_test_root) not in sys.path:
    sys.path.insert(0, str(_test_root))

from scripts.migrate_song_library import (
    clean_chinese_filename,
    load_poc_results,
    load_scraped_lyrics,
    get_source_audio_path,
    get_stems_path,
    get_existing_catalog,
    get_next_song_id,
    migrate_song,
)
from stream_of_worship.core.catalog import CatalogIndex


class TestCleanChineseFilename:
    """Tests for clean_chinese_filename function."""

    def test_basic_chinese(self):
        """Test basic Chinese name conversion."""
        result = clean_chinese_filename("將天敞開")
        assert "jiang_tian_chang_kai" in result
        assert "_" in result

    def test_chinese_with_spaces(self):
        """Test Chinese name with spaces."""
        result = clean_chinese_filename(" 將天敞開  ")
        assert "jiang" in result

    def test_mixed_chinese_english(self):
        """Test mixed Chinese and English."""
        result = clean_chinese_filename("將天Open")
        assert "jiang_tian_open" in result.lower()

    def test_special_characters_removed(self):
        """Test that special characters are removed."""
        result = clean_chinese_filename("將天敞開!@#")
        assert "jiang_tian_chang_kai" in result
        assert "!" not in result
        assert "#" not in result

    def test_empty_string(self):
        """Test empty string handling."""
        result = clean_chinese_filename("")
        assert result == "unknown"

    def test_pinyin_conversion(self):
        """Test that Chinese is converted to pinyin."""
        # Test a few known Chinese characters
        result1 = clean_chinese_filename("爱")
        assert "ai" in result1.lower()

        result2 = clean_chinese_filename("上帝")
        assert "shang" in result2.lower()

        result3 = clean_chinese_filename("敬拜")
        assert "jing_bai" in result3.lower()


class TestLoadPocResults:
    """Tests for load_poc_results function."""

    @pytest.fixture
    def poc_results_file(self, tmp_path):
        """Fixture providing temporary POC results file."""
        data = [
            {
                "filename": "test1.mp3",
                "title": "Test Song 1",
                "tempo": 120.0,
                "duration": 180.0,
            },
            {
                "filename": "test2.mp3",
                "title": "Test Song 2",
                "tempo": 100.0,
                "duration": 240.0,
            },
        ]

        file_path = tmp_path / "poc_full_results.json"
        with file_path.open("w") as f:
            json.dump(data, f)
        return file_path

    def test_load_parsing_success(self, poc_results_file):
        """Test loading valid POC results."""
        results = load_poc_results(poc_results_file)

        assert len(results) == 2
        assert results[0]["filename"] == "test1.mp3"
        assert results[1]["filename"] == "test2.mp3"

    def test_load_file_not_found(self):
        """Test loading non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_poc_results(Path("/nonexistent/poc_full_results.json"))

        assert "not found" in str(exc_info.value).lower()


class TestLoadScrapedLyrics:
    """Tests for load_scraped_lyrics function."""

    @pytest.fixture
    def lyrics_dir(self, tmp_path):
        """Fixture providing temporary lyrics directory."""
        lyrics_path = tmp_path / "lyrics" / "songs"
        lyrics_path.mkdir(parents=True)

        # Create some lyrics files
        (lyrics_path / "1.json").write_text(json.dumps({"lyrics": "Line 1\nLine 2"}))
        (lyrics_path / "2.json").write_text(json.dumps({"lyrics": "Line 3\nLine 4"}))

        return lyrics_path

    def test_load_multiple_files(self, lyrics_dir):
        """Test loading multiple lyrics files."""
        lyrics_data = load_scraped_lyrics(lyrics_dir)

        assert len(lyrics_data) == 2
        assert "1" in lyrics_data
        assert "2" in lyrics_data

    def test_load_empty_directory(self, tmp_path):
        """Test loading from empty directory."""
        empty_dir = tmp_path / "empty_lyrics"
        empty_dir.mkdir()

        lyrics_data = load_scraped_lyrics(empty_dir)

        assert lyrics_data == {}


class TestGetSourceAudioPath:
    """Tests for get_source_audio_path function."""

    @pytest.fixture
    def audio_dir(self, tmp_path):
        """Fixture providing temporary audio directory."""
        audio_path = tmp_path / "poc_audio"
        audio_path.mkdir()
        (audio_path / "test.mp3").touch()
        (audio_path / "Test.flac").touch()
        return audio_path

    def test_exact_match(self, audio_dir):
        """Test exact filename match."""
        result = get_source_audio_path("test.mp3", audio_dir)

        assert result is not None
        assert result.name == "test.mp3"

    def test_case_insensitive_match(self, audio_dir):
        """Test case-insensitive filename match."""
        result = get_source_audio_path("Test.mp3", audio_dir)

        assert result is not None
        assert result.name.lower() == "test.mp3"

    def test_not_found(self, tmp_path):
        """Test returning None when file not found."""
        empty_dir = tmp_path / "empty_audio"
        empty_dir.mkdir()

        result = get_source_audio_path("nonexistent.mp3", empty_dir)

        assert result is None


class TestGetStemsPath:
    """Tests for get_stems_path function."""

    @pytest.fixture
    def stems_dir(self, tmp_path):
        """Fixture providing temporary stems directory."""
        stems_path = tmp_path / "stems_output"
        stems_path.mkdir()
        song_stems = stems_path / "test_song"
        song_stems.mkdir()
        (song_stems / "vocals.wav").touch()
        return stems_path

    def test_exact_match(self, stems_dir):
        """Test exact directory name match."""
        result = get_stems_path("test_song", stems_dir)

        assert result is not None
        assert result.name == "test_song"

    def test_case_insensitive_match(self, stems_dir):
        """Test case-insensitive directory name match."""
        result = get_stems_path("Test_Song", stems_dir)

        assert result is not None
        result.name.lower() == "test_song"

    def test_not_found(self, tmp_path):
        """Test returning None when directory not found."""
        empty_dir = tmp_path / "empty_stems"
        empty_dir.mkdir()

        result = get_stems_path("nonexistent", empty_dir)

        assert result is None


class TestGetNextSongId:
    """Tests for get_next_song_id function."""

    def test_empty_catalog_returns_one(self):
        """Test that empty catalog returns ID 1."""
        # Create a simple mock catalog
        from unittest.mock import MagicMock
        mock_catalog = MagicMock()
        mock_catalog.songs = []

        result = get_next_song_id(mock_catalog)

        assert result == 1

    def test_catalog_with_ids_gets_next(self):
        """Test that existing IDs are used to get next ID."""
        # Create mock songs with IDs
        from unittest.mock import MagicMock
        mock_catalog = MagicMock()
        mock_catalog.songs = [
            MagicMock(id="song_1"),
            MagicMock(id="song_2"),
            MagicMock(id="song_9"),
        ]

        result = get_next_song_id(mock_catalog)

        assert result == 10  # Next after 9

    def test_catalog_with_non_standard_ids(self):
        """Test catalog with non-standard ID formats."""
        from unittest.mock import MagicMock
        mock_catalog = MagicMock()
        mock_catalog.songs = [
            MagicMock(id="custom_name_without_number"),
            MagicMock(id="song_5"),
        ]

        result = get_next_song_id(mock_catalog)

        # Should extract 5 from song_5 and return 6
        assert result == 6


class TestGetExistingCatalog:
    """Tests for get_existing_catalog function."""

    def test_file_not_found_returns_empty(self, tmp_path):
        """Test that non-existent file returns empty catalog."""
        # Create a temp directory that doesn't have catalog_index.json
        empty_dir = tmp_path / "empty_data"
        empty_dir.mkdir()

        # Monkeypatch get_catalog_index_path to return non-existent path
        from stream_of_worship.core.paths import get_catalog_index_path
        import stream_of_worship.core.paths as paths_module
        original_get_path = paths_module.get_catalog_index_path

        def mock_get_path():
            return empty_dir / "catalog_index.json"

        paths_module.get_catalog_index_path = mock_get_path

        try:
            catalog = get_existing_catalog()
            # Should return empty catalog when file doesn't exist
            assert isinstance(catalog, CatalogIndex)
            assert catalog.songs == []
        finally:
            # Restore original function
            paths_module.get_catalog_index_path = original_get_path


class TestMigrateSong:
    """Tests for migrate_song function."""

    @pytest.fixture
    def migration_setup(self, tmp_path, monkeypatch):
        """Fixture setting up migration environment."""
        # Create temporary directories
        poc_results = tmp_path / "poc_full_results.json"
        poc_results.write_text(json.dumps([
            {
                "filename": "test_song.mp3",
                "title": "Test Song",
                "tempo": 120.0,
                "duration": 180.0,
            },
        ]))

        lyrics_dir = tmp_path / "lyrics" / "songs"
        lyrics_dir.mkdir(parents=True)
        (lyrics_dir / "test_song.json").write_text(json.dumps({"lyrics_raw": "Test lyrics"}))

        audio_dir = tmp_path / "poc_audio"
        audio_dir.mkdir()
        (audio_dir / "test_song.mp3").touch()

        stems_dir = tmp_path / "stems_output"
        stems_dir.mkdir()
        song_stems = stems_dir / "test_song"
        song_stems.mkdir()
        (song_stems / "vocals.wav").touch()

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Mock path functions
        monkeypatch.setattr("scripts.migrate_song_library.POC_RESULTS_PATH", poc_results)
        monkeypatch.setattr("scripts.migrate_song_library.LYRICS_SOURCE_PATH", lyrics_dir)
        monkeypatch.setattr("scripts.migrate_song_library.AUDIO_SOURCE_PATH", audio_dir)
        monkeypatch.setattr("scripts.migrate_song_library.STEMS_SOURCE_PATH", stems_dir)

        # Mock catalog that actually tracks songs
        songs_list = []

        def mock_add_song(song):
            songs_list.append(song)

        catalog = MagicMock()
        catalog.songs = songs_list
        catalog.add_song = mock_add_song
        monkeypatch.setattr("scripts.migrate_song_library.get_existing_catalog", lambda: catalog)

        # Mock get_song_dir to use temp output
        def mock_get_song_dir(song_id):
            return output_dir / "songs" / song_id

        monkeypatch.setattr("scripts.migrate_song_library.get_song_dir", mock_get_song_dir)

        return {
            "poc_results": poc_results,
            "catalog": catalog,
            "output_dir": output_dir,
        }

    @patch("builtins.print")
    def test_migrate_song_success(self, mock_print, migration_setup):
        """Test successful song migration."""
        setup = migration_setup

        analysis = setup["poc_results"].read_text()
        analysis_data = json.loads(analysis)[0]

        success, next_id = migrate_song(
            analysis_data,
            {"test_song": {"lyrics_raw": "Test lyrics"}},
            1,
            setup["catalog"],
        )

        assert success is True
        assert next_id == 2
        assert len(setup["catalog"].songs) == 1

        # Check that song directory was created
        song_dir = setup["output_dir"] / "songs" / "test_song_1"
        assert song_dir.exists()
        assert (song_dir / "analysis.json").exists()
        assert (song_dir / "lyrics.json").exists()
        assert (song_dir / "audio.mp3").exists()

    def test_migrate_song_already_migrated(self, migration_setup):
        """Test that already-migrated songs are skipped."""
        setup = migration_setup

        # Add existing song to catalog
        existing_song = MagicMock(
            id="test_song_1",
            title="test_song",
        )
        setup["catalog"].songs = [existing_song]

        analysis_data = json.loads(setup["poc_results"].read_text())[0]

        # Track callback messages
        callback_messages = []

        def progress_callback(msg, _):
            callback_messages.append(msg)

        success, next_id = migrate_song(
            analysis_data,
            {"test_song": {"lyrics_raw": "Test lyrics"}},
            2,  # Next ID
            setup["catalog"],
            progress_callback=progress_callback,
        )

        assert success is True
        # Should still return next_id since we skipped
        # Check that "Skipping" was in callback messages
        any_skipping = any("Skipping" in msg for msg in callback_messages)
        assert any_skipping is True

    @patch("builtins.print")
    def test_migrate_song_missing_audio_warning(self, mock_print, migration_setup):
        """Test warning when audio file is missing."""
        setup = migration_setup

        # Remove audio file
        audio_file = setup["poc_results"].parent / "poc_audio" / "test_song.mp3"
        audio_file.unlink()

        analysis_data = json.loads(setup["poc_results"].read_text())[0]

        success, next_id = migrate_song(
            analysis_data,
            {"test_song": {"lyrics_raw": "Test lyrics"}},
            1,
            setup["catalog"],
        )

        assert success is True
        # Should have warning about missing audio
        printed_output = "".join(str(c) for c in mock_print.call_args_list)
        assert "No audio found" in printed_output
