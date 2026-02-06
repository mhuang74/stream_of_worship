"""Turso sync service for sow-admin.

Provides high-level sync operations and status checking for Turso
embedded replica synchronization.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from stream_of_worship.admin.db.client import DatabaseClient, SyncError


@dataclass
class SyncStatus:
    """Current sync status information.

    Attributes:
        enabled: Whether sync is enabled (Turso configured)
        database_path: Path to local database file
        turso_url: Turso database URL (masked)
        last_sync_at: ISO timestamp of last sync
        sync_version: Schema version for sync
        local_device_id: Unique device identifier
        libsql_available: Whether libsql library is installed
    """

    enabled: bool
    database_path: Path
    turso_url: str
    last_sync_at: Optional[str]
    sync_version: str
    local_device_id: str
    libsql_available: bool


@dataclass
class SyncResult:
    """Result of a sync operation.

    Attributes:
        success: Whether sync succeeded
        message: Human-readable result message
        records_synced: Number of records synced (if available)
        error: Error message if sync failed
    """

    success: bool
    message: str
    records_synced: Optional[int] = None
    error: Optional[str] = None


class SyncConfigError(Exception):
    """Error in sync configuration."""

    pass


class SyncNetworkError(Exception):
    """Error during sync network operation."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class SyncService:
    """Service for managing Turso database synchronization.

    Provides methods for checking sync status, validating configuration,
    and executing sync operations.
    """

    def __init__(
        self,
        db_path: Path,
        turso_url: Optional[str] = None,
        turso_token: Optional[str] = None,
    ):
        """Initialize the sync service.

        Args:
            db_path: Path to local database file
            turso_url: Turso database URL (optional)
            turso_token: Turso auth token (optional)
        """
        self.db_path = db_path
        self.turso_url = turso_url or ""
        self.turso_token = turso_token or ""

    def get_sync_status(self) -> SyncStatus:
        """Get current sync status.

        Returns:
            SyncStatus with current configuration and state
        """
        # Check if libsql is available
        try:
            import libsql

            libsql_available = True
        except ImportError:
            libsql_available = False

        # Get database stats if database exists
        last_sync_at = None
        sync_version = "1"
        local_device_id = ""

        if self.db_path.exists():
            try:
                client = DatabaseClient(
                    self.db_path,
                    turso_url=self.turso_url,
                    turso_token=self.turso_token,
                )
                stats = client.get_stats()
                last_sync_at = stats.last_sync_at
                sync_version = stats.sync_version
                local_device_id = stats.local_device_id
            except Exception:
                pass  # Database may not be initialized yet

        enabled = bool(
            self.turso_url
            and libsql_available
            and self.db_path.exists()
        )

        return SyncStatus(
            enabled=enabled,
            database_path=self.db_path,
            turso_url=self._mask_url(self.turso_url),
            last_sync_at=last_sync_at,
            sync_version=sync_version,
            local_device_id=local_device_id,
            libsql_available=libsql_available,
        )

    def validate_config(self) -> tuple[bool, list[str]]:
        """Validate sync configuration.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: list[str] = []

        # Check libsql availability
        try:
            import libsql  # noqa: F401
        except ImportError:
            errors.append("libsql not installed. Run: uv add --extra turso libsql")

        # Check database exists
        if not self.db_path.exists():
            errors.append(f"Database not found: {self.db_path}")

        # Check Turso URL
        if not self.turso_url:
            errors.append("Turso URL not configured")
        elif not self.turso_url.startswith("libsql://"):
            errors.append(f"Invalid Turso URL format: {self.turso_url}")

        # Check Turso token (from env or parameter)
        token = self.turso_token or os.environ.get("SOW_TURSO_TOKEN")
        if not token:
            errors.append("Turso token not configured (set SOW_TURSO_TOKEN)")

        return len(errors) == 0, errors

    def execute_sync(self) -> SyncResult:
        """Execute database sync with Turso.

        Returns:
            SyncResult with operation outcome

        Raises:
            SyncConfigError: If configuration is invalid
            SyncNetworkError: If network operation fails
        """
        # Validate configuration
        is_valid, errors = self.validate_config()
        if not is_valid:
            error_msg = "; ".join(errors)
            raise SyncConfigError(error_msg)

        # Execute sync
        client = DatabaseClient(
            self.db_path,
            turso_url=self.turso_url,
            turso_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN"),
        )

        try:
            client.sync()

            # Get updated stats
            stats = client.get_stats()

            return SyncResult(
                success=True,
                message="Sync completed successfully",
                records_synced=None,  # libsql doesn't expose this
            )
        except SyncError as e:
            raise SyncNetworkError(f"Sync failed: {e}")
        finally:
            client.close()

    def _ensure_device_id(self) -> str:
        """Ensure local device ID exists in database.

        Returns:
            The device ID (generates new one if needed)
        """
        if not self.db_path.exists():
            return ""

        client = DatabaseClient(
            self.db_path,
            turso_url=self.turso_url,
            turso_token=self.turso_token,
        )

        try:
            stats = client.get_stats()
            return stats.local_device_id
        finally:
            client.close()

    def _mask_url(self, url: str) -> str:
        """Mask sensitive parts of Turso URL for display.

        Args:
            url: Turso database URL

        Returns:
            Masked URL safe for display
        """
        if not url:
            return ""

        if url.startswith("libsql://"):
            # Show libsql://<database>.turso.io format
            parts = url.replace("libsql://", "").split("?")
            host = parts[0]
            return f"libsql://{host}"

        return url


def get_sync_service_from_config(config) -> SyncService:
    """Create SyncService from AdminConfig.

    Args:
        config: AdminConfig instance

    Returns:
        Configured SyncService
    """
    return SyncService(
        db_path=config.db_path,
        turso_url=config.turso_database_url,
        turso_token=os.environ.get("SOW_TURSO_TOKEN"),
    )
