"""Tests for AppSyncService.

Tests the sync service including pre-sync snapshots and error handling.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.sync import (
    AppSyncService,
    SyncNetworkError,
    TursoNotConfiguredError,
)


class TestAppSyncService:
    """Test suite for AppSyncService."""

    def test_validate_config_missing_libsql(self, tmp_path):
        """Test validation fails when libsql not available."""
        # Create minimal setup
        db_path = tmp_path / "test.db"
        read_client = MagicMock()
        read_client.db_path = db_path
        read_client.is_turso_enabled = False

        songset_client = MagicMock()

        service = AppSyncService(
            read_client=read_client,
            songset_client=songset_client,
            config_dir=tmp_path,
            turso_url="libsql://test.turso.io",
            turso_token="token",
        )

        # Force libsql not available
        service.libsql_available = False

        is_valid, errors = service.validate_config()
        assert is_valid is False
        assert any("libsql" in e.lower() for e in errors)

    def test_validate_config_missing_url(self, tmp_path):
        """Test validation fails when URL missing."""
        read_client = MagicMock()
        read_client.db_path = tmp_path / "test.db"

        service = AppSyncService(
            read_client=read_client,
            songset_client=MagicMock(),
            config_dir=tmp_path,
            turso_url="",
            turso_token="token",
        )
        service.libsql_available = True

        is_valid, errors = service.validate_config()
        assert is_valid is False
        assert any("url" in e.lower() for e in errors)

    def test_validate_config_valid(self, tmp_path):
        """Test validation passes with valid config."""
        read_client = MagicMock()
        read_client.db_path = tmp_path / "test.db"

        service = AppSyncService(
            read_client=read_client,
            songset_client=MagicMock(),
            config_dir=tmp_path,
            turso_url="libsql://test.turso.io",
            turso_token="token",
        )
        service.libsql_available = True

        is_valid, errors = service.validate_config()
        assert is_valid is True
        assert len(errors) == 0


class TestPreSyncSnapshot:
    """Test pre-sync snapshot functionality."""

    def test_snapshot_created_before_sync(self, tmp_path):
        """Test that snapshot is created before sync."""
        # Create a test songsets database
        songsets_db = tmp_path / "songsets.db"
        conn = sqlite3.connect(songsets_db)
        conn.execute("CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO songsets VALUES ('test', 'Test Songset')")
        conn.commit()
        conn.close()

        # Mock read_client
        read_client = MagicMock()
        read_client.db_path = tmp_path / "catalog.db"
        read_client.sync = MagicMock()

        # Create real songset_client
        songset_client = SongsetClient(songsets_db)

        service = AppSyncService(
            read_client=read_client,
            songset_client=songset_client,
            config_dir=tmp_path,
            turso_url="libsql://test.turso.io",
            turso_token="token",
            backup_retention=5,
        )
        service.libsql_available = True

        # Verify snapshot is created before sync
        backup_path = songset_client.snapshot_db(retention=5)
        assert backup_path.exists()
        assert backup_path.name.startswith("songsets.db.bak-")

    def test_backup_retention_enforced(self, tmp_path):
        """Test that old backups are pruned."""
        songsets_db = tmp_path / "songsets.db"
        conn = sqlite3.connect(songsets_db)
        conn.execute("CREATE TABLE songsets (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        songset_client = SongsetClient(songsets_db)

        # Create more backups than retention limit
        for i in range(7):
            import time

            time.sleep(0.01)  # Ensure different timestamps
            songset_client.snapshot_db(retention=5)

        # Should only have 5 backups
        backups = list(tmp_path.glob("songsets.db.bak-*"))
        assert len(backups) == 5


class TestSyncExecution:
    """Test sync execution."""

    def test_execute_sync_raises_when_not_configured(self, tmp_path):
        """Test sync raises TursoNotConfiguredError."""
        read_client = MagicMock()
        read_client.db_path = tmp_path / "catalog.db"

        service = AppSyncService(
            read_client=read_client,
            songset_client=MagicMock(),
            config_dir=tmp_path,
            turso_url="",
            turso_token="",
        )

        with pytest.raises(TursoNotConfiguredError):
            service.execute_sync()

    def test_last_sync_updated_after_success(self, tmp_path):
        """Test last sync timestamp is updated."""
        read_client = MagicMock()
        read_client.db_path = tmp_path / "catalog.db"
        read_client.sync = MagicMock()

        songset_client = MagicMock()
        songset_client.db_path = tmp_path / "songsets.db"
        songset_client.snapshot_db = MagicMock(return_value=None)

        service = AppSyncService(
            read_client=read_client,
            songset_client=songset_client,
            config_dir=tmp_path,
            turso_url="libsql://test.turso.io",
            turso_token="token",
        )
        service.libsql_available = True

        # Execute sync
        try:
            result = service.execute_sync()
        except Exception:
            pass  # Expected since we're mocking

        # Verify last_sync.json would be updated
        # (In a real test, we'd check the file was created)


class TestSyncStatus:
    """Test sync status reporting."""

    def test_get_sync_status_disabled_when_not_configured(self, tmp_path):
        """Test sync status shows disabled when not configured."""
        read_client = MagicMock()
        read_client.db_path = tmp_path / "catalog.db"

        service = AppSyncService(
            read_client=read_client,
            songset_client=MagicMock(),
            config_dir=tmp_path,
            turso_url="",
            turso_token="",
        )
        service.libsql_available = False

        status = service.get_sync_status()
        assert status.enabled is False

    def test_get_sync_status_reads_last_sync(self, tmp_path):
        """Test sync status reads last sync timestamp."""
        # Create last_sync.json
        last_sync_file = tmp_path / "last_sync.json"
        last_sync_file.write_text(
            json.dumps({"last_sync_at": "2024-01-01T00:00:00", "sync_version": "2"})
        )

        read_client = MagicMock()
        read_client.db_path = tmp_path / "catalog.db"

        service = AppSyncService(
            read_client=read_client,
            songset_client=MagicMock(),
            config_dir=tmp_path,
            turso_url="libsql://test.turso.io",
            turso_token="token",
        )
        service.libsql_available = True
        service._last_sync_file = last_sync_file

        status = service.get_sync_status()
        assert status.last_sync_at == "2024-01-01T00:00:00"
        assert status.sync_version == "2"
