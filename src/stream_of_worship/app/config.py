"""Configuration management for sow-app TUI.

Extends AdminConfig with app-specific settings for asset cache,
output directories, and playback preferences.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tomllib
import tomli_w

from stream_of_worship.admin.config import AdminConfig, get_config_dir as get_admin_config_dir


def get_app_config_dir() -> Path:
    """Get the platform-specific config directory for sow-app.

    Returns:
        Path to the config directory for sow-app.
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = __import__("os").environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "sow-app"
        return Path.home() / ".config" / "sow-app"
    elif sys.platform == "win32":
        appdata = __import__("os").environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "sow-app"
        return Path.home() / "AppData" / "Roaming" / "sow-app"
    else:
        return Path.home() / ".config" / "sow-app"


def get_app_config_path() -> Path:
    """Get the path to the app config.toml file.

    Returns:
        Path to config.toml
    """
    return get_app_config_dir() / "config.toml"


@dataclass
class AppConfig:
    """Configuration for sow-app TUI.

    Extends AdminConfig with app-specific settings for asset cache,
    output directories, and playback preferences.

    Attributes:
        admin_config: Base admin configuration (shared database, R2, etc.)
        cache_dir: Local directory for cached R2 assets
        output_dir: Directory for exported audio/video files
        preview_buffer_ms: Audio buffer size for playback in milliseconds
        default_gap_beats: Default gap duration between songs (in beats)
        default_video_template: Default video template name
        default_video_resolution: Default video resolution (e.g., "1080p")
    """

    # Base admin config (embedded)
    admin_config: AdminConfig = field(default_factory=AdminConfig)

    # App-specific paths
    cache_dir: Path = field(default_factory=lambda: get_app_config_dir() / "cache")
    output_dir: Path = field(default_factory=lambda: Path.home() / "StreamOfWorship" / "output")

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

        # Load base admin config from its own section or file
        admin_config = AdminConfig()
        if "admin" in data:
            admin_data = data["admin"]
            admin_config.analysis_url = admin_data.get("analysis_url", admin_config.analysis_url)
            admin_config.r2_bucket = admin_data.get("r2_bucket", admin_config.r2_bucket)
            admin_config.r2_endpoint_url = admin_data.get("r2_endpoint_url", admin_config.r2_endpoint_url)
            admin_config.r2_region = admin_data.get("r2_region", admin_config.r2_region)
            admin_config.turso_database_url = admin_data.get("turso_database_url", admin_config.turso_database_url)
            if "db_path" in admin_data:
                admin_config.db_path = Path(admin_data["db_path"])

        config = cls(admin_config=admin_config)

        # Load app-specific settings
        if "app" in data:
            app_data = data["app"]
            if "cache_dir" in app_data:
                config.cache_dir = Path(app_data["cache_dir"])
            if "output_dir" in app_data:
                config.output_dir = Path(app_data["output_dir"])
            config.preview_buffer_ms = app_data.get("preview_buffer_ms", config.preview_buffer_ms)
            config.preview_volume = app_data.get("preview_volume", config.preview_volume)
            config.default_gap_beats = app_data.get("default_gap_beats", config.default_gap_beats)
            config.default_video_template = app_data.get("default_video_template", config.default_video_template)
            config.default_video_resolution = app_data.get("default_video_resolution", config.default_video_resolution)

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
            "admin": {
                "analysis_url": self.admin_config.analysis_url,
                "r2_bucket": self.admin_config.r2_bucket,
                "r2_endpoint_url": self.admin_config.r2_endpoint_url,
                "r2_region": self.admin_config.r2_region,
                "turso_database_url": self.admin_config.turso_database_url,
                "db_path": str(self.admin_config.db_path),
            },
            "app": {
                "cache_dir": str(self.cache_dir),
                "output_dir": str(self.output_dir),
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

    @property
    def db_path(self) -> Path:
        """Get the database path from admin config."""
        return self.admin_config.db_path

    @property
    def r2_bucket(self) -> str:
        """Get R2 bucket from admin config."""
        return self.admin_config.r2_bucket

    @property
    def r2_endpoint_url(self) -> str:
        """Get R2 endpoint from admin config."""
        return self.admin_config.r2_endpoint_url

    @property
    def r2_region(self) -> str:
        """Get R2 region from admin config."""
        return self.admin_config.r2_region


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
