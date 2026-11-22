import hashlib
import logging
import os
import random
import shutil
import tempfile
import time
from pathlib import Path

import urllib3

from sow_render_worker.r2_client import R2Client, create_r2_client_from_env

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "/tmp/sow-assets/cache"
DEFAULT_TEMP_DIR = "/tmp/sow-assets/temp"
MAX_LRC_SIZE_BYTES = 1024 * 1024

GLUE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS = 30
GLUE_DOWNLOAD_READ_TIMEOUT_SECONDS = 300
GLUE_DOWNLOAD_RETRY_DELAY_CAPS_SECONDS = (1.0, 2.0, 4.0)
GLUE_AUDIO_UNAVAILABLE = "GLUE_AUDIO_UNAVAILABLE"

_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429})


class GlueDownloadError(RuntimeError):
    """A staged Glue take could not be fetched or verified.

    The message is safe to surface: it never embeds the signed URL, R2
    credentials, or a provider/transport body. Callers turn it into a visible
    render failure; there is no ordinary-gap fallback.
    """

    def __init__(self, message: str, *, code: str = GLUE_AUDIO_UNAVAILABLE):
        super().__init__(message)
        self.code = code


class _RetryableGlueDownloadError(Exception):
    """Internal signal: the failure is transient and the download may retry."""


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

    def download_glue(self, r2_key: str, *, expected_bytes: int | None,
                      expected_sha256: str | None, cache_key: str | None = None) -> str:
        """Download and verify one staged Glue take.

        Streams into a temp file in the fetcher's temp dir, validates the
        expected byte length and SHA-256, then atomically renames into the cache
        dir. A missing, corrupt, or partially written cache file is never a hit.
        Transient transport failures retry with full jitter (1 s, 2 s, 4 s caps);
        missing objects and checksum/length mismatches fail immediately. The
        raised ``GlueDownloadError`` message never contains the signed URL or
        secrets.
        """
        cache_path = self._glue_cache_path(r2_key, cache_key)
        if self._glue_cache_is_valid(cache_path, expected_bytes, expected_sha256):
            logger.info("Glue cache hit: %s", cache_path.name)
            return str(cache_path)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        max_attempts = len(GLUE_DOWNLOAD_RETRY_DELAY_CAPS_SECONDS) + 1
        last_transient: Exception | None = None
        for attempt in range(max_attempts):
            if attempt:
                delay_cap = GLUE_DOWNLOAD_RETRY_DELAY_CAPS_SECONDS[attempt - 1]
                delay = random.uniform(0, delay_cap)
                logger.warning(
                    "Glue download retry attempt %d/%d for %s in %.3fs",
                    attempt, max_attempts - 1, cache_path.name, delay,
                )
                time.sleep(delay)
            try:
                self._download_glue_once(r2_key, cache_path, expected_bytes, expected_sha256)
                logger.info("Glue downloaded: %s", cache_path.name)
                return str(cache_path)
            except _RetryableGlueDownloadError as exc:
                last_transient = exc
                logger.warning("Glue download attempt %d failed transiently", attempt + 1)

        raise GlueDownloadError(
            f"Glue audio download exhausted {max_attempts} attempts for {cache_path.name}"
        ) from last_transient

    def _glue_cache_path(self, r2_key: str, cache_key: str | None) -> Path:
        if cache_key:
            name = Path(cache_key).name
        else:
            name = hashlib.sha256(r2_key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{name}.wav"

    def _glue_cache_is_valid(self, cache_path: Path, expected_bytes: int | None,
                             expected_sha256: str | None) -> bool:
        try:
            if not cache_path.is_file():
                return False
            if expected_bytes is not None and cache_path.stat().st_size != expected_bytes:
                return False
            if expected_sha256 is not None:
                digest = hashlib.sha256()
                with open(cache_path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected_sha256.lower():
                    return False
            return True
        except OSError:
            return False

    def _download_glue_once(self, r2_key: str, cache_path: Path,
                             expected_bytes: int | None,
                             expected_sha256: str | None) -> None:
        tmp_path: Path | None = None
        try:
            signed_url = self._r2_client.generate_signed_url(
                r2_key, "audio", expires_in_seconds=3600, content_type="audio/wav"
            )
            response = self._http.request(
                "GET",
                signed_url,
                preload_content=False,
                timeout=urllib3.Timeout(
                    connect=GLUE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
                    read=GLUE_DOWNLOAD_READ_TIMEOUT_SECONDS,
                ),
            )
            try:
                if response.status != 200:
                    retryable = (
                        response.status in _RETRYABLE_HTTP_STATUSES
                        or response.status >= 500
                    )
                    if retryable:
                        raise _RetryableGlueDownloadError()
                    raise GlueDownloadError(
                        f"Glue audio unavailable for {cache_path.name}: HTTP {response.status}"
                    )

                fd, tmp_name = tempfile.mkstemp(
                    dir=self._temp_dir, prefix=f"{cache_path.stem}-", suffix=".part"
                )
                tmp_path = Path(tmp_name)
                digest = hashlib.sha256()
                total_bytes = 0
                with os.fdopen(fd, "wb") as f:
                    for chunk in response.stream(8192):
                        f.write(chunk)
                        digest.update(chunk)
                        total_bytes += len(chunk)
                self._validate_glue_content(
                    cache_path, total_bytes, digest.hexdigest(),
                    expected_bytes, expected_sha256,
                )
                os.replace(tmp_path, cache_path)
                tmp_path = None
            finally:
                response.release_conn()
        except GlueDownloadError:
            raise
        except _RetryableGlueDownloadError:
            raise
        except Exception as exc:
            raise _RetryableGlueDownloadError() from exc
        finally:
            if tmp_path is not None:
                self._remove_quietly(tmp_path)

    @staticmethod
    def _validate_glue_content(cache_path: Path, total_bytes: int, sha256: str,
                               expected_bytes: int | None,
                               expected_sha256: str | None) -> None:
        if expected_bytes is not None and total_bytes != expected_bytes:
            raise GlueDownloadError(
                f"Glue audio length mismatch for {cache_path.name}: "
                f"expected {expected_bytes}, received {total_bytes}"
            )
        if expected_sha256 is not None and sha256 != expected_sha256.lower():
            raise GlueDownloadError(
                f"Glue audio checksum mismatch for {cache_path.name}"
            )

    @staticmethod
    def _remove_quietly(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass
