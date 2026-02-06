"""Tests for database commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands.db import app, get_db_client
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.sync import (
    SyncConfigError,
    SyncNetworkError,
    SyncResult,
    SyncStatus,
)

runner = CliRunner()


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


class TestGetDbClient:
    """Tests for get_db_client function."""

    def test_creates_client_with_turso_config_from_env(self, temp_db_path, monkeypatch):
        """Test that get_db_client passes Turso config from environment."""
        monkeypatch.setenv("SOW_TURSO_TOKEN", "test-token-from-env")

        config = AdminConfig()
        config.db_path = temp_db_path
        config.turso_database_url = "libsql://test.turso.io"

        client = get_db_client(config)

        assert client.db_path == temp_db_path
        assert client.turso_url == "libsql://test.turso.io"
        assert client.turso_token == "test-token-from-env"

    def test_creates_client_without_turso_config(self, temp_db_path):
        """Test that get_db_client works without Turso config."""
        config = AdminConfig()
        config.db_path = temp_db_path
        config.turso_database_url = ""

        client = get_db_client(config)

        assert client.db_path == temp_db_path
        assert client.turso_url == ""
        assert client.turso_token is None


class TestShowStatusCommand:
    """Tests for db status command."""

    def test_status_when_config_not_found(self, tmp_path):
        """Test status when config file doesn't exist."""
        nonexistent_config = tmp_path / "nonexistent.toml"

        result = runner.invoke(app, ["status", "--config", str(nonexistent_config)])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_status_when_database_doesnt_exist(self, tmp_path):
        """Test status when database doesn't exist."""
        config_path = tmp_path / "config.toml"
        db_path = tmp_path / "nonexistent.db"

        config_content = f'[database]\npath = "{db_path}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Database Path" in result.output
        assert "Exists" in result.output
        assert "No" in result.output
        assert "Database does not exist" in result.output

    def test_status_display_with_sync_disabled(self, initialized_db, tmp_path):
        """Test status display when sync is disabled."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Database Information" in result.output
        assert "Database Statistics" in result.output
        assert "Sync Configuration" in result.output
        assert "Disabled" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_status_display_with_sync_enabled(self, mock_get_service, initialized_db, tmp_path):
        """Test status display when sync is enabled."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at="2024-06-01T12:00:00",
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Sync Configuration" in result.output
        assert "Enabled" in result.output


