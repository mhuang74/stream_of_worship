"""Health check endpoint."""

import logging

from fastapi import APIRouter, HTTPException

from .. import __version__
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Global aligner instance - set by main.py during lifespan
_aligner_getter: object | None = None


def set_aligner(getter: object) -> None:
    """Set the global aligner getter function.

    This is called by main.py during lifespan initialization.

    Args:
        getter: A callable that returns the aligner instance
    """
    global _aligner_getter
    _aligner_getter = getter


def get_aligner() -> object | None:
    """Get the aligner instance.

    Returns:
        The aligner instance or None if not set
    """
    if _aligner_getter is None:
        return None
    return _aligner_getter()


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint.

    Returns:
        Service health status. Returns 503 if model is not loaded.
    """
    aligner = get_aligner()
    if aligner is None or not aligner.is_ready:
        raise HTTPException(
            status_code=503,
            detail="Model not loaded",
        )

    return {
        "status": "healthy",
        "version": __version__,
        "model": "ready",
        "device": settings.DEVICE,
        "max_concurrent": settings.MAX_CONCURRENT,
    }
