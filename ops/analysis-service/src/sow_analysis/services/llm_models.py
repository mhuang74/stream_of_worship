"""Explicit LLM model validation against the provider's live /models list.

Used by the LRC job-start guard (workers/queue.py): when a job explicitly
requests an ``llm_model`` (e.g. ``sow-admin lyrics generate --llm-model``),
the model id is checked against the provider's model list before any job
work begins (fail-closed). An empty ``llm_model`` (the SOW_LLM_MODEL env
default) is never validated.
"""

import asyncio
import logging

from ..config import settings

logger = logging.getLogger(__name__)


class LLMModelValidationError(Exception):
    """Raised when an explicitly-requested LLM model id cannot be validated."""


def _fetch_provider_model_ids() -> set[str]:
    from openai import OpenAI  # lazy import, same pattern as workers/lrc.py _call_llm

    client = OpenAI(
        api_key=settings.SOW_LLM_API_KEY,
        base_url=settings.SOW_LLM_BASE_URL,
        max_retries=0,
        timeout=settings.SOW_LLM_MODEL_VALIDATION_TIMEOUT_SECONDS,
    )
    return {m.id for m in client.models.list()}  # SDK iterator auto-paginates


async def validate_llm_model(llm_model: str) -> set[str]:
    """Validate an explicitly-requested LLM model id against the provider.

    Fail-closed: missing config, provider/network errors, or a membership
    miss all raise :class:`LLMModelValidationError`. Exact string match on
    the model id.

    Returns the provider's model id set (for logging/tests).
    """
    if not settings.SOW_LLM_API_KEY:
        raise LLMModelValidationError(
            f"SOW_LLM_API_KEY not set; cannot validate llm_model '{llm_model}'"
        )
    if not settings.SOW_LLM_BASE_URL:
        raise LLMModelValidationError(
            f"SOW_LLM_BASE_URL not set; cannot validate llm_model '{llm_model}'"
        )
    try:
        model_ids: set[str] = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_provider_model_ids
        )
    except Exception as e:  # network, auth, openai not installed (ImportError), etc.
        raise LLMModelValidationError(
            f"LLM model validation failed for '{llm_model}': {e}"
        ) from e
    if llm_model not in model_ids:
        raise LLMModelValidationError(
            f"LLM model '{llm_model}' is not in the provider's model list (base_url={settings.SOW_LLM_BASE_URL})"
        )
    logger.debug(
        "LLM model '%s' found in provider model list (%d models)",
        llm_model,
        len(model_ids),
    )
    return model_ids
