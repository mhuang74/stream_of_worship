"""Shared database helper functions.

Provides common utilities for database operations across admin and app layers.
"""

from datetime import datetime
from typing import Optional


def to_str(val) -> Optional[str]:
    """Coerce a value to an ISO-8601 string, handling datetime objects.

    psycopg3 returns ``timestamptz`` columns as ``datetime`` objects with
    ``tzinfo=timezone.utc``.  This helper converts those back to strings so
    that dataclass fields can remain ``Optional[str]`` with minimal changes.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)
