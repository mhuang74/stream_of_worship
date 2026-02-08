"""Local disk cache for analysis results."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class CacheManager:
    """Manages local disk cache for analysis results and stems."""

    def __init__(self, cache_dir: Path):
        """Initialize cache manager.

        Args:
            cache_dir: Root directory for cache storage
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "stems").mkdir(exist_ok=True)

    def _get_hash_prefix(self, content_hash: str) -> str:
        """Get the first 32 chars of hash for cache keys."""
        return content_hash[:32]

    def get_analysis_result(self, content_hash: str) -> Optional[dict]:
        """Check if analysis result exists in cache.

        Args:
            content_hash: Full SHA-256 content hash

        Returns:
            Cached result dict or None
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}.json"

        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def get_stems_dir(self, content_hash: str) -> Optional[Path]:
        """Check if stems exist in cache.

        Args:
            content_hash: Full SHA-256 content hash

        Returns:
            Path to stems directory or None
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        stems_dir = self.cache_dir / "stems" / hash_prefix

        required_stems = ["bass", "drums", "other", "vocals"]
        if all((stems_dir / f"{stem}.wav").exists() for stem in required_stems):
            return stems_dir
        return None

    def save_analysis_result(self, content_hash: str, result: dict) -> Path:
        """Save analysis result to cache.

        Args:
            content_hash: Full SHA-256 content hash
            result: Analysis result dictionary

        Returns:
            Path to saved cache file
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}.json"

        cache_file.write_text(json.dumps(result, indent=2))
        return cache_file

    def save_stems(self, content_hash: str, source_stems_dir: Path) -> Path:
        """Copy stems to cache directory.

        Args:
            content_hash: Full SHA-256 content hash
            source_stems_dir: Directory containing stem files

        Returns:
            Path to cached stems directory
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        stems_dir = self.cache_dir / "stems" / hash_prefix

        stems_dir.mkdir(parents=True, exist_ok=True)

        for stem in ("bass", "drums", "other", "vocals"):
            source = source_stems_dir / f"{stem}.wav"
            dest = stems_dir / f"{stem}.wav"
            if source.exists():
                shutil.copy2(str(source), str(dest))

        return stems_dir

    def get_lrc_result(self, content_hash: str) -> Optional[dict]:
        """Check if LRC result exists in cache.

        Args:
            content_hash: Full SHA-256 content hash

        Returns:
            Cached LRC result dict or None
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}_lrc.json"

        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def save_lrc_result(self, content_hash: str, result: dict) -> Path:
        """Save LRC result to cache.

        Args:
            content_hash: Full SHA-256 content hash
            result: LRC result dictionary

        Returns:
            Path to saved cache file
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}_lrc.json"

        cache_file.write_text(json.dumps(result, indent=2))
        return cache_file

    def get_whisper_transcription(self, content_hash: str) -> Optional[list]:
        """Check if Whisper transcription exists in cache.

        Args:
            content_hash: Full SHA-256 content hash of the audio file

        Returns:
            List of transcription phrases (dicts with text, start, end) or None
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}_whisper.json"

        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                return data.get("phrases")
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def save_whisper_transcription(self, content_hash: str, phrases: list) -> Path:
        """Save Whisper transcription to cache.

        Args:
            content_hash: Full SHA-256 content hash of the audio file
            phrases: List of transcription phrases (dicts with text, start, end)

        Returns:
            Path to saved cache file
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_file = self.cache_dir / f"{hash_prefix}_whisper.json"

        cache_data = {
            "phrases": phrases,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_file.write_text(json.dumps(cache_data, indent=2))
        return cache_file

    def clear(self) -> None:
        """Clear all cached data."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / "stems").mkdir(exist_ok=True)
