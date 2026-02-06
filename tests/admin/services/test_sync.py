"""Tests for Turso sync service."""

import builtins
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.db.client import DatabaseClient, SyncError
from stream_of_worship.admin.db.models import DatabaseStats
from stream_of_worship.admin.services.sync import (
    SyncConfigError,
    SyncNetworkError,
    SyncResult,
    SyncService,
    SyncStatus,
    get_sync_service_from_config,
)


def mock_import_with_libsql(name, *args, **kwargs):
    """Mock __import__ that allows libsql to be imported."""
    if name == "libsql":
        return MagicMock()
    return builtins.__import__(name, *args, **kwargs)


def mock_import_without_libsql(name, *args, **kwargs):
    """Mock __import__ that raises ImportError for libsql."""
    if name == "libsql":
        raise ImportError("No module named 'libsql'")
    return builtins.__import__(name, *args, **kwargs)


@pytest.fixture
def temp_db_path(tmp_path):
    """Return a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def initialized_db(temp_db_path):
    """Return an initialized DatabaseClient."""
    client = DatabaseClient(temp_db_path)
    client.initialize_schema()
    return temp_db_path


class TestSyncStatus:
    """Tests for SyncStatus dataclass."""

    def test_default_initialization(self):
        """Test SyncStatus with default values."""
        status = SyncStatus(
            enabled=False,
            database_path=Path("/test/db.db"),
            turso_url="",
            last_sync_at=None,
            sync_version="1",
            local_device_id="",
            libsql_available=False,
        )

        assert status.enabled is False
        assert status.database_path == Path("/test/db.db")
        assert status.turso_url == ""
        assert status.last_sync_at is None
        assert status.sync_version == "1"
        assert status.local_device_id == ""
        assert status.libsql_available is False

    def test_full_initialization(self):
        """Test SyncStatus with all values set."""
        status = SyncStatus(
            enabled=True,
            database_path=Path("/test/db.db"),
            turso_url="libsql://test.turso.io",
            last_sync_at="2024-01-01T00:00:00",
            sync_version="2",
            local_device_id="abc123",
            libsql_available=True,
        )

        assert status.enabled is True
        assert status.turso_url == "libsql://test.turso.io"
        assert status.last_sync_at == "2024-01-01T00:00:00"
        assert status.sync_version == "2"
        assert status.local_device_id == "abc123"
        assert status.libsql_available is True


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_success_result(self):
        """Test SyncResult for successful sync."""
        result = SyncResult(
            success=True,
            message="Sync completed",
            records_synced=100,
        )

        assert result.success is True
        assert result.message == "Sync completed"
        assert result.records_synced == 100
        assert result.error is None

    def test_failure_result(self):
        """Test SyncResult for failed sync."""
        result = SyncResult(
            success=False,
            message="Sync failed",
            error="Network timeout",
        )

        assert result.success is False
        assert result.message == "Sync failed"
        assert result.error == "Network timeout"
        assert result.records_synced is None


class TestSyncConfigError:
    """Tests for SyncConfigError exception."""

    def test_exception_inheritance(self):
        """Test that SyncConfigError inherits from Exception."""
        error = SyncConfigError("test message")

        assert isinstance(error, Exception)
        assert str(error) == "test message"

    def test_message_propagation(self):
        """Test error message is preserved."""
        error = SyncConfigError("libsql not installed")

        assert "libsql not installed" in str(error)


class TestSyncNetworkError:
    """Tests for SyncNetworkError exception."""

    def test_with_status_code(self):
        """Test SyncNetworkError with status code."""
        error = SyncNetworkError("Connection failed", status_code=500)

        assert str(error) == "Connection failed"
        assert error.status_code == 500

    def test_without_status_code(self):
        """Test SyncNetworkError without status code."""
        error = SyncNetworkError("Network unreachable")

        assert str(error) == "Network unreachable"
        assert error.status_code is None


class TestSyncServiceGetSyncStatus:
    """Tests for SyncService.get_sync_status()."""

    def test_libsql_not_installed(self, temp_db_path):
        """Test status when libsql is not installed."""
        with patch.object(
            SyncService, "_mask_url", return_value=""
        ) as mock_mask:
            with patch.object(builtins, "__import__", mock_import_without_libsql):
                service = SyncService(temp_db_path)
                status = service.get_sync_status()

                assert status.libsql_available is False
                assert status.enabled is False

    def test_database_does_not_exist(self, temp_db_path):
        """Test status when database doesn't exist."""
        nonexistent_path = temp_db_path / "nonexistent"
        service = SyncService(nonexistent_path, turso_url="libsql://test.turso.io")

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            status = service.get_sync_status()

            # Should be disabled because DB doesn't exist
            assert status.enabled is False

    def test_turso_fully_configured(self, initialized_db):
        """Test status when Turso is fully configured."""
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            status = service.get_sync_status()

            assert status.libsql_available is True
            assert status.enabled is True
            assert status.turso_url == "libsql://test.turso.io"

    def test_metadata_retrieval_from_database(self, initialized_db):
        """Test that metadata is retrieved from database."""
        # Set up sync metadata in database
        client = DatabaseClient(initialized_db)
        client.update_sync_metadata("last_sync_at", "2024-06-01T12:00:00")
        client.update_sync_metadata("sync_version", "2")
        client.update_sync_metadata("local_device_id", "device123")
        client.close()

        service = SyncService(initialized_db, turso_url="libsql://test.turso.io")

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            status = service.get_sync_status()

            assert status.last_sync_at == "2024-06-01T12:00:00"
            assert status.sync_version == "2"
            assert status.local_device_id == "device123"


