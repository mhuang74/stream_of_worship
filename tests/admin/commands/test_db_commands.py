"""Tests for admin database CLI commands (PostgreSQL).

Fast unit tests with mocks (no Docker required).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands.db import app, get_db_client
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.models import DatabaseStats

runner = CliRunner()


class TestGetDbClient:
    """Tests for get_db_client function."""

    def test_creates_client_with_connection_provider(self, monkeypatch):
        """Test that get_db_client creates a ConnectionProvider-based client."""
        monkeypatch.setenv("SOW_DATABASE_PASSWORD", "secret")

        config = AdminConfig()
        config.database_url = "postgresql://admin@example.com/sow?sslmode=require"

        client = get_db_client(config)

        assert client is not None
        assert hasattr(client, "connection_provider")

    def test_creates_client_without_password(self):
        """Test that get_db_client works without password env var."""
        config = AdminConfig()
        config.database_url = "postgresql://admin@example.com/sow?sslmode=require"

        client = get_db_client(config)

        assert client is not None
        assert hasattr(client, "connection_provider")


class TestShowStatusCommand:
    """Tests for db status command."""

    def test_status_when_config_not_found(self, tmp_path):
        """Test status when config file doesn't exist."""
        nonexistent_config = tmp_path / "nonexistent.toml"

        result = runner.invoke(app, ["status", "--config", str(nonexistent_config)])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_status_when_database_url_not_configured(self, tmp_path):
        """Test status when database URL is not configured."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = ""\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Database Connection" in result.output
        assert "Not configured" in result.output

    @patch("stream_of_worship.admin.commands.db.check_database_connection")
    @patch("stream_of_worship.admin.commands.db.get_db_client")
    def test_status_display_with_connection_ok(self, mock_get_client, mock_check, tmp_path):
        """Test status display when connection is healthy."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        mock_check.return_value = True
        mock_client = MagicMock()
        mock_client.get_stats.return_value = DatabaseStats(
            table_counts={"songs": 42, "recordings": 10},
            is_healthy=True,
            sync_version="3",
        )
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Database Connection" in result.output
        assert "Connection" in result.output
        assert "OK" in result.output
        assert "42" in result.output
        assert "10" in result.output

    @patch("stream_of_worship.admin.commands.db.check_database_connection")
    def test_status_display_with_connection_failed(self, mock_check, tmp_path):
        """Test status display when connection fails."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        mock_check.return_value = False

        result = runner.invoke(app, ["status", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Database Connection" in result.output
        assert "FAILED" in result.output
        assert "Could not connect" in result.output


class TestInitCommand:
    """Tests for db init command."""

    @patch("stream_of_worship.admin.commands.db.get_db_client")
    def test_init_creates_schema(self, mock_get_client, tmp_path):
        """Test that init connects and creates schema."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        mock_client = MagicMock()
        mock_client.get_stats.return_value = DatabaseStats(
            table_counts={"songs": 0, "recordings": 0},
            is_healthy=True,
        )
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["init", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "Postgres schema initialized successfully" in result.output
        mock_client.initialize_schema.assert_called_once()

    @patch("stream_of_worship.admin.commands.db.AdminConfig.load")
    def test_init_when_config_not_found_creates_default(self, mock_load, tmp_path, monkeypatch):
        """Test that init creates default config when none exists."""
        from stream_of_worship.admin import commands
        monkeypatch.setattr(commands.db, "get_config_path", lambda: tmp_path / "config.toml")
        mock_load.side_effect = FileNotFoundError("not found")

        result = runner.invoke(app, ["init"])

        # Since no DB URL is in default config, it should error after creating config
        assert result.exit_code == 1
        assert "Created default config" in result.output

    def test_init_when_database_url_not_configured(self, tmp_path):
        """Test init when database URL is missing."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = ""\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["init", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Database URL not configured" in result.output


class TestUrlCommand:
    """Tests for db url command."""

    def test_url_shows_masked_url(self, tmp_path):
        """Test that url command masks password."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin:secret@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["url", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "****" in result.output
        assert "secret" not in result.output
        assert "postgresql" in result.output

    def test_url_shows_password_status_set(self, tmp_path, monkeypatch):
        """Test url command shows password status when env var is set."""
        monkeypatch.setenv("SOW_DATABASE_PASSWORD", "mypassword")
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["url", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "loaded from SOW_DATABASE_PASSWORD env var" in result.output

    def test_url_shows_password_status_not_set(self, tmp_path):
        """Test url command shows password status when env var is not set."""
        config_path = tmp_path / "config.toml"
        config_content = '[database]\nurl = "postgresql://admin@example.com/sow?sslmode=require"\n'
        config_path.write_text(config_content)

        result = runner.invoke(app, ["url", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "NOT SET" in result.output

    def test_url_without_config_shows_default(self, tmp_path, monkeypatch):
        """Test that url command shows default path when config doesn't exist."""
        from stream_of_worship.admin import config as config_module
        monkeypatch.setattr(config_module, "get_config_path", lambda: tmp_path / "config.toml")

        result = runner.invoke(app, ["url"])

        assert result.exit_code == 0
        assert "Database URL (masked):" in result.output
        assert "Password:" in result.output
