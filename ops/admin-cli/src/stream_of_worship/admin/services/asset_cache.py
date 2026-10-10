"""Asset cache service for admin workflows.

Manages local caching of R2 audio assets (stems, LRC files) to avoid
repeated downloads. Tracks cache state and provides cache cleanup.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile
from typing import Optional

from stream_of_worship.admin.services.r2 import R2Client


STEM_FILES: dict[str, str] = {
    "vocals_dry": "vocals_dry.flac",
    "vocals_clean": "vocals_clean.flac",  # legacy name, same content as vocals_dry
    "vocals": "vocals.flac",
    "vocals_wav": "vocals.wav",  # wet legacy fallback
}
CLEAN_VOCALS_ALIAS = "clean_vocals.flac"
DRY_STEM_SOURCES = ("vocals_dry", "vocals_clean")


@dataclass
class CacheEntry:
    """Information about a cached file.

    Attributes:
        local_path: Path to the cached file
        s3_key: Original S3 key
        downloaded_at: When the file was cached
        size_bytes: File size in bytes
        hash_prefix: Recording hash prefix
    """

    local_path: Path
    s3_key: str
    downloaded_at: datetime
    size_bytes: int
    hash_prefix: str


class AssetCache:
    """Local cache for R2 audio assets.

    Downloads and caches audio stems and LRC files from R2 to local storage.
    Tracks cache entries and provides cache management operations.

    Attributes:
        cache_dir: Base directory for cached files
        r2_client: R2 client for downloads
    """

    def __init__(self, cache_dir: Path, r2_client: R2Client):
        """Initialize the asset cache.

        Args:
            cache_dir: Base directory for cached files
            r2_client: R2 client for downloading
        """
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """Ensure the cache directory exists."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, hash_prefix: str, asset_type: str, filename: str) -> Path:
        """Get the local cache path for an asset.

        Args:
            hash_prefix: Recording hash prefix
            asset_type: Type of asset (e.g., 'stems', 'lrc')
            filename: Original filename

        Returns:
            Path in cache directory
        """
        return self.cache_dir / hash_prefix / asset_type / filename

    def _get_s3_key(self, hash_prefix: str, asset_type: str, filename: str) -> str:
        """Get the S3 key for an asset.

        Args:
            hash_prefix: Recording hash prefix
            asset_type: Type of asset
            filename: Original filename

        Returns:
            S3 key string
        """
        return f"{hash_prefix}/{asset_type}/{filename}"

    def get_audio_path(self, hash_prefix: str) -> Path:
        """Get the local cache path for an audio file.

        Args:
            hash_prefix: Recording hash prefix

        Returns:
            Local cache path
        """
        return self._get_cache_path(hash_prefix, "audio", "audio.mp3")

    def get_stem_path(self, hash_prefix: str, stem_name: str) -> Path:
        """Get the local cache path for a stem file.

        Args:
            hash_prefix: Recording hash prefix
            stem_name: Stem name (e.g., 'vocals_dry', 'vocals')

        Returns:
            Local cache path
        """
        return self._get_cache_path(
            hash_prefix, "stems", STEM_FILES.get(stem_name, f"{stem_name}.flac")
        )

    def get_lrc_path(self, hash_prefix: str) -> Path:
        """Get the local cache path for an LRC file.

        Args:
            hash_prefix: Recording hash prefix

        Returns:
            Local cache path
        """
        return self._get_cache_path(hash_prefix, "lrc", "lyrics.lrc")

    def is_cached(self, hash_prefix: str, asset_type: str, filename: str) -> bool:
        """Check if an asset is already cached.

        Args:
            hash_prefix: Recording hash prefix
            asset_type: Type of asset
            filename: Original filename

        Returns:
            True if cached and file exists
        """
        cache_path = self._get_cache_path(hash_prefix, asset_type, filename)
        return cache_path.exists()

    def download_audio(self, hash_prefix: str, force: bool = False) -> Optional[Path]:
        """Download and cache the main audio file.

        Args:
            hash_prefix: Recording hash prefix
            force: Re-download even if cached

        Returns:
            Path to cached file or None if download failed
        """
        cache_path = self.get_audio_path(hash_prefix)

        if not force and cache_path.exists():
            return cache_path

        s3_key = f"{hash_prefix}/audio.mp3"

        try:
            if not self.r2_client.file_exists(s3_key):
                return None

            self.r2_client.download_file(s3_key, cache_path)
            return cache_path if cache_path.exists() else None
        except Exception:
            # Clean up partial download
            if cache_path.exists():
                cache_path.unlink()
            return None

    def download_stem(
        self, hash_prefix: str, stem_name: str, force: bool = False
    ) -> Optional[Path]:
        """Download and cache a stem file.

        Args:
            hash_prefix: Recording hash prefix
            stem_name: Stem name (e.g., 'vocals_dry', 'vocals')
            force: Re-download even if cached

        Returns:
            Path to cached file or None if download failed
        """
        cache_path = self.get_stem_path(hash_prefix, stem_name)

        if not force and cache_path.exists():
            return cache_path

        s3_key = self._get_s3_key(
            hash_prefix, "stems", STEM_FILES.get(stem_name, f"{stem_name}.flac")
        )

        try:
            if not self.r2_client.file_exists(s3_key):
                return None

            self.r2_client.download_file(s3_key, cache_path)
            return cache_path if cache_path.exists() else None
        except Exception:
            if cache_path.exists():
                cache_path.unlink()
            return None

    def download_lrc(self, hash_prefix: str, force: bool = False) -> Optional[Path]:
        """Download and cache the LRC lyrics file.

        Args:
            hash_prefix: Recording hash prefix
            force: Re-download even if cached

        Returns:
            Path to cached file or None if download failed
        """
        cache_path = self.get_lrc_path(hash_prefix)

        if not force and cache_path.exists():
            return cache_path

        s3_key = f"{hash_prefix}/lyrics.lrc"

        try:
            if not self.r2_client.file_exists(s3_key):
                return None

            self.r2_client.download_file(s3_key, cache_path)
            return cache_path if cache_path.exists() else None
        except Exception:
            if cache_path.exists():
                cache_path.unlink()
            return None

    def download_all_stems(
        self, hash_prefix: str, stem_names: Optional[list[str]] = None, force: bool = False
    ) -> dict[str, Optional[Path]]:
        """Download all stem files for a recording.

        Args:
            hash_prefix: Recording hash prefix
            stem_names: List of stem names to download (default: vocal stems)
            force: Re-download even if cached

        Returns:
            Dictionary mapping stem names to cached paths (None if failed)
        """
        if stem_names is None:
            stem_names = ["vocals_dry", "vocals_clean", "vocals", "vocals_wav"]

        result = {}
        for stem_name in stem_names:
            result[stem_name] = self.download_stem(hash_prefix, stem_name, force)

        return result

    def download_clean_vocals(
        self, hash_prefix: str, force: bool = False
    ) -> tuple[Optional[Path], Optional[str]]:
        """Resolve the best R2 vocal stem into the canonical clean-vocal slot.

        Returns (path, source_name): source is the stem name that satisfied the
        request; local short-circuit returns (alias_path, "clean_vocals"); wet
        sources return the wet stem path WITHOUT aliasing it (wet vocals must
        not masquerade as clean); nothing found → (None, None).
        """
        alias_path = self._get_cache_path(hash_prefix, "stems", CLEAN_VOCALS_ALIAS)
        if not force and alias_path.exists():
            return alias_path, "clean_vocals"

        for stem_name in DRY_STEM_SOURCES + ("vocals", "vocals_wav"):
            path = self.download_stem(hash_prefix, stem_name, force=force)
            if path is None:
                continue
            if stem_name in DRY_STEM_SOURCES:
                copyfile(path, alias_path)
                return alias_path, stem_name
            return path, stem_name

        return None, None

    def get_cache_size(self, hash_prefix: Optional[str] = None) -> int:
        """Get the total size of cached files in bytes.

        Args:
            hash_prefix: If specified, only count this recording's cache

        Returns:
            Total size in bytes
        """
        target_dir = self.cache_dir / hash_prefix if hash_prefix else self.cache_dir

        if not target_dir.exists():
            return 0

        total_size = 0
        for path in target_dir.rglob("*"):
            if path.is_file():
                total_size += path.stat().st_size

        return total_size

    def get_cache_size_mb(self, hash_prefix: Optional[str] = None) -> float:
        """Get the total size of cached files in MB.

        Args:
            hash_prefix: If specified, only count this recording's cache

        Returns:
            Total size in MB
        """
        return self.get_cache_size(hash_prefix) / (1024 * 1024)

    def clear_cache(
        self, hash_prefix: Optional[str] = None, older_than_days: Optional[int] = None
    ) -> int:
        """Clear cached files.

        Args:
            hash_prefix: If specified, only clear this recording's cache
            older_than_days: Only clear files older than this many days

        Returns:
            Number of files removed
        """
        target_dir = self.cache_dir / hash_prefix if hash_prefix else self.cache_dir

        if not target_dir.exists():
            return 0

        removed = 0
        cutoff = None
        if older_than_days:
            cutoff = datetime.now() - timedelta(days=older_than_days)

        for path in target_dir.rglob("*"):
            if path.is_file():
                should_remove = True
                if cutoff:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                    should_remove = mtime < cutoff

                if should_remove:
                    path.unlink()
                    removed += 1

        # Clean up empty directories
        for path in sorted(target_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

        return removed

    def get_cached_recordings(self) -> list[str]:
        """Get list of hash prefixes that have cached files.

        Returns:
            List of hash prefixes
        """
        if not self.cache_dir.exists():
            return []

        return [
            d.name for d in self.cache_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
        ]
