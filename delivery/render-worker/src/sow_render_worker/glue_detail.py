"""Bounded, leak-free failure detail for durable Glue jobs.

One sanitizer for every sink that can echo a failure detail — the persisted
``glue_jobs.error_detail``, the ``glue_excerpts.safe_failure_detail`` negative
cache, and the structured log lines — so a provider body containing a URL, a
filesystem path, a key/token word, or a stack frame never leaves the process.
Callers keep their own fallback (usually the stable error code) because that is
the vocabulary the operator and the client already understand.
"""

from __future__ import annotations

import re

# Anything that could leak a URL, a filesystem path, a secret or a stack.
UNSAFE_DETAIL_PATTERN = re.compile(
    r"([/\\]|api[_-]?key|secret|token|bearer|password|traceback|\bat\s+\w+\()",
    re.IGNORECASE,
)

MAX_DETAIL_LENGTH = 200


def sanitize_detail(detail: object, *, fallback: str) -> str:
    """Collapse a failure detail to a short, leak-free sentence."""
    if not isinstance(detail, str):
        return fallback
    text = " ".join(detail.split())
    if not text:
        return fallback
    if UNSAFE_DETAIL_PATTERN.search(text):
        return fallback
    return text[:MAX_DETAIL_LENGTH]