class TestSyncCommand:
    """Tests for db sync command."""

    def test_sync_when_config_not_found(self, tmp_path):
        """Test sync when config file doesn't exist."""
        nonexistent_config = tmp_path / "nonexistent.toml"

        result = runner.invoke(app, ["sync", "--config", str(nonexistent_config)])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_sync_when_turso_not_configured(self, initialized_db, tmp_path):
        """Test sync when Turso URL is not configured."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Turso database URL not configured" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_when_libsql_not_installed(self, mock_get_service, initialized_db, tmp_path):
        """Test sync when libsql is not installed."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status with libsql not available
        mock_status = SyncStatus(
            enabled=False,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="",
            libsql_available=False,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "libsql is not installed" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_with_validation_errors_no_force(self, mock_get_service, initialized_db, tmp_path):
        """Test sync with validation errors and no force flag."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status with libsql available but config invalid
        mock_status = SyncStatus(
            enabled=False,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (False, ["Turso token not configured"])
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Sync configuration errors" in result.output
        assert "Turso token not configured" in result.output
        assert "--force" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_with_validation_errors_with_force(self, mock_get_service, initialized_db, tmp_path):
        """Test sync with validation errors but with force flag."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (False, ["Some warning"])
        mock_service.execute_sync.return_value = SyncResult(
            success=True,
            message="Sync completed successfully",
        )
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path), "--force"])

        assert result.exit_code == 0
        assert "Syncing with Turso" in result.output
        assert "Sync completed successfully" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_successful_sync(self, mock_get_service, initialized_db, tmp_path):
        """Test successful sync operation."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (True, [])
        mock_service.execute_sync.return_value = SyncResult(
            success=True,
            message="Sync completed successfully",
        )
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Syncing with Turso" in result.output
        assert "Sync completed successfully" in result.output
        mock_service.execute_sync.assert_called_once()

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_config_error_handling(self, mock_get_service, initialized_db, tmp_path):
        """Test SyncConfigError handling during sync."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (True, [])
        mock_service.execute_sync.side_effect = SyncConfigError("Invalid configuration")
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Configuration error" in result.output
        assert "Invalid configuration" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_network_error_handling(self, mock_get_service, initialized_db, tmp_path):
        """Test SyncNetworkError handling during sync."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (True, [])
        mock_service.execute_sync.side_effect = SyncNetworkError("Connection failed", status_code=500)
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Network error" in result.output
        assert "Connection failed" in result.output
        assert "Status code: 500" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_generic_exception_handling(self, mock_get_service, initialized_db, tmp_path):
        """Test generic exception handling during sync."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status
        mock_status = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.return_value = mock_status
        mock_service.validate_config.return_value = (True, [])
        mock_service.execute_sync.side_effect = Exception("Unexpected error")
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Unexpected error" in result.output

    @patch("stream_of_worship.admin.commands.db.get_sync_service_from_config")
    def test_sync_status_display_before_after(self, mock_get_service, initialized_db, tmp_path):
        """Test that sync shows status before and after."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n\n[turso]\ndatabase_url = "libsql://test.turso.io"\n'
        config_path.write_text(config_content)

        # Mock sync status - before with no last_sync, after with last_sync
        mock_status_before = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at=None,
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_status_after = SyncStatus(
            enabled=True,
            database_path=initialized_db,
            turso_url="libsql://test.turso.io",
            last_sync_at="2024-06-01T12:00:00",
            sync_version="1",
            local_device_id="abc123",
            libsql_available=True,
        )
        mock_service = MagicMock()
        mock_service.get_sync_status.side_effect = [mock_status_before, mock_status_after]
        mock_service.validate_config.return_value = (True, [])
        mock_service.execute_sync.return_value = SyncResult(
            success=True,
            message="Sync completed successfully",
        )
        mock_get_service.return_value = mock_service

        result = runner.invoke(app, ["sync", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Never" in result.output
        assert "2024-06-01T12:00:00" in result.output


class TestInitCommand:
    """Tests for db init command."""

    def test_init_creates_database(self, tmp_path):
        """Test that init creates a new database."""
        config_path = tmp_path / "config.toml"
        db_path = tmp_path / "sow.db"

        config_content = f'[database]\npath = "{db_path}"\n'
        config_path.write_text(config_content)

        assert not db_path.exists()

        result = runner.invoke(app, ["init", "--config", str(config_path)])

        assert result.exit_code == 0
        assert db_path.exists()
        assert "initialized successfully" in result.output

    def test_init_with_existing_database_shows_warning(self, initialized_db, tmp_path):
        """Test that init with existing database shows warning."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["init", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Database already exists" in result.output

    def test_init_force_resets_database(self, initialized_db, tmp_path):
        """Test that init --force resets existing database."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        # Add some data
        client = DatabaseClient(initialized_db)
        from datetime import datetime
        from stream_of_worship.admin.db.models import Song
        song = Song(
            id="test_song",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)
        client.close()

        result = runner.invoke(app, ["init", "--config", str(config_path), "--force"])

        assert result.exit_code == 0
        assert "reset" in result.output.lower() or "successfully" in result.output


class TestResetCommand:
    """Tests for db reset command."""

    def test_reset_without_confirm_shows_warning(self, initialized_db, tmp_path):
        """Test that reset without --confirm shows warning."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["reset", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "WARNING" in result.output
        assert "--confirm" in result.output

    def test_reset_with_confirm_deletes_data(self, initialized_db, tmp_path):
        """Test that reset --confirm deletes all data."""
        config_path = tmp_path / "config.toml"
        config_content = f'[database]\npath = "{initialized_db}"\n'
        config_path.write_text(config_content)

        # Add some data
        client = DatabaseClient(initialized_db)
        from datetime import datetime
        from stream_of_worship.admin.db.models import Song
        song = Song(
            id="test_song",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)
        client.close()

        result = runner.invoke(app, ["reset", "--config", str(config_path), "--confirm"])

        assert result.exit_code == 0
        assert "reset successfully" in result.output.lower()

        # Verify data is gone
        client = DatabaseClient(initialized_db)
        assert client.get_song("test_song") is None
        client.close()

    def test_reset_when_database_doesnt_exist(self, tmp_path):
        """Test reset when database doesn't exist."""
        config_path = tmp_path / "config.toml"
        db_path = tmp_path / "nonexistent.db"

        config_content = f'[database]\npath = "{db_path}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["reset", "--config", str(config_path), "--confirm"])

        assert result.exit_code == 0
        assert db_path.exists()  # Should create the database


class TestPathCommand:
    """Tests for db path command."""

    def test_path_shows_database_path(self, tmp_path):
        """Test that path command shows database path."""
        config_path = tmp_path / "config.toml"
        db_path = tmp_path / "sow.db"

        config_content = f'[database]\npath = "{db_path}"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["path", "--config", str(config_path)])

        assert result.exit_code == 0
        # Output may have newlines due to Rich console wrapping, check db name is present
        assert "sow.db" in result.output

    def test_path_without_config_shows_default_path(self, tmp_path, monkeypatch):
        """Test that path command shows default path when config doesn't exist."""
        from stream_of_worship.admin import config as config_module
        monkeypatch.setattr(config_module, "get_config_dir", lambda: tmp_path)

        result = runner.invoke(app, ["path"])

        assert result.exit_code == 0
        assert "sow.db" in result.output
