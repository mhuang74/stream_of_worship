"""Tests for app configuration.

Tests AppConfig load/save and directory management.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from stream_of_worship.app.config import (
    AppConfig,
    ensure_app_config_exists,
    get_app_config_dir,
    get_app_config_path,
)


class TestAppConfigDir:
    """Tests for config directory functions."""

    def test_get_app_config_dir_returns_path(self):
        """Verify get_app_config_dir returns a Path."""
        config_dir = get_app_config_dir()

        assert isinstance(config_dir, Path)
        assert config_dir.name == "sow"

    def test_get_app_config_path_returns_toml(self):
        """Verify get_app_config_path returns path to config.toml."""
        config_path = get_app_config_path()

        assert isinstance(config_path, Path)
        assert config_path.name == "config.toml"


class TestAppConfigDefaults:
    """Tests for default configuration values."""

    def test_default_database_url(self):
        """Verify default database_url is empty string."""
        config = AppConfig()

        assert config.database_url == ""

    def test_default_songsets_export_dir(self):
        """Verify default songsets_export_dir is set."""
        config = AppConfig()

        assert isinstance(config.songsets_export_dir, Path)
        assert "Documents" in str(config.songsets_export_dir)

    def test_default_cache_dir(self):
        """Verify default cache_dir is set."""
        config = AppConfig()

        assert isinstance(config.cache_dir, Path)
        assert "cache" in str(config.cache_dir)

    def test_default_output_dir(self):
        """Verify default output_dir is set."""
        config = AppConfig()

        assert isinstance(config.output_dir, Path)
        assert "output" in str(config.output_dir)

    def test_default_log_dir(self):
        """Verify log_dir defaults to data_dir/logs (not under cache_dir)."""
        config = AppConfig()

        assert isinstance(config.log_dir, Path)
        assert str(config.log_dir).endswith("logs")
        assert "cache" not in str(config.log_dir)

    def test_default_gap_beats(self):
        """Verify default value."""
        config = AppConfig()

        assert config.default_gap_beats == 2.0

    def test_default_video_template(self):
        """Verify default template."""
        config = AppConfig()

        assert config.default_video_template == "dark"


class TestAppConfigProperties:
    """Tests for config properties."""

    def test_database_url_property(self):
        """Verify database_url access."""
        config = AppConfig()

        assert isinstance(config.database_url, str)

    def test_r2_bucket_property(self):
        """Verify r2_bucket property reads from environment."""
        config = AppConfig()

        # Should return default or env value
        assert isinstance(config.r2_bucket, str)

    def test_r2_endpoint_url_property(self):
        """Verify r2_endpoint_url property reads from environment."""
        config = AppConfig()

        # Should return env value or empty string
        assert isinstance(config.r2_endpoint_url, str)

    def test_r2_region_property(self):
        """Verify r2_region property reads from environment."""
        config = AppConfig()

        # Should return default or env value
        assert isinstance(config.r2_region, str)


class TestAppConfigSaveLoad:
    """Tests for config persistence."""

    def test_load_reads_existing_config(self, tmp_path, monkeypatch):
        """Verify load() reads TOML."""
        config_path = tmp_path / "config.toml"

        # Create a config file
        config_path.write_text("""
[songsets]
backup_retention = 10
export_dir = "/test/exports"

[database]
url = "postgresql://sow_app@ep-xxx-pooler.neon.tech/sow?sslmode=require"

