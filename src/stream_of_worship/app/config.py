"""Configuration management for sow-app TUI.

Manages app-specific settings for asset cache, output directories,
playback preferences, and Postgres database configuration.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tomllib
import tomli_w

from stream_of_worship.core.paths import get_cache_dir as _get_core_cache_dir
from stream_of_worship.core.paths import get_user_data_dir as _get_core_data_dir


def get_app_config_dir() -> Path:
    """Get the platform-specific config directory for sow-app.

    Returns:
        Path to the config directory for sow-app (~/.config/sow/ on Linux/macOS).
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "sow"
        return Path.home() / ".config" / "sow"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "sow"
        return Path.home() / "AppData" / "Roaming" / "sow"
    else:
        return Path.home() / ".config" / "sow"


def get_app_config_path() -> Path:
    """Get the path to the app config.toml file.

    Returns:
        Path to config.toml
    """
    return get_app_config_dir() / "config.toml"


def get_default_export_dir() -> Path:
    """Get the default export directory for songsets.

    Returns:
        Path to default export directory
    """
    return Path.home() / "Documents" / "sow-songsets"


@dataclass
class AppConfig:
    """Configuration for sow-app TUI.

    Attributes:
        database_url: Postgres DSN for app role (without password)
        songsets_backup_retention: Number of songset backups to keep
        songsets_export_dir: Directory for songset JSON exports
        r2_bucket: R2 bucket name for audio storage
        r2_endpoint_url: R2 endpoint URL
        r2_region: R2 region
        cache_dir: Local directory for cached R2 assets
        output_dir: Directory for exported audio/video files
        log_dir: Directory for log files
        preview_buffer_ms: Audio buffer size for playback in milliseconds
        preview_volume: Default playback volume (0.0-1.0)
        default_gap_beats: Default gap duration between songs (in beats)
        default_video_template: Default video template name
        default_video_resolution: Default video resolution (e.g., "1080p")

    Note:
        The database password is read from ``SOW_DATABASE_PASSWORD`` environment
        variable only (not stored in config file for security).
    """

    # Postgres database (password via SOW_DATABASE_PASSWORD env var)
    database_url: str = ""

    # Songset settings
    songsets_backup_retention: int = 5
    songsets_export_dir: Path = field(default_factory=get_default_export_dir)

    # R2 storage settings
    r2_bucket: str = "sow-audio"
    r2_endpoint_url: str = ""
    r2_region: str = "auto"

    # App-specific paths
    cache_dir: Path = field(default_factory=_get_core_cache_dir)
    output_dir: Path = field(default_factory=lambda: Path.home() / "sow" / "output")
    log_dir: Path = field(default_factory=lambda: _get_core_data_dir() / "logs")

    # Playback settings
    preview_buffer_ms: int = 500
    preview_volume: float = 0.8

    # Export settings
    default_gap_beats: float = 2.0
    default_video_template: str = "dark"
    default_video_resolution: str = "1080p"

    def get_connection_url(self) -> str:
        """Return a Postgres DSN with password injected from env var.

        The ``database_url`` stored in TOML should NOT contain a password.
        The password is read from the ``SOW_DATABASE_PASSWORD`` environment
        variable and injected into the URL at runtime.

        Returns:
            A fully-formed ``postgresql://`` connection string.

        Raises:
            ValueError: If the URL is empty.
        """
        url = os.environ.get("SOW_DATABASE_URL", self.database_url)
        if not url:
            raise ValueError("database_url is not configured")
        password = os.environ.get("SOW_DATABASE_PASSWORD", "")
        if password and "://" in url and "@" in url:
            proto, rest = url.split("://", 1)
            user_host = rest.split("@", 1)
            if len(user_host) == 2 and ":" not in user_host[0]:
                # No password currently in DSN → insert one
                url = f"{proto}://{user_host[0]}:{password}@{user_host[1]}"
        return url

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        """Load configuration from TOML file.

        Args:
            path: Path to config file (defaults to standard location)

        Returns:
            AppConfig instance with loaded values

        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        if path is None:
            path = get_app_config_path()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "rb") as f:
            data = tomllib.load(f)

        config = cls()

        # Load database config (new [database] section)
        if "database" in data:
            db = data["database"]
            if "url" in db:
                config.database_url = db["url"]

        # Backward compatibility: silently ignore old [turso] section
        # (it may still exist in user configs from before migration)

        # Load songset settings
        if "songsets" in data:
            songsets = data["songsets"]
            config.songsets_backup_retention = songsets.get(
                "backup_retention", config.songsets_backup_retention
            )
            if "export_dir" in songsets:
                config.songsets_export_dir = Path(songsets["export_dir"]).expanduser()

        # Load R2 settings
        if "r2" in data:
            r2 = data["r2"]
            config.r2_bucket = r2.get("bucket", config.r2_bucket)
            config.r2_endpoint_url = r2.get("endpoint_url", config.r2_endpoint_url)
            config.r2_region = r2.get("region", config.r2_region)

        # Load app-specific settings
        if "app" in data:
            app_data = data["app"]
            if "cache_dir" in app_data:
                config.cache_dir = Path(app_data["cache_dir"]).expanduser()
            if "output_dir" in app_data:
                config.output_dir = Path(app_data["output_dir"]).expanduser()
            if "log_dir" in app_data:
                config.log_dir = Path(app_data["log_dir"]).expanduser()
            config.preview_buffer_ms = app_data.get("preview_buffer_ms", config.preview_buffer_ms)
            config.preview_volume = app_data.get("preview_volume", config.preview_volume)
            config.default_gap_beats = app_data.get("default_gap_beats", config.default_gap_beats)
            config.default_video_template = app_data.get(
                "default_video_template", config.default_video_template
            )
            config.default_video_resolution = app_data.get(
                "default_video_resolution", config.default_video_resolution
            )

        return config

    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to TOML file.

        Args:
            path: Path to save config (defaults to standard location)
        """
        if path is None:
            path = get_app_config_path()

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build TOML structure
        data = {
            "database": {"url": self.database_url},
            "songsets": {
                "backup_retention": self.songsets_backup_retention,
                "export_dir": str(self.songsets_export_dir),
            },
            "r2": {
                "bucket": self.r2_bucket,
                "endpoint_url": self.r2_endpoint_url,
                "region": self.r2_region,
            },
            "app": {
                "cache_dir": str(self.cache_dir),
                "output_dir": str(self.output_dir),
                "log_dir": str(self.log_dir),
                "preview_buffer_ms": self.preview_buffer_ms,
                "preview_volume": self.preview_volume,
                "default_gap_beats": self.default_gap_beats,
                "default_video_template": self.default_video_template,
                "default_video_resolution": self.default_video_resolution,
            },
        }

        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    def ensure_directories(self) -> None:
        """Ensure all configured directories exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.songsets_export_dir.mkdir(parents=True, exist_ok=True)


def ensure_app_config_exists() -> AppConfig:
    """Ensure config file exists, creating default if needed.

    Returns:
        AppConfig instance
    """
    config_path = get_app_config_path()

    if config_path.exists():
        try:
            return AppConfig.load(config_path)
        except Exception:
            # If config is corrupted, create a new one
            pass

    # Create default config
    config = AppConfig()
    config.save(config_path)
    return config
