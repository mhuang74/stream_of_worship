import os

import pytest

from sow_render_worker.config import ConfigError, RenderWorkerConfig, load_config


class TestRenderWorkerConfig:
    def test_from_env_with_all_vars(self, mock_env):
        config = RenderWorkerConfig.from_env()
        assert config.SOW_DATABASE_URL == "postgresql://user:pass@localhost:5432/testdb"
        assert config.SOW_R2_BUCKET == "test-bucket"
        assert config.SOW_R2_ENDPOINT_URL == "https://abc123.r2.cloudflarestorage.com"
        assert config.SOW_R2_ACCESS_KEY_ID == "test-access-key"
        assert config.SOW_R2_SECRET_ACCESS_KEY == "test-secret-key"
        assert config.SOW_AWS_REGION == "us-east-1"

    def test_from_env_missing_database_url(self, mock_env):
        del os.environ["SOW_DATABASE_URL"]
        with pytest.raises(ConfigError, match="SOW_DATABASE_URL"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_bucket(self, mock_env):
        del os.environ["SOW_R2_BUCKET"]
        with pytest.raises(ConfigError, match="SOW_R2_BUCKET"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_endpoint_url(self, mock_env):
        del os.environ["SOW_R2_ENDPOINT_URL"]
        with pytest.raises(ConfigError, match="SOW_R2_ENDPOINT_URL"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_access_key_id(self, mock_env):
        del os.environ["SOW_R2_ACCESS_KEY_ID"]
        with pytest.raises(ConfigError, match="SOW_R2_ACCESS_KEY_ID"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_secret_access_key(self, mock_env):
        del os.environ["SOW_R2_SECRET_ACCESS_KEY"]
        with pytest.raises(ConfigError, match="SOW_R2_SECRET_ACCESS_KEY"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_aws_region(self, mock_env):
        del os.environ["SOW_AWS_REGION"]
        with pytest.raises(ConfigError, match="SOW_AWS_REGION"):
            RenderWorkerConfig.from_env()

    def test_from_env_missing_multiple_vars(self, mock_env):
        del os.environ["SOW_DATABASE_URL"]
        del os.environ["SOW_AWS_REGION"]
        with pytest.raises(ConfigError, match="SOW_DATABASE_URL") as exc_info:
            RenderWorkerConfig.from_env()
        assert "SOW_AWS_REGION" in str(exc_info.value)

    def test_from_env_empty_string_treated_as_missing(self, mock_env):
        os.environ["SOW_DATABASE_URL"] = ""
        with pytest.raises(ConfigError, match="SOW_DATABASE_URL"):
            RenderWorkerConfig.from_env()

    def test_config_is_frozen(self, mock_env):
        config = RenderWorkerConfig.from_env()
        with pytest.raises(AttributeError):
            config.SOW_DATABASE_URL = "new-value"

    def test_load_config(self, mock_env):
        config = load_config()
        assert isinstance(config, RenderWorkerConfig)
        assert config.SOW_DATABASE_URL == "postgresql://user:pass@localhost:5432/testdb"
