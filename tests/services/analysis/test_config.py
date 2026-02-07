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
            assert settings.MAX_CONCURRENT_JOBS == 2
            assert settings.DEMUCS_MODEL == "htdemucs"
            assert settings.DEMUCS_DEVICE == "cpu"

    def test_custom_values_from_env(self):
        """Test loading custom values from environment."""
        env_vars = {
            "SOW_R2_BUCKET": "my-bucket",
            "SOW_R2_ENDPOINT_URL": "https://r2.example.com",
            "SOW_R2_ACCESS_KEY_ID": "access-key",
            "SOW_R2_SECRET_ACCESS_KEY": "secret-key",
            "SOW_ANALYSIS_API_KEY": "api-key",
            "CACHE_DIR": "/custom/cache",
            "MAX_CONCURRENT_JOBS": "4",
            "DEMUCS_MODEL": "demucs",
            "DEMUCS_DEVICE": "cuda",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.SOW_R2_BUCKET == "my-bucket"
            assert settings.SOW_R2_ENDPOINT_URL == "https://r2.example.com"
            assert settings.SOW_R2_ACCESS_KEY_ID == "access-key"
            assert settings.SOW_R2_SECRET_ACCESS_KEY == "secret-key"
            assert settings.SOW_ANALYSIS_API_KEY == "api-key"
            assert settings.CACHE_DIR == Path("/custom/cache")
            assert settings.MAX_CONCURRENT_JOBS == 4
            assert settings.DEMUCS_MODEL == "demucs"
            assert settings.DEMUCS_DEVICE == "cuda"

    def test_gpu_device_setting(self):
        """Test setting GPU device."""
        with patch.dict(os.environ, {"DEMUCS_DEVICE": "cuda"}, clear=True):
            settings = Settings()
            assert settings.DEMUCS_DEVICE == "cuda"

    def test_cpu_device_setting(self):
        """Test CPU device is default."""
        settings = Settings()
        assert settings.DEMUCS_DEVICE == "cpu"
