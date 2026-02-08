"""In-memory job queue for asynchronous processing."""

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..config import settings

logger = logging.getLogger(__name__)


def _compute_lrc_cache_key(content_hash: str, lyrics_text: str) -> str:
    """Compute cache key for LRC generation based on audio hash and lyrics.

    The cache key is a hash of both the audio content hash and the lyrics text.
    This ensures that if either the audio or lyrics change, a new LRC is generated.

    Args:
        content_hash: Hash of the audio file content
        lyrics_text: The scraped lyrics text

    Returns:
        Cache key string
    """
    # Create a composite string of both inputs
    lyrics_hash = hashlib.sha256(lyrics_text.encode("utf-8")).hexdigest()[:16]
    composite = f"{content_hash}:{lyrics_hash}"
    # Return a shorter hash of the composite
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:32]


from ..models import (
    AnalyzeJobRequest,
    JobResult,
    JobStatus,
    JobType,
    LrcJobRequest,
    Section,
)
from ..storage.cache import CacheManager
from ..storage.r2 import R2Client
# Optional imports for heavy dependencies
try:
    from .analyzer import analyze_audio
    from .separator import separate_stems
except ImportError:
    analyze_audio = None
    separate_stems = None

# Optional LRC imports - require whisper and openai
try:
    from .lrc import LRCWorkerError, generate_lrc
except ImportError:
    LRCWorkerError = Exception
    generate_lrc = None


@dataclass
class Job:
    """Represents a job in the queue."""

    id: str
    type: JobType
    status: JobStatus
    request: Union[AnalyzeJobRequest, LrcJobRequest]
    result: Optional[JobResult] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    progress: float = 0.0
    stage: str = ""


