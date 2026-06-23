"""Tests for cache functionality."""

import json
import tempfile
from pathlib import Path

import pytest

from sow_analysis.storage.cache import CacheManager
from sow_analysis.workers.lrc import WhisperPhrase


class TestWhisperTranscriptionCache:
    """Tests for Whisper transcription caching."""

    def test_save_and_get_whisper_transcription(self):
        """Test saving and retrieving Whisper transcription from cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            # Sample transcription data
            content_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            phrases = [
                {"text": "Hello world", "start": 0.0, "end": 2.5},
                {"text": "Second phrase", "start": 3.0, "end": 5.0},
            ]

            # Initially should be None (cache miss)
            result = cache_manager.get_whisper_transcription(content_hash)
            assert result is None, "Expected cache miss before saving"

            # Save to cache
            cache_file = cache_manager.save_whisper_transcription(content_hash, phrases)
            assert cache_file.exists(), "Cache file should exist after saving"

            # Now should get cache hit
            cached_phrases = cache_manager.get_whisper_transcription(content_hash)
            assert cached_phrases is not None, "Expected cache hit after saving"
            assert len(cached_phrases) == 2, f"Expected 2 phrases, got {len(cached_phrases)}"
            assert cached_phrases[0]["text"] == "Hello world"
            assert cached_phrases[0]["start"] == 0.0
            assert cached_phrases[0]["end"] == 2.5

    def test_whisper_cache_key_consistency(self):
        """Test that cache keys are consistent between save and get."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            phrases = [{"text": "Test", "start": 0.0, "end": 1.0}]

            # Save
            cache_manager.save_whisper_transcription(content_hash, phrases)

            # Get - should use same hash prefix logic
            cached = cache_manager.get_whisper_transcription(content_hash)
            assert cached is not None

            # Verify the actual file name uses first 32 chars
            expected_filename = f"{content_hash[:32]}_whisper.json"
            cache_file = cache_dir / expected_filename
            assert cache_file.exists(), f"Expected {expected_filename} to exist"

    def test_whisper_cache_file_format(self):
        """Test that cache file has expected format with 'phrases' key."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "test123"
            phrases = [{"text": "Hello", "start": 0.0, "end": 1.0}]

            cache_manager.save_whisper_transcription(content_hash, phrases)

            # Read raw file to verify format
            cache_file = cache_dir / f"{content_hash[:32]}_whisper.json"
            data = json.loads(cache_file.read_text())

            assert "phrases" in data, "Cache should have 'phrases' key"
            assert "cached_at" in data, "Cache should have 'cached_at' key"
            assert isinstance(data["phrases"], list)


class TestLRCResultCache:
    """Tests for LRC result caching."""

    def test_save_and_get_lrc_result(self):
        """Test saving and retrieving LRC result from cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "lrc123"
            result = {"lrc_url": "s3://bucket/song.lrc", "line_count": 42}

            # Initially None
            cached = cache_manager.get_lrc_result(content_hash)
            assert cached is None

            # Save
            cache_manager.save_lrc_result(content_hash, result)

            # Now should be cached
            cached = cache_manager.get_lrc_result(content_hash)
            assert cached is not None
            assert cached["lrc_url"] == "s3://bucket/song.lrc"
            assert cached["line_count"] == 42


class TestCacheKeyTruncation:
    """Tests for cache key truncation behavior."""

    def test_long_hash_truncated_to_32_chars(self):
        """Test that long content hashes are truncated to 32 chars for cache keys."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            # 64-character hash (typical SHA-256 hex)
            long_hash = "a" * 64
            phrases = [{"text": "Test", "start": 0.0, "end": 1.0}]

            cache_manager.save_whisper_transcription(long_hash, phrases)

            # File should use first 32 chars only
            expected_file = cache_dir / ("a" * 32 + "_whisper.json")
            assert expected_file.exists()

            # Should also be retrievable with full hash
            cached = cache_manager.get_whisper_transcription(long_hash)
            assert cached is not None


class TestWhisperPhraseDataclass:
    """Tests for WhisperPhrase dataclass creation from cache."""

    def test_whisper_phrase_from_cache_dict(self):
        """Test creating WhisperPhrase objects from cached dict data."""
        cached_data = [
            {"text": "Hello", "start": 0.0, "end": 1.5},
            {"text": "World", "start": 2.0, "end": 3.5},
        ]

        phrases = [WhisperPhrase(**p) for p in cached_data]

        assert len(phrases) == 2
        assert phrases[0].text == "Hello"
        assert phrases[0].start == 0.0
        assert phrases[0].end == 1.5
        assert phrases[1].text == "World"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
