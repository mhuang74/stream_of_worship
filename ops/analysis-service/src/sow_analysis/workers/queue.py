"""In-memory job queue for asynchronous processing."""

import asyncio
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Union

from ..config import settings
from ..logging_config import set_job_id

logger = logging.getLogger(__name__)

FINISHED_JOB_MEMORY_RETENTION_SECONDS = 300.0


@dataclass
class ResolvedTranscriptionAudio:
    path: Path
    r2_url: Optional[str]
    stem_kind: str
    is_dry_or_clean_vocals: bool


@asynccontextmanager
async def optional_semaphore(sem: Optional[asyncio.Semaphore]) -> AsyncIterator[None]:
    """Context manager that acquires semaphore if provided, otherwise no-op.

    This is a Python 3.8+ compatible alternative to `async with (sem or nullcontext())`.
    """
    if sem is not None:
        async with sem:
            yield
    else:
        yield


def _compute_lrc_cache_key(content_hash: str, lyrics_text: str, language: str = "zh") -> str:
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
    composite = f"{content_hash}:{lyrics_hash}:{language}:lrc-lang-v2"
    # Return a shorter hash of the composite
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:32]


from ..models import (
    AnalyzeJobRequest,
    EmbeddingJobRequest,
    EmbeddingJobResult,
    FastAnalyzeJobRequest,
    ForcedAlignmentJobRequest,
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
    from .analyzer import analyze_audio, analyze_audio_fast
    from .separator import separate_stems
except ImportError:
    analyze_audio = None
    analyze_audio_fast = None
    separate_stems = None

# Optional LRC imports - require whisper and openai
try:
    from .lrc import (
        LRCWorkerError,
        build_whisper_transcription_cache_key,
        build_qwen3_asr_cache_key,
        generate_lrc,
        generate_lrc_from_qwen3_asr,
        resolve_lrc_language,
        warn_if_lrc_language_script_mismatch,
    )
    from ..services.qwen3_asr_client import Qwen3AsrError, Qwen3AsrQuotaExhaustedError
except ImportError:
    LRCWorkerError = Exception
    Qwen3AsrError = Exception
    Qwen3AsrQuotaExhaustedError = Exception
    build_qwen3_asr_cache_key = None
    build_whisper_transcription_cache_key = None
    generate_lrc = None
    generate_lrc_from_qwen3_asr = None
    resolve_lrc_language = None
    warn_if_lrc_language_script_mismatch = None

# Optional embedding imports - require openai
try:
    from .embedder import EmbeddingWorker
except ImportError:
    EmbeddingWorker = None

# Optional stem separation imports - require audio-separator
try:
    from .stem_separation import StemSeparationWorkerError, process_stem_separation
except ImportError:
    StemSeparationWorkerError = Exception
    process_stem_separation = None

# Optional forced alignment imports - require qwen-asr
try:
    from .forced_alignment import (
        format_timestamp,
        map_segments_to_lines,
        validate_audio_duration,
    )
except ImportError:
    format_timestamp = None
    map_segments_to_lines = None
    validate_audio_duration = None


class JobQueue:
    """In-memory job queue with concurrent execution control."""

    def __init__(
        self,
        max_concurrent_local_model: int = 1,
        cache_dir: Path = Path("/cache"),
        db_path: Optional[Path] = None,
    ):
        """Initialize job queue.

        Args:
            max_concurrent_local_model: Maximum concurrent local model executions
                (Whisper, Qwen3, audio-separator, allin1, demucs). Default 1 due to
                memory constraints - only one local model can run at a time.
            cache_dir: Directory for caching results
            db_path: Path to job database (default: cache_dir / "jobs.db")
        """
        self.max_concurrent_local_model = max_concurrent_local_model
        self.cache_manager = CacheManager(cache_dir)
        self.r2_client: Optional[R2Client] = None
        self._separator_wrapper: Optional[Any] = None
        self._mvsep_client: Optional[Any] = None
        self._forced_aligner_wrapper: Optional[Any] = None
        self._qwen3_client: Any = None
        self._mvsep_quota_waiter: Optional[Any] = None
        self._qwen3_quota_waiter: Optional[Any] = None
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # Global semaphore for local model execution (Whisper, Qwen3, audio-separator, allin1, demucs)
        # Cloud operations (YouTube transcript, MVSEP, LLM alignment) don't acquire this.
        self._local_model_semaphore = asyncio.Semaphore(max_concurrent_local_model)
        self._dashscope_asr_semaphore = asyncio.Semaphore(settings.SOW_DASHSCOPE_ASR_MAX_CONCURRENT)
        # Separate semaphore for embedding jobs (external API, no GPU needed)
        self._embedding_semaphore = asyncio.Semaphore(5)
        # Separate semaphore for fast analysis (librosa-only, CPU/memory heavy).
        # Distinct from _local_model_semaphore (allin1/demucs) so fast and full
        # analysis do not coordinate; operator sizes both together.
        self._fast_analyze_semaphore = asyncio.Semaphore(settings.SOW_FAST_ANALYZE_MAX_CONCURRENT)
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

    def set_forced_aligner_wrapper(self, wrapper: Any) -> None:
        """Set the forced aligner wrapper for forced alignment jobs.

        Args:
            wrapper: ForcedAlignerWrapper instance
        """
        self._forced_aligner_wrapper = wrapper

    def set_quota_waiters(self, mvsep: Any = None, qwen3: Any = None) -> None:
        """Set QuotaWaiter instances for free-only patient mode.

        Args:
            mvsep: QuotaWaiter for MVSEP quota (or None)
            qwen3: QuotaWaiter for DashScope Qwen3 ASR quota (or None)
        """
        self._mvsep_quota_waiter = mvsep
        self._qwen3_quota_waiter = qwen3

    def set_qwen3_client(self, qwen3_client: Any) -> None:
        """Set the Qwen3 ASR client singleton for LRC jobs.

        Args:
            qwen3_client: Qwen3AsrClient instance
        """
        self._qwen3_client = qwen3_client

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
            job.status = JobStatus.QUEUED
            # Preserve progress from DB — _update_stage() persists it during quota waits.
            # job.progress is already set from the DB row deserialization; do NOT reset to 0.0.
            job.stage = "requeued"
            job.updated_at = datetime.now(timezone.utc)

            self._jobs[job.id] = job
            await self._queue.put(job.id)
            # Update DB to reflect requeued status (status + stage only, preserve progress)
            try:
                await self.job_store.update_job(
                    job.id, status="queued", stage="requeued"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in DB during recovery: {e}")

        if interrupted:
            sample_ids = ", ".join(str(job.id) for job in interrupted[:5])
            more = f", ... +{len(interrupted) - 5} more" if len(interrupted) > 5 else ""
            logger.info(f"Recovered {len(interrupted)} interrupted jobs (e.g., {sample_ids}{more})")

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

        # Recover WAITING jobs (dequeued but hadn't started real work)
        waiting = await self.job_store.get_waiting_jobs()
        for job in waiting:
            job.status = JobStatus.QUEUED
            job.stage = "requeued"
            job.progress = 0.0
            job.updated_at = datetime.now(timezone.utc)

            self._jobs[job.id] = job
            await self._queue.put(job.id)
            try:
                await self.job_store.update_job(
                    job.id, status="queued", progress=0.0, stage="requeued"
                )
            except Exception as e:
                logger.error(f"Failed to update WAITING job {job.id} during recovery: {e}")

        if waiting:
            logger.info(f"Recovered {len(waiting)} waiting jobs from database")

    async def submit(
        self,
        job_type: JobType,
        request: Union[
            AnalyzeJobRequest,
            LrcJobRequest,
            StemSeparationJobRequest,
            EmbeddingJobRequest,
            ForcedAlignmentJobRequest,
            FastAnalyzeJobRequest,
        ],
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

        # Transition from QUEUED to WAITING — job is now dequeued and assigned to a task
        # but hasn't started real work yet
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.WAITING
            job.stage = "waiting"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(job.id, status="waiting", stage="waiting")
            except Exception as e:
                logger.error(f"Failed to update job {job.id} to WAITING in database: {e}")

        if job.type == JobType.ANALYZE:
            # Analysis always uses local models (allin1, demucs) - acquire semaphore for entire job
            async with self._local_model_semaphore:
                await self._process_analysis_job(job)
        elif job.type == JobType.LRC:
            # LRC tries YouTube (cloud) first; semaphore acquired inside generate_lrc()
            # only for Whisper/Qwen3 (local models)
            await self._process_lrc_job(job)
        elif job.type == JobType.STEM_SEPARATION:
            # Stem separation tries MVSEP (cloud) first; semaphore acquired inside
            # process_stem_separation() only for local fallback
            await self._process_stem_separation_job(job)
        elif job.type == JobType.EMBEDDING:
            # Embedding uses external OpenAI API - separate semaphore
            async with self._embedding_semaphore:
                await self._process_embedding_job(job)
        elif job.type == JobType.FORCED_ALIGNMENT:
            # Forced alignment: semaphore acquired inside _process_forced_alignment_job()
            # only around the align() call, not the entire job (prevents deadlock with stem separation)
            await self._process_forced_alignment_job(job)
        elif job.type == JobType.FAST_ANALYZE:
            # Fast analysis (librosa-only) uses its own semaphore, distinct from
            # _local_model_semaphore (allin1/demucs) so the two do not coordinate.
            async with self._fast_analyze_semaphore:
                # Re-check cancellation after acquiring the semaphore — a job
                # may have been cancelled while queued behind it.
                latest = self._jobs.get(job.id, job)
                if latest.status == JobStatus.CANCELLED:
                    return
                await self._process_fast_analyze_job(job)

        # Schedule cleanup for finished jobs (to prevent unbounded memory growth)
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            asyncio.create_task(self._cleanup_finished_job(job.id))

    async def _cleanup_finished_job(
        self,
        job_id: str,
        delay: float = FINISHED_JOB_MEMORY_RETENTION_SECONDS,
    ):
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
                    force=request.options.force,
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
                    key_algorithm_version=analysis_result.get("key_algorithm_version"),
                    key_score_margin=analysis_result.get("key_score_margin"),
                    key_window_agreement=analysis_result.get("key_window_agreement"),
                    key_candidates=analysis_result.get("key_candidates"),
                    key_detected_at=analysis_result.get("key_detected_at"),
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

    async def _process_fast_analyze_job(self, job: Job) -> None:
        """Process a fast analysis job (librosa-only).

        Produces only the fast-tier subset: duration_seconds, tempo_bpm,
        musical_key, musical_mode, key_confidence, loudness_db. Does NOT
        upload to R2 and does NOT touch the full-tier {hash_prefix}.json cache.

        Args:
            job: Job to process
        """
        set_job_id(job.id)
        job_start_time = time.time()
        logger.info(f"Starting fast analysis job for audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "downloading"
        job.progress = 0.1

        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="downloading", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, FastAnalyzeJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for fast analysis job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id, status="failed", error_message="Invalid request type"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        # Check if fast analysis dependency is available
        if analyze_audio_fast is None:
            job.status = JobStatus.FAILED
            job.error_message = "Fast analysis dependency not available (librosa)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="Fast analysis dependency not available (librosa)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        try:
            # Validate R2 client configuration before attempting audio fetch
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            if not self.r2_client:
                raise RuntimeError(
                    "R2 client not configured; cannot download audio for fast analysis"
                )

            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                audio_path = temp_path / "audio.mp3"

                logger.info("Downloading audio from R2 for fast analysis...")
                download_start = time.time()
                await self.r2_client.download_audio(request.audio_url, audio_path)
                download_elapsed = time.time() - download_start
                logger.info(f"Audio download completed in {download_elapsed:.2f}s")

                # Guard for truncated downloads
                if not audio_path.exists() or audio_path.stat().st_size == 0:
                    raise RuntimeError("Downloaded audio file is missing or empty")

                job.stage = "analyzing"
                job.progress = 0.3
                try:
                    await self.job_store.update_job(job.id, stage="analyzing", progress=0.3)
                except Exception as e:
                    logger.error(f"Failed to update job {job.id} in database: {e}")

                analysis_result = await analyze_audio_fast(
                    audio_path,
                    self.cache_manager,
                    request.content_hash,
                    sample_rate=request.options.sample_rate,
                    hop_length=request.options.hop_length,
                    start_bpm=request.options.start_bpm,
                    force=request.options.force,
                    lrc_content=request.options.lrc_content,
                )

                # Build job result (fast subset only; full-only fields stay None)
                job.result = JobResult(
                    duration_seconds=analysis_result.get("duration_seconds"),
                    tempo_bpm=analysis_result.get("tempo_bpm"),
                    musical_key=analysis_result.get("musical_key"),
                    musical_mode=analysis_result.get("musical_mode"),
                    key_confidence=analysis_result.get("key_confidence"),
                    key_algorithm_version=analysis_result.get("key_algorithm_version"),
                    key_score_margin=analysis_result.get("key_score_margin"),
                    key_window_agreement=analysis_result.get("key_window_agreement"),
                    key_candidates=analysis_result.get("key_candidates"),
                    key_detected_at=analysis_result.get("key_detected_at"),
                    loudness_db=analysis_result.get("loudness_db"),
                )

                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"

                total_elapsed = time.time() - job_start_time
                logger.info(f"Fast analysis job completed in {total_elapsed:.2f}s")

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
            logger.error(f"Fast analysis job failed: {e}")

            try:
                await self.job_store.update_job(
                    job.id, status="failed", stage="error", error_message=str(e)
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

        finally:
            job.updated_at = datetime.now(timezone.utc)

    async def _update_stage(
        self,
        job: Job,
        stage: str,
        progress: Optional[float] = None,
    ) -> None:
        job.stage = stage
        if progress is not None:
            job.progress = progress
        job.updated_at = datetime.now(timezone.utc)
        fields: dict[str, Any] = {"stage": stage}
        if progress is not None:
            fields["progress"] = progress
        try:
            await self.job_store.update_job(job.id, **fields)
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

    async def _resolve_lrc_transcription_audio(
        self,
        job: Job,
        request: LrcJobRequest,
        temp_path: Path,
        audio_path: Path,
    ) -> ResolvedTranscriptionAudio:
        """Resolve the best shared audio input for Qwen ASR and Whisper."""
        return await self._resolve_transcription_audio(
            job,
            request.audio_url,
            request.content_hash,
            request.options.use_vocals_stem,
            temp_path,
            audio_path,
        )

    async def _resolve_transcription_audio(
        self,
        job: Job,
        audio_url: str,
        content_hash: str,
        use_vocals_stem: bool,
        temp_path: Path,
        audio_path: Path,
    ) -> ResolvedTranscriptionAudio:
        """Resolve the best shared audio input for transcription/alignment."""
        await self._update_stage(job, "resolving_transcription_audio", 0.3)
        if not use_vocals_stem or not self.r2_client:
            return ResolvedTranscriptionAudio(audio_path, None, "full_mix", False)

        from .stem_separation import get_vocals_dry_url

        vocals_url = await get_vocals_dry_url(content_hash, self.r2_client)
        if vocals_url:
            ext = ".flac" if vocals_url.endswith(".flac") else ".wav"
            stem_path = temp_path / f"vocals_stem{ext}"
            await self.r2_client.download_audio(vocals_url, stem_path)
            logger.info("Using existing vocals_dry for transcription: %s", vocals_url)
            return ResolvedTranscriptionAudio(stem_path, vocals_url, "vocals_dry", True)

        logger.info("No clean vocals found, auto-triggering stem separation")
        await self._update_stage(job, "submitting_stem_separation_child")
        child_request = StemSeparationJobRequest(
            audio_url=audio_url,
            content_hash=content_hash,
            options={"force": False},
        )
        child_job = await self.submit(JobType.STEM_SEPARATION, child_request)
        child_id = child_job.id
        await self._update_stage(job, f"awaiting_stem_separation:{child_id}")

        wait_start = time.time()
        while True:
            await asyncio.sleep(3.0)
            if job.status == JobStatus.CANCELLED:
                logger.info("Parent job %s cancelled; aborting wait for child job", job.id)
                break
            child_job = await self.get_job(child_id)
            if not child_job:
                logger.error("Child stem separation job %s not found", child_id)
                break
            if child_job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                break
            if time.time() - wait_start > 7200.0:
                logger.warning("Timeout waiting for child stem separation job %s", child_id)
                break

        child_job = await self.get_job(child_id)
        if child_job and child_job.status == JobStatus.COMPLETED and child_job.result:
            candidates = [
                (child_job.result.vocals_dry_url, "vocals_dry", True),
                (child_job.result.vocals_url, "vocals", False),
            ]
            for url, stem_kind, is_clean in candidates:
                if not url:
                    continue
                ext = ".flac" if url.endswith(".flac") else ".wav"
                stem_path = temp_path / f"{stem_kind}{ext}"
                await self.r2_client.download_audio(url, stem_path)
                logger.info("Using %s for transcription: %s", stem_kind, url)
                return ResolvedTranscriptionAudio(stem_path, url, stem_kind, is_clean)

        logger.warning("Stem resolution failed or incomplete; using full mix")
        return ResolvedTranscriptionAudio(audio_path, None, "full_mix", False)

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
                f"— will try YouTube transcript first, LLM-based ASR as fallback"
            )
        else:
            logger.info("No YouTube URL — will use Whisper transcription directly")

        if resolve_lrc_language is None or warn_if_lrc_language_script_mismatch is None:
            job.status = JobStatus.FAILED
            job.error_message = "LRC language resolver is not available"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="LRC language resolver is not available",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        language_resolution = resolve_lrc_language(
            request.options.language,
            request.song_title,
            request.lyrics_text,
        )
        resolved_language = language_resolution.resolved
        logger.info(
            "Resolved LRC language: requested=%s resolved=%s reason=%s title=%r",
            language_resolution.requested,
            resolved_language,
            language_resolution.reason,
            request.song_title,
        )
        warn_if_lrc_language_script_mismatch(resolved_language, request.lyrics_text)

        # Compute composite cache key based on audio hash + lyrics hash
        lrc_cache_key = _compute_lrc_cache_key(
            request.content_hash, request.lyrics_text, resolved_language
        )
        logger.info(f"LRC cache key: {lrc_cache_key} (audio_hash={request.content_hash[:12]}...)")

        try:
            # Initialize R2 if not done (needed for ETag capture and upload)
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

            hash_prefix = request.content_hash[:12]

            # Capture ETag of official lyrics.lrc at job start for stale-object protection
            official_lrc_etag: Optional[str] = None
            if self.r2_client:
                from ..storage.r2 import StaleObjectError

                official_lrc_url = f"s3://{self.r2_client.bucket}/{hash_prefix}/lyrics.lrc"
                try:
                    head_resp = await self.r2_client.head_object(official_lrc_url)
                    official_lrc_etag = head_resp.get("ETag", "").strip('"')
                except Exception:
                    official_lrc_etag = None

            # Check LRC result cache first (unless force=True - allows prompt improvements)
            if not request.options.force:
                cached = self.cache_manager.get_lrc_result(lrc_cache_key)
                if cached:
                    # Legacy metadata-only cache entries have no cached text; ignore and regenerate
                    cached_text = cached.get("lrc_text")
                    if cached_text:
                        logger.info(
                            "LRC cache hit with cached text - rewriting official lyrics.lrc"
                        )
                        import tempfile

                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_path = Path(temp_dir)
                            lrc_path = temp_path / "lyrics.lrc"
                            lrc_path.write_text(cached_text, encoding="utf-8")
                            line_count = cached.get("line_count", 0)
                            lrc_source = cached.get("lrc_source")

                            if self.r2_client:
                                try:
                                    lrc_url = await self.r2_client.upload_official_lrc(
                                        hash_prefix,
                                        lrc_path,
                                        expected_etag=official_lrc_etag,
                                    )
                                except StaleObjectError as e:
                                    job.status = JobStatus.FAILED
                                    job.error_message = str(e)
                                    job.stage = "stale_object"
                                    job.updated_at = datetime.now(timezone.utc)
                                    try:
                                        await self.job_store.update_job(
                                            job.id,
                                            status="failed",
                                            stage="stale_object",
                                            error_message=str(e),
                                        )
                                    except Exception as db_err:
                                        logger.error(
                                            f"Failed to update job {job.id} in database: {db_err}"
                                        )
                                    return

                            job.result = JobResult(
                                lrc_url=lrc_url,
                                line_count=line_count,
                                lrc_source=lrc_source,
                            )
                            job.status = JobStatus.COMPLETED
                            job.progress = 1.0
                            job.stage = "cached"
                            job.updated_at = datetime.now(timezone.utc)

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
                    else:
                        logger.info(
                            "LRC cache hit with metadata-only legacy entry - ignoring and regenerating"
                        )

            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                lrc_path = temp_path / "lyrics.lrc"
                whisper_phrases = []
                line_count = 0
                lrc_source: Optional[str] = None

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
                        resolved_language,
                    )
                    if youtube_lrc_result:
                        lrc_path, line_count, whisper_phrases = youtube_lrc_result
                        lrc_source = "youtube_transcript"
                        job.stage = "youtube_transcript_done"
                        logger.info(
                            "YouTube transcript succeeded — skipping audio download and stem separation"
                        )

                # Stage 2: Qwen ASR then Whisper fallback — only when YouTube failed
                if youtube_lrc_result is None:
                    await self._update_stage(job, "downloading", 0.2)
                    audio_path = temp_path / "audio.mp3"
                    if self.r2_client:
                        logger.info("Downloading audio from R2...")
                        download_start = time.time()
                        await self.r2_client.download_audio(request.audio_url, audio_path)
                        download_elapsed = time.time() - download_start
                        logger.info(f"Audio download completed in {download_elapsed:.2f}s")

                    resolved_audio = await self._resolve_lrc_transcription_audio(
                        job, request, temp_path, audio_path
                    )
                    if job.status == JobStatus.CANCELLED:
                        logger.info("LRC job %s cancelled; skipping transcription", job.id)
                        return

                    if request.options.use_qwen3_asr:
                        # Guard: free-only mode requires DashScope to be configured
                        if settings.SOW_FREE_ONLY_MODE and not settings.SOW_DASHSCOPE_API_KEY:
                            raise LRCWorkerError(
                                "SOW_FREE_ONLY_MODE is enabled but DashScope Qwen3 ASR "
                                "is not configured. Set SOW_DASHSCOPE_API_KEY to use "
                                "free-only mode, or disable use_qwen3_asr to skip "
                                "ASR-based LRC generation."
                            )

                        lrc_source = None  # Initialize before retry loop
                        while True:
                            try:
                                from .lrc import _build_qwen3_context

                                context_limit = min(
                                    request.options.qwen3_asr_context_max_chars,
                                    settings.SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS,
                                )
                                context = _build_qwen3_context(
                                    request.lyrics_text, context_limit, resolved_language
                                )
                                qwen_cache_key = build_qwen3_asr_cache_key(
                                    request.content_hash,
                                    request.lyrics_text,
                                    resolved_audio.stem_kind,
                                    settings.SOW_DASHSCOPE_ASR_FLASH_MODEL,
                                    settings.SOW_DASHSCOPE_ASR_REGION,
                                    resolved_language,
                                    context_limit,
                                    context,
                                )
                                if (
                                    not request.options.force_qwen3_asr
                                    and self.cache_manager.get_qwen3_asr_transcription(qwen_cache_key)
                                ):
                                    await self._update_stage(job, "qwen3_asr_cached", 0.4)
                                else:
                                    await self._update_stage(job, "qwen3_asr_transcribing", 0.4)
                                lrc_path, line_count, _qwen_phrases = await generate_lrc_from_qwen3_asr(
                                    resolved_audio.path,
                                    request.lyrics_text,
                                    request.options,
                                    output_path=lrc_path,
                                    cache_key=qwen_cache_key,
                                    cache_manager=self.cache_manager,
                                    dashscope_semaphore=self._dashscope_asr_semaphore,
                                    resolved_language=resolved_language,
                                    qwen3_client=self._qwen3_client,
                                )
                                lrc_source = "qwen3_asr"
                                await self._update_stage(job, "qwen3_asr_done", 0.7)
                                break  # success
                            except Qwen3AsrQuotaExhaustedError:
                                if settings.SOW_FREE_ONLY_MODE:
                                    await self._qwen3_quota_waiter.mark_exhausted()
                                    await self._update_stage(
                                        job, "waiting_for_qwen3_asr_quota_reset", 0.4
                                    )
                                    # Loop with 60s heartbeat
                                    while True:
                                        available = await self._qwen3_quota_waiter.wait(
                                            job,
                                            lambda: job.status == JobStatus.CANCELLED,
                                            max_wait_seconds=60,
                                        )
                                        if available:
                                            break
                                        if job.status == JobStatus.CANCELLED:
                                            return  # cancelled
                                        # Heartbeat: refresh updated_at
                                        await self._update_stage(
                                            job, "waiting_for_qwen3_asr_quota_reset", 0.4
                                        )
                                    await self._update_stage(job, "qwen3_asr_transcribing", 0.4)
                                    continue  # retry Qwen3 ASR
                                else:
                                    # Existing: fall back to Whisper
                                    await self._update_stage(job, "falling_back_to_whisper", 0.45)
                                    break
                            except Qwen3AsrError as e:
                                # Existing: fall back to Whisper (unchanged)
                                logger.warning("Qwen3 ASR failed; falling back to LLM-based ASR: %s", e)
                                await self._update_stage(job, "falling_back_to_whisper", 0.45)
                                break
                            except Exception as e:
                                # Existing: fall back to Whisper (unchanged)
                                logger.warning(
                                    "Qwen3 ASR unexpected failure; falling back to LLM-based ASR: %s",
                                    e,
                                )
                                await self._update_stage(job, "falling_back_to_whisper", 0.45)
                                break
                    else:
                        logger.info("Qwen3 ASR disabled (use_qwen3_asr=False); using Whisper")
                        await self._update_stage(job, "falling_back_to_whisper", 0.35)

                    # Check for cached Whisper transcription with language/prompt-aware key.
                    if lrc_source != "qwen3_asr":
                        cached_phrases = None
                        if build_whisper_transcription_cache_key is None:
                            raise LRCWorkerError("Whisper cache key builder is not available")
                        whisper_cache_key = build_whisper_transcription_cache_key(
                            request.content_hash,
                            request.lyrics_text,
                            resolved_audio.stem_kind,
                            request.options.whisper_model,
                            resolved_language,
                        )
                        if request.options.force_whisper:
                            logger.info("Whisper cache bypassed (force_whisper=True)")
                        else:
                            cached_data = self.cache_manager.get_whisper_transcription(
                                whisper_cache_key
                            )
                            if cached_data:
                                from .lrc import WhisperPhrase

                                cached_phrases = [WhisperPhrase(**p) for p in cached_data]
                                logger.info(
                                    f"Whisper cache hit - using {len(cached_phrases)} cached phrases"
                                )
                                await self._update_stage(job, "transcription_cached")
                            else:
                                logger.info("Whisper cache miss - will run transcription")

                        await self._update_stage(job, "transcribing", 0.5)
                        lrc_path, line_count, whisper_phrases = await generate_lrc(
                            resolved_audio.path,
                            request.lyrics_text,
                            request.options,
                            output_path=lrc_path,
                            cached_phrases=cached_phrases,
                            youtube_url=None,
                            content_hash=request.content_hash,
                            vocals_stem_url=resolved_audio.r2_url,
                            local_model_semaphore=self._local_model_semaphore,
                            resolved_language=resolved_language,
                        )
                        lrc_source = "whisper_asr"

                        if cached_phrases is None and whisper_phrases:
                            phrases_data = [
                                {"text": p.text, "start": p.start, "end": p.end}
                                for p in whisper_phrases
                            ]
                            self.cache_manager.save_whisper_transcription(
                                whisper_cache_key, phrases_data
                            )
                            logger.info("Whisper transcription cached for future use")

                job.stage = "uploading"
                job.progress = 0.8
                job.updated_at = datetime.now(timezone.utc)

                # Upload official LRC to R2 with backup + ETag protection
                lrc_url = None
                if self.r2_client:
                    from ..storage.r2 import BackupFailedError, StaleObjectError

                    try:
                        lrc_url = await self.r2_client.upload_official_lrc(
                            hash_prefix,
                            lrc_path,
                            expected_etag=official_lrc_etag,
                        )
                    except StaleObjectError as e:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.stage = "stale_object"
                        job.updated_at = datetime.now(timezone.utc)
                        try:
                            await self.job_store.update_job(
                                job.id,
                                status="failed",
                                stage="stale_object",
                                error_message=str(e),
                            )
                        except Exception as db_err:
                            logger.error(f"Failed to update job {job.id} in database: {db_err}")
                        return
                    except BackupFailedError as e:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.stage = "backup_failed"
                        job.updated_at = datetime.now(timezone.utc)
                        try:
                            await self.job_store.update_job(
                                job.id,
                                status="failed",
                                stage="backup_failed",
                                error_message=str(e),
                            )
                        except Exception as db_err:
                            logger.error(f"Failed to update job {job.id} in database: {db_err}")
                        return

                # Save to cache using composite key (audio hash + lyrics hash)
                # Include LRC text for cache-hit rewrite path
                lrc_text = lrc_path.read_text(encoding="utf-8") if lrc_path.exists() else ""
                cache_result = {
                    "lrc_url": lrc_url,
                    "line_count": line_count,
                    "lrc_source": lrc_source,
                    "lrc_text": lrc_text,
                }
                self.cache_manager.save_lrc_result(lrc_cache_key, cache_result)
                logger.info(f"LRC result cached with key: {lrc_cache_key}")

                # Set job result
                job.result = JobResult(
                    lrc_url=lrc_url, line_count=line_count, lrc_source=lrc_source
                )
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

    async def _process_forced_alignment_job(self, job: Job) -> None:
        """Process a forced alignment job."""
        set_job_id(job.id)
        job_start_time = time.time()
        logger.info(f"Starting forced alignment job for original audio: {job.request.audio_url}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "starting"
        job.progress = 0.1

        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="starting", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, ForcedAlignmentJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for forced alignment job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id, status="failed", error_message="Invalid request type"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        if map_segments_to_lines is None or validate_audio_duration is None:
            job.status = JobStatus.FAILED
            job.error_message = "Forced alignment dependencies not available (qwen-asr, soundfile)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="Forced alignment dependencies not available (qwen-asr, soundfile)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        if not self._forced_aligner_wrapper:
            job.status = JobStatus.FAILED
            job.error_message = "Forced aligner wrapper not available"
            job.stage = "missing_aligner"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_aligner",
                    error_message="Forced aligner wrapper not available",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        if request.options.language == "auto":
            if resolve_lrc_language is None:
                job.status = JobStatus.FAILED
                job.error_message = "Language resolver not available for auto-detection"
                job.stage = "missing_dependencies"
                job.updated_at = datetime.now(timezone.utc)
                try:
                    await self.job_store.update_job(
                        job.id,
                        status="failed",
                        stage="missing_dependencies",
                        error_message="Language resolver not available for auto-detection",
                    )
                except Exception as e:
                    logger.error(f"Failed to update job {job.id} in database: {e}")
                return
            resolution = resolve_lrc_language("auto", request.song_title, request.lyrics_text)
            detected_lang = resolution.resolved
            logger.info(
                "Auto-detected forced alignment language: %s (reason: %s)",
                detected_lang,
                resolution.reason,
            )
            if warn_if_lrc_language_script_mismatch is not None:
                warn_if_lrc_language_script_mismatch(detected_lang, request.lyrics_text)
            language_map = {"zh": "Chinese", "en": "English"}
            language_mapped = language_map[detected_lang]
            resolved_lang_code = detected_lang
        else:
            language_map = {"zh": "Chinese", "en": "English"}
            language_mapped = language_map.get(request.options.language, "Chinese")
            resolved_lang_code = request.options.language

        try:
            if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
                self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)
            if not self.r2_client:
                raise RuntimeError(
                    "R2 client is not initialized. Forced alignment requires R2 storage."
                )

            hash_prefix = request.content_hash[:12]

            # Capture ETag of official lyrics.lrc at job start for stale-object protection
            official_lrc_etag: Optional[str] = None
            official_lrc_url = f"s3://{self.r2_client.bucket}/{hash_prefix}/lyrics.lrc"
            try:
                head_resp = await self.r2_client.head_object(official_lrc_url)
                official_lrc_etag = head_resp.get("ETag", "").strip('"')
            except Exception:
                official_lrc_etag = None

            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                await self._update_stage(job, "downloading", 0.2)
                audio_path = temp_path / "audio.mp3"
                if self.r2_client:
                    logger.info("Downloading audio from R2...")
                    download_start = time.time()
                    await self.r2_client.download_audio(request.audio_url, audio_path)
                    download_elapsed = time.time() - download_start
                    logger.info(f"Audio download completed in {download_elapsed:.2f}s")

                resolved_audio = await self._resolve_transcription_audio(
                    job,
                    request.audio_url,
                    request.content_hash,
                    request.options.use_vocals_stem,
                    temp_path,
                    audio_path,
                )
                if job.status == JobStatus.CANCELLED:
                    logger.info("Forced alignment job %s cancelled; skipping alignment", job.id)
                    return

                logger.info(
                    "Forced alignment audio input resolved: stem_kind=%s "
                    "is_dry_or_clean_vocals=%s use_vocals_stem=%s source_url=%s local_path=%s",
                    resolved_audio.stem_kind,
                    resolved_audio.is_dry_or_clean_vocals,
                    request.options.use_vocals_stem,
                    resolved_audio.r2_url or request.audio_url,
                    resolved_audio.path,
                )

                await self._update_stage(job, "validating_duration", 0.3)
                validate_audio_duration(resolved_audio.path, max_seconds=300.0)

                await self._update_stage(job, "aligning", 0.4)
                async with optional_semaphore(self._local_model_semaphore):
                    raw_segments = await self._forced_aligner_wrapper.align(
                        resolved_audio.path, request.lyrics_text, language_mapped
                    )

                await self._update_stage(job, "mapping_segments", 0.6)
                lyrics_lines = [line.rstrip() for line in request.lyrics_text.splitlines()]
                while lyrics_lines and not lyrics_lines[-1]:
                    lyrics_lines.pop()

                line_alignments = map_segments_to_lines(raw_segments, lyrics_lines)

                lrc_lines = []
                for start, _end, text in line_alignments:
                    timestamp = format_timestamp(start)
                    lrc_lines.append(f"{timestamp} {text}")
                lrc_content = "\n".join(lrc_lines)

                lrc_path = temp_path / "lyrics.lrc"
                lrc_path.write_text(lrc_content, encoding="utf-8")

                await self._update_stage(job, "uploading", 0.8)

                lrc_url = None
                if self.r2_client:
                    from ..storage.r2 import BackupFailedError, StaleObjectError

                    try:
                        lrc_url = await self.r2_client.upload_official_lrc(
                            hash_prefix,
                            lrc_path,
                            expected_etag=official_lrc_etag,
                        )
                    except StaleObjectError as e:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.stage = "stale_object"
                        job.updated_at = datetime.now(timezone.utc)
                        try:
                            await self.job_store.update_job(
                                job.id,
                                status="failed",
                                stage="stale_object",
                                error_message=str(e),
                            )
                        except Exception as db_err:
                            logger.error(f"Failed to update job {job.id} in database: {db_err}")
                        return
                    except BackupFailedError as e:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.stage = "backup_failed"
                        job.updated_at = datetime.now(timezone.utc)
                        try:
                            await self.job_store.update_job(
                                job.id,
                                status="failed",
                                stage="backup_failed",
                                error_message=str(e),
                            )
                        except Exception as db_err:
                            logger.error(f"Failed to update job {job.id} in database: {db_err}")
                        return

                job.result = JobResult(
                    lrc_url=lrc_url,
                    line_count=len(line_alignments),
                    lrc_source="forced_alignment",
                )
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"

                total_elapsed = time.time() - job_start_time
                logger.info(f"Forced alignment job completed in {total_elapsed:.2f}s")

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

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = f"Forced alignment failed: {e}"
            job.stage = "error"
            logger.error(f"Forced alignment job failed: {e}")

            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="error",
                    error_message=f"Forced alignment failed: {e}",
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

        finally:
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
                local_model_semaphore=self._local_model_semaphore,
                stage_updater=lambda s, p=None: self._update_stage(job, s, p),
                mvsep_quota_waiter=self._mvsep_quota_waiter,
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

    async def _process_embedding_job(self, job: Job) -> None:
        """Process an embedding job.

        Args:
            job: Job to process
        """
        set_job_id(job.id)
        logger.info(f"Starting embedding job for song: {job.request.song_id}")

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        job.stage = "embedding"
        job.progress = 0.1

        try:
            await self.job_store.update_job(
                job.id, status="processing", stage="embedding", progress=0.1
            )
        except Exception as e:
            logger.error(f"Failed to update job {job.id} in database: {e}")

        request = job.request
        if not isinstance(request, EmbeddingJobRequest):
            job.status = JobStatus.FAILED
            job.error_message = "Invalid request type for embedding job"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id, status="failed", error_message="Invalid request type"
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        if EmbeddingWorker is None:
            job.status = JobStatus.FAILED
            job.error_message = "Embedding dependencies not available (openai)"
            job.stage = "missing_dependencies"
            job.updated_at = datetime.now(timezone.utc)
            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="missing_dependencies",
                    error_message="Embedding dependencies not available (openai)",
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")
            return

        try:
            worker = EmbeddingWorker()
            result = await worker.embed_song(request)

            job.result = result
            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.stage = "completed"
            job.updated_at = datetime.now(timezone.utc)

            logger.info(
                f"Embedding job completed for song {result.song_id}: "
                f"{len(result.line_embeddings)} line embeddings"
            )

            try:
                await self.job_store.update_job(
                    job.id,
                    status="completed",
                    stage="completed",
                    progress=1.0,
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")

        except Exception as e:
            logger.error(f"Embedding job {job.id} failed: {e}")
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "error"
            job.updated_at = datetime.now(timezone.utc)

            try:
                await self.job_store.update_job(
                    job.id,
                    status="failed",
                    stage="error",
                    error_message=f"Unexpected error: {e}",
                )
            except Exception as db_err:
                logger.error(f"Failed to update job {job.id} in database: {db_err}")

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

        # Warning if job was processing (running task may continue)
        if previous_status == JobStatus.PROCESSING:
            warning = "Job was PROCESSING. The running task continues until service restart."
        elif previous_status == JobStatus.WAITING:
            # WAITING jobs have a running task but no side effects yet — safe to cancel
            pass

        return job, warning

    async def clear_queue(self) -> list[Job]:
        """Cancel all queued and processing jobs.

        Returns:
            List of jobs that were cancelled
        """
        cancelled_jobs: list[Job] = []

        # Find all QUEUED, WAITING, and PROCESSING jobs in memory
        for job_id, job in list(self._jobs.items()):
            if job.status in (JobStatus.QUEUED, JobStatus.WAITING, JobStatus.PROCESSING):
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

        # Also query DB for WAITING jobs not in memory
        try:
            db_waiting_jobs = await self.job_store.list_jobs(status=JobStatus.WAITING, limit=1000)
            for job in db_waiting_jobs:
                if job.id not in self._jobs:
                    job.status = JobStatus.CANCELLED
                    job.updated_at = datetime.now(timezone.utc)
                    job.stage = "cancelled"
                    try:
                        await self.job_store.update_job(job.id, status="cancelled", stage="cancelled")
                    except Exception as e:
                        logger.error(f"Failed to update cancelled job {job.id} in database: {e}")
                    self._jobs[job.id] = job
                    cancelled_jobs.append(job)
        except Exception as e:
            logger.error(f"Failed to list waiting jobs from database: {e}")

        return cancelled_jobs

    async def stop(self) -> None:
        """Stop processing jobs."""
        self._running = False
        await self.stop_periodic_logging()
        if self._forced_aligner_wrapper is not None:
            await self._forced_aligner_wrapper.cleanup()
        await self.job_store.close()

    def _log_queue_state(self) -> None:
        """Log current queue state statistics."""
        now = datetime.now(timezone.utc)

        stats: Dict[JobType, Dict[JobStatus, int]] = {
            jt: {status: 0 for status in JobStatus} for jt in JobType
        }
        queued_wait_times: Dict[JobType, list] = {jt: [] for jt in JobType}
        processing_durations: Dict[JobType, list] = {jt: [] for jt in JobType}

        has_reportable_jobs = False
        for job in self._jobs.values():
            stats[job.type][job.status] += 1
            if job.status == JobStatus.QUEUED:
                queued_wait_times[job.type].append(
                    (now - job.created_at).total_seconds()
                )
                has_reportable_jobs = True
            elif job.status == JobStatus.WAITING:
                queued_wait_times[job.type].append(
                    (now - job.created_at).total_seconds()
                )
                has_reportable_jobs = True
            elif job.status == JobStatus.PROCESSING:
                processing_durations[job.type].append(
                    (now - job.updated_at).total_seconds()
                )
                has_reportable_jobs = True
            elif (
                job.status == JobStatus.FAILED
                and (now - job.updated_at).total_seconds()
                <= FINISHED_JOB_MEMORY_RETENTION_SECONDS
            ):
                has_reportable_jobs = True

        if not has_reportable_jobs:
            return

        parts: list[str] = []
        wait_parts: list[str] = []
        for jt in JobType:
            s = stats[jt]
            parts.append(
                f"{jt.name}[queued:{s[JobStatus.QUEUED]},"
                f"waiting:{s[JobStatus.WAITING]},"
                f"processing:{s[JobStatus.PROCESSING]},"
                f"completed:{s[JobStatus.COMPLETED]},"
                f"failed:{s[JobStatus.FAILED]}]"
            )
            qwt = queued_wait_times[jt]
            if qwt:
                waits = ",".join(f"{w:.0f}s" for w in qwt[:3])
                if len(qwt) > 3:
                    waits += f",...+{len(qwt) - 3}more"
                wait_parts.append(f"{jt.name} queued=[{waits}]")
            pd = processing_durations[jt]
            if pd:
                avg_dur = sum(pd) / len(pd)
                wait_parts.append(f"{jt.name} processing={avg_dur:.0f}s")

        wait_time_str = " " + " ".join(wait_parts) if wait_parts else " none"
        logger.info(f"Queue state: {' '.join(parts)} | Wait times:{wait_time_str}")

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
