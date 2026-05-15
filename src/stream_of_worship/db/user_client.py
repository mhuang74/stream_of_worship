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
        if limit:
            query += f" LIMIT {int(limit)}"
        cursor.execute(query)
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
