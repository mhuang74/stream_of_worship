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


class TestFastAnalyzeCache:
    """Tests for fast analysis result caching."""

    def test_save_and_get_fast_analyze_result(self):
        """Test saving and retrieving fast analysis result."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "a" * 64
            result = {
                "duration_seconds": 180.5,
                "tempo_bpm": 120.0,
                "musical_key": "C",
                "musical_mode": "major",
                "key_confidence": 0.85,
                "loudness_db": -12.3,
            }

            assert cache_manager.get_fast_analyze_result(content_hash) is None

            cache_file = cache_manager.save_fast_analyze_result(content_hash, result)
            assert cache_file.exists()
            assert cache_file.name.endswith("_fast.json")

            cached = cache_manager.get_fast_analyze_result(content_hash)
            assert cached is not None
            assert cached["tempo_bpm"] == 120.0
            assert cached["musical_key"] == "C"

    def test_fast_cache_distinct_from_full(self):
        """Fast cache must not overwrite the full-tier cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "b" * 64
            full_result = {"tempo_bpm": 100.0, "beats": [1.0, 2.0]}
            fast_result = {"tempo_bpm": 120.0}

            cache_manager.save_analysis_result(content_hash, full_result)
            cache_manager.save_fast_analyze_result(content_hash, fast_result)

            full_cached = cache_manager.get_analysis_result(content_hash)
            fast_cached = cache_manager.get_fast_analyze_result(content_hash)

            assert full_cached["tempo_bpm"] == 100.0
            assert fast_cached["tempo_bpm"] == 120.0

    def test_fast_cache_corrupt_json_falls_back_to_miss(self):
        """Corrupt fast cache JSON should be deleted and treated as a miss."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "c" * 64
            hash_prefix = content_hash[:32]
            cache_file = cache_dir / f"{hash_prefix}_fast.json"
            cache_file.write_text("{invalid json")

            assert cache_manager.get_fast_analyze_result(content_hash) is None
            assert not cache_file.exists()

    def test_fast_cache_uses_bpm_versioned_filename(self):
        """Fast result saves under the new BPM-versioned filename."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "d" * 64
            result = {"tempo_bpm": 68.0}

            cache_file = cache_manager.save_fast_analyze_result(content_hash, result)
            # Filename must include both KEY and BPM version suffixes before _fast.json
            assert cache_file.name.endswith("_fast.json")
            assert ".v" in cache_file.name
            assert cache_file.exists()

            cached = cache_manager.get_fast_analyze_result(content_hash)
            assert cached is not None
            assert cached["tempo_bpm"] == 68.0

    def test_fast_cache_reads_legacy_file_as_fallback(self):
        """Legacy v4 fast cache file is read as fallback when no versioned file exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "e" * 64
            hash_prefix = content_hash[:32]
            # Write a legacy-style file (pre-BPM-versioning format)
            legacy_file = cache_dir / f"{hash_prefix}_fast.json"
            legacy_file.write_text('{"tempo_bpm": 92.0}')

            cached = cache_manager.get_fast_analyze_result(content_hash)
            assert cached is not None
            assert cached["tempo_bpm"] == 92.0

    def test_fast_cache_prefers_versioned_over_legacy(self):
        """Versioned fast file takes precedence over legacy file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_manager = CacheManager(cache_dir)

            content_hash = "f" * 64
            hash_prefix = content_hash[:32]
            legacy_file = cache_dir / f"{hash_prefix}_fast.json"
            legacy_file.write_text('{"tempo_bpm": 92.0}')

            versioned_file = cache_manager.save_fast_analyze_result(
                content_hash, {"tempo_bpm": 68.0}
            )

            cached = cache_manager.get_fast_analyze_result(content_hash)
            assert cached is not None
            assert cached["tempo_bpm"] == 68.0
            assert versioned_file.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
