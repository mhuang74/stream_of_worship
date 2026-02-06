"""Configuration management for sow-admin CLI.

Handles loading, saving, and validating TOML configuration stored in:
- macOS: ~/.config/sow-admin/config.toml
- Linux: ~/.config/sow-admin/config.toml (XDG_CONFIG_HOME)
- Windows: %APPDATA%\\sow-admin\\config.toml
"""

import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import tomllib
import tomli_w


@dataclass
class AdminConfig:
    """Configuration for sow-admin CLI.

    Attributes:
        analysis_url: URL of the analysis service
        r2_bucket: Cloudflare R2 bucket name
        r2_endpoint_url: R2 endpoint URL
        r2_region: R2 region (usually "auto")
        turso_database_url: Turso database URL for sync
        db_path: Local SQLite database path
    """

    # Analysis Service
    analysis_url: str = "http://localhost:8000"

    # Cloudflare R2
    r2_bucket: str = "sow-audio"
    r2_endpoint_url: str = ""
    r2_region: str = "auto"

    # Turso (for sync)
    turso_database_url: str = ""

    # Local Database
    db_path: Path = field(default_factory=lambda: get_default_db_path())

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AdminConfig":
        """Load configuration from TOML file.

        Args:
            path: Path to config file (defaults to standard location)

        Returns:
            AdminConfig instance with loaded values

        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        if path is None:
            path = get_config_path()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "rb") as f:
            data = tomllib.load(f)

        config = cls()

        # Load service config
        if "service" in data:
            config.analysis_url = data["service"].get("analysis_url", config.analysis_url)

        # Load R2 config from file
        if "r2" in data:
            r2 = data["r2"]
            config.r2_bucket = r2.get("bucket", config.r2_bucket)
            config.r2_endpoint_url = r2.get("endpoint_url", config.r2_endpoint_url)
            config.r2_region = r2.get("region", config.r2_region)

        # Override R2 config from environment variables (takes precedence)
        env_bucket = os.environ.get("SOW_R2_BUCKET")
        if env_bucket:
            config.r2_bucket = env_bucket

        env_endpoint = os.environ.get("SOW_R2_ENDPOINT_URL")
        if env_endpoint:
            config.r2_endpoint_url = env_endpoint

        env_region = os.environ.get("SOW_R2_REGION")
        if env_region:
            config.r2_region = env_region

        # Load Turso config
        if "turso" in data:
            config.turso_database_url = data["turso"].get(
                "database_url", config.turso_database_url
            )

        # Load database path
        if "database" in data:
            db_path = data["database"].get("path")
            if db_path:
                config.db_path = Path(db_path)

        return config

    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to TOML file.

        Args:
            path: Path to save config (defaults to standard location)
        """
        if path is None:
            path = get_config_path()

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build TOML structure
        data = {
            "service": {"analysis_url": self.analysis_url},
            "r2": {
                "bucket": self.r2_bucket,
                "endpoint_url": self.r2_endpoint_url,
                "region": self.r2_region,
            },
            "turso": {"database_url": self.turso_database_url},
            "database": {"path": str(self.db_path)},
        }

        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a configuration value by key.

        Supports dot notation for nested values (e.g., "r2.bucket").

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        parts = key.split(".")
        value = self

        for part in parts:
            if hasattr(value, part):
                value = getattr(value, part)
            else:
                return default

        if isinstance(value, Path):
            return str(value)
        return value

    def set(self, key: str, value: str) -> None:
        """Set a configuration value by key.

        Supports dot notation for nested values (e.g., "r2.bucket").

        Args:
            key: Configuration key
            value: Configuration value
        """
        parts = key.split(".")
        target = self

        for part in parts[:-1]:
            if hasattr(target, part):
                target = getattr(target, part)
            else:
                raise ValueError(f"Invalid config key: {key}")

        final_key = parts[-1]
        if not hasattr(target, final_key):
            raise ValueError(f"Invalid config key: {key}")

        # Try to preserve type
        current = getattr(target, final_key)
        if isinstance(current, bool):
            new_value = value.lower() in ("true", "1", "yes")
        elif isinstance(current, int):
            new_value = int(value)
        elif isinstance(current, Path):
            new_value = Path(value)
        else:
            new_value = value

        setattr(target, final_key, new_value)


def get_config_dir() -> Path:
    """Get the platform-specific config directory.

    Returns:
        Path to the config directory for sow-admin.
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "sow-admin"
        return Path.home() / ".config" / "sow-admin"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "sow-admin"
        return Path.home() / "AppData" / "Roaming" / "sow-admin"
    else:
        return Path.home() / ".config" / "sow-admin"


def get_config_path() -> Path:
    """Get the path to the config.toml file.

    Returns:
        Path to config.toml
    """
    return get_config_dir() / "config.toml"


def get_default_db_path() -> Path:
    """Get the default database path.

    Returns:
        Path to default database location
    """
    return get_config_dir() / "db" / "sow.db"


def ensure_config_exists() -> AdminConfig:
    """Ensure config file exists, creating default if needed.

    Returns:
        AdminConfig instance
    """
    config_path = get_config_path()

    if config_path.exists():
        try:
            return AdminConfig.load(config_path)
        except Exception:
            # If config is corrupted, create a new one
            pass

    # Create default config
    config = AdminConfig()
    config.save(config_path)
    return config


def get_env_var_name(key: str) -> str:
    """Get the environment variable name for a config key.

    Args:
        key: Configuration key

    Returns:
        Environment variable name
    """
    return f"SOW_{key.upper().replace('.', '_')}"


def get_secret(key: str) -> Optional[str]:
    """Get a secret value from environment variable.

    Args:
        key: Secret key (e.g., "r2.access_key_id")

    Returns:
        Secret value or None
    """
    env_var = get_env_var_name(key)
    return os.environ.get(env_var)
