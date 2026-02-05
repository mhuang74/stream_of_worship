"""Tests for cache manager."""

import json
import tempfile
from pathlib import Path

import pytest
from sow_analysis.storage.cache import CacheManager


class TestCacheManager:
    """Test CacheManager class."""

    @pytest.fixture
    def temp_cache(self):
        """Create a temporary cache directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    def test_init_creates_directories(self, temp_cache):
        """Test initialization creates cache directories."""
        cache = CacheManager(temp_cache)
        assert cache.cache_dir.exists()
        assert (cache.cache_dir / "stems").exists()

    def test_save_and_get_analysis_result(self, temp_cache):
        """Test saving and retrieving analysis results."""
        cache = CacheManager(temp_cache)
        content_hash = "abc123" * 8  # 48 char hash
        result = {"tempo_bpm": 120.0, "key": "C major"}

        cache.save_analysis_result(content_hash, result)

        cached = cache.get_analysis_result(content_hash)
        assert cached == result

    def test_get_missing_analysis_result(self, temp_cache):
        """Test getting non-existent analysis result."""
        cache = CacheManager(temp_cache)
        content_hash = "nonexistent" * 5

        result = cache.get_analysis_result(content_hash)
        assert result is None

    def test_save_and_get_stems(self, temp_cache):
        """Test saving and retrieving stems."""
        cache = CacheManager(temp_cache)
        content_hash = "abc123" * 8

        # Create fake stem files
        source_dir = temp_cache / "source_stems"
        source_dir.mkdir()
        for stem in ("bass", "drums", "other", "vocals"):
            (source_dir / f"{stem}.wav").write_text(f"fake {stem} audio")

        cache.save_stems(content_hash, source_dir)

        stems_dir = cache.get_stems_dir(content_hash)
        assert stems_dir is not None
        assert (stems_dir / "vocals.wav").exists()
        assert (stems_dir / "bass.wav").read_text() == "fake bass audio"

    def test_get_missing_stems(self, temp_cache):
        """Test getting non-existent stems."""
        cache = CacheManager(temp_cache)
        content_hash = "nonexistent" * 5

        stems_dir = cache.get_stems_dir(content_hash)
        assert stems_dir is None

    def test_get_incomplete_stems(self, temp_cache):
        """Test getting stems when some are missing."""
        cache = CacheManager(temp_cache)
        content_hash = "abc123" * 8

        # Create incomplete stems
        stems_dir = temp_cache / "stems" / content_hash[:32]
        stems_dir.mkdir(parents=True)
        (stems_dir / "vocals.wav").write_text("only vocals")

        result = cache.get_stems_dir(content_hash)
        assert result is None

    def test_save_and_get_lrc_result(self, temp_cache):
        """Test saving and retrieving LRC results."""
        cache = CacheManager(temp_cache)
        content_hash = "abc123" * 8
        result = {"lrc_url": "s3://bucket/lrc", "line_count": 20}

        cache.save_lrc_result(content_hash, result)

        cached = cache.get_lrc_result(content_hash)
        assert cached == result

    def test_clear_cache(self, temp_cache):
        """Test clearing cache."""
        cache = CacheManager(temp_cache)
        content_hash = "abc123" * 8

        # Add some data
        cache.save_analysis_result(content_hash, {"tempo": 120})

        # Clear cache
        cache.clear()

        # Verify cleared
        assert cache.get_analysis_result(content_hash) is None
        assert cache.cache_dir.exists()
