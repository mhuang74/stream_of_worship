"""Shared database connection utilities for PostgreSQL.

Provides ``ConnectionProvider``, a lightweight wrapper around psycopg that
manages a single connection with automatic reconnection and cold-start retry.
This decouples client classes from connection lifecycle and allows both
``ReadOnlyClient`` and ``SongsetClient`` to share the same underlying
``psycopg.Connection``.
"""

import threading
import time
from typing import Optional

import psycopg


class ConnectionProvider:
    """Manages a single psycopg connection with auto-reconnect and cold-start retry.

    Thread-safe: uses a lock to serialize connection creation, preventing
    race conditions when multiple threads call get_connection() simultaneously
    on a closed or None connection.

    Attributes:
        database_url: Fully-formed ``postgresql://`` connection string.
    """

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()

    def get_connection(self) -> psycopg.Connection:
        """Return an open psycopg connection, reconnecting if necessary.
        
        Performs a health check on cached connections to detect broken connections
        (e.g., from idle-in-transaction timeouts on serverless PostgreSQL).
        """
        with self._lock:
            if self._connection is None or self._connection.closed:
                self._connection = self._connect_with_retry()
            else:
                try:
                    self._connection.execute("SELECT 1")
                except Exception:
                    self._connection = self._connect_with_retry()
            return self._connection

    def _connect_with_retry(self) -> psycopg.Connection:
        """Attempt to connect with exponential backoff for cold starts."""
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            conn = None
            try:
                conn = psycopg.connect(
                    self.database_url,
                    connect_timeout=10,
                    autocommit=True,
                    sslmode="require",
                )
                conn.execute("SELECT 1")
                return conn
            except Exception as exc:
                if conn:
                    conn.close()
                last_error = exc
                if attempt == self.MAX_RETRIES:
                    break
                time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_error if last_error else RuntimeError("Connection failed without error")

    def close(self) -> None:
        """Close the managed connection if it is open."""
        with self._lock:
            if self._connection and not self._connection.closed:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "ConnectionProvider":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def check_database_connection(database_url: str, timeout: int = 10) -> bool:
    """Verify that a PostgreSQL database is reachable.

    Args:
        database_url: Postgres DSN (with password if required).
        timeout: Connection timeout in seconds.

    Returns:
        True if the connection succeeds and ``SELECT 1`` returns a row,
        False otherwise.
    """
    try:
        with psycopg.connect(
            database_url, connect_timeout=timeout, sslmode="require"
        ) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
