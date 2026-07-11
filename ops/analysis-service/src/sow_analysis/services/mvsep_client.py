"""MVSEP Cloud API client for stem separation.

Async client using httpx.AsyncClient for cloud-based vocal stem separation.
Provides configurable two-stage separation: Stage 1 (vocal/instrumental separation)
+ optional Stage 2 (reverb removal).
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

MVSEP_API_BASE_URL = "https://mvsep.com/api/separation"
MVSEP_MAX_POLL_INTERVAL = 30.0  # Maximum seconds between poll attempts

# Keywords indicating daily quota/limit exhaustion from MVSEP API
_QUOTA_KEYWORDS = (
    "daily limit",
    "daily quota",
    "limit exceeded",
    "quota exceeded",
    "limit reached",
    "too many jobs",
    "too many requests today",
    "day limit",
    "per day",
    "exceeded your",
)


def _is_quota_exhausted(error_text: str) -> bool:
    """Check if an MVSEP API error message indicates daily quota exhaustion.

    Args:
        error_text: Lowercased error message from API response.

    Returns:
        True if the message matches quota-exhaustion patterns.
    """
    return any(kw in error_text for kw in _QUOTA_KEYWORDS)


class MvsepClientError(Exception):
    """Base exception for MVSEP client errors."""

    pass


class MvsepNonRetriableError(MvsepClientError):
    """Exception raised for non-retriable errors (401/403/invalid key/etc).

    These errors disable MVSEP service-wide.
    """

    pass


class MvsepTimeoutError(MvsepClientError):
    """Exception raised when MVSEP operations time out."""

    pass


class MvsepQueueFullError(MvsepClientError):
    """Exception raised when MVSEP queue is full (HTTP 400 with queue-full message).

    This is a retriable error that signals the worker should wait longer before retrying.
    """

    pass


class MvsepClient:
    """Async HTTP client for MVSEP Cloud API stem separation.

    Provides configurable two-stage stem separation:
    - Stage 1: Vocal/instrumental separation (configurable sep_type)
    - Stage 2: Optional reverb removal from vocals (can be skipped)

    Includes daily quota detection from API responses with UTC-day rollover
    and service-wide disable on non-retriable errors.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        enabled: Optional[bool] = None,
        stage1_sep_type: Optional[int] = None,
        stage1_add_opt1: Optional[int] = None,
        stage1_add_opt2: Optional[int] = None,
        stage2_sep_type: Optional[int] = None,
        stage2_add_opt1: Optional[int] = None,
        stage2_add_opt2: Optional[int] = None,
        http_timeout: Optional[int] = None,
        stage_timeout: Optional[int] = None,
        max_concurrent: Optional[int] = None,
    ) -> None:
        """Initialize MVSEP client.

        Args:
            api_token: MVSEP API token. Defaults to settings.SOW_MVSEP_API_KEY.
            enabled: Whether MVSEP is enabled. Defaults to settings.SOW_MVSEP_ENABLED.
            stage1_sep_type: Separation type for Stage 1. Defaults to settings.SOW_MVSEP_STAGE1_SEP_TYPE.
            stage1_add_opt1: Model option for Stage 1. Defaults to settings.SOW_MVSEP_STAGE1_ADD_OPT1.
            stage1_add_opt2: Additional option for Stage 1. Defaults to settings.SOW_MVSEP_STAGE1_ADD_OPT2.
            stage2_sep_type: Separation type for Stage 2 (None to skip). Defaults to settings.SOW_MVSEP_STAGE2_SEP_TYPE.
            stage2_add_opt1: Model option for Stage 2. Defaults to settings.SOW_MVSEP_STAGE2_ADD_OPT1.
            stage2_add_opt2: Additional option for Stage 2. Defaults to settings.SOW_MVSEP_STAGE2_ADD_OPT2.
            http_timeout: Seconds per HTTP request. Defaults to settings.SOW_MVSEP_HTTP_TIMEOUT.
            stage_timeout: Max seconds per stage. Defaults to settings.SOW_MVSEP_STAGE_TIMEOUT.
            max_concurrent: Max concurrent MVSEP API operations. Defaults to settings.SOW_MVSEP_MAX_CONCURRENT.
        """
        from ..config import settings

        self.api_token = api_token if api_token is not None else settings.SOW_MVSEP_API_KEY
        self.enabled = enabled if enabled is not None else settings.SOW_MVSEP_ENABLED
        self.stage1_sep_type = (
            stage1_sep_type if stage1_sep_type is not None else settings.SOW_MVSEP_STAGE1_SEP_TYPE
        )
        self.stage1_add_opt1 = (
            stage1_add_opt1 if stage1_add_opt1 is not None else settings.SOW_MVSEP_STAGE1_ADD_OPT1
        )
        self.stage1_add_opt2 = (
            stage1_add_opt2 if stage1_add_opt2 is not None else settings.SOW_MVSEP_STAGE1_ADD_OPT2
        )
        self.stage2_sep_type = (
            stage2_sep_type if stage2_sep_type is not None else settings.SOW_MVSEP_STAGE2_SEP_TYPE
        )
        self.stage2_add_opt1 = (
            stage2_add_opt1 if stage2_add_opt1 is not None else settings.SOW_MVSEP_STAGE2_ADD_OPT1
        )
        self.stage2_add_opt2 = (
            stage2_add_opt2 if stage2_add_opt2 is not None else settings.SOW_MVSEP_STAGE2_ADD_OPT2
        )
        self.http_timeout = (
            http_timeout if http_timeout is not None else settings.SOW_MVSEP_HTTP_TIMEOUT
        )
        self.stage_timeout = (
            stage_timeout if stage_timeout is not None else settings.SOW_MVSEP_STAGE_TIMEOUT
        )
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None else settings.SOW_MVSEP_MAX_CONCURRENT
        )

        self._disabled = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._quota_exhausted = False
        self._quota_reset_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        self._client = httpx.AsyncClient(timeout=self.http_timeout)

    @property
    def is_available(self) -> bool:
        """Check if MVSEP is available for use.

        Returns:
            True when enabled, api_token is non-empty, not disabled,
            and daily quota is not exhausted.
        """
        if not self.enabled:
            return False
        if not self.api_token:
            return False
        if self._disabled:
            return False
        if self._quota_exhausted:
            self._check_quota_reset()
            if self._quota_exhausted:
                return False
        return True

    def _check_quota_reset(self) -> None:
        """Reset quota-exhausted flag on new UTC day."""
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._quota_reset_utc < today_start:
            self._quota_exhausted = False
            self._quota_reset_utc = today_start
            logger.info("MVSEP daily quota reset for new UTC day")

    @property
    def is_quota_exhausted(self) -> bool:
        """True if daily quota is exhausted (will reset at UTC midnight).

        Distinguishes from ``_disabled`` (permanent). Callers use:
        - ``not is_available AND is_quota_exhausted`` -> wait (daily, resets)
        - ``not is_available AND not is_quota_exhausted`` -> fail (permanent)
        """
        if self._quota_exhausted:
            self._check_quota_reset()
        return self._quota_exhausted

    async def _submit_job(
        self,
        audio_path: Path,
        sep_type: int,
        add_opt1: int,
        add_opt2: Optional[int] = None,
        output_format: int = 2,
    ) -> str:
        """Submit a job to MVSEP API.

        Args:
            audio_path: Path to input audio file
            sep_type: Separation type code (e.g., 48 = MelBand Roformer, 40 = BS Roformer, 22 = Reverb Removal)
            add_opt1: Model option (specific to sep_type)
            add_opt2: Additional option for sep_type=22
            output_format: Output format (2 = FLAC 16-bit)

        Returns:
            Job hash string

        Raises:
            MvsepNonRetriableError: On 401/403/invalid key/insufficient credits
            MvsepClientError: On other HTTP errors
        """
        url = f"{MVSEP_API_BASE_URL}/create"

        data = {
            "api_token": self.api_token,
            "sep_type": str(sep_type),
            "add_opt1": str(add_opt1),
            "output_format": str(output_format),
        }
        if add_opt2 is not None:
            data["add_opt2"] = str(add_opt2)

        if not audio_path.exists():
            raise MvsepClientError(f"Audio file not found: {audio_path}")

        files = {
            "audiofile": (audio_path.name, open(audio_path, "rb"), "audio/mpeg"),
        }

        try:
            response = await self._client.post(url, data=data, files=files)
            response.raise_for_status()
            result = response.json()

            success = result.get("success", False)
            result_data = result.get("data", {})

            if not success:
                error_msg = result_data.get("message", "Unknown error")
                error_lower = error_msg.lower()
                if "invalid" in error_lower and "key" in error_lower:
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Invalid API key: {error_msg}")
                if "insufficient" in error_lower and "credit" in error_lower:
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Insufficient credits: {error_msg}")
                if _is_quota_exhausted(error_lower):
                    self._quota_exhausted = True
                    logger.warning(f"MVSEP daily quota exhausted: {error_msg}")
                    raise MvsepNonRetriableError(f"Daily quota exhausted: {error_msg}")
                raise MvsepClientError(f"MVSEP API error: {error_msg}")

            job_hash = result_data.get("hash")
            if not job_hash:
                raise MvsepClientError("No job hash in response")

            logger.debug(f"MVSEP job submitted: {job_hash}")
            return job_hash

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code in (401, 403):
                self._disabled = True
                raise MvsepNonRetriableError(f"Authentication failed: {status_code}") from e
            if status_code == 400:
                error_text = e.response.text.lower()
                if "queue" in error_text or "wait before adding" in error_text:
                    raise MvsepQueueFullError(f"MVSEP queue full: {e.response.text}") from e
                if _is_quota_exhausted(error_text):
                    self._quota_exhausted = True
                    logger.warning(f"MVSEP daily quota exhausted: {e.response.text}")
                    raise MvsepNonRetriableError(f"Daily quota exhausted: {e.response.text}") from e
            raise MvsepClientError(f"HTTP error {status_code}: {e.response.text}") from e
        except httpx.TimeoutException as e:
            raise MvsepClientError("Request timed out") from e
        except httpx.RequestError as e:
            raise MvsepClientError(f"Request failed: {e}") from e

    async def _poll_job(self, job_hash: str) -> dict:
        """Poll job status until complete or timeout.

        Args:
            job_hash: Job hash from submit_job

        Returns:
            Data dict with job results

        Raises:
            MvsepTimeoutError: When polling exceeds stage_timeout
            MvsepNonRetriableError: On terminal failure status
            MvsepClientError: On API errors
        """
        url = f"{MVSEP_API_BASE_URL}/get"
        poll_interval = 8.0
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.stage_timeout:
                raise MvsepTimeoutError(f"Polling timeout after {elapsed:.0f}s")

            try:
                response = await self._client.get(url, params={"hash": job_hash})
                response.raise_for_status()
                result = response.json()

            except httpx.HTTPStatusError as e:
                raise MvsepClientError(f"HTTP error {e.response.status_code}") from e
            except httpx.TimeoutException as e:
                raise MvsepClientError("Poll request timed out") from e
            except httpx.RequestError as e:
                raise MvsepClientError(f"Poll request failed: {e}") from e

            status = result.get("status", "unknown")

            if status == "done":
                return result
            elif status in ("failed", "error"):
                raise MvsepNonRetriableError(
                    f"MVSEP job failed: {result.get('data', {}).get('message', status)}"
                )
            elif status == "not_found":
                raise MvsepNonRetriableError(f"MVSEP job not found: {job_hash}")

            if not result.get("success", False):
                error_msg = result.get("data", {}).get("message", status)
                raise MvsepClientError(f"MVSEP poll error: {error_msg}")

            # Exponential backoff: 1.5x factor, max 30s
            poll_interval = min(poll_interval * 1.5, MVSEP_MAX_POLL_INTERVAL)
            await asyncio.sleep(poll_interval)

    async def _download_files(self, file_entries: list, output_dir: Path) -> list[Path]:
        """Download result files from MVSEP.

        Args:
            file_entries: List of file entry dicts with 'url' and optionally 'name'
            output_dir: Directory to save files

        Returns:
            List of downloaded file paths
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []

        for entry in file_entries:
            url = entry.get("url")
            if not url:
                continue

            # Determine filename
            filename = entry.get("name")
            if not filename:
                # Extract from URL
                from urllib.parse import urlparse

                parsed = urlparse(url)
                filename = Path(parsed.path).name or "download.bin"

            output_path = output_dir / filename

            try:
                async with self._client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(output_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                downloaded.append(output_path)
                logger.debug(f"Downloaded: {output_path}")
            except Exception as e:
                logger.warning(f"Failed to download {url}: {e}")

        return downloaded

    async def separate_vocals(
        self,
        input_path: Path,
        output_dir: Path,
        stage_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Run Stage 1: Separate vocals from instrumental.

        Uses configured sep_type and add_opt1/add_opt2 from settings.

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (vocals_path, instrumental_path)
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
            if stage_callback:
                stage_callback("mvsep_stage1_submitting")

            job_hash = await self._submit_job(
                audio_path=input_path,
                sep_type=self.stage1_sep_type,
                add_opt1=self.stage1_add_opt1,
                add_opt2=self.stage1_add_opt2,
                output_format=2,  # FLAC 16-bit
            )

            if stage_callback:
                stage_callback("mvsep_stage1_polling")

            result = await self._poll_job(job_hash)

            if stage_callback:
                stage_callback("mvsep_stage1_downloading")

            file_entries = result.get("data", {}).get("files", [])
            downloaded = await self._download_files(file_entries, output_dir)

            # Build map of download filenames to their API type (robust classification)
            entry_type_by_download: dict[str, str] = {}
            for entry in file_entries:
                download_name = (entry.get("download") or "").lower()
                file_type = entry.get("type", "").lower()
                if download_name and file_type:
                    entry_type_by_download[download_name] = file_type

            # Identify outputs by API type first, then filename fallback
            vocals_file: Optional[Path] = None
            instrumental_file: Optional[Path] = None

            for path in downloaded:
                name_lower = path.name.lower()

                # Primary: match by API type field using download filename
                matched = False
                for dl_name, file_type in entry_type_by_download.items():
                    if dl_name in name_lower:
                        if file_type == "vocals":
                            vocals_file = path
                        elif file_type in ("other", "instrumental", "accompaniment", "music"):
                            instrumental_file = path
                        matched = True
                        break

                if matched:
                    continue

                # Fallback: filename pattern matching
                if "vocal" in name_lower:
                    vocals_file = path
                elif (
                    "instrumental" in name_lower
                    or "accompaniment" in name_lower
                    or "other" in name_lower
                ):
                    instrumental_file = path

        return vocals_file, instrumental_file

    async def remove_reverb(
        self,
        vocals_path: Path,
        output_dir: Path,
        stage_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Run Stage 2: Remove reverb/echo from vocals.

        Uses configured sep_type and add_opt1/add_opt2 from settings.

        Args:
            vocals_path: Path to vocals file (Stage 1 output)
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (dry_vocals_path, reverb_path)
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
            if stage_callback:
                stage_callback("mvsep_stage2_submitting")

            job_hash = await self._submit_job(
                audio_path=vocals_path,
                sep_type=self.stage2_sep_type,
                add_opt1=self.stage2_add_opt1,
                add_opt2=self.stage2_add_opt2,
                output_format=2,  # FLAC 16-bit
            )

            if stage_callback:
                stage_callback("mvsep_stage2_polling")

            result = await self._poll_job(job_hash)

            if stage_callback:
                stage_callback("mvsep_stage2_downloading")

            file_entries = result.get("data", {}).get("files", [])
            downloaded = await self._download_files(file_entries, output_dir)

            # Identify outputs by filename
            dry_vocals_file: Optional[Path] = None
            reverb_file: Optional[Path] = None

            for path in downloaded:
                name_lower = path.name.lower()
                if any(
                    x in name_lower for x in ["no reverb", "noreverb", "no_echo", "no echo", "dry"]
                ):
                    dry_vocals_file = path
                elif "reverb" in name_lower or "echo" in name_lower:
                    reverb_file = path

            # Fallback: if dry not found but we have files, use first as dry
            if not dry_vocals_file and downloaded:
                dry_vocals_file = downloaded[0]

        return dry_vocals_file, reverb_file

    async def separate_stems(
        self,
        input_path: Path,
        output_dir: Path,
        stage_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Run full two-stage stem separation pipeline.

        Stage 2 is optional; if stage2_sep_type is None, only Stage 1 runs
        and vocals_dry_path will be None.

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (vocals_dry_path, vocals_path, instrumental_path).
            vocals_dry_path is None when Stage 2 is skipped.
            vocals_path is Stage 1 vocals (before de-reverb).
        """
        stage1_dir = output_dir / "stage1"
        stage2_dir = output_dir / "stage2"

        # Stage 1: Vocal separation
        vocals_file, instrumental_file = await self.separate_vocals(
            input_path, stage1_dir, stage_callback
        )

        if not vocals_file:
            raise MvsepClientError("Stage 1 failed: No vocals file produced")

        # Stage 2: De-reverb (optional)
        if self.stage2_sep_type is None:
            logger.info("MVSEP Stage 2 disabled (stage2_sep_type not set), skipping")
            return None, vocals_file, instrumental_file

        dry_vocals_file, _ = await self.remove_reverb(vocals_file, stage2_dir, stage_callback)

        if not dry_vocals_file:
            raise MvsepClientError("Stage 2 failed: No dry vocals file produced")

        return dry_vocals_file, vocals_file, instrumental_file

    async def aclose(self) -> None:
        """Close httpx.AsyncClient connection pool."""
        await self._client.aclose()
        logger.info("MVSEP client closed")