class JobQueue:
    """In-memory job queue with concurrent execution control."""

    def __init__(
        self,
        max_concurrent: int = 2,
        cache_dir: Path = Path("/cache"),
    ):
        """Initialize job queue.

        Args:
            max_concurrent: Maximum number of concurrent jobs
            cache_dir: Directory for caching results
        """
        self.max_concurrent = max_concurrent
        self.cache_manager = CacheManager(cache_dir)
        self.r2_client: Optional[R2Client] = None
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False

    def initialize_r2(self, bucket: str, endpoint_url: str) -> None:
        """Initialize R2 client.

        Args:
            bucket: R2 bucket name
            endpoint_url: R2 endpoint URL
        """
        self.r2_client = R2Client(bucket, endpoint_url)

    async def submit(
        self, job_type: JobType, request: Union[AnalyzeJobRequest, LrcJobRequest]
    ) -> Job:
        """Submit a new job to the queue.

        Args:
            job_type: Type of job (analyze or lrc)
            request: Job request data

        Returns:
            Created job instance
        """
        job_id = f"job_{uuid.uuid4().hex[:12]}"

        job = Job(
            id=job_id,
            type=job_type,
            status=JobStatus.QUEUED,
            request=request,
        )

        self._jobs[job_id] = job
        await self._queue.put(job_id)

        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID.

        Args:
            job_id: Job ID to look up

        Returns:
            Job instance or None if not found
        """
        return self._jobs.get(job_id)

    async def process_jobs(self) -> None:
        """Background task that processes queued jobs."""
        self._running = True

        while self._running:
            try:
                # Wait for a job with timeout to allow checking _running
                job_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                job = self._jobs.get(job_id)

                if job:
                    # Process job in background with semaphore
                    asyncio.create_task(self._process_job_with_semaphore(job))

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _process_job_with_semaphore(self, job: Job) -> None:
        """Process a job with concurrency control."""
        async with self._semaphore:
            if job.type == JobType.ANALYZE:
                await self._process_analysis_job(job)
            elif job.type == JobType.LRC:
                await self._process_lrc_job(job)

    async def _process_analysis_job(self, job: Job) -> None:
        """Process an analysis job.

        Args:
            job: Job to process
        """
        job_start_time = time.time()
        logger.info(f"Starting analysis job {job.id} for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "downloading"
        job.progress = 0.1

        request = job.request
        if not isinstance(request, AnalyzeJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for analysis job"
            job.updated_at = datetime.now(timezone.utc)
            return

        # Check if analysis dependencies are available
        if analyze_audio is None or separate_stems is None:
            job.status = JobStatus.FAILED
            job.error_message = (
                "Analysis dependencies not available (librosa, allin1, demucs)"
            )
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            return

        try:
            # Initialize R2 if not done
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            # Download audio from R2
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                audio_path = temp_path / "audio.mp3"

                if self.r2_client:
                    logger.info(f"[{job.id}] Downloading audio from R2...")
                    download_start = time.time()
                    await self.r2_client.download_audio(request.audio_url, audio_path)
                    download_elapsed = time.time() - download_start
                    logger.info(f"[{job.id}] Audio download completed in {download_elapsed:.2f}s")

                job.stage = "analyzing"
                job.progress = 0.3

                # Run analysis
                logger.info(f"[{job.id}] Starting audio analysis...")
                analysis_result = await analyze_audio(
                    audio_path,
                    self.cache_manager,
                    force=request.options.force,
                )

                job.progress = 0.6

                # Generate stems if requested
                stems_url = None
                if request.options.generate_stems:
                    job.stage = "separating"
                    logger.info(f"[{job.id}] Starting stem separation...")

                    stems_dir = temp_path / "stems"
                    await separate_stems(
                        audio_path,
                        stems_dir,
                        model=request.options.stem_model,
                        device=settings.SOW_DEMUCS_DEVICE,
                        cache_manager=self.cache_manager,
                        content_hash=request.content_hash,
                        force=request.options.force,
                    )

                    job.progress = 0.8

                    # Upload stems to R2
                    if self.r2_client:
                        hash_prefix = request.content_hash[:12]
                        stems_url = await self.r2_client.upload_stems(
                            hash_prefix, stems_dir
                        )

                    job.progress = 0.9

                # Upload analysis result to R2
                if self.r2_client:
                    hash_prefix = request.content_hash[:12]
                    analysis_data = {**analysis_result}
                    if stems_url:
                        analysis_data["stems_url"] = stems_url
                    await self.r2_client.upload_analysis_result(
                        hash_prefix, analysis_data
                    )

                # Build job result
                sections = [
                    Section(**s) for s in analysis_result.get("sections", [])
                ]

                job.result = JobResult(
                    duration_seconds=analysis_result.get("duration_seconds"),
                    tempo_bpm=analysis_result.get("tempo_bpm"),
                    musical_key=analysis_result.get("musical_key"),
                    musical_mode=analysis_result.get("musical_mode"),
                    key_confidence=analysis_result.get("key_confidence"),
                    loudness_db=analysis_result.get("loudness_db"),
                    beats=analysis_result.get("beats"),
                    downbeats=analysis_result.get("downbeats"),
                    sections=sections if sections else None,
                    embeddings_shape=analysis_result.get("embeddings_shape"),
                    stems_url=stems_url,
                )

                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"

                total_elapsed = time.time() - job_start_time
                logger.info(f"[{job.id}] Analysis job completed in {total_elapsed:.2f}s")

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "error"
            logger.error(f"[{job.id}] Analysis job failed: {e}")

        finally:
            job.updated_at = datetime.now(timezone.utc)

    async def _process_lrc_job(self, job: Job) -> None:
        """Process an LRC generation job.

        Downloads audio from R2, optionally uses vocals stem for cleaner
        transcription, runs Whisper + LLM alignment, uploads LRC to R2.

        Args:
            job: Job to process
        """
        job_start_time = time.time()
        logger.info(f"Starting LRC job {job.id} for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "starting"
        job.progress = 0.1

        request = job.request
        if not isinstance(request, LrcJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for LRC job"
            job.updated_at = datetime.now(timezone.utc)
            return

        # Check if LRC dependencies are available
        if generate_lrc is None:
            job.status = JobStatus.FAILED
            job.error_message = "LRC dependencies not available (whisper, openai)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            return

        # Compute composite cache key based on audio hash + lyrics hash
        lrc_cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text)
        logger.info(f"[{job.id}] LRC cache key: {lrc_cache_key} (audio_hash={request.content_hash[:12]}...)")

        try:
            # Check cache first (unless force=True)
            if not request.options.force:
                cached = self.cache_manager.get_lrc_result(lrc_cache_key)
                if cached:
                    logger.info(f"[{job.id}] LRC cache hit - returning cached result")
                    job.result = JobResult(
                        lrc_url=cached.get("lrc_url"),
                        line_count=cached.get("line_count"),
                    )
                    job.status = JobStatus.COMPLETED
                    job.progress = 1.0
                    job.stage = "cached"
                    job.updated_at = datetime.now(timezone.utc)
                    return

            # Initialize R2 if not done
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                hash_prefix = request.content_hash[:12]

                # Download audio from R2
                job.stage = "downloading"
                job.progress = 0.2
                job.updated_at = datetime.now(timezone.utc)

                audio_path = temp_path / "audio.mp3"
                if self.r2_client:
                    logger.info(f"[{job.id}] Downloading audio from R2...")
                    download_start = time.time()
                    await self.r2_client.download_audio(request.audio_url, audio_path)
                    download_elapsed = time.time() - download_start
                    logger.info(f"[{job.id}] Audio download completed in {download_elapsed:.2f}s")

                # Check if vocals stem exists and should be used
                transcription_path = audio_path
                if request.options.use_vocals_stem and self.r2_client:
                    vocals_url = f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/vocals.wav"
                    if await self.r2_client.check_exists(vocals_url):
                        vocals_path = temp_path / "vocals.wav"
                        await self.r2_client.download_audio(vocals_url, vocals_path)
                        transcription_path = vocals_path
                        job.stage = "using_vocals_stem"

                # Check for cached Whisper transcription (audio hash only, not lyrics)
                cached_phrases = None
                if not request.options.force:
                    cached_data = self.cache_manager.get_whisper_transcription(request.content_hash)
                    if cached_data:
                        from .lrc import WhisperPhrase
                        cached_phrases = [WhisperPhrase(**p) for p in cached_data]
                        logger.info(f"[{job.id}] Whisper cache hit - using {len(cached_phrases)} cached phrases")
                        job.stage = "transcription_cached"
                    else:
                        logger.info(f"[{job.id}] Whisper cache miss - will run transcription")

                # Run Whisper transcription (or use cached)
                job.stage = "transcribing"
                job.progress = 0.4
                job.updated_at = datetime.now(timezone.utc)

                lrc_path = temp_path / "lyrics.lrc"
                lrc_path, line_count, whisper_phrases = await generate_lrc(
                    transcription_path,
                    request.lyrics_text,
                    request.options,
                    output_path=lrc_path,
                    cached_phrases=cached_phrases,
                )

                # Cache the Whisper transcription for future use (if not using cache)
                if cached_phrases is None and whisper_phrases:
                    phrases_data = [
                        {"text": p.text, "start": p.start, "end": p.end}
                        for p in whisper_phrases
                    ]
                    self.cache_manager.save_whisper_transcription(request.content_hash, phrases_data)
                    logger.info(f"[{job.id}] Whisper transcription cached for future use")

                job.stage = "uploading"
                job.progress = 0.8
                job.updated_at = datetime.now(timezone.utc)

                # Upload LRC to R2
                lrc_url = None
                if self.r2_client:
                    lrc_url = await self.r2_client.upload_lrc(hash_prefix, lrc_path)

                # Save to cache using composite key (audio hash + lyrics hash)
                cache_result = {"lrc_url": lrc_url, "line_count": line_count}
                self.cache_manager.save_lrc_result(lrc_cache_key, cache_result)
                logger.info(f"[{job.id}] LRC result cached with key: {lrc_cache_key}")

                # Set job result
                job.result = JobResult(lrc_url=lrc_url, line_count=line_count)
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"

                total_elapsed = time.time() - job_start_time
                logger.info(f"[{job.id}] LRC job completed in {total_elapsed:.2f}s")

        except LRCWorkerError as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "lrc_error"
            logger.error(f"[{job.id}] LRC job failed: {e}")
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = f"Unexpected error: {e}"
            job.stage = "error"
            logger.error(f"[{job.id}] LRC job failed with unexpected error: {e}")

        job.updated_at = datetime.now(timezone.utc)

    def stop(self) -> None:
        """Stop processing jobs."""
        self._running = False
