"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .logging_config import configure_logging

# Configure logging with job_id support
configure_logging(level=logging.INFO, suppress_external=True)
logger = logging.getLogger(__name__)

from . import __version__
from .config import settings
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

# Global job queue instance
job_queue: JobQueue

# Global separator wrapper instance
separator_wrapper: "AudioSeparatorWrapper | None" = None

# Global MVSEP client instance
mvsep_client: "MvsepClient | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global job_queue, separator_wrapper, mvsep_client

    # Startup
    job_queue = JobQueue(
        max_concurrent_analysis=settings.SOW_MAX_CONCURRENT_ANALYSIS_JOBS,
        max_concurrent_lrc=settings.SOW_MAX_CONCURRENT_LRC_JOBS,
        max_concurrent_stem_separation=settings.SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS,
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

    # Cleanup MVSEP client
    if mvsep_client is not None:
        await mvsep_client.aclose()
        logger.info("MVSEP client closed")

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
