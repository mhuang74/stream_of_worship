"""Explicit LLM model validation against the provider's live /models list.

Used by the LRC job-start guard (workers/queue.py): when a job explicitly
requests an ``llm_model`` (e.g. ``sow-admin lyrics generate --llm-model``),
the model id is checked against the provider's model list before any job
work begins (fail-closed). An empty ``llm_model`` (the SOW_LLM_MODEL env
default) is never validated.

``llm_model`` may carry a reasoning-effort suffix, ``model:effort``
(e.g. ``deepseek-v4.1-flash:low``). The suffix is parsed off before the
model-id membership check and, when valid, is forwarded by the pipeline's
chat calls as the ``reasoning_effort`` request parameter (the provider has
no ``:effort`` model ids). Supported efforts: none|minimal|low|medium|high|
xhigh|max, validated per model against the provider's reported
``metadata.reasoning`` accepted/supported efforts.

``chat_model_kwargs`` is the single entry point every chat call site uses to
build its ``model``/``reasoning_effort`` request parameters.
"""

import asyncio
import logging
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)


class LLMModelValidationError(Exception):
    """Raised when an explicitly-requested LLM model id cannot be validated."""


#: Reasoning-effort variants accepted by the provider's ``reasoning_effort``
#: chat parameter (source: provider 400 error message listing the variants).
KNOWN_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def split_llm_model_effort(llm_model: str) -> tuple[str, Optional[str]]:
    """Split a ``model:effort`` llm_model string into ``(model_id, effort)``.

    Splits on the LAST ``:`` and only when the suffix is a known reasoning
    effort; anything else (no colon, or an OpenRouter-style suffix like
    ``some/provider:free``) is returned untouched so it still fails the
    provider membership check as-is. Case-sensitive: the provider's effort
    variants are lowercase.
    """
    if ":" not in llm_model:
        return llm_model, None
    model_id, _, suffix = llm_model.rpartition(":")
    if suffix in KNOWN_REASONING_EFFORTS:
        return model_id, suffix
    return llm_model, None


def chat_model_kwargs(llm_model: str) -> dict[str, str]:
    """Chat-create kwargs for a ``model:effort`` string.

    ``{"model": model_id}``, plus ``{"reasoning_effort": effort}`` when the
    suffix is a known reasoning effort. A model id is never passed to the
    provider with its effort suffix attached.
    """
    model_id, effort = split_llm_model_effort(llm_model)
    kwargs: dict[str, str] = {"model": model_id}
    if effort is not None:
        kwargs["reasoning_effort"] = effort
    return kwargs


def _extract_reasoning_meta(model_obj) -> Optional[dict]:
    """Defensively read ``metadata.reasoning`` off a models.list() entry.

    The SDK's ``Model`` objects are extra-allow, so ``metadata`` may surface
    as a pydantic field, a plain dict, or live in ``model_extra``; the
    ``reasoning`` value may likewise be a dict or an object. Returns None
    when either level is absent or unrecognized.
    """
    metadata = getattr(model_obj, "metadata", None)
    if metadata is None and isinstance(getattr(model_obj, "model_extra", None), dict):
        metadata = model_obj.model_extra.get("metadata")
    if metadata is None:
        return None
    if isinstance(metadata, dict):
        reasoning = metadata.get("reasoning")
    else:
        reasoning = getattr(metadata, "reasoning", None)
    if reasoning is None:
        return None
    if isinstance(reasoning, dict):
        return reasoning
    projected = {
        key: getattr(reasoning, key)
        for key in ("accepted_efforts", "supported_efforts", "effort_aliases", "mandatory")
        if hasattr(reasoning, key)
    }
    return projected or None


def _fetch_provider_models() -> tuple[set[str], dict[str, dict]]:
    from openai import OpenAI  # lazy import, same pattern as workers/lrc.py _call_llm

    client = OpenAI(
        api_key=settings.SOW_LLM_API_KEY,
        base_url=settings.SOW_LLM_BASE_URL,
        max_retries=0,
        timeout=settings.SOW_LLM_MODEL_VALIDATION_TIMEOUT_SECONDS,
    )
    model_ids: set[str] = set()
    reasoning_meta: dict[str, dict] = {}
    for m in client.models.list():  # SDK iterator auto-paginates
        model_ids.add(m.id)
        meta = _extract_reasoning_meta(m)
        if meta is not None:
            reasoning_meta[m.id] = meta
    return model_ids, reasoning_meta


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
    logger.info(
        "Validating LLM model '%s' against provider /models (base_url=%s)",
        llm_model,
        settings.SOW_LLM_BASE_URL,
    )
    try:
        model_ids, _reasoning_meta = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_provider_models
        )
    except Exception as e:  # network, auth, openai not installed (ImportError), etc.
        raise LLMModelValidationError(f"LLM model validation failed for '{llm_model}': {e}") from e
    if llm_model not in model_ids:
        raise LLMModelValidationError(
            f"LLM model '{llm_model}' is not in the provider's model list (base_url={settings.SOW_LLM_BASE_URL})"
        )
    logger.info(
        "LLM model '%s' validated against provider (%d models listed)",
        llm_model,
        len(model_ids),
    )
    return model_ids


async def validate_llm_model_request(llm_model: str) -> tuple[str, Optional[str]]:
    """Validate a ``model:effort`` llm_model string against the provider.

    Same fail-closed philosophy as :func:`validate_llm_model`: the model id
    (with the effort suffix parsed off) must exist in the provider's live
    /models list, and a requested reasoning effort must be accepted for
    that model per the provider's ``metadata.reasoning``.

    Returns ``(model_id, effort)`` where ``effort`` is None when no
    ``:effort`` suffix was present.
    """
    model_id, effort = split_llm_model_effort(llm_model)
    if not settings.SOW_LLM_API_KEY:
        raise LLMModelValidationError(
            f"SOW_LLM_API_KEY not set; cannot validate llm_model '{llm_model}'"
        )
    if not settings.SOW_LLM_BASE_URL:
        raise LLMModelValidationError(
            f"SOW_LLM_BASE_URL not set; cannot validate llm_model '{llm_model}'"
        )
    logger.info(
        "Validating LLM model '%s' against provider /models (base_url=%s)",
        model_id,
        settings.SOW_LLM_BASE_URL,
    )
    try:
        model_ids, reasoning_meta = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_provider_models
        )
    except Exception as e:  # network, auth, openai not installed (ImportError), etc.
        raise LLMModelValidationError(f"LLM model validation failed for '{llm_model}': {e}") from e
    if model_id not in model_ids:
        raise LLMModelValidationError(
            f"LLM model '{model_id}' is not in the provider's model list "
            f"(base_url={settings.SOW_LLM_BASE_URL})"
        )
    if effort is not None:
        meta = reasoning_meta.get(model_id)
        if meta is None:
            raise LLMModelValidationError(
                f"Provider does not report reasoning metadata for model '{model_id}'; "
                f"cannot validate reasoning effort '{effort}'"
            )
        accepted = meta.get("accepted_efforts") or meta.get("supported_efforts")
        if not accepted or effort not in accepted:
            sorted_accepted = sorted(accepted) if accepted else []
            raise LLMModelValidationError(
                f"Reasoning effort '{effort}' is not accepted for model '{model_id}'; "
                f"accepted efforts: {sorted_accepted}"
            )
    logger.info(
        "LLM model '%s' validated against provider (%d models listed)",
        model_id,
        len(model_ids),
    )
    return model_id, effort
