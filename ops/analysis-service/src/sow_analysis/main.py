"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .logging_config import configure_logging

from . import __version__
from .config import settings

# Configure logging with job_id support
configure_logging(
    level=getattr(logging, settings.SOW_LOG_LEVEL, logging.INFO),
    suppress_external=True,
)
logger = logging.getLogger(__name__)

from .routes import health, jobs
from .routes.jobs import set_job_queue
from .workers.queue import JobQueue

# Optional imports for heavy dependencies
try:
    from .workers.separator_wrapper import AudioSeparatorWrapper
except ImportError:
    AudioSeparatorWrapper = None

try:
    from .services.mvsep_client import MvsepClient
except ImportError:
    MvsepClient = None

try:
    from .workers.forced_aligner import ForcedAlignerWrapper
except ImportError:
    ForcedAlignerWrapper = None

try:
    from .services.qwen3_asr_client import Qwen3AsrClient
except ImportError:
    Qwen3AsrClient = None

# Global job queue instance
job_queue: JobQueue

# Global separator wrapper instance
separator_wrapper: "AudioSeparatorWrapper | None" = None

# Global MVSEP client instance
mvsep_client: "MvsepClient | None" = None

# Global forced aligner wrapper instance
forced_aligner_wrapper: "ForcedAlignerWrapper | None" = None

# Global Qwen3 ASR client instance
qwen3_client: "Qwen3AsrClient | None" = None

