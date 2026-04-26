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
        assert "sow-app" in str(config_dir)

    def test_get_app_config_path_returns_toml(self):
        """Verify get_app_config_path returns path to config.toml."""
        config_path = get_app_config_path()

        assert isinstance(config_path, Path)
        assert config_path.name == "config.toml"


class TestAppConfigDefaults:
    """Tests for default configuration values."""

    def test_default_db_path(self):
        """Verify default db_path is set."""
        config = AppConfig()

        assert isinstance(config.db_path, Path)
        assert "sow.db" in str(config.db_path)

    def test_default_songsets_db_path(self):
        """Verify default songsets_db_path is set."""
        config = AppConfig()

        assert isinstance(config.songsets_db_path, Path)
        assert "songsets.db" in str(config.songsets_db_path)

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

    def test_db_path_property(self):
        """Verify db_path access."""
        config = AppConfig()

        assert isinstance(config.db_path, Path)

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

    def test_load_reads_existing_config(self, tmp_path):
        """Verify load() reads TOML."""
        config_path = tmp_path / "config.toml"

        # Create a config file
        config_path.write_text("""
[database]
db_path = "/test/db.sqlite"
songsets_db_path = "/test/songsets.db"

[songsets]
backup_retention = 10
export_dir = "/test/exports"

[turso]
database_url = "libsql://test.turso.io"
readonly_token = "test-token"
sync_on_startup = false

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

        assert config.db_path == Path("/test/db.sqlite")
        assert config.songsets_db_path == Path("/test/songsets.db")
        assert config.songsets_backup_retention == 10
        assert config.songsets_export_dir == Path("/test/exports")
        assert config.turso_database_url == "libsql://test.turso.io"
        assert config.turso_readonly_token == "test-token"
        assert config.sync_on_startup is False
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
        assert "[database]" in content
        assert "[songsets]" in content
        assert "[turso]" in content
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
        cache_dir = tmp_path / "test_cache"
        output_dir = tmp_path / "test_output"

        config = AppConfig()
        config.cache_dir = cache_dir
        config.output_dir = output_dir
        config.db_path = tmp_path / "db" / "sow.db"
        config.songsets_db_path = tmp_path / "db" / "songsets.db"
        config.songsets_export_dir = tmp_path / "exports"

        config.ensure_directories()

        assert cache_dir.exists()
        assert cache_dir.is_dir()
        assert output_dir.exists()
        assert output_dir.is_dir()


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
db_path = "/existing/db.sqlite"
songsets_db_path = "/existing/songsets.db"

[songsets]
backup_retention = 3
export_dir = "/existing/exports"

[turso]
database_url = ""
readonly_token = ""
sync_on_startup = false

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

        assert config.db_path == Path("/existing/db.sqlite")
        assert config.default_gap_beats == 1.0


class TestTursoConfig:
    """Tests for Turso configuration."""

    def test_is_turso_configured_requires_url_and_token(self):
        """Verify Turso config requires both URL and token."""
        config = AppConfig()

        # Neither set
        config.turso_database_url = ""
        config.turso_readonly_token = ""
        assert config.is_turso_configured is False

        # Only URL set
        config.turso_database_url = "libsql://test.turso.io"
        config.turso_readonly_token = ""
        assert config.is_turso_configured is False

        # Both set
        config.turso_database_url = "libsql://test.turso.io"
        config.turso_readonly_token = "test-token"
        assert config.is_turso_configured is True

    def test_turso_url_from_environment(self, tmp_path, monkeypatch):
        """Verify Turso URL can be set via environment."""
        monkeypatch.setenv("SOW_TURSO_DATABASE_URL", "libsql://env.turso.io")

        config_path = tmp_path / "config.toml"
        config_path.write_text("""
[database]
db_path = "/test/db.sqlite"
songsets_db_path = "/test/songsets.db"

[songsets]
backup_retention = 5
export_dir = "/test/exports"

[turso]
database_url = "libsql://file.turso.io"
readonly_token = ""
sync_on_startup = true

[app]
cache_dir = "/test/cache"
output_dir = "/test/output"
preview_buffer_ms = 500
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
""")

        config = AppConfig.load(config_path)

        # Environment should override file
        assert config.turso_database_url == "libsql://env.turso.io"

    def test_turso_token_from_environment(self, tmp_path, monkeypatch):
        """Verify Turso token can be set via environment."""
        monkeypatch.setenv("SOW_TURSO_READONLY_TOKEN", "env-token")

        config_path = tmp_path / "config.toml"
        config_path.write_text("""
[database]
db_path = "/test/db.sqlite"
songsets_db_path = "/test/songsets.db"

[songsets]
backup_retention = 5
export_dir = "/test/exports"

[turso]
database_url = ""
readonly_token = "file-token"
sync_on_startup = true

[app]
cache_dir = "/test/cache"
output_dir = "/test/output"
preview_buffer_ms = 500
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
""")

        config = AppConfig.load(config_path)

        # Environment should override file
        assert config.turso_readonly_token == "env-token"
