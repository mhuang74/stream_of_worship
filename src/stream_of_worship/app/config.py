"""Configuration management for sow-app TUI.

Manages app-specific settings for asset cache, output directories,
playback preferences, and Turso sync configuration.
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


def get_default_db_path() -> Path:
    """Get the default database path.

    Returns:
        Path to default database location (Turso embedded replica)
    """
    return get_app_config_dir() / "db" / "sow.db"


def get_default_songsets_db_path() -> Path:
    """Get the default songsets database path.

    Returns:
        Path to default songsets database location
    """
    return get_app_config_dir() / "db" / "songsets.db"


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
        db_path: Path to the catalog database (Turso embedded replica)
        songsets_db_path: Path to the local songsets database
        songsets_backup_retention: Number of songset backups to keep
        songsets_export_dir: Directory for songset JSON exports
        turso_database_url: Turso database URL for sync
        turso_readonly_token: Turso read-only token
        sync_on_startup: Whether to sync at app startup
        cache_dir: Local directory for cached R2 assets
        output_dir: Directory for exported audio/video files
        preview_buffer_ms: Audio buffer size for playback in milliseconds
        preview_volume: Default playback volume (0.0-1.0)
        default_gap_beats: Default gap duration between songs (in beats)
        default_video_template: Default video template name
        default_video_resolution: Default video resolution (e.g., "1080p")
    """

    # Database paths
    db_path: Path = field(default_factory=get_default_db_path)
    songsets_db_path: Path = field(default_factory=get_default_songsets_db_path)

    # Songset settings
    songsets_backup_retention: int = 5
    songsets_export_dir: Path = field(default_factory=get_default_export_dir)

    # Turso sync settings
    turso_database_url: str = ""
    turso_readonly_token: str = ""
    sync_on_startup: bool = True

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

        # Load database paths
        if "database" in data:
            db = data["database"]
            if "db_path" in db:
                config.db_path = Path(db["db_path"])
            if "songsets_db_path" in db:
                config.songsets_db_path = Path(db["songsets_db_path"])

        # Load songset settings
        if "songsets" in data:
            songsets = data["songsets"]
            config.songsets_backup_retention = songsets.get(
                "backup_retention", config.songsets_backup_retention
            )
            if "export_dir" in songsets:
                config.songsets_export_dir = Path(songsets["export_dir"])

        # Load Turso settings
        if "turso" in data:
            turso = data["turso"]
            config.turso_database_url = turso.get("database_url", config.turso_database_url)
            config.turso_readonly_token = turso.get("readonly_token", config.turso_readonly_token)
            config.sync_on_startup = turso.get("sync_on_startup", config.sync_on_startup)

        # Override from environment
        env_url = os.environ.get("SOW_TURSO_DATABASE_URL")
        if env_url:
            config.turso_database_url = env_url

        env_token = os.environ.get("SOW_TURSO_READONLY_TOKEN")
        if env_token:
            config.turso_readonly_token = env_token

        # Load app-specific settings
        if "app" in data:
            app_data = data["app"]
            if "cache_dir" in app_data:
                config.cache_dir = Path(app_data["cache_dir"])
            if "output_dir" in app_data:
                config.output_dir = Path(app_data["output_dir"])
            if "log_dir" in app_data:
                config.log_dir = Path(app_data["log_dir"])
            config.preview_buffer_ms = app_data.get("preview_buffer_ms", config.preview_buffer_ms)
            config.preview_volume = app_data.get("preview_volume", config.preview_volume)
            config.default_gap_beats = app_data.get("default_gap_beats", config.default_gap_beats)
            config.default_video_template = app_data.get(
                "default_video_template", config.default_video_template
            )
            config.default_video_resolution = app_data.get(
                "default_video_resolution", config.default_video_resolution
            )

        # SOW_CACHE_DIR env var wins over TOML cache_dir
        env_cache_dir = os.environ.get("SOW_CACHE_DIR")
        if env_cache_dir:
            config.cache_dir = Path(env_cache_dir)

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
            "database": {
                "db_path": str(self.db_path),
                "songsets_db_path": str(self.songsets_db_path),
            },
            "songsets": {
                "backup_retention": self.songsets_backup_retention,
                "export_dir": str(self.songsets_export_dir),
            },
            "turso": {
                "database_url": self.turso_database_url,
                "readonly_token": self.turso_readonly_token,
                "sync_on_startup": self.sync_on_startup,
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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.songsets_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.songsets_export_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_turso_configured(self) -> bool:
        """Check if Turso sync is configured.

        Returns:
            True if Turso URL and token are configured
        """
        return bool(self.turso_database_url and self.turso_readonly_token)

    @property
    def r2_bucket(self) -> str:
        """Get R2 bucket (from environment or default).

        Returns:
            R2 bucket name
        """
        return os.environ.get("SOW_R2_BUCKET", "sow-audio")

    @property
    def r2_endpoint_url(self) -> str:
        """Get R2 endpoint (from environment).

        Returns:
            R2 endpoint URL
        """
        return os.environ.get("SOW_R2_ENDPOINT_URL", "")

    @property
    def r2_region(self) -> str:
        """Get R2 region (from environment or default).

        Returns:
            R2 region
        """
        return os.environ.get("SOW_R2_REGION", "auto")


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

    # Create default config with placeholder Turso values
    config = AppConfig()
    config.save(config_path)
    return config
