"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from . import __version__
from .config import settings
from .routes import align, health
from .workers.aligner import Qwen3AlignerWrapper

# Global aligner instance (initialized in lifespan)
aligner: Qwen3AlignerWrapper | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global aligner

    # Startup
    logger.info("Qwen3 Alignment Service starting up")

    # Initialize aligner instance
    aligner = Qwen3AlignerWrapper(
        model_path=settings.MODEL_PATH,
        device=settings.DEVICE,
        max_concurrent=settings.MAX_CONCURRENT,
    )

    # Load model with graceful failure handling (service can start even if loading fails)
    try:
        await aligner.initialize()
        logger.info("Qwen3 Alignment Service ready")
    except Exception as e:
        logger.error(f"Failed to load model on startup: {e}")
        logger.warning("Service will start with model NOT ready (health check will return 503)")

    # Set aligner in health router for health checks
    health.set_aligner(lambda: aligner)

    yield

    # Shutdown
    logger.info("Qwen3 Alignment Service shutting down")
    if aligner:
        await aligner.cleanup()


app = FastAPI(
    title="Stream of Worship Qwen3 Alignment Service",
    version=__version__,
    lifespan=lifespan,
)

# Include routers
app.include_router(health.router)
app.include_router(align.router)


@app.get("/")
async def root() -> dict:
    """Root endpoint.

    Returns:
        Service info
    """
    return {
        "message": "Stream of Worship Qwen3 Alignment Service",
        "version": __version__,
    }


def main() -> None:
    """Entry point for running the service directly."""
    import uvicorn

    uvicorn.run(
        "sow_qwen3.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
