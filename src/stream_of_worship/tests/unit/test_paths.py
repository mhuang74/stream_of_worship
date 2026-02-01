"""Tests for platform-specific path resolution."""

import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from stream_of_worship.core.paths import (
    get_user_data_dir,
    get_cache_dir,
    ensure_directories,
    get_song_library_path,
    get_catalog_index_path,
    get_playlists_path,
    get_output_path,
    get_config_path,
    get_whisper_cache_path,
    get_song_dir,
    get_project_root,
    get_bundled_font_path,
)


class TestGetUserDataDir:
    """Tests for get_user_data_dir function."""

    def test_env_override(self):
        """Test that STREAM_OF_WORSHIP_DATA_DIR environment variable overrides path."""
        with patch.dict(os.environ, {"STREAM_OF_WORSHIP_DATA_DIR": "/custom/path"}):
            result = get_user_data_dir()
            assert result == Path("/custom/path")

    @patch.dict(os.environ, {}, clear=True)
    def test_linux_paths(self):
        """Test Linux XDG paths."""
        original_platform = sys.platform
        try:
            sys.platform = "linux"

            with patch.dict(os.environ, {}, clear=True):
                result = get_user_data_dir()
                # Should use XDG_DATA_HOME or ~/.local/share
                expected = Path.home() / ".local" / "share" / "stream_of_worship"
                assert result == expected
        finally:
            sys.platform = original_platform

    @patch.dict(os.environ, {}, clear=True)
    def test_macos_paths(self):
        """Test macOS paths."""
        original_platform = sys.platform
        try:
            sys.platform = "darwin"

            with patch.dict(os.environ, {}, clear=True):
                result = get_user_data_dir()
                expected = Path.home() / "Library" / "Application Support" / "StreamOfWorship"
                assert result == expected
        finally:
            sys.platform = original_platform

    @patch.dict(os.environ, {}, clear=True)
    def test_windows_paths(self):
        """Test Windows paths."""
        original_platform = sys.platform
        try:
            sys.platform = "win32"

            with patch.dict(os.environ, {}, clear=True):
                result = get_user_data_dir()
                # Should use APPDATA
                appdata = os.environ.get("APPDATA", "")
                if appdata:
                    expected = Path(appdata) / "StreamOfWorship"
                else:
                    expected = Path.home() / "AppData" / "Roaming" / "StreamOfWorship"
                assert result == expected
        finally:
            sys.platform = original_platform


class TestGetCacheDir:
    """Tests for get_cache_dir function."""

    @patch.dict(os.environ, {}, clear=True)
    def test_linux_cache(self):
        """Test Linux cache paths."""
        original_platform = sys.platform
        try:
            sys.platform = "linux"
            result = get_cache_dir()
            expected = Path.home() / ".cache" / "stream_of_worship"
            assert result == expected
        finally:
            sys.platform = original_platform

    @patch.dict(os.environ, {}, clear=True)
    def test_macos_cache(self):
        """Test macOS cache paths."""
        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            result = get_cache_dir()
            expected = Path.home() / "Library" / "Caches" / "StreamOfWorship"
            assert result == expected
        finally:
            sys.platform = original_platform

    @patch.dict(os.environ, {}, clear=True)
    def test_windows_cache(self):
        """Test Windows cache paths."""
        original_platform = sys.platform
        try:
            sys.platform = "win32"
            result = get_cache_dir()
            localappdata = os.environ.get("LOCALAPPDATA", "")
            if localappdata:
                expected = Path(localappdata) / "StreamOfWorship" / "cache"
            else:
                expected = Path.home() / "AppData" / "Local" / "StreamOfWorship" / "cache"
            assert result == expected
        finally:
            sys.platform = original_platform


class TestEnsureDirectories:
    """Tests for ensure_directories function."""

    @pytest.fixture
    def temp_base_dir(self, tmp_path):
        """Fixture providing temporary directory for testing."""
        return tmp_path

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    @patch("stream_of_worship.core.paths.get_cache_dir")
    def test_creates_all_directories(self, mock_cache_dir, mock_data_dir, temp_base_dir):
        """Test that ensure_directories creates all required directories."""
        mock_data_dir.return_value = temp_base_dir / "data"
        mock_cache_dir.return_value = temp_base_dir / "cache"

        ensure_directories()

        # Verify all expected directories were created
        expected_dirs = [
            temp_base_dir / "data",
            temp_base_dir / "data" / "song_library",
            temp_base_dir / "data" / "playlists",
            temp_base_dir / "data" / "assets" / "backgrounds",
            temp_base_dir / "data" / "output" / "audio",
            temp_base_dir / "data" / "output" / "video",
            temp_base_dir / "cache",
            temp_base_dir / "cache" / "whisper_cache",
            temp_base_dir / "cache" / "temp",
        ]

        for directory in expected_dirs:
            assert directory.exists()
            assert directory.is_dir()

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_idempotent(self, mock_data_dir, tmp_path):
        """Test that ensure_directories is idempotent."""
        mock_data_dir.return_value = tmp_path / "data"

        ensure_directories()

        # Should not raise errors on second call
        ensure_directories()


class TestPathHelperFunctions:
    """Tests for path helper functions."""

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_song_library_path(self, mock_data_dir):
        """Test get_song_library_path."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_song_library_path()
        assert result == Path("/custom/data/song_library")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_catalog_index_path(self, mock_data_dir):
        """Test get_catalog_index_path."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_catalog_index_path()
        assert result == Path("/custom/data/song_library/catalog_index.json")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_playlists_path(self, mock_data_dir):
        """Test get_playlists_path."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_playlists_path()
        assert result == Path("/custom/data/playlists")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_output_path_default(self, mock_data_dir):
        """Test get_output_path with no subdir."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_output_path()
        assert result == Path("/custom/data/output")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_output_path_with_subdir(self, mock_data_dir):
        """Test get_output_path with subdir."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_output_path("audio")
        assert result == Path("/custom/data/output/audio")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_config_path(self, mock_data_dir):
        """Test get_config_path."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_config_path()
        assert result == Path("/custom/data/config.json")

    @patch("stream_of_worship.core.paths.get_cache_dir")
    def test_get_whisper_cache_path(self, mock_cache_dir):
        """Test get_whisper_cache_path."""
        mock_cache_dir.return_value = Path("/custom/cache")
        result = get_whisper_cache_path()
        assert result == Path("/custom/cache/whisper_cache")

    @patch("stream_of_worship.core.paths.get_user_data_dir")
    def test_get_song_dir(self, mock_data_dir):
        """Test get_song_dir."""
        mock_data_dir.return_value = Path("/custom/data")
        result = get_song_dir("test_song_123")
        assert result == Path("/custom/data/song_library/songs/test_song_123")

    def test_get_project_root(self):
        """Test get_project_root."""
        # This test verifies the path traversal logic
        # The function goes up 4 levels from core/paths.py
        # We'll test the basic structure without mocking the traversal
        result = get_project_root()
        assert isinstance(result, Path)
        # Result should exist and contain src/
        assert (result / "src").exists()

    def test_get_bundled_font_path(self):
        """Test get_bundled_font_path."""
        result = get_bundled_font_path()
        # Path should be relative to project root
        assert "NotoSansTC-Bold.ttf" in str(result)
        assert result.name == "NotoSansTC-Bold.ttf"
