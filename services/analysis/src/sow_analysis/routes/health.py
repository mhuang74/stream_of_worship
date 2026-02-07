"""Health check endpoint."""

from fastapi import APIRouter

from .. import __version__
from ..config import settings
from ..storage.cache import CacheManager

router = APIRouter()


def check_cache_access() -> dict:
    """Check if cache directory is accessible.

    Returns:
        Status dictionary
    """
    try:
        cache = CacheManager(settings.CACHE_DIR)
        test_file = cache.cache_dir / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        return {"status": "healthy", "path": str(settings.CACHE_DIR)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def check_r2_connection() -> dict:
    """Check if R2 is configured.

    Returns:
        Status dictionary
    """
    if not settings.SOW_R2_ENDPOINT_URL:
        return {"status": "not_configured"}
    if not settings.SOW_R2_ACCESS_KEY_ID or not settings.SOW_R2_SECRET_ACCESS_KEY:
        return {"status": "missing_credentials"}
    return {"status": "configured", "bucket": settings.SOW_R2_BUCKET}


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint.

    Returns:
        Service health status
    """
    return {
        "status": "healthy",
        "version": __version__,
        "services": {
            "r2": check_r2_connection(),
            "cache": check_cache_access(),
        },
    }
