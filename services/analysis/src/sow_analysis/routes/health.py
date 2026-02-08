"""Health check endpoint."""

import logging

from fastapi import APIRouter

from .. import __version__
from ..config import settings
from ..storage.cache import CacheManager

logger = logging.getLogger(__name__)
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


def check_llm_connection() -> dict:
    """Check if LLM is configured and can make a completion call.

    Returns:
        Status dictionary with test result
    """
    # Check configuration
    if not settings.SOW_LLM_BASE_URL:
        return {"status": "not_configured", "error": "SOW_LLM_BASE_URL not set"}
    if not settings.SOW_LLM_API_KEY:
        return {"status": "missing_credentials", "error": "SOW_LLM_API_KEY not set"}
    if not settings.SOW_LLM_MODEL:
        return {"status": "missing_model", "error": "SOW_LLM_MODEL not set"}

    # Test actual API call
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
        )

        response = client.chat.completions.create(
            model=settings.SOW_LLM_MODEL,
            messages=[
                {"role": "user", "content": "Give me a short inspirational quote (max 40 words)"},
            ],
            max_tokens=400,
            temperature=0.7,
        )

        content = response.choices[0].message.content
        return {
            "status": "healthy",
            "model": settings.SOW_LLM_MODEL,
            "quote": content.strip() if content else None,
        }

    except Exception as e:
        logger.warning(f"LLM health check failed: {e}")
        return {
            "status": "unhealthy",
            "model": settings.SOW_LLM_MODEL,
            "error": str(e),
        }


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
            "llm": check_llm_connection(),
        },
    }
