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
        self._lrc_cache: dict[str, str | None] = {}
        self._job_temp_dir: Path | None = None

    def initialize(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    def get_temp_dir(self) -> Path:
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        return self._temp_dir

    def get_job_temp_dir(self, job_id: str) -> Path:
        self._job_temp_dir = self._temp_dir / job_id
        self._job_temp_dir.mkdir(parents=True, exist_ok=True)
        return self._job_temp_dir

    def get_cache_dir(self) -> Path:
        return self._cache_dir

    def download_audio(self, hash_prefix: str) -> str | None:
        cache_path = self._cache_dir / f"{hash_prefix}.mp3"
        if cache_path.exists():
            return str(cache_path)

        try:
            signed_url = self._r2_client.get_audio_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url)
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
        if hash_prefix in self._lrc_cache:
            return self._lrc_cache[hash_prefix]

        try:
            signed_url = self._r2_client.get_lrc_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url)
            if response.status != 200:
                raise RuntimeError(
                    f"Failed to download LRC: {response.status}"
                )

            content = response.data.decode("utf-8")
            self._lrc_cache[hash_prefix] = content
            return content
        except Exception:
            logger.exception("Failed to download LRC for %s", hash_prefix)
            return None

    def cleanup_temp(self) -> None:
        if self._job_temp_dir is not None:
            try:
                shutil.rmtree(self._job_temp_dir, ignore_errors=True)
            except Exception:
                pass