[app]
cache_dir = "/test/cache"
output_dir = "/test/output"
preview_buffer_ms = 1000
preview_volume = 0.5
default_gap_beats = 4.0
default_video_template = "gradient_warm"
default_video_resolution = "720p"
""")

        config = AppConfig.load(config_path)

        assert config.songsets_backup_retention == 10
        assert config.songsets_export_dir == Path("/test/exports")
        assert config.database_url == "postgresql://sow_app@ep-xxx-pooler.neon.tech/sow?sslmode=require"
        assert config.cache_dir == Path("/test/cache")
        assert config.output_dir == Path("/test/output")
        assert config.preview_buffer_ms == 1000
        assert config.preview_volume == 0.5
        assert config.default_gap_beats == 4.0
        assert config.default_video_template == "gradient_warm"
        assert config.default_video_resolution == "720p"

    def test_load_raises_when_missing(self, tmp_path):
        """Verify load() raises when config missing."""
        config_path = tmp_path / "nonexistent.toml"

        with pytest.raises(FileNotFoundError):
            AppConfig.load(config_path)

    def test_save_creates_file(self, tmp_path):
        """Verify save() creates config file."""
        config_path = tmp_path / "config.toml"
        config = AppConfig()

        config.save(config_path)

        assert config_path.exists()
        content = config_path.read_text()
        assert "[songsets]" in content
        assert "[database]" in content

    def test_save_creates_parent_directories(self, tmp_path):
        """Verify save() creates parent directories."""
        config_path = tmp_path / "nested" / "dirs" / "config.toml"
        config = AppConfig()

        config.save(config_path)

        assert config_path.exists()

    def test_roundtrip_save_load(self, tmp_path):
        """Verify save then load preserves values."""
        config_path = tmp_path / "config.toml"

        original = AppConfig()
        original.default_gap_beats = 3.5
        original.default_video_template = "gradient_blue"

        original.save(config_path)
        loaded = AppConfig.load(config_path)

        assert loaded.default_gap_beats == 3.5
        assert loaded.default_video_template == "gradient_blue"


class TestEnsureDirectories:
    """Tests for directory creation."""

    def test_ensure_directories_creates_paths(self, tmp_path):
        """Verify directory creation."""
        cache_dir = tmp_path / "test_cache"
        output_dir = tmp_path / "test_output"
        log_dir = tmp_path / "test_logs"

        config = AppConfig()
        config.cache_dir = cache_dir
        config.output_dir = output_dir
        config.log_dir = log_dir
        config.songsets_export_dir = tmp_path / "exports"

        config.ensure_directories()

        assert cache_dir.exists()
        assert cache_dir.is_dir()
        assert output_dir.exists()
        assert output_dir.is_dir()
        assert log_dir.exists()
        assert log_dir.is_dir()


class TestEnsureAppConfigExists:
    """Tests for ensure_app_config_exists."""

    def test_creates_default_config_when_missing(self, tmp_path, monkeypatch):
        """Verify load() creates default if missing."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        config = ensure_app_config_exists()

        assert isinstance(config, AppConfig)
        assert get_app_config_path().exists()

    def test_loads_existing_config(self, tmp_path, monkeypatch):
        """Verify loads existing config when present."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        # Create existing config
        config_path = get_app_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("""
[songsets]
backup_retention = 3
export_dir = "/existing/exports"

[database]
url = "postgresql://sow@host/db"

[app]
cache_dir = "/existing/cache"
output_dir = "/existing/output"
preview_buffer_ms = 250
preview_volume = 0.9
default_gap_beats = 1.0
default_video_template = "dark"
default_video_resolution = "1080p"
""")

        config = ensure_app_config_exists()

        assert config.songsets_backup_retention == 3
        assert config.database_url == "postgresql://sow@host/db"
        assert config.default_gap_beats == 1.0


class TestGetConnectionUrl:
    """Tests for get_connection_url DSN assembly."""

    def test_returns_toml_url_by_default(self):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        assert config.get_connection_url() == "postgresql://user@host/db"

    def test_sow_database_url_overrides_toml(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.setenv("SOW_DATABASE_URL", "postgresql://other@otherhost/otherdb")
        assert config.get_connection_url() == "postgresql://other@otherhost/otherdb"

    def test_inserts_password_from_env_var(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.setenv("SOW_DATABASE_PASSWORD", "secret123")
        assert config.get_connection_url() == "postgresql://user:secret123@host/db"

    def test_no_password_no_insert(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.delenv("SOW_DATABASE_PASSWORD", raising=False)
        assert config.get_connection_url() == "postgresql://user@host/db"

    def test_preserves_query_params(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db?sslmode=require&connect_timeout=10"
        monkeypatch.setenv("SOW_DATABASE_PASSWORD", "pw")
        assert config.get_connection_url() == "postgresql://user:pw@host/db?sslmode=require&connect_timeout=10"
