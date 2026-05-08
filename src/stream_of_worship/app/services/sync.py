"""Connection health checker for sow-app.

Provides a simple function to verify PostgreSQL connectivity.
The old Turso sync infrastructure has been removed as part of the
Neon/PostgreSQL migration.
"""

import psycopg


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
        with psycopg.connect(database_url, connect_timeout=timeout) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
