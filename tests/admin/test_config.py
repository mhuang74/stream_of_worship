"""Tests for sow-admin configuration management."""

import os
import tempfile
from pathlib import Path

import pytest

from stream_of_worship.admin.config import (
    AdminConfig,
    ensure_config_exists,
    get_config_dir,
    get_config_path,
    get_default_db_path,
    get_env_var_name,
    get_secret,
)


class TestAdminConfig:
    """Tests for AdminConfig class."""

    def test_default_values(self):
        """Test that default config values are set correctly."""
        config = AdminConfig()

        assert config.analysis_url == "http://localhost:8000"
        assert config.r2_bucket == "sow-audio"
        assert config.r2_endpoint_url == ""
        assert config.r2_region == "auto"
        assert config.turso_database_url == ""

    def test_load_from_file(self, tmp_path):
        """Test loading config from TOML file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[service]
analysis_url = "https://analysis.example.com"

[r2]
bucket = "my-bucket"
endpoint_url = "https://xxx.r2.cloudflarestorage.com"
region = "us-east-1"

[turso]
database_url = "libsql://my-db.turso.io"

[database]
path = "/custom/path/sow.db"
"""
        )

        config = AdminConfig.load(config_file)

        assert config.analysis_url == "https://analysis.example.com"
        assert config.r2_bucket == "my-bucket"
        assert config.r2_endpoint_url == "https://xxx.r2.cloudflarestorage.com"
        assert config.r2_region == "us-east-1"
        assert config.turso_database_url == "libsql://my-db.turso.io"
        assert str(config.db_path) == "/custom/path/sow.db"

    def test_load_missing_file(self, tmp_path):
        """Test that loading missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            AdminConfig.load(tmp_path / "nonexistent.toml")

    def test_load_from_file_with_env_override(self, tmp_path, monkeypatch):
        """Test that environment variables override file config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[r2]
bucket = "file-bucket"
endpoint_url = "https://file.r2.cloudflarestorage.com"
region = "file-region"
"""
        )

        # Set environment variables
        monkeypatch.setenv("SOW_R2_BUCKET", "env-bucket")
        monkeypatch.setenv("SOW_R2_ENDPOINT_URL", "https://env.r2.cloudflarestorage.com")
        monkeypatch.setenv("SOW_R2_REGION", "env-region")

        config = AdminConfig.load(config_file)

        # Environment variables should take precedence
        assert config.r2_bucket == "env-bucket"
        assert config.r2_endpoint_url == "https://env.r2.cloudflarestorage.com"
        assert config.r2_region == "env-region"

    def test_load_env_vars_only(self, tmp_path, monkeypatch):
        """Test loading config when only env vars are set (no file values)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[service]
analysis_url = "http://localhost:8000"
""")

        # Set environment variables
        monkeypatch.setenv("SOW_R2_BUCKET", "env-only-bucket")
        monkeypatch.setenv("SOW_R2_ENDPOINT_URL", "https://env-only.r2.cloudflarestorage.com")

        config = AdminConfig.load(config_file)

        # Should use env vars for R2, defaults for others
        assert config.r2_bucket == "env-only-bucket"
        assert config.r2_endpoint_url == "https://env-only.r2.cloudflarestorage.com"
        assert config.r2_region == "auto"  # default

    def test_save_and_load(self, tmp_path):
        """Test saving and loading config preserves values."""
        config = AdminConfig()
        config.analysis_url = "https://test.example.com"
        config.r2_bucket = "test-bucket"

        config_file = tmp_path / "config.toml"
        config.save(config_file)

        loaded = AdminConfig.load(config_file)

        assert loaded.analysis_url == "https://test.example.com"
        assert loaded.r2_bucket == "test-bucket"

    def test_get_config_value(self):
        """Test getting config values with dot notation."""
        config = AdminConfig()
        config.r2_bucket = "my-bucket"

        assert config.get("r2_bucket") == "my-bucket"
        assert config.get("r2.bucket") is None  # Nested access not supported this way
        assert config.get("nonexistent") is None
        assert config.get("nonexistent", "default") == "default"

    def test_set_config_value(self):
        """Test setting config values with dot notation."""
        config = AdminConfig()

        config.set("analysis_url", "https://new.example.com")
        assert config.analysis_url == "https://new.example.com"

        config.set("r2_bucket", "new-bucket")
        assert config.r2_bucket == "new-bucket"

    def test_set_invalid_key(self):
        """Test that setting invalid key raises ValueError."""
        config = AdminConfig()

        with pytest.raises(ValueError):
            config.set("nonexistent.key", "value")

    def test_set_preserves_bool_type(self):
        """Test that setting bool values preserves type."""
        # Note: AdminConfig doesn't have bool fields currently
        # This is a placeholder for future bool fields
        pass


class TestConfigPaths:
    """Tests for config path functions."""

    def test_get_config_dir_returns_path(self):
        """Test that get_config_dir returns a Path."""
        config_dir = get_config_dir()
        assert isinstance(config_dir, Path)
        assert "sow-admin" in str(config_dir).lower()

    def test_get_config_path_returns_toml(self):
        """Test that get_config_path returns path to config.toml."""
        config_path = get_config_path()
        assert isinstance(config_path, Path)
        assert config_path.name == "config.toml"

    def test_get_default_db_path(self):
        """Test that get_default_db_path returns correct path."""
        db_path = get_default_db_path()
        assert isinstance(db_path, Path)
        assert db_path.name == "sow.db"


class TestEnsureConfigExists:
    """Tests for ensure_config_exists function."""

    def test_creates_default_config(self, tmp_path, monkeypatch):
        """Test that missing config is created with defaults."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        config = ensure_config_exists()

        assert isinstance(config, AdminConfig)
        assert get_config_path().exists()

    def test_loads_existing_config(self, tmp_path, monkeypatch):
        """Test that existing config is loaded."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        # Create custom config
        config_dir = tmp_path / "sow-admin"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text('[service]\nanalysis_url = "https://custom.example.com"\n')

        config = ensure_config_exists()

        assert config.analysis_url == "https://custom.example.com"


class TestEnvironmentVariables:
    """Tests for environment variable functions."""

    def test_get_env_var_name(self):
        """Test that env var names are formatted correctly."""
        assert get_env_var_name("r2.access_key_id") == "SOW_R2_ACCESS_KEY_ID"
        assert get_env_var_name("analysis_url") == "SOW_ANALYSIS_URL"
        assert get_env_var_name("turso.auth_token") == "SOW_TURSO_AUTH_TOKEN"

    def test_get_secret_from_env(self, monkeypatch):
        """Test getting secrets from environment."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")

        assert get_secret("r2.access_key_id") == "test-key"

    def test_get_secret_not_set(self):
        """Test getting non-existent secret returns None."""
        assert get_secret("nonexistent.var") is None
