"""Read-write database client for the Better Auth ``"user"`` table.

Used by the admin CLI to seed users (``sow-admin users add``) and by the
TUI to render the pick-a-user login screen. The other three Better Auth
tables (``account``, ``session``, ``verification``) are owned by the future
Next.js webapp and are not exposed through this client.
"""

from contextlib import contextmanager
from typing import Generator, Optional

import psycopg

from stream_of_worship.db.auth_models import User
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.helpers import to_str


class DuplicateEmailError(Exception):
    """Raised when create_user is called with an email that already exists."""

    def __init__(self, email: str):
        super().__init__(f"User with email '{email}' already exists")
        self.email = email


_USER_COLUMNS = '"id", "name", "email", "emailVerified", "image", "createdAt", "updatedAt"'


class UserClient:
    """CRUD client for the Better Auth ``"user"`` table.

    Attributes:
        connection_provider: ``ConnectionProvider`` instance.
    """

    def __init__(self, connection_provider: ConnectionProvider):
        self.connection_provider = connection_provider

    @property
    def connection(self) -> psycopg.Connection:
        return self.connection_provider.get_connection()

    def close(self) -> None:
        self.connection_provider.close()

    def __enter__(self) -> "UserClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection, None, None]:
        conn = self.connection
        with conn.transaction():
            yield conn

    def create_user(self, email: str, name: Optional[str] = None) -> User:
        """Create a new user and return the row, with the DB-assigned ID.

        Args:
            email: Login email (must be unique).
            name: Display name; defaults to the local-part of the email.

        Returns:
            The created ``User``.

        Raises:
            DuplicateEmailError: If a user with this email already exists.
        """
        display_name = name if name else email.split("@", 1)[0]
        try:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    INSERT INTO "user" ("name", "email")
                    VALUES (%s, %s)
                    RETURNING {_USER_COLUMNS}
                    """,
                    (display_name, email),
                )
                row = cursor.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateEmailError(email) from exc
        return User.from_row(tuple(row))

    def get_user(self, user_id: int) -> Optional[User]:
        """Fetch a user by ID, or None if not found."""
        cursor = self.connection.cursor()
        cursor.execute(
            f'SELECT {_USER_COLUMNS} FROM "user" WHERE "id" = %s', (user_id,)
        )
        row = cursor.fetchone()
        return User.from_row(tuple(row)) if row else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Fetch a user by email, or None if not found."""
        cursor = self.connection.cursor()
        cursor.execute(
            f'SELECT {_USER_COLUMNS} FROM "user" WHERE "email" = %s', (email,)
        )
        row = cursor.fetchone()
        return User.from_row(tuple(row)) if row else None

    def list_users(self, limit: Optional[int] = None) -> list[User]:
        """List all users, ordered by ID ascending (creation order)."""
        cursor = self.connection.cursor()
        query = f'SELECT {_USER_COLUMNS} FROM "user" ORDER BY "id" ASC'
        params: list = []
        if limit:
            query += " LIMIT %s"
            params.append(int(limit))
        cursor.execute(query, params)
        return [User.from_row(tuple(row)) for row in cursor.fetchall()]

    def delete_user(self, user_id: int) -> bool:
        """Delete a user. Returns True if a row was deleted.

        Cascades to ``songsets`` (and their items), ``user_settings``,
        ``user_lrc_override``, ``lyric_mark``, ``songset_share``,
        ``account``, and ``session`` via FK ON DELETE CASCADE.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM "user" WHERE "id" = %s', (user_id,))
            return cursor.rowcount > 0

    def preview_cascade_delete(self, user_id: int) -> dict[str, list[dict]]:
        """Return the rows that deleting a user would cascade-delete.

        Read-only: pure SELECTs, no transaction. Each cascade table is keyed
        by its name in cascade-graph order (``songsets`` before
        ``songset_items``); every key is always present, with an empty list
        when the table has no matching rows. Timestamp values are normalized
        to ISO strings via ``to_str`` so callers can render them directly.

        Args:
            user_id: The user whose cascade rows should be previewed.

        Returns:
            Mapping of table name to a list of row dicts (column → value).
        """
        queries = {
            "songsets": (
                """
                SELECT id, name, description, created_at FROM songsets
                WHERE user_id = %s ORDER BY created_at
                """,
                (user_id,),
            ),
            "songset_items": (
                """
                SELECT id, songset_id, song_id, position, created_at
                FROM songset_items
                WHERE songset_id IN (SELECT id FROM songsets WHERE user_id = %s)
                ORDER BY songset_id, position
                """,
                (user_id,),
            ),
            "user_settings": (
                """
                SELECT user_id, offline_auto_cache, created_at, updated_at
                FROM user_settings WHERE user_id = %s
                """,
                (user_id,),
            ),
            "user_lrc_override": (
                """
                SELECT id, recording_content_hash, created_at, updated_at
                FROM user_lrc_override WHERE user_id = %s ORDER BY created_at
                """,
                (user_id,),
            ),
            "lyric_mark": (
                """
                SELECT id, recording_content_hash, timestamp_seconds, created_at
                FROM lyric_mark WHERE user_id = %s ORDER BY created_at
                """,
                (user_id,),
            ),
            "songset_share": (
                """
                SELECT token, songset_id, render_job_id, created_by_user_id,
                       allow_download, created_at
                FROM songset_share WHERE created_by_user_id = %s ORDER BY created_at
                """,
                (user_id,),
            ),
            "account": (
                """
                SELECT id, "providerId", "accountId", "createdAt", "updatedAt"
                FROM "account" WHERE "userId" = %s ORDER BY "createdAt"
                """,
                (user_id,),
            ),
            "session": (
                """
                SELECT id, token, "expiresAt", "createdAt", "updatedAt"
                FROM "session" WHERE "userId" = %s ORDER BY "createdAt"
                """,
                (user_id,),
            ),
        }
        preview: dict[str, list[dict]] = {}
        cursor = self.connection.cursor()
        for table, (sql, params) in queries.items():
            try:
                cursor.execute(sql, params)
                columns = [col.name for col in cursor.description]
                rows = [
                    {col: to_str(value) for col, value in zip(columns, record)}
                    for record in cursor.fetchall()
                ]
            except psycopg.errors.UndefinedTable:
                # Older deployment may lack a cascade table (e.g.
                # songset_share): a missing table means zero rows would be
                # deleted from it, so report it as empty rather than crash.
                rows = []
            preview[table] = rows
        return preview
