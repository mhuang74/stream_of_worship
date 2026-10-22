"""Audit trail for important admin mutations.

Emits one INFO line per mutation so operators can reconstruct who/what changed
from ``sow-admin.log``. Free-text values are never logged (only lengths/flags),
keeping the ``k=v`` shape parseable.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger("stream_of_worship.admin.audit")


def _clean(value: object) -> str:
    """Render an audit field: None/empty -> '-', strip CR/LF, else str()."""
    if value is None:
        return "-"
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    text = text.strip()
    return text or "-"


def log_change(
    event: str,
    *,
    song_id: Optional[str] = None,
    hash_prefix: Optional[str] = None,
    **extra_fields: Any,
) -> None:
    """Log one audit line: ``audit event=<e> song_id=<id|-> hash_prefix=<hash|-> k=v ...``."""
    extra = "".join(f" {key}={_clean(value)}" for key, value in extra_fields.items())
    logger.info(
        "audit event=%s song_id=%s hash_prefix=%s%s",
        event,
        _clean(song_id),
        _clean(hash_prefix),
        extra,
    )
