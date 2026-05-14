"""Configuration management for sow-app TUI.

Manages app-specific settings for asset cache, output directories,
playback preferences, and Postgres database configuration.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import tomllib
import tomli_w

from stream_of_worship.core.paths import get_cache_dir as _get_core_cache_dir


def get_app_config_dir() -> Path:
    """Get the platform-specific config directory for sow-app.

    Returns:
        Path to the config directory for sow-app (~/.config/stream-of-worship/ on Linux/macOS).
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "stream-of-worship"
        return Path.home() / ".config" / "stream-of-worship"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "stream-of-worship"
        return Path.home() / "AppData" / "Roaming" / "stream-of-worship"
    else:
        return Path.home() / ".config" / "stream-of-worship"


def get_app_config_path() -> Path:
    """Get the path to the app config.toml file.

    Returns:
        Path to config.toml
    """
    return get_app_config_dir() / "config.toml"


@dataclass
class AppConfig:
    """Configuration for sow-app TUI.

    Attributes:
        database_url: Postgres DSN for app role (without password)
        songsets_backup_retention: Number of songset backups to keep
        r2_bucket: R2 bucket name for audio storage
        r2_endpoint_url: R2 endpoint URL
        r2_region: R2 region
        working_dir: Working directory for logs, output, and backups
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

    # R2 storage settings
    r2_bucket: str = "stream-of-worship"
    r2_endpoint_url: str = ""
    r2_region: str = "auto"

    # Working directory (only configurable path)
    working_dir: Path = field(default_factory=lambda: Path.home() / "stream-of-worship")

    # Playback settings
    preview_buffer_ms: int = 500
    preview_volume: float = 0.8

    # Export settings
    default_gap_beats: float = 2.0
    default_video_template: str = "dark"
    default_video_resolution: str = "1080p"

    @property
    def cache_dir(self) -> Path:
        """Cache directory - always at standard platform location."""
        return _get_core_cache_dir()

    @property
    def log_dir(self) -> Path:
        """Log directory - derived from working_dir."""
        return self.working_dir / "logs"

    @property
    def output_dir(self) -> Path:
        """Output directory - derived from working_dir."""
        return self.working_dir / "output"

    @property
    def songsets_backup_dir(self) -> Path:
        """Songset backup directory - derived from working_dir."""
        return self.working_dir / "backup"

    @property
    def songsets_export_dir(self) -> Path:
        """Deprecated: Use songsets_backup_dir instead."""
        return self.songsets_backup_dir

    def ensure_directories(self) -> None:
        """Ensure all configured directories exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.songsets_backup_dir.mkdir(parents=True, exist_ok=True)

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
                encoded_password = quote(password, safe="")
                url = f"{proto}://{user_host[0]}:{encoded_password}@{user_host[1]}"
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
            # Backward compatibility: silently ignore old export_dir key

        # Load R2 settings
        if "r2" in data:
            r2 = data["r2"]
            config.r2_bucket = r2.get("bucket", config.r2_bucket)
            config.r2_endpoint_url = r2.get("endpoint_url", config.r2_endpoint_url)
            config.r2_region = r2.get("region", config.r2_region)

        # Load app-specific settings
        if "app" in data:
            app_data = data["app"]
            if "working_dir" in app_data:
                config.working_dir = Path(app_data["working_dir"]).expanduser()
            # Backward compatibility: silently ignore old path keys
            # (cache_dir, output_dir, log_dir are now derived from working_dir)
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
            },
            "r2": {
                "bucket": self.r2_bucket,
                "endpoint_url": self.r2_endpoint_url,
                "region": self.r2_region,
            },
            "app": {
                "working_dir": str(self.working_dir),
                "preview_buffer_ms": self.preview_buffer_ms,
                "preview_volume": self.preview_volume,
                "default_gap_beats": self.default_gap_beats,
                "default_video_template": self.default_video_template,
                "default_video_resolution": self.default_video_resolution,
            },
        }

        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a configuration value by key.

        Supports dot notation mapping to flat attributes:
        - "r2.bucket" -> r2_bucket
        - "database.url" -> database_url
        - "app.working_dir" -> working_dir

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        attr_name = self._key_to_attr(key)
        if hasattr(self, attr_name):
            value = getattr(self, attr_name)
            if isinstance(value, Path):
                return str(value)
            return value
        return default

    def set(self, key: str, value: str) -> None:
        """Set a configuration value by key.

        Supports dot notation mapping to flat attributes:
        - "r2.bucket" -> r2_bucket
        - "database.url" -> database_url
        - "app.working_dir" -> working_dir

        Args:
            key: Configuration key
            value: Configuration value
        """
        attr_name = self._key_to_attr(key)
        if attr_name not in self.__dataclass_fields__:
            raise ValueError(f"Invalid or read-only config key: {key}")

        current = getattr(self, attr_name)
        if isinstance(current, bool):
            new_value = value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            new_value = int(value)
        elif isinstance(current, float):
            new_value = float(value)
        elif isinstance(current, Path):
            new_value = Path(value)
        else:
            new_value = value

        setattr(self, attr_name, new_value)

    @staticmethod
    def _key_to_attr(key: str) -> str:
        """Convert dot-notation key to attribute name.

        Maps TOML section paths to flat attribute names:
        - "r2.bucket" -> "r2_bucket"
        - "database.url" -> "database_url"
        - "app.working_dir" -> "working_dir" (app prefix is dropped)
        """
        if "." not in key:
            return key
        section, attr = key.split(".", 1)
        if section == "app":
            return attr
        return f"{section}_{attr}"


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
