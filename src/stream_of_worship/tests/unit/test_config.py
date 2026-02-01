"""Tests for configuration management."""

import json
from pathlib import Path
import pytest

from stream_of_worship.core.config import (
    Config,
    create_default_config,
    ensure_config_exists,
)


class TestConfig:
    """Tests for Config dataclass."""

    def test_default_values(self):
        """Test that Config has correct default values."""
        config = Config()

        assert config.audio_format == "ogg"
        assert config.audio_bitrate == "192k"
        assert config.audio_sample_rate == 48000
        assert config.video_resolution == "1080p"
        assert config.llm_model == "openai/gpt-4o-mini"
        assert config.error_logging is True
        assert config.session_logging is False
        assert config.lyrics_lookahead_beats == 1.0

    def test_video_resolution_tuple(self):
        """Test video_resolution_tuple property."""
        config = Config()

        # Default
        assert config.video_resolution_tuple == (1920, 1080)

        # 720p
        config.video_resolution = "720p"
        assert config.video_resolution_tuple == (1280, 720)

        # 1440p
        config.video_resolution = "1440p"
        assert config.video_resolution_tuple == (2560, 1440)

        # 4k
        config.video_resolution = "4k"
        assert config.video_resolution_tuple == (3840, 2160)

        # Unknown - defaults to 1080p
        config.video_resolution = "unknown"
        assert config.video_resolution_tuple == (1920, 1080)

    def test_lyrics_lookahead_seconds(self):
        """Test lyrics_lookahead_seconds property."""
        config = Config()

        # At 60 BPM, 1 beat = 1 second
        lookahead = config.lyrics_lookahead_seconds(60.0)
        assert lookahead == 1.0

        # At 120 BPM, 1 beat = 0.5 seconds
        lookahead = config.lyrics_lookahead_seconds(120.0)
        assert lookahead == 0.5

        # At 90 BPM, 1 beat = 0.667 seconds
        lookahead = config.lyrics_lookahead_seconds(90.0)
        assert abs(lookahead - 0.667) < 0.01

    def test_update(self):
        """Test config update method."""
        config = Config()

        config.update(
            audio_format="wav",
            video_resolution="4k",
            error_logging=False
        )

        assert config.audio_format == "wav"
        assert config.video_resolution == "4k"
        assert config.error_logging is False


