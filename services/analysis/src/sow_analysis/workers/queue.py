"""In-memory job queue for asynchronous processing."""

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..config import settings
from ..logging_config import set_job_id

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
    Job,
    JobResult,
    JobStatus,
    JobType,
    LrcJobRequest,
    Section,
    StemSeparationJobRequest,
)
from ..storage.cache import CacheManager
from ..storage.db import JobStore
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

# Optional stem separation imports - require audio-separator
try:
    from .stem_separation import StemSeparationWorkerError, process_stem_separation
except ImportError:
    StemSeparationWorkerError = Exception
    process_stem_separation = None


class JobQueue:
    """In-memory job queue with concurrent execution control."""

    def __init__(
        self,
        max_concurrent_analysis: int = 1,
        max_concurrent_lrc: int = 2,
        max_concurrent_stem_separation: int = 1,
        cache_dir: Path = Path("/cache"),
        db_path: Optional[Path] = None,
    ):
        """Initialize job queue.

        Args:
            max_concurrent_analysis: Maximum concurrent analysis jobs (1 = serialized)
            max_concurrent_lrc: Maximum concurrent LRC jobs
            max_concurrent_stem_separation: Maximum concurrent stem separation jobs (1 = serialized)
            cache_dir: Directory for caching results
            db_path: Path to job database (default: cache_dir / "jobs.db")
        """
        self.max_concurrent_analysis = max_concurrent_analysis
        self.max_concurrent_lrc = max_concurrent_lrc
        self.max_concurrent_stem_separation = max_concurrent_stem_separation
        self.cache_manager = CacheManager(cache_dir)
        self.r2_client: Optional[R2Client] = None
        self._separator_wrapper: Optional[Any] = None
        self._mvsep_client: Optional[Any] = None
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # Analysis jobs use lock for serialization (high memory/CPU with allin1)
        self._analysis_lock = asyncio.Lock()
        # LRC jobs use semaphore for concurrency (faster-whisper is more efficient)
        self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc)
        # Stem separation uses lock for serialization (high memory/CPU with vocal model)
        self._stem_separation_lock = asyncio.Lock()
        self._running = False
        self._logging_task: Optional[asyncio.Task] = None
        self._log_interval_seconds: float = 60.0

        # Persistent job store
        db_path = db_path if db_path is not None else cache_dir / "jobs.db"
        self.job_store = JobStore(db_path)

    def initialize_r2(self, bucket: str, endpoint_url: str) -> None:
        """Initialize R2 client.

        Args:
            bucket: R2 bucket name
            endpoint_url: R2 endpoint URL
        """
        self.r2_client = R2Client(bucket, endpoint_url)
        # Set cache manager on job store for job reconstruction
        self.job_store.set_cache_manager(self.cache_manager)

    def set_separator_wrapper(self, separator_wrapper: Any) -> None:
        """Set the audio separator wrapper for stem separation jobs.

        Args:
            separator_wrapper: AudioSeparatorWrapper instance
        """
        self._separator_wrapper = separator_wrapper

    def set_mvsep_client(self, mvsep_client: Any) -> None:
        """Set the MVSEP client for cloud stem separation.

        Args:
            mvsep_client: MvsepClient instance
        """
        self._mvsep_client = mvsep_client

    async def initialize(self) -> None:
        """Initialize persistent store and recover interrupted jobs."""
        await self.job_store.initialize()

        # Purge old completed/failed jobs
        purged = await self.job_store.purge_old_jobs(max_age_days=7)
        if purged:
            logger.info(f"Purged {purged} old jobs from database")

        # Recover interrupted jobs (were PROCESSING when service died)
        interrupted = await self.job_store.get_interrupted_jobs()
        for job in interrupted:
            logger.info(f"Recovering interrupted job {job.id} (was {job.status})")
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.stage = "requeued"
            job.updated_at = datetime.now(timezone.utc)

            self._jobs[job.id] = job
            await self._queue.put(job.id)
            # Update DB to reflect requeued status
            try:
                await self.job_store.update_job(
                    job.id, status="queued", progress=0.0, stage="requeued"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in DB during recovery: {e}")

        if interrupted:
            logger.info(f"Recovered {len(interrupted)} interrupted jobs")

        # Load queued jobs into memory (but don't re-add to queue, they're already in DB)
        queued = await self.job_store.get_queued_jobs()
        for job in queued:
            # Only add if not already in _jobs (e.g., from recovery above)
            if job.id not in self._jobs:
                job.stage = "requeued"
                self._jobs[job.id] = job
                await self._queue.put(job.id)
                # Update DB to reflect requeued stage
                try:
                    await self.job_store.update_job(job.id, stage="requeued")
                except Exception as e:
                    logger.error(f"Failed to update job {job.id} stage in DB: {e}")

        if queued:
            logger.info(f"Loaded {len(queued)} queued jobs from database")

    async def submit(
        self,
        job_type: JobType,
        request: Union[AnalyzeJobRequest, LrcJobRequest, StemSeparationJobRequest],
    ) -> Job:
        """Submit a new job to the queue.

        Args:
            job_type: Type of job (analyze, lrc, or stem_separation)
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

        # Persist job to database
        try:
            await self.job_store.insert_job(job)
        except Exception as e:
            logger.error(f"Failed to persist job {job_id} to database: {e}")

        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID.

        Args:
            job_id: Job ID to look up

        Returns:
            Job instance or None if not found
        """
        # Try in-memory cache first (for active jobs with live progress)
        job = self._jobs.get(job_id)
        if job:
            return job

        # Fall back to DB for completed/failed jobs that may have been evicted from memory
        try:
            return await self.job_store.get_job(job_id)
        except Exception as e:
            logger.error(f"Failed to retrieve job {job_id} from database: {e}")
            return None

    async def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        job_type: Optional[JobType] = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs with optional filtering.

        Args:
            status: Filter by job status
            job_type: Filter by job type
            limit: Maximum number of jobs to return

        Returns:
            List of jobs matching filters
        """
        try:
            return await self.job_store.list_jobs(status, job_type, limit)
        except Exception as e:
            logger.error(f"Failed to list jobs from database: {e}")
            return []

    async def process_jobs(self) -> None:
        """Background task that processes queued jobs."""
        self._running = True
        self._start_periodic_logging()

        start_delay = settings.SOW_QUEUE_START_DELAY_SECONDS
        if start_delay > 0:
            logger.info(
                f"Queue processing paused for {start_delay}s — use this window to cancel/clear jobs"
            )
            for _ in range(start_delay):
                if not self._running:
                    return
                await asyncio.sleep(1.0)
            logger.info("Queue processing starting")

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
        # Check if job was cancelled before processing
        job_id = job.id
        current_job = self._jobs.get(job_id)
        if current_job and current_job.status == JobStatus.CANCELLED:
            logger.info(f"Skipping cancelled job {job_id}")
            return

        if job.type == JobType.ANALYZE:
            # Analysis jobs use lock for serialization (allin1 is memory/CPU intensive)
            async with self._analysis_lock:
                await self._process_analysis_job(job)
        elif job.type == JobType.LRC:
            # LRC jobs use semaphore for concurrency (faster-whisper is more efficient)
            async with self._lrc_semaphore:
                await self._process_lrc_job(job)
        elif job.type == JobType.STEM_SEPARATION:
            # Stem separation jobs use lock for serialization (BS-Roformer is memory/CPU intensive)
            async with self._stem_separation_lock:
                await self._process_stem_separation_job(job)

        # Schedule cleanup for finished jobs (to prevent unbounded memory growth)
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            asyncio.create_task(self._cleanup_finished_job(job.id))

    async def _cleanup_finished_job(self, job_id: str, delay: float = 300.0):
        """Remove finished job from in-memory cache after delay.

        Args:
            job_id: Job ID to clean up
            delay: Delay in seconds before cleanup (default: 5 minutes)
        """
        await asyncio.sleep(delay)
        self._jobs.pop(job_id, None)
        logger.debug(f"Cleaned up finished job {job_id} from in-memory cache")

    async def _process_analysis_job(self, job: Job) -> None:
        """Process an analysis job.

        Args:
            job: Job to process
        """
        set_job_id(job.id)
        job_start_time = time.time()
        logger.info(f"Starting analysis job for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "downloading"
        job.progress = 0.1

        # Persist state change to database
        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="downloading", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, AnalyzeJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for analysis job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id, status="failed", error_message="Invalid request type"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Check if analysis dependencies are available
        if analyze_audio is None or separate_stems is None:
            job.status = JobStatus.FAILED
            job.error_message = "Analysis dependencies not available (librosa, allin1, demucs)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="Analysis dependencies not available (librosa, allin1, demucs)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
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
                    logger.info("Downloading audio from R2...")
                    download_start = time.time()
                    await self.r2_client.download_audio(request.audio_url, audio_path)
                    download_elapsed = time.time() - download_start
                    logger.info(f"Audio download completed in {download_elapsed:.2f}s")

                job.stage = "analyzing"
                job.progress = 0.3

                # Run analysis
                logger.info("Starting audio analysis...")
                analysis_result = await analyze_audio(
                    audio_path,
                    self.cache_manager,
                    request.content_hash,
                )

                job.progress = 0.6

                # Generate stems if requested
                stems_url = None
                if request.options.generate_stems:
                    job.stage = "separating"
                    logger.info("Starting stem separation...")

                    stems_dir = temp_path / "stems"
                    await separate_stems(
                        audio_path,
                        stems_dir,
                        model=request.options.stem_model,
                        device=settings.SOW_DEMUCS_DEVICE,
                        cache_manager=self.cache_manager,
                        content_hash=request.content_hash,
                    )

                    job.progress = 0.8

                    # Upload stems to R2
                    if self.r2_client:
                        hash_prefix = request.content_hash[:12]
                        stems_url = await self.r2_client.upload_stems(hash_prefix, stems_dir)

                    job.progress = 0.9

                # Upload analysis result to R2
                if self.r2_client:
                    hash_prefix = request.content_hash[:12]
                    analysis_data = {**analysis_result}
                    if stems_url:
                        analysis_data["stems_url"] = stems_url
                    await self.r2_client.upload_analysis_result(hash_prefix, analysis_data)

                # Build job result
                sections = [Section(**s) for s in analysis_result.get("sections", [])]

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
                logger.info(f"Analysis job completed in {total_elapsed:.2f}s")

                # Persist completion to database
                try:
                    await self.job_store.update_job(
                        job.id,
                        status="completed",
                        progress=1.0,
                        stage="complete",
                        result_json=job.result.model_dump_json() if job.result else None,
                    )
                except Exception as e:
                    logger.error(f"Failed to update job {job.id} in database: {e}")

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "error"
            logger.error(f"Analysis job failed: {e}")

            # Persist failure to database
            try:
                await self.job_store.update_job(
                    job.id, status="failed", stage="error", error_message=str(e)
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

        finally:
            job.updated_at = datetime.now(timezone.utc)

    async def _process_lrc_job(self, job: Job) -> None:
        """Process an LRC generation job.

        Downloads audio from R2, optionally uses vocals stem for cleaner
        transcription, runs Whisper + LLM alignment, uploads LRC to R2.

        Args:
            job: Job to process
        """
        set_job_id(job.id)
        job_start_time = time.time()
        logger.info(f"Starting LRC job for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "starting"
        job.progress = 0.1

        # Persist state change to database
        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="starting", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, LrcJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for LRC job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id, status="failed", error_message="Invalid request type for LRC job"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Check if LRC dependencies are available
        if generate_lrc is None:
            job.status = JobStatus.FAILED
            job.error_message = "LRC dependencies not available (whisper, openai)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="LRC dependencies not available (whisper, openai)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Log LRC generation strategy
        if request.youtube_url:
            logger.info(
                f"YouTube URL provided: {request.youtube_url} "
                f"— will try YouTube transcript first, Whisper as fallback"
            )
        else:
            logger.info("No YouTube URL — will use Whisper transcription directly")

        # Compute composite cache key based on audio hash + lyrics hash
        lrc_cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text)
        logger.info(
            f"LRC cache key: {lrc_cache_key} (audio_hash={request.content_hash[:12]}...)"
        )

        try:
            # Check LRC result cache first (unless force=True - allows prompt improvements)
            if not request.options.force:
                cached = self.cache_manager.get_lrc_result(lrc_cache_key)
                if cached:
                    logger.info("LRC cache hit - returning cached result")
                    job.result = JobResult(
                        lrc_url=cached.get("lrc_url"),
                        line_count=cached.get("line_count"),
                    )
                    job.status = JobStatus.COMPLETED
                    job.progress = 1.0
                    job.stage = "cached"
                    job.updated_at = datetime.now(timezone.utc)

                    # Persist cache hit result
                    try:
                        await self.job_store.update_job(
                            job.id,
                            status="completed",
                            progress=1.0,
                            stage="cached",
                            result_json=job.result.model_dump_json(),
                        )
                    except Exception as e:
                        logger.error(f"Failed to update job {job.id} in database: {e}")

                    return

            # Initialize R2 if not done
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                hash_prefix = request.content_hash[:12]
                lrc_path = temp_path / "lyrics.lrc"
                whisper_phrases = []
                line_count = 0

                # Stage 1: Try YouTube transcript first — no audio download or stem needed
                youtube_lrc_result = None
                if request.youtube_url:
                    from .lrc import try_youtube_transcript_lrc

                    job.stage = "trying_youtube_transcript"
                    job.progress = 0.2
                    job.updated_at = datetime.now(timezone.utc)
                    youtube_lrc_result = await try_youtube_transcript_lrc(
                        request.youtube_url,
                        request.lyrics_text,
                        request.options,
                        lrc_path,
                    )
                    if youtube_lrc_result:
                        lrc_path, line_count, whisper_phrases = youtube_lrc_result
                        job.stage = "youtube_transcript_done"
                        logger.info(
                            "YouTube transcript succeeded — skipping audio download and stem separation"
                        )

                # Stage 2: Whisper path — only when YouTube is unavailable or failed
                if youtube_lrc_result is None:
                    # Download audio from R2
                    job.stage = "downloading"
                    job.progress = 0.2
                    job.updated_at = datetime.now(timezone.utc)

                    audio_path = temp_path / "audio.mp3"
                    if self.r2_client:
                        logger.info("Downloading audio from R2...")
                        download_start = time.time()
                        await self.r2_client.download_audio(request.audio_url, audio_path)
                        download_elapsed = time.time() - download_start
                        logger.info(
                            f"Audio download completed in {download_elapsed:.2f}s"
                        )

                    # Check if clean vocals stem exists and should be used for Whisper
                    # Only vocals_clean.flac is accepted; if missing, trigger stem separation
                    transcription_path = audio_path
                    vocals_stem_url: Optional[str] = None

                    if request.options.use_vocals_stem and self.r2_client:
                        # Check for clean vocals in priority order
                        from .stem_separation import get_clean_vocals_url

                        vocals_stem_url = await get_clean_vocals_url(
                            request.content_hash, self.r2_client
                        )

                        if vocals_stem_url:
                            ext = ".flac" if vocals_stem_url.endswith(".flac") else ".wav"
                            stem_path = temp_path / f"vocals_stem{ext}"
                            await self.r2_client.download_audio(vocals_stem_url, stem_path)
                            transcription_path = stem_path
                            logger.info(
                                f"Using vocals stem for transcription: {vocals_stem_url}"
                            )
                            job.stage = "using_vocals_stem"
                        else:
                            # No clean vocals cached — auto-trigger stem separation
                            logger.info(
                                "No clean vocals found, auto-triggering stem separation"
                            )
                            job.stage = "submitting_stem_separation_child"
                            job.updated_at = datetime.now(timezone.utc)

                            try:
                                await self.job_store.update_job(
                                    job.id, stage="submitting_stem_separation_child"
                                )
                            except Exception as e:
                                logger.error(f"Failed to update job {job.id} in database: {e}")

                            child_request = StemSeparationJobRequest(
                                audio_url=request.audio_url,
                                content_hash=request.content_hash,
                                options={"force": False},
                            )
                            child_job = await self.submit(JobType.STEM_SEPARATION, child_request)
                            child_id = child_job.id
                            logger.info(
                                f"Submitted child stem separation job: {child_id}"
                            )

                            job.stage = f"awaiting_stem_separation:{child_id}"
                            try:
                                await self.job_store.update_job(
                                    job.id, stage=f"awaiting_stem_separation:{child_id}"
                                )
                            except Exception as e:
                                logger.error(f"Failed to update job {job.id} in database: {e}")

                            logger.info(
                                f"Releasing LRC semaphore to wait for child job {child_id}"
                            )
                            self._lrc_semaphore.release()

                            poll_interval = 3.0
                            max_wait = 7200.0  # 2 hour timeout
                            wait_start = time.time()

                            try:
                                while True:
                                    await asyncio.sleep(poll_interval)

                                    child_job = await self.get_job(child_id)
                                    if not child_job:
                                        logger.error(f"Child job {child_id} not found")
                                        raise LRCWorkerError(
                                            f"Child stem separation job {child_id} not found"
                                        )

                                    if child_job.status == JobStatus.COMPLETED:
                                        logger.info(
                                            f"Child stem separation job {child_id} completed"
                                        )
                                        break
                                    elif child_job.status == JobStatus.FAILED:
                                        logger.error(
                                            f"Child stem separation job {child_id} failed: {child_job.error_message}"
                                        )
                                        break

                                    # Timeout — fall back to full audio rather than failing the LRC job
                                    if time.time() - wait_start > max_wait:
                                        logger.warning(
                                            f"Timeout waiting for child stem separation job {child_id} "
                                            f"after {max_wait:.0f}s — falling back to full audio for transcription"
                                        )
                                        break
                            except asyncio.CancelledError:
                                logger.warning(
                                    f"[{job.id}] Cancelled while polling for child job {child_id}"
                                )
                                raise
                            finally:
                                # Re-acquire the LRC semaphore slot before continuing.
                                # asyncio.shield prevents CancelledError from interrupting
                                # the acquire, ensuring the semaphore stays balanced with
                                # the outer async with on line 316.
                                logger.info(f"Re-acquiring LRC semaphore slot")
                                try:
                                    await asyncio.shield(self._lrc_semaphore.acquire())
                                except asyncio.CancelledError:
                                    # shield's inner future completed but we were
                                    # cancelled externally — acquire still succeeded
                                    pass
                                logger.info(f"Re-acquired LRC semaphore slot")

                            child_job = await self.get_job(child_id)
                            if (
                                child_job
                                and child_job.status == JobStatus.COMPLETED
                                and child_job.result
                            ):
                                vocals_stem_url = child_job.result.vocals_clean_url
                                if vocals_stem_url:
                                    ext = ".flac" if vocals_stem_url.endswith(".flac") else ".wav"
                                    stem_path = temp_path / f"vocals_clean{ext}"
                                    await self.r2_client.download_audio(vocals_stem_url, stem_path)
                                    transcription_path = stem_path
                                    logger.info(
                                        f"Downloaded clean vocals from child job: {vocals_stem_url}"
                                    )
                                    job.stage = "using_vocals_clean_stem"
                                else:
                                    logger.warning(
                                        f"Child job completed but no vocals_clean_url in result"
                                    )
                            else:
                                logger.warning(
                                    f"Child stem separation failed or incomplete, using full audio"
                                )

                    # Check for cached Whisper transcription (audio hash only, not lyrics)
                    cached_phrases = None
                    if request.options.force_whisper:
                        logger.info(f"Whisper cache bypassed (force_whisper=True)")
                    else:
                        cached_data = self.cache_manager.get_whisper_transcription(
                            request.content_hash
                        )
                        if cached_data:
                            from .lrc import WhisperPhrase

                            cached_phrases = [WhisperPhrase(**p) for p in cached_data]
                            logger.info(
                                f"Whisper cache hit - using {len(cached_phrases)} cached phrases"
                            )
                            job.stage = "transcription_cached"
                        else:
                            logger.info(f"Whisper cache miss - will run transcription")

                    # Run Whisper transcription (or use cached); youtube_url=None since we already tried it
                    job.stage = "transcribing"
                    job.progress = 0.4
                    job.updated_at = datetime.now(timezone.utc)

                    lrc_path, line_count, whisper_phrases = await generate_lrc(
                        transcription_path,
                        request.lyrics_text,
                        request.options,
                        output_path=lrc_path,
                        cached_phrases=cached_phrases,
                        youtube_url=None,
                        content_hash=request.content_hash,
                        vocals_stem_url=vocals_stem_url,
                    )

                    # Cache the Whisper transcription for future use (if not using cache)
                    if cached_phrases is None and whisper_phrases:
                        phrases_data = [
                            {"text": p.text, "start": p.start, "end": p.end}
                            for p in whisper_phrases
                        ]
                        self.cache_manager.save_whisper_transcription(
                            request.content_hash, phrases_data
                        )
                        logger.info(f"Whisper transcription cached for future use")

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
                logger.info(f"LRC result cached with key: {lrc_cache_key}")

                # Set job result
                job.result = JobResult(lrc_url=lrc_url, line_count=line_count)
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"

                total_elapsed = time.time() - job_start_time
                logger.info(f"LRC job completed in {total_elapsed:.2f}s")

                # Persist completion to database
                try:
                    await self.job_store.update_job(
                        job.id,
                        status="completed",
                        progress=1.0,
                        stage="complete",
                        result_json=job.result.model_dump_json(),
                    )
                except Exception as e:
                    logger.error(f"Failed to update job {job.id} in database: {e}")

        except LRCWorkerError as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "lrc_error"
            logger.error(f"LRC job failed: {e}")

            # Persist failure to database
            try:
                await self.job_store.update_job(
                    job.id, status="failed", stage="lrc_error", error_message=str(e)
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = f"Unexpected error: {e}"
            job.stage = "error"
            logger.error(f"LRC job failed with unexpected error: {e}")

            # Persist failure to database
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="error",
                    error_message=f"Unexpected error: {e}",
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

        job.updated_at = datetime.now(timezone.utc)

    async def _process_stem_separation_job(self, job: Job) -> None:
        """Process a stem separation job.

        Args:
            job: Job to process
        """
        job_start_time = time.time()
        logger.info(f"Starting stem separation job {job.id} for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "starting"
        job.progress = 0.1

        # Persist state change to database
        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="starting", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, StemSeparationJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for stem separation job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    error_message="Invalid request type for stem separation job",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Check if stem separation dependencies are available
        if process_stem_separation is None:
            job.status = JobStatus.FAILED
            job.error_message = "Stem separation dependencies not available (audio-separator)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="Stem separation dependencies not available (audio-separator)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Check if separator wrapper is available (it lazy-initializes on first use)
        if not self._separator_wrapper:
            job.status = JobStatus.FAILED
            job.error_message = "Separator wrapper not available"
            job.stage = "missing_separator"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_separator",
                    error_message="Separator wrapper not available",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        try:
            # Initialize R2 if not done
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            # Process the stem separation
            await process_stem_separation(
                job=job,
                separator_wrapper=self._separator_wrapper,
                r2_client=self.r2_client,
                cache_manager=self.cache_manager,
                mvsep_client=self._mvsep_client,
            )

            # Persist completion to database
            try:
                await self.job_store.update_job(
                    job.id,
                    status="completed",
                    progress=1.0,
                    stage="complete",
                    result_json=job.result.model_dump_json() if job.result else None,
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")

            total_elapsed = time.time() - job_start_time
            logger.info(f"Stem separation job completed in {total_elapsed:.2f}s")

        except StemSeparationWorkerError as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "stem_separation_error"
            logger.error(f"Stem separation job failed: {e}")

            # Persist failure to database
            try:
                await self.job_store.update_job(
                    job.id, status="failed", stage="stem_separation_error", error_message=str(e)
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = f"Unexpected error: {e}"
            job.stage = "error"
            logger.error(f"Stem separation job failed with unexpected error: {e}")

            # Persist failure to database
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="error",
                    error_message=f"Unexpected error: {e}",
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

        job.updated_at = datetime.now(timezone.utc)

    async def cancel_job(self, job_id: str) -> tuple[Optional[Job], Optional[str]]:
        """Cancel a job by ID.

        Args:
            job_id: Job ID to cancel

        Returns:
            Tuple of (job, warning_message) - job is None if not found,
            warning_message is set if job was PROCESSING.
        """
        job = self._jobs.get(job_id)

        if not job:
            # Try to get from DB
            try:
                job = await self.job_store.get_job(job_id)
            except Exception as e:
                logger.error(f"Failed to retrieve job {job_id} from database: {e}")
                return None, None

        if not job:
            return None, None

        # Terminal states: no-op
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job, None

        warning = None
        previous_status = job.status

        # Update job status
        job.status = JobStatus.CANCELLED
        job.updated_at = datetime.now(timezone.utc)
        if job.stage != "cancelled":
            job.stage = "cancelled"

        # Persist to database
        try:
            await self.job_store.update_job(
                job.id,
                status="cancelled",
                stage="cancelled",
            )
        except Exception as e:
            logger.error(f"Failed to update cancelled job {job_id} in database: {e}")

        # Warning if job was processing
        if previous_status == JobStatus.PROCESSING:
            warning = "Job was PROCESSING. The running task continues until service restart."

        return job, warning

    async def clear_queue(self) -> list[Job]:
        """Cancel all queued and processing jobs.

        Returns:
            List of jobs that were cancelled
        """
        cancelled_jobs: list[Job] = []

        # Find all QUEUED and PROCESSING jobs in memory
        for job_id, job in list(self._jobs.items()):
            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                job.status = JobStatus.CANCELLED
                job.updated_at = datetime.now(timezone.utc)
                job.stage = "cancelled"

                try:
                    await self.job_store.update_job(
                        job.id,
                        status="cancelled",
                        stage="cancelled",
                    )
                except Exception as e:
                    logger.error(f"Failed to update cancelled job {job_id} in database: {e}")

                cancelled_jobs.append(job)

        # Also query DB for QUEUED jobs not in memory
        try:
            db_queued_jobs = await self.job_store.list_jobs(status=JobStatus.QUEUED, limit=1000)
            for job in db_queued_jobs:
                if job.id not in self._jobs:
                    job.status = JobStatus.CANCELLED
                    job.updated_at = datetime.now(timezone.utc)
                    job.stage = "cancelled"

                    try:
                        await self.job_store.update_job(
                            job.id,
                            status="cancelled",
                            stage="cancelled",
                        )
                    except Exception as e:
                        logger.error(f"Failed to update cancelled job {job.id} in database: {e}")

                    self._jobs[job.id] = job
                    cancelled_jobs.append(job)
        except Exception as e:
            logger.error(f"Failed to list queued jobs from database: {e}")

        # Also query DB for PROCESSING jobs not in memory
        try:
            db_processing_jobs = await self.job_store.list_jobs(
                status=JobStatus.PROCESSING, limit=1000
            )
            for job in db_processing_jobs:
                if job.id not in self._jobs:
                    job.status = JobStatus.CANCELLED
                    job.updated_at = datetime.now(timezone.utc)
                    job.stage = "cancelled"

                    try:
                        await self.job_store.update_job(
                            job.id,
                            status="cancelled",
                            stage="cancelled",
                        )
                    except Exception as e:
                        logger.error(f"Failed to update cancelled job {job.id} in database: {e}")

                    self._jobs[job.id] = job
                    cancelled_jobs.append(job)
        except Exception as e:
            logger.error(f"Failed to list processing jobs from database: {e}")

        return cancelled_jobs

    async def stop(self) -> None:
        """Stop processing jobs."""
        self._running = False
        await self.stop_periodic_logging()
        await self.job_store.close()

    def _log_queue_state(self) -> None:
        """Log current queue state statistics."""
        now = datetime.now(timezone.utc)

        # Count jobs by type and status
        stats: Dict[JobType, Dict[JobStatus, int]] = {
            JobType.ANALYZE: {status: 0 for status in JobStatus},
            JobType.LRC: {status: 0 for status in JobStatus},
            JobType.STEM_SEPARATION: {status: 0 for status in JobStatus},
        }

        # Track wait times for queued and processing jobs
        queued_wait_times: Dict[JobType, list] = {
            JobType.ANALYZE: [],
            JobType.LRC: [],
            JobType.STEM_SEPARATION: [],
        }
        processing_durations: Dict[JobType, list] = {
            JobType.ANALYZE: [],
            JobType.LRC: [],
            JobType.STEM_SEPARATION: [],
        }

        for job in self._jobs.values():
            stats[job.type][job.status] += 1

            if job.status == JobStatus.QUEUED:
                wait_time = (now - job.created_at).total_seconds()
                queued_wait_times[job.type].append(wait_time)
            elif job.status == JobStatus.PROCESSING:
                duration = (now - job.updated_at).total_seconds()
                processing_durations[job.type].append(duration)

        # Build summary line
        analyze_stats = f"queued:{stats[JobType.ANALYZE][JobStatus.QUEUED]},processing:{stats[JobType.ANALYZE][JobStatus.PROCESSING]},completed:{stats[JobType.ANALYZE][JobStatus.COMPLETED]},failed:{stats[JobType.ANALYZE][JobStatus.FAILED]}"
        lrc_stats = f"queued:{stats[JobType.LRC][JobStatus.QUEUED]},processing:{stats[JobType.LRC][JobStatus.PROCESSING]},completed:{stats[JobType.LRC][JobStatus.COMPLETED]},failed:{stats[JobType.LRC][JobStatus.FAILED]}"
        stem_stats = f"queued:{stats[JobType.STEM_SEPARATION][JobStatus.QUEUED]},processing:{stats[JobType.STEM_SEPARATION][JobStatus.PROCESSING]},completed:{stats[JobType.STEM_SEPARATION][JobStatus.COMPLETED]},failed:{stats[JobType.STEM_SEPARATION][JobStatus.FAILED]}"

        wait_time_str = ""
        if queued_wait_times[JobType.ANALYZE]:
            waits = ",".join(f"{w:.0f}s" for w in queued_wait_times[JobType.ANALYZE][:3])
            if len(queued_wait_times[JobType.ANALYZE]) > 3:
                waits += f",...+{len(queued_wait_times[JobType.ANALYZE]) - 3}more"
            wait_time_str += f" ANALYZE queued=[{waits}]"
        if queued_wait_times[JobType.LRC]:
            waits = ",".join(f"{w:.0f}s" for w in queued_wait_times[JobType.LRC][:3])
            if len(queued_wait_times[JobType.LRC]) > 3:
                waits += f",...+{len(queued_wait_times[JobType.LRC]) - 3}more"
            wait_time_str += f" LRC queued=[{waits}]"
        if queued_wait_times[JobType.STEM_SEPARATION]:
            waits = ",".join(f"{w:.0f}s" for w in queued_wait_times[JobType.STEM_SEPARATION][:3])
            if len(queued_wait_times[JobType.STEM_SEPARATION]) > 3:
                waits += f",...+{len(queued_wait_times[JobType.STEM_SEPARATION]) - 3}more"
            wait_time_str += f" STEM queued=[{waits}]"
        if processing_durations[JobType.ANALYZE]:
            avg_dur = sum(processing_durations[JobType.ANALYZE]) / len(
                processing_durations[JobType.ANALYZE]
            )
            wait_time_str += f" ANALYZE processing={avg_dur:.0f}s"
        if processing_durations[JobType.LRC]:
            avg_dur = sum(processing_durations[JobType.LRC]) / len(
                processing_durations[JobType.LRC]
            )
            wait_time_str += f" LRC processing={avg_dur:.0f}s"
        if processing_durations[JobType.STEM_SEPARATION]:
            avg_dur = sum(processing_durations[JobType.STEM_SEPARATION]) / len(
                processing_durations[JobType.STEM_SEPARATION]
            )
            wait_time_str += f" STEM processing={avg_dur:.0f}s"

        logger.info(
            f"Queue state: ANALYZE[{analyze_stats}] LRC[{lrc_stats}] STEM[{stem_stats}] | Wait times:{wait_time_str if wait_time_str else ' none'}"
        )

    async def _periodic_logging_loop(self) -> None:
        """Background task that logs queue state periodically."""
        while self._running:
            self._log_queue_state()
            try:
                await asyncio.sleep(self._log_interval_seconds)
            except asyncio.CancelledError:
                break

    def _start_periodic_logging(self) -> None:
        """Start the periodic logging background task."""
        self._logging_task = asyncio.create_task(self._periodic_logging_loop())

    async def stop_periodic_logging(self) -> None:
        """Stop the periodic logging task gracefully."""
        if self._logging_task:
            self._logging_task.cancel()
            try:
                await self._logging_task
            except asyncio.CancelledError:
                pass
