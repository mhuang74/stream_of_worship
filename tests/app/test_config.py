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
        assert config_dir.name == "stream-of-worship"

    def test_get_app_config_path_returns_toml(self):
        """Verify get_app_config_path returns path to config.toml."""
        config_path = get_app_config_path()

        assert isinstance(config_path, Path)
        assert config_path.name == "config.toml"


class TestAppConfigDefaults:
    """Tests for default configuration values."""

    def test_default_database_url(self):
        """Verify default database_url is empty."""
        config = AppConfig()

        assert config.database_url == ""

    def test_default_cache_dir(self):
        """Verify default cache_dir is set."""
        config = AppConfig()

        assert isinstance(config.cache_dir, Path)
        assert "stream-of-worship" in str(config.cache_dir)

    def test_default_output_dir(self):
        """Verify default output_dir is derived from working_dir."""
        config = AppConfig()

        assert isinstance(config.output_dir, Path)
        assert "output" in str(config.output_dir)
        assert config.output_dir == config.working_dir / "output"

    def test_default_log_dir(self):
        """Verify log_dir is derived from working_dir."""
        config = AppConfig()

        assert isinstance(config.log_dir, Path)
        assert str(config.log_dir).endswith("logs")
        assert config.log_dir == config.working_dir / "logs"

    def test_default_working_dir(self):
        """Verify default working_dir is set."""
        config = AppConfig()

        assert isinstance(config.working_dir, Path)
        assert config.working_dir.name == "stream-of-worship"

    def test_default_songsets_backup_dir(self):
        """Verify songsets_backup_dir is derived from working_dir."""
        config = AppConfig()

        assert isinstance(config.songsets_backup_dir, Path)
        assert config.songsets_backup_dir == config.working_dir / "backup"

    def test_default_gap_beats(self):
        """Verify default value."""
        config = AppConfig()

        assert config.default_gap_beats == 2.0

    def test_default_video_template(self):
        """Verify default template."""
        config = AppConfig()

        assert config.default_video_template == "dark"


class TestConnectionUrl:
    """Tests for get_connection_url()."""

    def test_returns_url_when_no_password(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.delenv("SOW_DATABASE_PASSWORD", raising=False)
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        assert config.get_connection_url() == "postgresql://user@host/db"

    def test_injects_password_from_env(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.setenv("SOW_DATABASE_PASSWORD", "secret")
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        assert config.get_connection_url() == "postgresql://user:secret@host/db"

    def test_sow_database_url_env_overrides_toml(self, monkeypatch):
        config = AppConfig()
        config.database_url = "postgresql://user@host/db"
        monkeypatch.setenv("SOW_DATABASE_URL", "postgresql://other@host/other")
        monkeypatch.delenv("SOW_DATABASE_PASSWORD", raising=False)
        assert config.get_connection_url() == "postgresql://other@host/other"

    def test_raises_when_empty(self, monkeypatch):
        config = AppConfig()
        config.database_url = ""
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        with pytest.raises(ValueError, match="database_url is not configured"):
            config.get_connection_url()

    def test_ignores_old_turso_section(self, tmp_path, monkeypatch):
        """Old [turso] section is silently ignored."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[turso]
database_url = "libsql://old.turso.io"
sync_on_startup = true

[database]
url = "postgresql://user@host/db"
""")
        monkeypatch.delenv("SOW_DATABASE_URL", raising=False)
        config = AppConfig.load(config_file)
        assert config.database_url == "postgresql://user@host/db"
        assert config.get_connection_url() == "postgresql://user@host/db"


class TestAppConfigSaveLoad:
    """Tests for config persistence."""

    def test_load_reads_existing_config(self, tmp_path, monkeypatch):
        """Verify load() reads TOML."""
        config_path = tmp_path / "config.toml"

        # Create a config file
        config_path.write_text("""
[database]
url = "postgresql://user@host/db"

[songsets]
backup_retention = 10

[app]
working_dir = "/test/working"
preview_buffer_ms = 1000
preview_volume = 0.5
default_gap_beats = 4.0
default_video_template = "gradient_warm"
default_video_resolution = "720p"
""")

        config = AppConfig.load(config_path)

        assert config.database_url == "postgresql://user@host/db"
        assert config.songsets_backup_retention == 10
        assert config.working_dir == Path("/test/working")
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
        assert "[database]" in content
        assert "[songsets]" in content
        assert "[app]" in content

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
        working_dir = tmp_path / "test_working"

        config = AppConfig()
        config.working_dir = working_dir

        config.ensure_directories()

        assert config.cache_dir.exists()
        assert config.cache_dir.is_dir()
        assert config.output_dir.exists()
        assert config.output_dir.is_dir()
        assert config.log_dir.exists()
        assert config.log_dir.is_dir()
        assert config.songsets_backup_dir.exists()
        assert config.songsets_backup_dir.is_dir()


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
[database]
url = "postgresql://user@host/db"

[songsets]
backup_retention = 3

[app]
working_dir = "/existing/working"
preview_buffer_ms = 250
preview_volume = 0.9
default_gap_beats = 1.0
default_video_template = "dark"
default_video_resolution = "1080p"
""")

        config = ensure_app_config_exists()

        assert config.database_url == "postgresql://user@host/db"
        assert config.default_gap_beats == 1.0
