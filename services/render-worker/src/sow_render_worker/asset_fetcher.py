import logging
import shutil
from pathlib import Path

import urllib3

from sow_render_worker.r2_client import R2Client, create_r2_client_from_env

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "/tmp/sow-assets/cache"
DEFAULT_TEMP_DIR = "/tmp/sow-assets/temp"


class AssetFetcher:
    def __init__(
        self,
        cache_dir: str | None = None,
        temp_dir: str | None = None,
        r2_client: R2Client | None = None,
    ):
        self._cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self._temp_dir = Path(temp_dir or DEFAULT_TEMP_DIR)
        self._r2_client = r2_client or create_r2_client_from_env()
        self._http = urllib3.PoolManager()

    def initialize(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    def get_temp_dir(self) -> Path:
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        return self._temp_dir

    def get_cache_dir(self) -> Path:
        return self._cache_dir

    def download_audio(self, hash_prefix: str) -> str | None:
        cache_path = self._cache_dir / f"{hash_prefix}.mp3"
        if cache_path.exists():
            return str(cache_path)

        try:
            signed_url_result = self._r2_client.get_audio_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url_result.url)
            if response.status != 200:
                raise RuntimeError(
                    f"Failed to download audio: {response.status}"
                )

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(response.data)

            return str(cache_path)
        except Exception:
            logger.exception("Failed to download audio for %s", hash_prefix)
            return None

    def download_lrc(self, hash_prefix: str) -> str | None:
        try:
            signed_url_result = self._r2_client.get_lrc_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url_result.url)
            if response.status != 200:
                raise RuntimeError(
                    f"Failed to download LRC: {response.status}"
                )

            return response.data.decode("utf-8")
        except Exception:
            logger.exception("Failed to download LRC for %s", hash_prefix)
            return None

    def is_cached(self, hash_prefix: str) -> bool:
        cache_path = self._cache_dir / f"{hash_prefix}.mp3"
        return cache_path.exists()

    def clear_file_cache(self) -> None:
        try:
            for file in self._cache_dir.iterdir():
                file.unlink()
        except Exception:
            pass

    def get_cache_stats(self) -> dict[str, int]:
        try:
            files = list(self._cache_dir.iterdir())
            total_size = sum(f.stat().st_size for f in files if f.is_file())
            return {
                "file_count": len(files),
                "total_size_bytes": total_size,
            }
        except Exception:
            return {
                "file_count": 0,
                "total_size_bytes": 0,
            }

    def cleanup_temp(self) -> None:
        try:
            for file in self._temp_dir.iterdir():
                file.unlink()
        except Exception:
            pass
