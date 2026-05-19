import os
from unittest.mock import patch

import pytest

from sow_render_worker.config import ConfigError, RenderWorkerConfig, load_config


class TestRenderWorkerConfig:
    def test_from_env_with_all_vars(self, mock_env):
        config = RenderWorkerConfig.from_env()
        assert config.DATABASE_URL == "postgresql://user:pass@localhost:5432/testdb"
        assert config.R2_BUCKET == "test-bucket"
        assert config.R2_ENDPOINT_URL == "https://abc123.r2.cloudflarestorage.com"
        assert config.R2_ACCESS_KEY_ID == "test-access-key"
        assert config.R2_SECRET_ACCESS_KEY == "test-secret-key"
        assert config.AWS_REGION == "us-east-1"
        assert config.SQS_QUEUE_URL == "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"

    def test_from_env_missing_database_url(self, env_vars):
        del env_vars["DATABASE_URL"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="DATABASE_URL"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_bucket(self, env_vars):
        del env_vars["R2_BUCKET"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="R2_BUCKET"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_endpoint_url(self, env_vars):
        del env_vars["R2_ENDPOINT_URL"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="R2_ENDPOINT_URL"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_access_key_id(self, env_vars):
        del env_vars["R2_ACCESS_KEY_ID"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="R2_ACCESS_KEY_ID"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_r2_secret_access_key(self, env_vars):
        del env_vars["R2_SECRET_ACCESS_KEY"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="R2_SECRET_ACCESS_KEY"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_aws_region(self, env_vars):
        del env_vars["AWS_REGION"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="AWS_REGION"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_sqs_queue_url(self, env_vars):
        del env_vars["SQS_QUEUE_URL"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="SQS_QUEUE_URL"):
                RenderWorkerConfig.from_env()

    def test_from_env_missing_multiple_vars(self, env_vars):
        del env_vars["DATABASE_URL"]
        del env_vars["AWS_REGION"]
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="DATABASE_URL") as exc_info:
                RenderWorkerConfig.from_env()
            assert "AWS_REGION" in str(exc_info.value)

    def test_from_env_empty_string_treated_as_missing(self, env_vars):
        env_vars["DATABASE_URL"] = ""
        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(ConfigError, match="DATABASE_URL"):
                RenderWorkerConfig.from_env()

    def test_config_is_frozen(self, mock_env):
        config = RenderWorkerConfig.from_env()
        with pytest.raises(AttributeError):
            config.DATABASE_URL = "new-value"

    def test_load_config(self, mock_env):
        config = load_config()
        assert isinstance(config, RenderWorkerConfig)
        assert config.DATABASE_URL == "postgresql://user:pass@localhost:5432/testdb"
