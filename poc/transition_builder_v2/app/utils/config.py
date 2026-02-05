"""Configuration loader."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Application configuration."""
    audio_folder: Path
    output_folder: Path
    output_songs_folder: Path
    analysis_json: Path
    stems_folder: Path
    default_transition_type: str = "crossfade"
    max_history_size: int = 50
    auto_play_on_generate: bool = True
    session_logging: bool = True
    error_logging: bool = True

    @classmethod
    def load(cls, config_path: Path) -> "Config":
        """Load configuration from JSON file.

        Args:
            config_path: Path to config.json

        Returns:
            Config instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is malformed
        """
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed config JSON: {e}")

        # Resolve paths relative to config file location
        config_dir = config_path.parent

        return cls(
            audio_folder=(config_dir / data.get("audio_folder", "./poc_audio")).resolve(),
            output_folder=(config_dir / data.get("output_folder", "./output_transitions")).resolve(),
            output_songs_folder=(config_dir / data.get("output_songs_folder", "./output_songs")).resolve(),
            analysis_json=(config_dir / data.get("analysis_json", "./poc_output_allinone/poc_full_results.json")).resolve(),
            stems_folder=(config_dir / data.get("stems_folder", "./poc_output_allinone/stems")).resolve(),
            default_transition_type=data.get("default_transition_type", "crossfade"),
            max_history_size=data.get("max_history_size", 50),
            auto_play_on_generate=data.get("auto_play_on_generate", True),
            session_logging=data.get("session_logging", True),
            error_logging=data.get("error_logging", True)
        )
