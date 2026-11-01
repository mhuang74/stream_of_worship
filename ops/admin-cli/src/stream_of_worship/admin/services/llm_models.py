"""Reasoning-effort suffixes on ``SOW_LLM_MODEL`` / ``--llm-model`` values.

``llm_model`` may carry a reasoning-effort suffix, ``model:effort``
(e.g. ``deepseek-v4.1-flash:low``). The provider has no ``:effort`` model ids,
so the suffix is parsed off before any local chat client is built and passed
as the ``reasoning_effort`` request parameter instead.

This module is the admin-CLI twin of
``ops/analysis-service/src/sow_analysis/services/llm_models.py``
(``KNOWN_REASONING_EFFORTS`` / ``split_llm_model_effort`` / ``chat_model_kwargs``).
The splitting semantics MUST stay behaviourally identical; only the shape of
``chat_model_kwargs`` differs (see its docstring).
"""

from typing import Any

#: Reasoning-effort variants accepted by the provider's ``reasoning_effort``
#: chat parameter (source: provider 400 error message listing the variants).
KNOWN_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def split_llm_model_effort(llm_model: str) -> tuple[str, str | None]:
    """Split a ``model:effort`` llm_model string into ``(model_id, effort)``.

    Splits on the LAST ``:`` and only when the suffix is a known reasoning
    effort; anything else (no colon, or an OpenRouter-style suffix like
    ``some/provider:free``) is returned untouched so it still reaches the
    provider as-is. Case-sensitive: the provider's effort variants are
    lowercase.
    """
    if ":" not in llm_model:
        return llm_model, None
    model_id, _, suffix = llm_model.rpartition(":")
    if suffix in KNOWN_REASONING_EFFORTS:
        return model_id, suffix
    return llm_model, None


def chat_model_kwargs(llm_model: str) -> dict[str, Any]:
    """``ChatOpenAI`` constructor kwargs for a ``model:effort`` string.

    ``{"model": model_id}``, plus ``{"extra_body": {"reasoning_effort": effort}}``
    when the suffix is a known reasoning effort. A model id is never passed to
    the provider with its effort suffix attached.

    Note: this differs from the analysis-service function of the same name,
    which targets ``client.chat.completions.create`` and puts
    ``reasoning_effort`` top-level. ``extra_body`` reproduces that same
    top-level request-body placement through the LangChain client.
    """
    model_id, effort = split_llm_model_effort(llm_model)
    kwargs: dict[str, Any] = {"model": model_id}
    if effort is not None:
        kwargs["extra_body"] = {"reasoning_effort": effort}
    return kwargs
