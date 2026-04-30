"""MVSEP Cloud API client for stem separation.

Async client using httpx.AsyncClient for cloud-based vocal stem separation.
Provides two-stage separation: Stage 1 (BS Roformer) + Stage 2 (Reverb Removal).
Follows the Qwen3Client pattern from services/qwen3_client.py.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

MVSEP_API_BASE_URL = "https://api.mvsep.com"
MVSEP_MAX_POLL_INTERVAL = 30.0  # Maximum seconds between poll attempts


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


class MvsepClient:
    """Async HTTP client for MVSEP Cloud API stem separation.

    Provides two-stage stem separation:
    - Stage 1: BS Roformer vocal/instrumental separation
    - Stage 2: Reverb removal from vocals

    Includes daily cost tracking with UTC-day rollover and service-wide
    disable on non-retriable errors.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        enabled: Optional[bool] = None,
        vocal_model: Optional[int] = None,
        dereverb_model: Optional[int] = None,
        http_timeout: Optional[int] = None,
        stage_timeout: Optional[int] = None,
        daily_job_limit: Optional[int] = None,
    ) -> None:
        """Initialize MVSEP client.

        Args:
            api_token: MVSEP API token. Defaults to settings.SOW_MVSEP_API_KEY.
            enabled: Whether MVSEP is enabled. Defaults to settings.SOW_MVSEP_ENABLED.
            vocal_model: Vocal separation model ID. Defaults to settings.SOW_MVSEP_VOCAL_MODEL.
            dereverb_model: De-reverb model ID. Defaults to settings.SOW_MVSEP_DEREVERB_MODEL.
            http_timeout: Seconds per HTTP request. Defaults to settings.SOW_MVSEP_HTTP_TIMEOUT.
            stage_timeout: Max seconds per stage. Defaults to settings.SOW_MVSEP_STAGE_TIMEOUT.
            daily_job_limit: Max jobs per UTC day. Defaults to settings.SOW_MVSEP_DAILY_JOB_LIMIT.
        """
        from ..config import settings

        self.api_token = api_token if api_token is not None else settings.SOW_MVSEP_API_KEY
        self.enabled = enabled if enabled is not None else settings.SOW_MVSEP_ENABLED
        self.vocal_model = vocal_model if vocal_model is not None else settings.SOW_MVSEP_VOCAL_MODEL
        self.dereverb_model = dereverb_model if dereverb_model is not None else settings.SOW_MVSEP_DEREVERB_MODEL
        self.http_timeout = http_timeout if http_timeout is not None else settings.SOW_MVSEP_HTTP_TIMEOUT
        self.stage_timeout = stage_timeout if stage_timeout is not None else settings.SOW_MVSEP_STAGE_TIMEOUT
        self.daily_job_limit = daily_job_limit if daily_job_limit is not None else settings.SOW_MVSEP_DAILY_JOB_LIMIT

        self._disabled = False
        self._daily_job_count = 0
        self._daily_reset_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        self._client = httpx.AsyncClient(timeout=self.http_timeout)

    @property
    def is_available(self) -> bool:
        """Check if MVSEP is available for use.

        Returns:
            True when enabled, api_token is non-empty, not disabled,
            and daily job limit is not exceeded.
        """
        if not self.enabled:
            return False
        if not self.api_token:
            return False
        if self._disabled:
            return False
        return self._check_daily_limit()

    def _check_daily_limit(self) -> bool:
        """Check if under daily job limit, resetting counter on new UTC day.

        Returns:
            True if under daily job limit.
        """
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._daily_reset_utc < today_start:
            self._daily_job_count = 0
            self._daily_reset_utc = today_start
            logger.info("MVSEP daily job count reset for new UTC day")
        return self._daily_job_count < self.daily_job_limit

    def _increment_daily_count(self) -> None:
        """Increment the daily job count."""
        self._daily_job_count += 1
        logger.debug(f"MVSEP daily job count: {self._daily_job_count}/{self.daily_job_limit}")

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
            sep_type: Separation type code (40 = BS Roformer, 22 = Reverb Removal)
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
            "sep_type": sep_type,
            "add_opt1": add_opt1,
            "output_format": output_format,
        }
        if add_opt2 is not None:
            data["add_opt2"] = add_opt2

        files = {
            "audio_file": (audio_path.name, open(audio_path, "rb"), "audio/mpeg"),
        }

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            response = await self._client.post(
                url, data=data, files=files, headers=headers
            )
            response.raise_for_status()
            result = response.json()

            if "error" in result and result["error"]:
                error_msg = result.get("message", result["error"])
                if "invalid" in error_msg.lower() and "key" in error_msg.lower():
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Invalid API key: {error_msg}")
                if "insufficient" in error_msg.lower() and "credit" in error_msg.lower():
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Insufficient credits: {error_msg}")
                raise MvsepClientError(f"MVSEP API error: {error_msg}")

            job_hash = result.get("hash") or result.get("job_hash")
            if not job_hash:
                raise MvsepClientError("No job hash in response")

            logger.debug(f"MVSEP job submitted: {job_hash}")
            return job_hash

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code in (401, 403):
                self._disabled = True
                raise MvsepNonRetriableError(f"Authentication failed: {status_code}") from e
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
        poll_interval = 5.0
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.stage_timeout:
                raise MvsepTimeoutError(f"Polling timeout after {elapsed:.0f}s")

            try:
                response = await self._client.get(
                    url, params={"hash": job_hash}
                )
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
                raise MvsepNonRetriableError(f"MVSEP job failed: {result.get('message', status)}")
            elif status == "not_found":
                raise MvsepNonRetriableError(f"MVSEP job not found: {job_hash}")

            # Exponential backoff: 1.5x factor, max 30s
            poll_interval = min(poll_interval * 1.5, MVSEP_MAX_POLL_INTERVAL)
            await asyncio.sleep(poll_interval)

    async def _download_files(
        self, file_entries: list, output_dir: Path
    ) -> list[Path]:
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

        Uses BS Roformer model (sep_type=40).

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (vocals_path, instrumental_path)
        """
        self._increment_daily_count()

        if stage_callback:
            stage_callback("mvsep_stage1_submitting")

        job_hash = await self._submit_job(
            audio_path=input_path,
            sep_type=40,
            add_opt1=self.vocal_model,
            output_format=2,  # FLAC 16-bit
        )

        if stage_callback:
            stage_callback("mvsep_stage1_polling")

        result = await self._poll_job(job_hash)

        if stage_callback:
            stage_callback("mvsep_stage1_downloading")

        file_entries = result.get("files", [])
        downloaded = await self._download_files(file_entries, output_dir)

        # Identify outputs by filename
        vocals_file: Optional[Path] = None
        instrumental_file: Optional[Path] = None

        for path in downloaded:
            name_lower = path.name.lower()
            if "vocal" in name_lower:
                vocals_file = path
            elif "instrumental" in name_lower or "accompaniment" in name_lower:
                instrumental_file = path

        return vocals_file, instrumental_file

    async def remove_reverb(
        self,
        vocals_path: Path,
        output_dir: Path,
        stage_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Run Stage 2: Remove reverb/echo from vocals.

        Uses FoxJoy MDX23C model (sep_type=22, add_opt2=1).

        Args:
            vocals_path: Path to vocals file (Stage 1 output)
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (dry_vocals_path, reverb_path)
        """
        if stage_callback:
            stage_callback("mvsep_stage2_submitting")

        job_hash = await self._submit_job(
            audio_path=vocals_path,
            sep_type=22,
            add_opt1=self.dereverb_model,
            add_opt2=1,  # Reverb removal mode
            output_format=2,  # FLAC 16-bit
        )

        if stage_callback:
            stage_callback("mvsep_stage2_polling")

        result = await self._poll_job(job_hash)

        if stage_callback:
            stage_callback("mvsep_stage2_downloading")

        file_entries = result.get("files", [])
        downloaded = await self._download_files(file_entries, output_dir)

        # Identify outputs by filename
        dry_vocals_file: Optional[Path] = None
        reverb_file: Optional[Path] = None

        for path in downloaded:
            name_lower = path.name.lower()
            if any(x in name_lower for x in ["no reverb", "noreverb", "no_echo", "no echo", "dry"]):
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

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output files
            stage_callback: Optional callback for stage updates

        Returns:
            Tuple of (vocals_clean_path, vocals_reverb_path, instrumental_path)
            vocals_reverb_path is Stage 1 vocals (before de-reverb)
        """
        stage1_dir = output_dir / "stage1"
        stage2_dir = output_dir / "stage2"

        # Stage 1: Vocal separation
        vocals_file, instrumental_file = await self.separate_vocals(
            input_path, stage1_dir, stage_callback
        )

        if not vocals_file:
            raise MvsepClientError("Stage 1 failed: No vocals file produced")

        # Stage 2: De-reverb
        dry_vocals_file, _ = await self.remove_reverb(
            vocals_file, stage2_dir, stage_callback
        )

        if not dry_vocals_file:
            raise MvsepClientError("Stage 2 failed: No dry vocals file produced")

        return dry_vocals_file, vocals_file, instrumental_file

    async def aclose(self) -> None:
        """Close httpx.AsyncClient connection pool."""
        await self._client.aclose()
        logger.info("MVSEP client closed")
