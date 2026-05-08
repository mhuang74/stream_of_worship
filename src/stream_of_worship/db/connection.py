"""Connection management for PostgreSQL databases.

Provides a ``ConnectionProvider`` that manages a single psycopg connection
with auto-reconnect and cold-start retry.  Both ``ReadOnlyClient`` and
``SongsetClient`` accept a ``ConnectionProvider`` instead of managing their
own connections, which keeps connection overhead low on Neon and makes the
clients easy to test with mock connections.
"""

from __future__ import annotations

import time
from typing import Optional

import psycopg


class ConnectionProvider:
    """Manages a single psycopg connection with auto-reconnect and cold-start
    retry.
    """

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None

    def get_connection(self) -> psycopg.Connection:
        """Return an active psycopg connection, reconnecting if necessary."""
        if self._connection is None or self._connection.closed:
            self._connection = self._connect_with_retry()
        return self._connection

    def _connect_with_retry(self) -> psycopg.Connection:
        """Attempt to connect with exponential backoff on failure."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                conn = psycopg.connect(
                    self.database_url,
                    connect_timeout=10,
                )
                # Verify connection is alive
                conn.execute("SELECT 1")
                return conn
            except Exception as exc:
                last_exc = exc
                if attempt == self.MAX_RETRIES:
                    break
                time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_exc if last_exc is not None else RuntimeError("Failed to connect to database")

    def close(self) -> None:
        """Close the current connection if open."""
        if self._connection is not None and not self._connection.closed:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> ConnectionProvider:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
