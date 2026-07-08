"""Local disk cache for analysis results."""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import settings


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

    def _versioned_analysis_file(self, content_hash: str, suffix: str = "") -> Path:
        hash_prefix = self._get_hash_prefix(content_hash)
        version = settings.KEY_ALGORITHM_VERSION
        return self.cache_dir / f"{hash_prefix}.v{version}{suffix}.json"

    def _versioned_fast_file(self, content_hash: str) -> Path:
        """Fast-tier cache filename incorporating both KEY and BPM algorithm versions.

        Format: ``{hash32}.v{KEY_ALGORITHM_VERSION}.v{BPM_ALGORITHM_VERSION}_fast.json``
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        key_version = settings.KEY_ALGORITHM_VERSION
        bpm_version = settings.BPM_ALGORITHM_VERSION
        return self.cache_dir / f"{hash_prefix}.v{key_version}.v{bpm_version}_fast.json"

    def get_analysis_result(self, content_hash: str) -> Optional[dict]:
        """Check if analysis result exists in cache.

        Args:
            content_hash: Full SHA-256 content hash

        Returns:
            Cached result dict or None
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_files = [
            self._versioned_analysis_file(content_hash),
            self.cache_dir / f"{hash_prefix}.json",
        ]

        for index, cache_file in enumerate(cache_files):
            if not cache_file.exists():
                continue
            try:
                data = json.loads(cache_file.read_text())
                if index > 0:
                    data.setdefault("key_algorithm_version", "ks_fulltrack_v1")
                return data
            except (json.JSONDecodeError, IOError):
                continue
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
        cache_file = self._versioned_analysis_file(content_hash)

        cache_file.write_text(json.dumps(result, indent=2))
        return cache_file

    def get_fast_analyze_result(self, content_hash: str) -> Optional[dict]:
        """Check if fast analysis result exists in cache.

        Distinct from the full-tier {hash_prefix}.json cache. Fast results are
        stored as {hash_prefix}_fast.json and never overwrite the full cache.

        Reads in order of preference:
        1. ``{hash32}.v{KEY}.v{BPM}_fast.json`` (new versioned file)
        2. ``{hash32}.v{KEY}_fast.json`` (legacy v4 file)
        3. ``{hash32}_fast.json`` (pre-versioning file)

        Args:
            content_hash: Full SHA-256 content hash

        Returns:
            Cached fast result dict or None (also None on corrupt JSON)
        """
        hash_prefix = self._get_hash_prefix(content_hash)
        cache_files = [
            self._versioned_fast_file(content_hash),
            self._versioned_analysis_file(content_hash, "_fast"),
            self.cache_dir / f"{hash_prefix}_fast.json",
        ]

        for index, cache_file in enumerate(cache_files):
            if not cache_file.exists():
                continue
            try:
                data = json.loads(cache_file.read_text())
                if index > 0:
                    data.setdefault("key_algorithm_version", "ks_fulltrack_v1")
                return data
            except (json.JSONDecodeError, IOError):
                # Corrupt cache: delete and treat as miss
                try:
                    cache_file.unlink()
                except OSError:
                    pass
                continue
        return None

    def save_fast_analyze_result(self, content_hash: str, result: dict) -> Path:
        """Save fast analysis result to cache atomically.

        Uses a NamedTemporaryFile in the same directory followed by os.replace
        so a mid-write reader sees either the old or new file, never a partial.

        Args:
            content_hash: Full SHA-256 content hash
            result: Fast analysis result dictionary

        Returns:
            Path to saved cache file
        """
        cache_file = self._versioned_fast_file(content_hash)

        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(cache_file.parent),
            prefix=f".{cache_file.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(json.dumps(result, indent=2))
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(cache_file))
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

    def get_qwen3_asr_transcription(self, cache_key: str) -> Optional[dict]:
        """Check if Qwen3 ASR transcription exists in cache."""
        hash_prefix = self._get_hash_prefix(cache_key)
        cache_dir = self.cache_dir / "qwen3_asr"
        cache_file = cache_dir / f"{hash_prefix}.json"

        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def save_qwen3_asr_transcription(self, cache_key: str, payload: dict) -> Path:
        """Save Qwen3 ASR transcription payload to cache."""
        hash_prefix = self._get_hash_prefix(cache_key)
        cache_dir = self.cache_dir / "qwen3_asr"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{hash_prefix}.json"
        cache_data = {
            **payload,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_file.write_text(json.dumps(cache_data, indent=2, ensure_ascii=False))
        return cache_file

    def clear(self) -> None:
        """Clear all cached data."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / "stems").mkdir(exist_ok=True)
