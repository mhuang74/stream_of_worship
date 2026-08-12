"""Tests for R2Client component result download/upload helpers."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from stream_of_worship.admin.services.r2 import R2Client


@pytest.fixture
def r2_env(monkeypatch):
    monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret-key")


def _make_r2_client(r2_env):
    with patch("stream_of_worship.admin.services.r2.boto3.client"):
        return R2Client(bucket="test-bucket", endpoint_url="https://test.r2.com")


class TestDownloadComponentResult:
    """Tests for R2Client.download_component_result."""

    def test_404_returns_none(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        error = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "GetObject",
        )
        mock_s3.get_object.side_effect = error
        assert client.download_component_result("abc123def456") is None

    def test_no_such_key_returns_none(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        error = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
            "GetObject",
        )
        mock_s3.get_object.side_effect = error
        assert client.download_component_result("abc123def456") is None

    def test_happy_path_returns_parsed_dict(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        payload = {"schema_version": 2, "components": [{"role": "entry"}]}
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(payload).encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        result = client.download_component_result("abc123def456")
        assert result == payload
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="abc123def456/components.json"
        )

    def test_non_404_client_error_reraises(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
            "GetObject",
        )
        mock_s3.get_object.side_effect = error
        with pytest.raises(ClientError):
            client.download_component_result("abc123def456")


class TestUploadComponentResult:
    """Tests for R2Client.upload_component_result."""

    def test_calls_put_object_with_correct_key_and_body(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        payload = {"schema_version": 2, "components": []}
        url = client.upload_component_result("abc123def456", payload)
        assert url == "s3://test-bucket/abc123def456/components.json"
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "abc123def456/components.json"
        assert call_kwargs["ContentType"] == "application/json"
        # Body should be valid JSON matching the payload
        body = json.loads(call_kwargs["Body"].decode("utf-8"))
        assert body == payload

    def test_payload_roundtrip_equality(self, r2_env):
        client = _make_r2_client(r2_env)
        mock_s3 = MagicMock()
        client._client = mock_s3
        payload = {
            "schema_version": 2,
            "content_hash": "abc123",
            "hash_prefix": "abc123def456",
            "component_source": "component_analysis",
            "components": [
                {"role": "entry", "theme": "讚美"},
                {"role": "exit", "theme": "感恩"},
            ],
        }
        client.upload_component_result("abc123def456", payload)
        call_kwargs = mock_s3.put_object.call_args.kwargs
        body = json.loads(call_kwargs["Body"].decode("utf-8"))
        assert body == payload
        # Ensure non-ASCII (Chinese) is preserved
        assert body["components"][0]["theme"] == "讚美"