class TestConfigLoadAndSave:
    """Tests for config loading and saving."""

    @pytest.fixture
    def config_file(self, tmp_path):
        """Fixture providing a temporary config file."""
        return tmp_path / "config.json"

    def test_load_creates_config_object(self, config_file):
        """Test that load creates Config object from file."""
        config_data = {
            "audio_format": "wav",
            "video_resolution": "720p",
            "llm_model": "custom/model",
            "error_logging": False,
        }

        with config_file.open("w") as f:
            json.dump(config_data, f)

        config = Config.load(config_file)

        assert config.audio_format == "wav"
        assert config.video_resolution == "720p"
        assert config.llm_model == "custom/model"
        assert config.error_logging is False

    def test_load_converts_paths_to_path_objects(self, config_file):
        """Test that load converts path strings to Path objects."""
        config_data = {
            "audio_folder": "/custom/audio",
            "output_folder": "/custom/output",
        }

        with config_file.open("w") as f:
            json.dump(config_data, f)

        config = Config.load(config_file)

        assert isinstance(config.audio_folder, Path)
        assert isinstance(config.output_folder, Path)
        assert config.audio_folder == Path("/custom/audio")
        assert config.output_folder == Path("/custom/output")

    def test_load_uses_defaults_for_missing_keys(self, config_file):
        """Test that load uses defaults for missing keys."""
        config_data = {
            "audio_format": "flac",
        }

        with config_file.open("w") as f:
            json.dump(config_data, f)

        config = Config.load(config_file)

        assert config.audio_format == "flac"
        # Should use defaults
        assert config.video_resolution == "1080p"
        assert config.audio_bitrate == "192k"

    def test_load_raises_file_not_found(self):
        """Test that load raises FileNotFoundError for non-existent file."""
        with pytest.raises(FileNotFoundError):
            Config.load(Path("/nonexistent/config.json"))

    def test_save_creates_directory(self, tmp_path):
        """Test that save creates directory if needed."""
        config = Config()
        config_path = tmp_path / "subdir" / "config.json"

        config.save(config_path)

        assert config_path.exists()
        assert config_path.parent.exists()

    def test_save_converts_paths_to_strings(self, tmp_path):
        """Test that save converts Path objects to strings."""
        config = Config()
        config.audio_folder = Path("/custom/audio")
        config_path = tmp_path / "config.json"

        config.save(config_path)

        with config_path.open("r") as f:
            data = json.load(f)

        assert isinstance(data["audio_folder"], str)
        assert data["audio_folder"] == "/custom/audio"

    def test_save_preserves_all_values(self, config_file):
        """Test that save preserves all configuration values."""
        config = Config()
        config.audio_format = "flac"
        config.video_resolution = "1440p"
        config.llm_model = "custom/model"
        config.lyrics_lookahead_beats = 2.0

        config.save(config_file)

        with config_file.open("r") as f:
            data = json.load(f)

        assert data["audio_format"] == "flac"
        assert data["video_resolution"] == "1440p"
        assert data["llm_model"] == "custom/model"
        assert data["lyrics_lookahead_beats"] == 2.0

    def test_save_without_path_uses_default(self, tmp_path, monkeypatch):
        """Test that save without path uses default config path."""
        config = Config()

        # Mock get_config_path to return test path
        test_config_path = tmp_path / "config.json"
        monkeypatch.setattr(
            "stream_of_worship.core.config.get_config_path",
            lambda: test_config_path
        )

        config.save()

        assert test_config_path.exists()

    def test_round_trip(self, config_file):
        """Test that save/load round-trip preserves data."""
        original = Config()
        original.audio_format = "flac"
        original.video_resolution = "720p"
        original.audio_folder = Path("/test/audio")
        original.error_logging = False

        original.save(config_file)
        loaded = Config.load(config_file)

        assert loaded.audio_format == original.audio_format
        assert loaded.video_resolution == original.video_resolution
        assert loaded.audio_folder == original.audio_folder
        assert loaded.error_logging == original.error_logging


class TestCreateDefaultConfig:
    """Tests for create_default_config function."""

    def test_returns_config_instance(self):
        """Test that create_default_config returns Config instance."""
        config = create_default_config()
        assert isinstance(config, Config)

    def test_has_default_values(self):
        """Test that returned config has default values."""
        config = create_default_config()
        assert config.video_resolution == "1080p"
        assert config.audio_format == "ogg"


class TestEnsureConfigExists:
    """Tests for ensure_config_exists function."""

    @pytest.fixture
    def mock_config_path(self, tmp_path, monkeypatch):
        """Fixture mocking get_config_path."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(
            "stream_of_worship.core.config.get_config_path",
            lambda: config_path
        )
        return config_path

    def test_creates_config_if_not_exists(self, mock_config_path):
        """Test that ensure_config_exists creates config if it doesn't exist."""
        assert not mock_config_path.exists()

        config = ensure_config_exists()

        assert mock_config_path.exists()
        assert isinstance(config, Config)

    def test_loads_existing_config(self, mock_config_path):
        """Test that ensure_config_exists loads existing config."""
        # Create config first
        test_data = {"video_resolution": "720p"}
        with mock_config_path.open("w") as f:
            json.dump(test_data, f)

        config = ensure_config_exists()

        assert config.video_resolution == "720p"

    def test_creates_new_config_if_corrupted(self, mock_config_path):
        """Test that ensure_config_exists replaces corrupted config."""
        # Write invalid JSON
        with mock_config_path.open("w") as f:
            f.write("invalid json")

        config = ensure_config_exists()

        assert isinstance(config, Config)
        assert config.video_resolution == "1080p"  # Default
