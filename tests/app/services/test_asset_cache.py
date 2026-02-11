"""Tests for AssetCache.

Tests local caching of R2 audio assets.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from stream_of_worship.app.services.asset_cache import AssetCache, CacheEntry


@pytest.fixture
def mock_r2_client():
    """Mocked R2Client."""
    client = MagicMock()
    client.download_file = Mock(return_value=Path("/downloaded/file.mp3"))
    client.file_exists = Mock(return_value=True)
    return client


@pytest.fixture
def asset_cache(tmp_path, mock_r2_client):
    """AssetCache with temporary directory and mock R2Client."""
    cache_dir = tmp_path / "cache"
    return AssetCache(cache_dir=cache_dir, r2_client=mock_r2_client)


class TestPathGeneration:
    """Tests for cache path generation."""

    def test_get_audio_path_returns_correct_path(self, asset_cache):
        """Verify path construction."""
        path = asset_cache.get_audio_path("abc123def456")

        assert "abc123def456" in str(path)
        assert "audio" in str(path)
        assert path.name == "audio.mp3"

    def test_get_stem_path_returns_correct_path(self, asset_cache):
        """Verify path construction per stem."""
        path = asset_cache.get_stem_path("abc123def456", "vocals")

        assert "abc123def456" in str(path)
        assert "stems" in str(path)
        assert path.name == "vocals.mp3"

    def test_get_lrc_path_returns_correct_path(self, asset_cache):
        """Verify LRC path construction."""
        path = asset_cache.get_lrc_path("abc123def456")

        assert "abc123def456" in str(path)
        assert "lrc" in str(path)
        assert path.name == "lyrics.lrc"


class TestCacheStatus:
    """Tests for cache status checks."""

    def test_is_cached_returns_true_when_exists(self, asset_cache, tmp_path):
        """Verify cache hit detection."""
        # Create the expected file
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_text("fake audio")

        result = asset_cache.is_cached("abc123def456", "audio", "audio.mp3")

        assert result is True

    def test_is_cached_returns_false_when_missing(self, asset_cache):
        """Verify cache miss detection."""
        result = asset_cache.is_cached("abc123def456", "audio", "audio.mp3")

        assert result is False


class TestDownloadOperations:
    """Tests for download operations."""

    def test_download_audio_creates_file(self, asset_cache, tmp_path, mock_r2_client):
        """Verify file created on download."""
        # Mock download to actually copy a file
        dest_file = tmp_path / "cache" / "abc123def456" / "audio" / "audio.mp3"

        def mock_download(s3_key, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("fake audio content")
            return dest

        mock_r2_client.download_file.side_effect = mock_download

        result = asset_cache.download_audio("abc123def456")

        assert result is not None
        assert result.exists()
        assert result.read_text() == "fake audio content"

    def test_download_audio_uses_cached_when_exists(self, asset_cache, tmp_path, mock_r2_client):
        """Verify no re-download if cached."""
        # Create cached file
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_text("cached audio")

        result = asset_cache.download_audio("abc123def456")

        assert result is not None
        assert result.read_text() == "cached audio"
        mock_r2_client.download_file.assert_not_called()

    def test_download_stem_creates_file(self, asset_cache, tmp_path, mock_r2_client):
        """Verify stem download works."""
        dest_file = tmp_path / "cache" / "abc123def456" / "stems" / "vocals.wav"

        def mock_download(s3_key, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("fake stem")
            return dest

        mock_r2_client.download_file.side_effect = mock_download

        result = asset_cache.download_stem("abc123def456", "vocals")

        assert result is not None
        assert result.exists()

    def test_download_lrc_creates_file(self, asset_cache, tmp_path, mock_r2_client):
        """Verify LRC download works."""
        dest_file = tmp_path / "cache" / "abc123def456" / "lrc" / "lyrics.lrc"

        def mock_download(s3_key, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("[00:00.00]Line 1")
            return dest

        mock_r2_client.download_file.side_effect = mock_download

        result = asset_cache.download_lrc("abc123def456")

        assert result is not None
        assert result.exists()

    def test_download_all_stems_downloads_four_stems(self, asset_cache, tmp_path, mock_r2_client):
        """Verify all stems fetched."""
        def mock_download(s3_key, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("fake stem")
            return dest

        mock_r2_client.download_file.side_effect = mock_download
        mock_r2_client.file_exists.return_value = True

        result = asset_cache.download_all_stems("abc123def456")

        assert len(result) == 4
        assert "vocals" in result
        assert "drums" in result
        assert "bass" in result
        assert "other" in result
        assert all(path is not None for path in result.values())


class TestCacheManagement:
    """Tests for cache management operations."""

    def test_get_cache_size_calculates_total(self, asset_cache, tmp_path):
        """Verify size summation."""
        # Create some cached files
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_text("a" * 1000)  # 1000 bytes

        stems_dir = tmp_path / "cache" / "abc123def456" / "stems"
        stems_dir.mkdir(parents=True)
        (stems_dir / "vocals.wav").write_text("b" * 500)  # 500 bytes

        size = asset_cache.get_cache_size()

        assert size == 1500

    def test_get_cache_size_for_specific_hash(self, asset_cache, tmp_path):
        """Verify size calculation for specific hash."""
        # Create files for two different hashes
        for hash_prefix in ["abc123def456", "xyz789abc012"]:
            hash_dir = tmp_path / "cache" / hash_prefix / "audio"
            hash_dir.mkdir(parents=True)
            (hash_dir / "audio.mp3").write_text("a" * 1000)

        size = asset_cache.get_cache_size("abc123def456")

        assert size == 1000

    def test_get_cache_size_mb(self, asset_cache, tmp_path):
        """Verify size in MB conversion."""
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_bytes(b"x" * (1024 * 1024))  # 1 MB

        size_mb = asset_cache.get_cache_size_mb()

        assert abs(size_mb - 1.0) < 0.01  # Approximately 1 MB

    def test_clear_cache_removes_all_files(self, asset_cache, tmp_path):
        """Verify cleanup works."""
        # Create some files
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_text("content")

        # Clear cache
        removed = asset_cache.clear_cache()

        assert removed == 1
        assert not (hash_dir / "audio.mp3").exists()

    def test_clear_cache_with_age_filter(self, asset_cache, tmp_path):
        """Verify clear with older_than_days filter."""
        # Create a file with old mtime
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        file_path = hash_dir / "audio.mp3"
        file_path.write_text("content")

        # Set mtime to 10 days ago
        old_time = datetime.now() - timedelta(days=10)
        import os
        os.utime(file_path, (old_time.timestamp(), old_time.timestamp()))

        # Clear files older than 5 days
        removed = asset_cache.clear_cache(older_than_days=5)

        assert removed == 1

    def test_clear_cache_keeps_recent_files(self, asset_cache, tmp_path):
        """Verify recent files are kept with age filter."""
        # Create a file with recent mtime
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        file_path = hash_dir / "audio.mp3"
        file_path.write_text("content")

        # Clear files older than 5 days - recent file should remain
        removed = asset_cache.clear_cache(older_than_days=5)

        assert removed == 0
        assert file_path.exists()

    def test_clear_cache_specific_hash(self, asset_cache, tmp_path):
        """Verify clearing specific hash only."""
        # Create files for two hashes
        for hash_prefix in ["abc123def456", "xyz789abc012"]:
            hash_dir = tmp_path / "cache" / hash_prefix / "audio"
            hash_dir.mkdir(parents=True)
            (hash_dir / "audio.mp3").write_text("content")

        # Clear only one hash
        removed = asset_cache.clear_cache(hash_prefix="abc123def456")

        assert removed == 1
        assert not (tmp_path / "cache" / "abc123def456" / "audio" / "audio.mp3").exists()
        assert (tmp_path / "cache" / "xyz789abc012" / "audio" / "audio.mp3").exists()

    def test_get_cached_recordings(self, asset_cache, tmp_path):
        """Verify list of hash prefixes with cached files."""
        # Create directories for two hashes
        for hash_prefix in ["abc123def456", "xyz789abc012"]:
            hash_dir = tmp_path / "cache" / hash_prefix / "audio"
            hash_dir.mkdir(parents=True)
            (hash_dir / "audio.mp3").write_text("content")

        recordings = asset_cache.get_cached_recordings()

        assert len(recordings) == 2
        assert "abc123def456" in recordings
        assert "xyz789abc012" in recordings


class TestDownloadFailureHandling:
    """Tests for download error handling."""

    def test_download_audio_returns_none_when_file_not_in_r2(self, asset_cache, mock_r2_client):
        """Verify None returned when file doesn't exist in R2."""
        mock_r2_client.file_exists.return_value = False

        result = asset_cache.download_audio("abc123def456")

        assert result is None

    def test_download_audio_cleans_up_partial(self, asset_cache, tmp_path, mock_r2_client):
        """Verify partial download is cleaned up on error."""
        def mock_download(s3_key, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("partial")
            raise Exception("Download failed")

        mock_r2_client.download_file.side_effect = mock_download

        result = asset_cache.download_audio("abc123def456")

        assert result is None
        # Verify partial file is cleaned up
        partial_file = tmp_path / "cache" / "abc123def456" / "audio" / "audio.mp3"
        assert not partial_file.exists()

    def test_force_download_re_downloads(self, asset_cache, tmp_path, mock_r2_client):
        """Verify force=True re-downloads even if cached."""
        # Create cached file
        hash_dir = tmp_path / "cache" / "abc123def456" / "audio"
        hash_dir.mkdir(parents=True)
        (hash_dir / "audio.mp3").write_text("old content")

        # Mock download to return new content
        def mock_download(s3_key, dest):
            dest.write_text("new content")
            return dest

        mock_r2_client.download_file.side_effect = mock_download

        result = asset_cache.download_audio("abc123def456", force=True)

        mock_r2_client.download_file.assert_called_once()