# Global QuotaWaiter instances
mvsep_quota_waiter = None
qwen3_quota_waiter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global job_queue, separator_wrapper, mvsep_client, forced_aligner_wrapper
    global qwen3_client, mvsep_quota_waiter, qwen3_quota_waiter

    # Startup
    job_queue = JobQueue(
        max_concurrent_local_model=settings.SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS,
        cache_dir=settings.CACHE_DIR,
    )

    # Initialize persistent store and recover interrupted jobs
    await job_queue.initialize()

    # Initialize R2 if configured
    if settings.SOW_R2_ENDPOINT_URL:
        job_queue.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

    # Initialize MVSEP client if configured
    if MvsepClient is not None:
        if settings.SOW_MVSEP_API_KEY and settings.SOW_MVSEP_ENABLED:
            mvsep_client = MvsepClient()
            job_queue.set_mvsep_client(mvsep_client)
            logger.info("MVSEP client initialized (cloud stem separation enabled)")
        else:
            logger.info("MVSEP not configured (using local audio-separator only)")
    else:
        logger.warning("MvsepClient not available")

    # Create Qwen3 ASR client singleton if configured
    qwen3_client = None
    if Qwen3AsrClient is not None:
        if settings.SOW_DASHSCOPE_API_KEY:
            qwen3_client = Qwen3AsrClient(
                api_key=settings.SOW_DASHSCOPE_API_KEY,
                region=settings.SOW_DASHSCOPE_ASR_REGION,
                flash_model=settings.SOW_DASHSCOPE_ASR_FLASH_MODEL,
                filetrans_model=settings.SOW_DASHSCOPE_ASR_FILETRANS_MODEL,
            )
            job_queue.set_qwen3_client(qwen3_client)
            logger.info("Qwen3 ASR client singleton initialized")
        else:
            logger.info("Qwen3 ASR not configured (SOW_DASHSCOPE_API_KEY not set)")
    else:
        logger.warning("Qwen3AsrClient not available")

    # Create QuotaWaiters (for free-only patient mode)
    mvsep_quota_waiter = None
    qwen3_quota_waiter = None
    if mvsep_client is not None:
        from .workers.quota_waiter import QuotaWaiter

        mvsep_quota_waiter = QuotaWaiter(
            "mvsep",
            lambda: mvsep_client.is_available,
            settings.SOW_QUOTA_POLL_INTERVAL_SECONDS,
        )
    if qwen3_client is not None:
        from .workers.quota_waiter import QuotaWaiter

        qwen3_quota_waiter = QuotaWaiter(
            "qwen3",
            lambda: qwen3_client.is_available,
            settings.SOW_QUOTA_POLL_INTERVAL_SECONDS,
        )
    job_queue.set_quota_waiters(mvsep=mvsep_quota_waiter, qwen3=qwen3_quota_waiter)

    # Startup validation: warn about missing keys in free-only mode
    if settings.SOW_FREE_ONLY_MODE:
        if not settings.SOW_DASHSCOPE_API_KEY:
            logger.warning(
                "SOW_FREE_ONLY_MODE is enabled but SOW_DASHSCOPE_API_KEY is not set. "
                "LRC jobs with use_qwen3_asr=True will FAIL instead of waiting. "
                "Set SOW_DASHSCOPE_API_KEY to use free-only mode."
            )
        if not settings.SOW_MVSEP_API_KEY:
            logger.warning(
                "SOW_FREE_ONLY_MODE is enabled but SOW_MVSEP_API_KEY is not set. "
                "Stem separation jobs will FAIL (permanently unavailable). "
                "Set SOW_MVSEP_API_KEY to use free-only mode."
            )

    # Create separator wrapper (lazy init — models validated on first use, not at startup)
    if AudioSeparatorWrapper is not None:
        separator_wrapper = AudioSeparatorWrapper(
            model_dir=settings.SOW_AUDIO_SEPARATOR_MODEL_DIR,
            vocal_model=settings.SOW_VOCAL_SEPARATION_MODEL,
            dereverb_model=settings.SOW_DEREVERB_MODEL,
            output_format="FLAC",
        )
        job_queue.set_separator_wrapper(separator_wrapper)
        logger.info("Audio separator wrapper created (lazy init on first use)")
    else:
        logger.warning("AudioSeparatorWrapper not available (audio-separator not installed)")

    # Create forced aligner wrapper (lazy init)
    if ForcedAlignerWrapper is not None:
        forced_aligner_wrapper = ForcedAlignerWrapper(
            model_path=settings.SOW_FORCED_ALIGNER_MODEL_PATH,
            device=settings.SOW_FORCED_ALIGNER_DEVICE,
        )
        job_queue.set_forced_aligner_wrapper(forced_aligner_wrapper)
        logger.info("Forced aligner wrapper created (lazy init on first use)")
    else:
        logger.warning("ForcedAlignerWrapper not available (qwen-asr not installed)")

    # Log startup configuration (non-sensitive values only)
    headers = ("Category", "Setting", "Value")
    config_rows = [
        (
            "Processing",
            "max_concurrent_local_model",
            str(settings.SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS),
        ),
        ("Processing", "cache_dir", str(settings.CACHE_DIR)),
        ("Processing", "queue_start_delay", f"{settings.SOW_QUEUE_START_DELAY_SECONDS}s"),
        ("BPM (Fast Analysis)", "algorithm", settings.BPM_ALGORITHM_VERSION),
        ("LLM", "model", settings.SOW_LLM_MODEL or "(not set)"),
        ("LLM", "provider", settings.SOW_LLM_BASE_URL or "(not set)"),
        ("Embedding", "model", settings.SOW_EMBEDDING_MODEL),
        ("Embedding", "provider", settings.SOW_EMBEDDING_BASE_URL or "(not set)"),
        ("DashScope Qwen3 ASR", "configured", str(bool(settings.SOW_DASHSCOPE_API_KEY))),
        ("DashScope Qwen3 ASR", "region", settings.SOW_DASHSCOPE_ASR_REGION),
        ("DashScope Qwen3 ASR", "flash_model", settings.SOW_DASHSCOPE_ASR_FLASH_MODEL),
        ("DashScope Qwen3 ASR", "filetrans_model", settings.SOW_DASHSCOPE_ASR_FILETRANS_MODEL),
        ("DashScope Qwen3 ASR", "max_concurrent", str(settings.SOW_DASHSCOPE_ASR_MAX_CONCURRENT)),
        ("Qwen3 ForcedAligner", "model_path", settings.SOW_FORCED_ALIGNER_MODEL_PATH),
        ("Qwen3 ForcedAligner", "device", settings.SOW_FORCED_ALIGNER_DEVICE),
        ("Whisper", "device", settings.SOW_WHISPER_DEVICE),
        ("Whisper", "cache_dir", str(settings.SOW_WHISPER_CACHE_DIR)),
        ("Demucs", "model", settings.SOW_DEMUCS_MODEL),
        ("Demucs", "device", settings.SOW_DEMUCS_DEVICE),
        ("Audio Separator", "model_dir", str(settings.SOW_AUDIO_SEPARATOR_MODEL_DIR)),
        ("Audio Separator", "vocal_model", settings.SOW_VOCAL_SEPARATION_MODEL),
        ("Audio Separator", "dereverb_model", settings.SOW_DEREVERB_MODEL),
        ("MVSEP", "enabled", str(settings.SOW_MVSEP_ENABLED)),
        ("MVSEP", "stage1_sep_type", str(settings.SOW_MVSEP_STAGE1_SEP_TYPE)),
        ("MVSEP", "stage2_sep_type", str(settings.SOW_MVSEP_STAGE2_SEP_TYPE)),
        ("MVSEP", "max_concurrent", str(settings.SOW_MVSEP_MAX_CONCURRENT)),
        ("Free-Only Mode", "enabled", str(settings.SOW_FREE_ONLY_MODE)),
        ("Free-Only Mode", "poll_interval_seconds", str(settings.SOW_QUOTA_POLL_INTERVAL_SECONDS)),
        ("YouTube", "proxy", settings.SOW_YOUTUBE_PROXY or "(not set)"),
        ("YouTube", "proxy_retries", str(settings.SOW_YOUTUBE_PROXY_RETRIES)),
        ("R2", "bucket", settings.SOW_R2_BUCKET),
        ("R2", "endpoint", settings.SOW_R2_ENDPOINT_URL or "(not set)"),
    ]
    col_widths = [max(len(r[i]) for r in config_rows + [headers]) for i in range(3)]
    separator = f"+-{'-' * col_widths[0]}-+-{'-' * col_widths[1]}-+-{'-' * col_widths[2]}-+"
    table_lines = [
        separator,
        f"| {headers[0]:<{col_widths[0]}} | {headers[1]:<{col_widths[1]}} | {headers[2]:<{col_widths[2]}} |",
        separator,
    ]
    for row in config_rows:
        table_lines.append(
            f"| {row[0]:<{col_widths[0]}} | {row[1]:<{col_widths[1]}} | {row[2]:<{col_widths[2]}} |"
        )
    table_lines.append(separator)
    logger.info("Startup configuration:\n%s", "\n".join(table_lines))

    # Set job queue in routes
    set_job_queue(job_queue)

    # Start background job processor
    task = asyncio.create_task(job_queue.process_jobs())

    yield

    # Shutdown
    await job_queue.stop()

    # Cleanup separator wrapper
    if separator_wrapper is not None:
        await separator_wrapper.cleanup()
        logger.info("Audio separator wrapper cleaned up")

    # Cleanup forced aligner wrapper
    if forced_aligner_wrapper is not None:
        await forced_aligner_wrapper.cleanup()
        logger.info("Forced aligner wrapper cleaned up")

    # Cleanup MVSEP client
    if mvsep_client is not None:
        await mvsep_client.aclose()
        logger.info("MVSEP client closed")

    # Stop QuotaWaiters
    if mvsep_quota_waiter is not None:
        await mvsep_quota_waiter.stop()
        logger.info("MVSEP quota waiter stopped")
    if qwen3_quota_waiter is not None:
        await qwen3_quota_waiter.stop()
        logger.info("Qwen3 quota waiter stopped")

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Stream of Worship Analysis Service",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    """Root endpoint.

    Returns:
        Service info
    """
    return {
        "message": "Stream of Worship Analysis Service",
        "version": __version__,
    }


def main() -> None:
    """Entry point for running the service directly."""
    import uvicorn

    uvicorn.run(
        "sow_analysis.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
