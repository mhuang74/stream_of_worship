"""Data models for Better Auth identity entities.

Shared between admin (``sow-admin users``) and app (TUI login screen). The
``User`` dataclass maps the Better Auth ``"user"`` table's camelCase columns
to Python snake_case fields. IDs are sequential integers assigned by the
database (``BIGINT GENERATED ALWAYS AS IDENTITY``).
"""

from dataclasses import dataclass
from typing import Any, Optional

from stream_of_worship.db.helpers import to_str


@dataclass
class User:
    """A user identity (Better Auth ``"user"`` table row).

    Attributes:
        id: DB-assigned sequential integer ID.
        name: Display name.
        email: Login email (unique).
        email_verified: Whether the email has been verified.
        image: Optional avatar URL.
        created_at: ISO timestamp when created.
        updated_at: ISO timestamp when last updated.
    """

    id: int
    name: str
    email: str
    email_verified: bool = False
    image: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "User":
        """Create a User from a ``"user"`` table row.

        Args:
            row: Row tuple in column order:
                (id, name, email, emailVerified, image, createdAt, updatedAt).

        Returns:
            User instance.
        """
        return cls(
            id=row[0],
            name=row[1],
            email=row[2],
            email_verified=bool(row[3]),
            image=row[4],
            created_at=to_str(row[5]),
            updated_at=to_str(row[6]),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert User to a snake_case dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "email_verified": self.email_verified,
            "image": self.image,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
