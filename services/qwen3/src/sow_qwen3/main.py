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

# Global aligner instance (will be initialized in plan 02)
aligner: object | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    # Startup
    # Initialize aligner (implemented in plan 02)
    logger.info("Qwen3 Alignment Service starting up")

    yield

    # Shutdown
    # Clean up aligner (implemented in plan 02)
    logger.info("Qwen3 Alignment Service shutting down")


app = FastAPI(
    title="Stream of Worship Qwen3 Alignment Service",
    version=__version__,
    lifespan=lifespan,
)

# Include routers (implemented in plans 02-03)


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
