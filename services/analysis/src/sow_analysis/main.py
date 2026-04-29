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

# Global job queue instance
job_queue: JobQueue

# Global separator wrapper instance
separator_wrapper: "AudioSeparatorWrapper | None" = None


async def _init_separator_wrapper(job_queue: JobQueue, cfg) -> None:
    """Load separator models in background; set on queue when ready."""
    global separator_wrapper
    wrapper = AudioSeparatorWrapper(
        model_dir=cfg.SOW_AUDIO_SEPARATOR_MODEL_DIR,
        bs_roformer_model=cfg.SOW_BS_ROFORMER_MODEL,
        dereverb_model=cfg.SOW_DEREVERB_MODEL,
        output_format="FLAC",
    )
    await wrapper.initialize()
    if wrapper.is_ready:
        separator_wrapper = wrapper
        job_queue.set_separator_wrapper(wrapper)
        logger.info("Audio separator wrapper initialized and ready")
    else:
        logger.error(
            "Audio separator wrapper initialization failed - "
            "stem separation jobs will fail. "
            "Check that model files exist in: %s",
            cfg.SOW_AUDIO_SEPARATOR_MODEL_DIR,
        )
        job_queue.notify_separator_init_failed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global job_queue, separator_wrapper

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

    # Set job queue in routes
    set_job_queue(job_queue)

    # Start background job processor
    task = asyncio.create_task(job_queue.process_jobs())

    # Initialize separator wrapper in background so the service
    # starts accepting requests immediately (LRC jobs don't need it)
    bg_separator_task = None
    if AudioSeparatorWrapper is not None:
        bg_separator_task = asyncio.create_task(_init_separator_wrapper(job_queue, settings))
    else:
        logger.warning("AudioSeparatorWrapper not available (audio-separator not installed)")

    yield

    # Shutdown
    if bg_separator_task is not None:
        bg_separator_task.cancel()
        try:
            await bg_separator_task
        except asyncio.CancelledError:
            pass

    await job_queue.stop()

    # Cleanup separator wrapper
    if separator_wrapper is not None:
        await separator_wrapper.cleanup()
        logger.info("Audio separator wrapper cleaned up")

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
