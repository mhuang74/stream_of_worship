"""FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import settings
from .routes import health, jobs
from .routes.jobs import set_job_queue
from .workers.queue import JobQueue

# Global job queue instance
job_queue: JobQueue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global job_queue

    # Startup
    job_queue = JobQueue(
        max_concurrent=settings.SOW_MAX_CONCURRENT_JOBS,
        cache_dir=settings.CACHE_DIR,
    )

    # Initialize R2 if configured
    if settings.SOW_R2_ENDPOINT_URL:
        job_queue.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

    # Set job queue in routes
    set_job_queue(job_queue)

    # Start background job processor
    task = asyncio.create_task(job_queue.process_jobs())

    yield

    # Shutdown
    job_queue.stop()
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
