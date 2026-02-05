"""Configuration management for Stream of Worship.

This module handles loading, saving, and validating configuration
stored in config.json in the user data directory.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any

from stream_of_worship.core.paths import get_config_path, get_user_data_dir


@dataclass
class Config:
    """Configuration for Stream of Worship application."""

    # Paths
    audio_folder: Path = field(default_factory=lambda: get_user_data_dir() / "song_library")
    output_folder: Path = field(default_factory=lambda: get_user_data_dir() / "output_transitions")
    output_songs_folder: Path = field(default_factory=lambda: get_user_data_dir() / "output_songs")
    stems_folder: Path = field(default_factory=lambda: Path("stems_output"))
    analysis_json: Path = field(default_factory=lambda: Path("poc/output_allinone/poc_full_results.json"))
    lyrics_folder: Path = field(default_factory=lambda: Path("data/lyrics/songs"))

    # TUI Settings
    error_logging: bool = True
    session_logging: bool = False

    # Audio Settings
    audio_format: str = "ogg"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000

    # Video Settings
    video_resolution: str = "1080p"
    default_background: str = "default.jpg"
    font_path: str = "assets/fonts/NotoSansTC-Bold.ttf"

    # Song Library Settings
    song_library_path: str = "song_library/"
    output_path: str = "output/"

    # LLM Settings
    llm_model: str = "openai/gpt-4o-mini"
    openrouter_api_key: Optional[str] = None

    # Lyrics Display
    lyrics_lookahead_beats: float = 1.0
    lyrics_line_height: int = 100
    lyrics_font_size: int = 48

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load configuration from a JSON file.

        Args:
            path: Path to config.json file

        Returns:
            Config instance with loaded values

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config file contains invalid JSON
        """
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Convert paths to Path objects
        for key, value in data.items():
            if "folder" in key or "path" in key or key.endswith("_json"):
                if value and isinstance(value, str):
                    data[key] = Path(value)

        # Create config instance, using defaults for missing keys
        config = cls()
        for key, value in data.items():
            if hasattr(config, key):
                setattr(config, key, value)

        return config

    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to a JSON file.

        Args:
            path: Path to save config.json (defaults to default config path)
        """
        if path is None:
            path = get_config_path()

        # Convert Path objects to strings for JSON serialization
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, Path):
                data[key] = str(value)

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def update(self, **kwargs: Any) -> None:
        """Update configuration values.

        Args:
            **kwargs: Key-value pairs to update
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @property
    def video_resolution_tuple(self) -> tuple[int, int]:
        """Get video resolution as (width, height) tuple.

        Returns:
            Tuple of (width, height)
        """
        resolutions = {
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "1440p": (2560, 1440),
            "4k": (3840, 2160),
        }
        return resolutions.get(self.video_resolution, (1920, 1080))

    def lyrics_lookahead_seconds(self, bpm: float) -> float:
        """Get lyrics look-ahead time in seconds based on BPM.

        Args:
            bpm: Beats per minute

        Returns:
            Look-ahead time in seconds
        """
        return self.lyrics_lookahead_beats * (60.0 / bpm)


def create_default_config() -> Config:
    """Create a default configuration instance.

    Returns:
        Config instance with default values
    """
    return Config()


def ensure_config_exists() -> Config:
    """Ensure config file exists, creating it with defaults if needed.

    Returns:
        Config instance (loaded from file or newly created)
    """
    config_path = get_config_path()

    if config_path.exists():
        try:
            return Config.load(config_path)
        except (json.JSONDecodeError, ValueError):
            # If config is corrupted, create a new one
            return create_default_config()
    else:
        # Create default config
        config = create_default_config()
        config.save(config_path)
        return config
