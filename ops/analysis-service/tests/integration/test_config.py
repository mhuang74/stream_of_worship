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
            assert settings.SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS == 1
            assert settings.SOW_DEMUCS_MODEL == "htdemucs"
            assert settings.SOW_DEMUCS_DEVICE == "cpu"
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
            "SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS": "4",
            "SOW_DEMUCS_MODEL": "demucs",
            "SOW_DEMUCS_DEVICE": "cuda",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.SOW_R2_BUCKET == "my-bucket"
            assert settings.SOW_R2_ENDPOINT_URL == "https://r2.example.com"
            assert settings.SOW_R2_ACCESS_KEY_ID == "access-key"
            assert settings.SOW_R2_SECRET_ACCESS_KEY == "secret-key"
            assert settings.SOW_ANALYSIS_API_KEY == "api-key"
            assert settings.CACHE_DIR == Path("/custom/cache")
            assert settings.SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS == 4
            assert settings.SOW_DEMUCS_MODEL == "demucs"
            assert settings.SOW_DEMUCS_DEVICE == "cuda"

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

    def test_gpu_device_setting(self):
        """Test setting GPU device."""
        with patch.dict(os.environ, {"SOW_DEMUCS_DEVICE": "cuda"}, clear=True):
            settings = Settings()
            assert settings.SOW_DEMUCS_DEVICE == "cuda"

    def test_cpu_device_setting(self):
        """Test CPU device is default."""
        settings = Settings()
        assert settings.SOW_DEMUCS_DEVICE == "cpu"