class TestSyncServiceValidateConfig:
    """Tests for SyncService.validate_config()."""

    def test_all_valid_configuration(self, initialized_db):
        """Test validation with all valid config."""
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is True
            assert len(errors) == 0

    def test_libsql_not_installed(self, initialized_db):
        """Test validation when libsql is not installed."""
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
        )

        with patch.object(builtins, "__import__", mock_import_without_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is False
            assert any("libsql not installed" in e for e in errors)

    def test_database_not_found(self, temp_db_path):
        """Test validation when database doesn't exist."""
        nonexistent = temp_db_path / "nonexistent.db"
        service = SyncService(nonexistent, turso_url="libsql://test.turso.io")

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is False
            assert any("Database not found" in e for e in errors)

    def test_missing_turso_url(self, initialized_db):
        """Test validation when Turso URL is missing."""
        service = SyncService(initialized_db, turso_url="")

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is False
            assert any("Turso URL not configured" in e for e in errors)

    def test_invalid_url_format(self, initialized_db):
        """Test validation with invalid URL format."""
        service = SyncService(initialized_db, turso_url="https://invalid.url")

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is False
            assert any("Invalid Turso URL format" in e for e in errors)

    def test_missing_token(self, initialized_db, monkeypatch):
        """Test validation when token is missing."""
        monkeypatch.delenv("SOW_TURSO_TOKEN", raising=False)
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            is_valid, errors = service.validate_config()

            assert is_valid is False
            assert any("Turso token not configured" in e for e in errors)


class TestSyncServiceExecuteSync:
    """Tests for SyncService.execute_sync()."""

    @patch.object(DatabaseClient, "sync")
    def test_successful_sync(self, mock_sync, initialized_db):
        """Test successful sync execution."""
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            result = service.execute_sync()

            assert result.success is True
            assert "completed successfully" in result.message
            mock_sync.assert_called_once()

    def test_config_validation_failure(self, temp_db_path):
        """Test sync with invalid config raises SyncConfigError."""
        service = SyncService(temp_db_path)  # No URL configured

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            with pytest.raises(SyncConfigError):
                service.execute_sync()

    @patch.object(DatabaseClient, "sync")
    def test_sync_operation_failure(self, mock_sync, initialized_db):
        """Test sync failure raises SyncNetworkError."""
        mock_sync.side_effect = SyncError("Sync failed")

        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            with pytest.raises(SyncNetworkError, match="Sync failed"):
                service.execute_sync()

    @patch.object(DatabaseClient, "sync")
    def test_connection_cleanup_in_finally(self, mock_sync, initialized_db):
        """Test that connection is closed after sync."""
        service = SyncService(
            initialized_db,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        with patch.object(builtins, "__import__", mock_import_with_libsql):
            with patch.object(DatabaseClient, "close") as mock_close:
                service.execute_sync()
                mock_close.assert_called_once()


class TestSyncServiceMaskUrl:
    """Tests for SyncService._mask_url()."""

    def test_libsql_url_masking(self, temp_db_path):
        """Test masking of libsql URL."""
        service = SyncService(temp_db_path)

        url = "libsql://my-db-org.turso.io?authToken=secret123"
        masked = service._mask_url(url)

        assert "secret123" not in masked
        assert masked == "libsql://my-db-org.turso.io"

    def test_empty_url(self, temp_db_path):
        """Test masking empty URL."""
        service = SyncService(temp_db_path)

        masked = service._mask_url("")

        assert masked == ""

    def test_non_libsql_url(self, temp_db_path):
        """Test masking non-libsql URL."""
        service = SyncService(temp_db_path)

        url = "https://example.com/database"
        masked = service._mask_url(url)

        assert masked == "https://example.com/database"


class TestGetSyncServiceFromConfig:
    """Tests for get_sync_service_from_config factory function."""

    def test_factory_creates_correct_instance(self, temp_db_path, monkeypatch):
        """Test that factory creates SyncService with correct config."""
        from stream_of_worship.admin.config import AdminConfig

        config = AdminConfig()
        config.db_path = temp_db_path
        config.turso_database_url = "libsql://test.turso.io"
        monkeypatch.setenv("SOW_TURSO_TOKEN", "test-token")

        service = get_sync_service_from_config(config)

        assert isinstance(service, SyncService)
        assert service.db_path == temp_db_path
        assert service.turso_url == "libsql://test.turso.io"
        assert service.turso_token == "test-token"


class TestSyncServiceEnsureDeviceId:
    """Tests for SyncService._ensure_device_id()."""

    def test_returns_existing_device_id(self, initialized_db):
        """Test returning existing device ID from database."""
        client = DatabaseClient(initialized_db)
        client.update_sync_metadata("local_device_id", "existing123")
        client.close()

        service = SyncService(initialized_db)
        device_id = service._ensure_device_id()

        assert device_id == "existing123"

    def test_returns_empty_when_no_database(self, temp_db_path):
        """Test empty string when database doesn't exist."""
        nonexistent = temp_db_path / "nonexistent.db"
        service = SyncService(nonexistent)

        device_id = service._ensure_device_id()

        assert device_id == ""
