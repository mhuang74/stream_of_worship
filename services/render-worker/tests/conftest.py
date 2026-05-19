import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def env_vars():
    return {
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb",
        "R2_BUCKET": "test-bucket",
        "R2_ENDPOINT_URL": "https://abc123.r2.cloudflarestorage.com",
        "R2_ACCESS_KEY_ID": "test-access-key",
        "R2_SECRET_ACCESS_KEY": "test-secret-key",
        "AWS_REGION": "us-east-1",
        "SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
    }


@pytest.fixture
def mock_env(env_vars):
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
