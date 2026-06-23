import logging
import os
import shutil
from pathlib import Path

import urllib3

from sow_render_worker.r2_client import R2Client, create_r2_client_from_env

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "/tmp/sow-assets/cache"
DEFAULT_TEMP_DIR = "/tmp/sow-assets/temp"
MAX_LRC_SIZE_BYTES = 1024 * 1024


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
        self._http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=30, read=300)
        )
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
            logger.info("Audio cache hit: %s", hash_prefix)
            return str(cache_path)

        try:
            signed_url = self._r2_client.get_audio_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url, preload_content=False)
            try:
                if response.status != 200:
                    raise RuntimeError(
                        f"Failed to download audio: HTTP {response.status}"
                    )

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = cache_path.with_suffix(".tmp")
                try:
                    total_bytes = 0
                    with open(tmp_path, "wb") as f:
                        for chunk in response.stream(8192):
                            f.write(chunk)
                            total_bytes += len(chunk)
                    os.replace(tmp_path, cache_path)
                except BaseException:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise
            finally:
                response.release_conn()

            logger.info(
                "Audio downloaded: %s (%d bytes, cached at %s)",
                hash_prefix, total_bytes, cache_path,
            )
            return str(cache_path)
        except Exception as exc:
            logger.exception("Failed to download audio for %s", hash_prefix)
            raise RuntimeError(
                f"Failed to download audio for {hash_prefix}: {exc}"
            ) from exc

    def download_lrc(self, hash_prefix: str) -> str | None:
        if hash_prefix in self._lrc_cache:
            logger.debug("LRC cache hit: %s", hash_prefix)
            return self._lrc_cache[hash_prefix]

        try:
            signed_url = self._r2_client.get_lrc_signed_url(
                hash_prefix, expires_in_seconds=3600
            )

            response = self._http.request("GET", signed_url, preload_content=False)
            chunks: list[bytes] = []
            total_size = 0
            try:
                if response.status != 200:
                    raise RuntimeError(
                        f"Failed to download LRC: HTTP {response.status}"
                    )

                for chunk in response.stream(8192):
                    total_size += len(chunk)
                    if total_size > MAX_LRC_SIZE_BYTES:
                        raise RuntimeError(
                            f"LRC response too large: exceeds {MAX_LRC_SIZE_BYTES} limit"
                        )
                    chunks.append(chunk)
            finally:
                response.release_conn()

            content = b"".join(chunks).decode("utf-8")
            self._lrc_cache[hash_prefix] = content
            logger.info("LRC downloaded: %s (%d bytes)", hash_prefix, total_size)
            return content
        except Exception as exc:
            logger.exception("Failed to download LRC for %s", hash_prefix)
            raise RuntimeError(
                f"Failed to download LRC for {hash_prefix}: {exc}"
            ) from exc

    def cleanup_temp(self) -> None:
        if self._job_temp_dir is not None:
            try:
                shutil.rmtree(self._job_temp_dir, ignore_errors=True)
            except Exception:
                pass
