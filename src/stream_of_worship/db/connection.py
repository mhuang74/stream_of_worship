"""Shared database connection utilities for PostgreSQL.

Provides ``ConnectionProvider``, a lightweight wrapper around psycopg that
manages a single connection with automatic reconnection and cold-start retry.
This decouples client classes from connection lifecycle and allows both
``ReadOnlyClient`` and ``SongsetClient`` to share the same underlying
``psycopg.Connection``.
"""

import time
from typing import Optional

import psycopg


class ConnectionProvider:
    """Manages a single psycopg connection with auto-reconnect and cold-start retry.

    Attributes:
        database_url: Fully-formed ``postgresql://`` connection string.
    """

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None

    def get_connection(self) -> psycopg.Connection:
        """Return an open psycopg connection, reconnecting if necessary."""
        if self._connection is None or self._connection.closed:
            self._connection = self._connect_with_retry()
        return self._connection

    def _connect_with_retry(self) -> psycopg.Connection:
        """Attempt to connect with exponential backoff for cold starts."""
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                conn = psycopg.connect(
                    self.database_url,
                    connect_timeout=10,
                )
                conn.execute("SELECT 1")
                return conn
            except Exception as exc:
                last_error = exc
                if attempt == self.MAX_RETRIES:
                    break
                time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def close(self) -> None:
        """Close the managed connection if it is open."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "ConnectionProvider":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
