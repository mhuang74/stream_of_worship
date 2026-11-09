"""Tests for configuration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sow_analysis.config import Settings


class TestSettings:
    """Test Settings class."""

    def test_default_values(self):
        """Test default configuration values."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

            assert settings.SOW_R2_BUCKET == "sow-audio"
            assert settings.SOW_R2_ENDPOINT_URL == ""
            assert settings.SOW_R2_ACCESS_KEY_ID == ""
            assert settings.SOW_R2_SECRET_ACCESS_KEY == ""
            assert settings.SOW_ANALYSIS_API_KEY == ""
            assert settings.CACHE_DIR == Path("/cache")
            assert settings.SOW_MODAL_ANALYZER_APP_NAME == "sow-analyzer"
            assert settings.SOW_MODAL_ANALYZER_CLASS_NAME == "SongAnalyzer"
            assert settings.SOW_MODAL_DOWNBEATS_APP_NAME == "sow-downbeats"
            assert settings.SOW_MODAL_DOWNBEATS_CLASS_NAME == "DownbeatDetector"
            assert settings.SOW_MODAL_DOWNBEATS_MAX_ATTEMPTS == 2
            assert settings.SOW_MODAL_DOWNBEATS_RETRY_DELAY_SECONDS == 5.0
            assert settings.SOW_MODAL_DOWNBEATS_MAX_AUDIO_BYTES == 150_000_000
            assert settings.SOW_ANALYSIS_CACHE_VERSION == 2
            assert settings.SOW_ANALYZE_MAX_CONCURRENT == 2
            assert settings.SOW_EMBEDDING_API_KEY == ""
            assert settings.SOW_EMBEDDING_BASE_URL == ""
            assert settings.SOW_EMBEDDING_MODEL == "text-embedding-3-small"

    def test_custom_values_from_env(self):
        """Test loading custom values from environment."""
        env_vars = {
            "SOW_R2_BUCKET": "my-bucket",
            "SOW_R2_ENDPOINT_URL": "https://r2.example.com",
            "SOW_R2_ACCESS_KEY_ID": "access-key",
            "SOW_R2_SECRET_ACCESS_KEY": "secret-key",
            "SOW_ANALYSIS_API_KEY": "api-key",
            "CACHE_DIR": "/custom/cache",
            "SOW_MODAL_ANALYZER_APP_NAME": "sow-analyzer-test",
            "SOW_MODAL_DOWNBEATS_APP_NAME": "sow-downbeats-test",
            "SOW_ANALYZE_MAX_CONCURRENT": "4",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.SOW_R2_BUCKET == "my-bucket"
            assert settings.SOW_R2_ENDPOINT_URL == "https://r2.example.com"
            assert settings.SOW_R2_ACCESS_KEY_ID == "access-key"
            assert settings.SOW_R2_SECRET_ACCESS_KEY == "secret-key"
            assert settings.SOW_ANALYSIS_API_KEY == "api-key"
            assert settings.CACHE_DIR == Path("/custom/cache")
            assert settings.SOW_MODAL_ANALYZER_APP_NAME == "sow-analyzer-test"
            assert settings.SOW_MODAL_DOWNBEATS_APP_NAME == "sow-downbeats-test"
            assert settings.SOW_ANALYZE_MAX_CONCURRENT == 4

    def test_embedding_values_from_env(self):
        """Test loading embedding-specific values from environment."""
        env_vars = {
            "SOW_EMBEDDING_API_KEY": "embedding-key",
            "SOW_EMBEDDING_BASE_URL": "https://embeddings.example.com/v1",
            "SOW_EMBEDDING_MODEL": "provider/text-embedding-3-small",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.SOW_EMBEDDING_API_KEY == "embedding-key"
            assert settings.SOW_EMBEDDING_BASE_URL == "https://embeddings.example.com/v1"
            assert settings.SOW_EMBEDDING_MODEL == "provider/text-embedding-3-small"

    def test_old_llm_embedding_model_env_is_ignored(self):
        """Test SOW_LLM_EMBEDDING_MODEL is no longer a recognized setting."""
        with patch.dict(
            os.environ,
            {"SOW_LLM_EMBEDDING_MODEL": "legacy/provider-model"},
            clear=True,
        ):
            settings = Settings()

            assert not hasattr(settings, "SOW_LLM_EMBEDDING_MODEL")
            assert settings.SOW_EMBEDDING_MODEL == "text-embedding-3-small"

    def test_analyze_max_concurrent_validator(self):
        """SOW_ANALYZE_MAX_CONCURRENT must be >= 1."""
        with patch.dict(os.environ, {"SOW_ANALYZE_MAX_CONCURRENT": "0"}, clear=True):
            import pytest

            with pytest.raises(ValueError):
                Settings()

    def test_analyze_max_concurrent_env(self):
        """Test the analyze concurrency override parses from env."""
        with patch.dict(os.environ, {"SOW_ANALYZE_MAX_CONCURRENT": "3"}, clear=True):
            settings = Settings()
            assert settings.SOW_ANALYZE_MAX_CONCURRENT == 3
