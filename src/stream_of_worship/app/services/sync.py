"""App sync service for Turso embedded replica synchronization.

Provides high-level sync operations for the user app, including
pre-sync snapshots and local sync metadata tracking.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from stream_of_worship.app.db.read_client import ReadOnlyClient, SyncError
from stream_of_worship.app.db.songset_client import SongsetClient


@dataclass
class SyncStatus:
    """Current sync status information.

    Attributes:
        enabled: Whether sync is enabled (Turso configured)
        database_path: Path to local database file
        turso_url: Turso database URL (masked)
        last_sync_at: ISO timestamp of last sync
        sync_version: Schema version for sync
        libsql_available: Whether libsql library is installed
    """

    enabled: bool
    database_path: Path
    turso_url: str
    last_sync_at: Optional[str]
    sync_version: str
    libsql_available: bool


@dataclass
class SyncResult:
    """Result of a sync operation.

    Attributes:
        success: Whether sync succeeded
        message: Human-readable result message
        backup_path: Path to pre-sync backup (if created)
        error: Error message if sync failed
    """

    success: bool
    message: str
    backup_path: Optional[Path] = None
    error: Optional[str] = None


class TursoNotConfiguredError(Exception):
    """Error when Turso is not configured."""

    pass


class SyncAuthError(Exception):
    """Error during sync authentication."""

    pass


class SyncNetworkError(Exception):
    """Error during sync network operation."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class AppSyncService:
    """Service for managing Turso database synchronization in the user app.

    Provides methods for checking sync status, validating configuration,
    and executing sync operations with pre-sync snapshots.
    """

    def __init__(
        self,
        read_client: ReadOnlyClient,
        songset_client: SongsetClient,
        config_dir: Path,
        turso_url: Optional[str] = None,
        turso_token: Optional[str] = None,
        backup_retention: int = 5,
    ):
        """Initialize the sync service.

        Args:
            read_client: ReadOnlyClient for catalog database
            songset_client: SongsetClient for songsets database
            config_dir: Directory for storing sync metadata
            turso_url: Turso database URL (optional)
            turso_token: Turso auth token (optional)
            backup_retention: Number of backups to keep
        """
        self.read_client = read_client
        self.songset_client = songset_client
        self.config_dir = config_dir
        self.turso_url = turso_url or ""
        self.turso_token = turso_token or ""
        self.backup_retention = backup_retention
        self._last_sync_file = config_dir / "last_sync.json"

        # Check libsql availability
        try:
            import libsql  # noqa: F401

            self.libsql_available = True
        except ImportError:
            self.libsql_available = False

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
            parts = url.replace("libsql://", "").split("?")
            host = parts[0]
            return f"libsql://{host}"

        return url

    def get_sync_status(self) -> SyncStatus:
        """Get current sync status.

        Returns:
            SyncStatus with current configuration and state
        """
        last_sync_at = None
        sync_version = "2"

        # Read local last sync timestamp
        if self._last_sync_file.exists():
            try:
                with open(self._last_sync_file) as f:
                    data = json.load(f)
                    last_sync_at = data.get("last_sync_at")
                    sync_version = data.get("sync_version", "2")
            except Exception:
                pass

        enabled = bool(self.turso_url and self.libsql_available)

        return SyncStatus(
            enabled=enabled,
            database_path=self.read_client.db_path,
            turso_url=self._mask_url(self.turso_url),
            last_sync_at=last_sync_at,
            sync_version=sync_version,
            libsql_available=self.libsql_available,
        )

    def validate_config(self) -> tuple[bool, list[str]]:
        """Validate sync configuration.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: list[str] = []

        # Check libsql availability
        if not self.libsql_available:
            errors.append("libsql not installed")

        # Check Turso URL
        if not self.turso_url:
            errors.append("Turso URL not configured")
        elif not self.turso_url.startswith("libsql://"):
            errors.append(f"Invalid Turso URL format: {self.turso_url}")

        # Check Turso token
        token = self.turso_token or os.environ.get("SOW_TURSO_READONLY_TOKEN")
        if not token:
            errors.append("Turso token not configured (set SOW_TURSO_READONLY_TOKEN)")

        return len(errors) == 0, errors

    def _update_last_sync(self) -> None:
        """Update local last sync timestamp."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_sync_at": datetime.now().isoformat(),
            "sync_version": "2",
        }
        with open(self._last_sync_file, "w") as f:
            json.dump(data, f)

    def execute_sync(self) -> SyncResult:
        """Execute database sync with Turso.

        Steps:
        1. Create pre-sync snapshot of songsets.db
        2. Sync catalog database via libsql
        3. Update local last sync timestamp

        Returns:
            SyncResult with operation outcome

        Raises:
            TursoNotConfiguredError: If configuration is invalid
            SyncNetworkError: If network operation fails
            SyncAuthError: If authentication fails
        """
        # Validate configuration
        is_valid, errors = self.validate_config()
        if not is_valid:
            error_msg = "; ".join(errors)
            raise TursoNotConfiguredError(error_msg)

        # Step 1: Create pre-sync snapshot
        backup_path = None
        try:
            backup_path = self.songset_client.snapshot_db(retention=self.backup_retention)
        except FileNotFoundError:
            # songsets.db doesn't exist yet, that's OK
            pass

        # Step 2: Execute sync
        try:
            self.read_client.sync()

            # Step 3: Update local last sync timestamp (NOT in the replica - RO token)
            self._update_last_sync()

            return SyncResult(
                success=True,
                message="Sync completed successfully",
                backup_path=backup_path,
            )

        except SyncError as e:
            raise SyncNetworkError(f"Sync failed: {e}")
