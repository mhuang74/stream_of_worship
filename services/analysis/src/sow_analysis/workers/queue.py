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


class JobQueue:
    """In-memory job queue with concurrent execution control."""

    def __init__(
        self,
        max_concurrent_analysis: int = 1,
        max_concurrent_lrc: int = 2,
        cache_dir: Path = Path("/cache"),
        db_path: Optional[Path] = None,
    ):
        """Initialize job queue.

        Args:
            max_concurrent_analysis: Maximum concurrent analysis jobs (1 = serialized)
            max_concurrent_lrc: Maximum concurrent LRC jobs
            cache_dir: Directory for caching results
            db_path: Path to job database (default: cache_dir / "jobs.db")
        """
        self.max_concurrent_analysis = max_concurrent_analysis
        self.max_concurrent_lrc = max_concurrent_lrc
        self.cache_manager = CacheManager(cache_dir)
        self.r2_client: Optional[R2Client] = None
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # Analysis jobs use lock for serialization (high memory/CPU with allin1)
        self._analysis_lock = asyncio.Lock()
        # LRC jobs use semaphore for concurrency (faster-whisper is more efficient)
        self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc)
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

    async def initialize(self) -> None:
        """Initialize persistent store and recover interrupted jobs."""
        await self.job_store.initialize()

        # Purge old completed/failed jobs
        purged = await self.job_store.purge_old_jobs(max_age_days=7)
        if purged:
            logger.info(f"Purged {purged} old jobs from database")

        # Recover interrupted jobs (were QUEUED or PROCESSING when service died)
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
        self, status: Optional[JobStatus] = None, job_type: Optional[JobType] = None, limit: int = 100
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
        if job.type == JobType.ANALYZE:
            # Analysis jobs use lock for serialization (allin1 is memory/CPU intensive)
            async with self._analysis_lock:
                await self._process_analysis_job(job)
        elif job.type == JobType.LRC:
            # LRC jobs use semaphore for concurrency (faster-whisper is more efficient)
            async with self._lrc_semaphore:
                await self._process_lrc_job(job)

        # Schedule cleanup for finished jobs (to prevent unbounded memory growth)
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
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
        job_start_time = time.time()
        logger.info(f"Starting analysis job {job.id} for audio: {job.request.audio_url}")

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
            job.error_message = (
                "Analysis dependencies not available (librosa, allin1, demucs)"
            )
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
                    request.content_hash,
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
            logger.error(f"[{job.id}] Analysis job failed: {e}")

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
        job_start_time = time.time()
        logger.info(f"Starting LRC job {job.id} for audio: {job.request.audio_url}")

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
                f"[{job.id}] YouTube URL provided: {request.youtube_url} "
                f"— will try YouTube transcript first, Whisper as fallback"
            )
        else:
            logger.info(f"[{job.id}] No YouTube URL — will use Whisper transcription directly")

        # Compute composite cache key based on audio hash + lyrics hash
        lrc_cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text)
        logger.info(f"[{job.id}] LRC cache key: {lrc_cache_key} (audio_hash={request.content_hash[:12]}...)")

        try:
            # Check LRC result cache first (unless force=True - allows prompt improvements)
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
                    youtube_url=request.youtube_url or None,
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
            logger.error(f"[{job.id}] LRC job failed: {e}")

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
            logger.error(f"[{job.id}] LRC job failed with unexpected error: {e}")

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
        }

        # Track wait times for queued and processing jobs
        queued_wait_times: Dict[JobType, list] = {JobType.ANALYZE: [], JobType.LRC: []}
        processing_durations: Dict[JobType, list] = {JobType.ANALYZE: [], JobType.LRC: []}

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
        if processing_durations[JobType.ANALYZE]:
            avg_dur = sum(processing_durations[JobType.ANALYZE]) / len(processing_durations[JobType.ANALYZE])
            wait_time_str += f" ANALYZE processing={avg_dur:.0f}s"
        if processing_durations[JobType.LRC]:
            avg_dur = sum(processing_durations[JobType.LRC]) / len(processing_durations[JobType.LRC])
            wait_time_str += f" LRC processing={avg_dur:.0f}s"

        logger.info(
            f"Queue state: ANALYZE[{analyze_stats}] LRC[{lrc_stats}] | Wait times:{wait_time_str if wait_time_str else ' none'}"
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
