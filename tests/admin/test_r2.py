"""Tests for R2 storage client."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from stream_of_worship.admin.services.r2 import R2Client


@pytest.fixture
def r2_env(monkeypatch):
    """Set both required R2 credential environment variables."""
    monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret-key")


class TestR2ClientInit:
    """Tests for R2Client construction."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_creates_s3_client_with_credentials(self, mock_boto_client, r2_env):
        """boto3.client is called with the correct parameters."""
        R2Client(
            bucket="test-bucket",
            endpoint_url="https://test.r2.cloudflarestorage.com",
            region="auto",
        )

        mock_boto_client.assert_called_once_with(
            "s3",
            endpoint_url="https://test.r2.cloudflarestorage.com",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            region_name="auto",
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_stores_bucket_and_endpoint(self, mock_boto_client, r2_env):
        """Bucket, endpoint, and region are stored as attributes."""
        client = R2Client(
            bucket="my-bucket",
            endpoint_url="https://endpoint.example.com",
            region="us-east-1",
        )
        assert client.bucket == "my-bucket"
        assert client.endpoint_url == "https://endpoint.example.com"
        assert client.region == "us-east-1"

    def test_missing_access_key_raises(self, monkeypatch):
        """Raises ValueError when SOW_R2_ACCESS_KEY_ID is unset."""
        monkeypatch.delenv("SOW_R2_ACCESS_KEY_ID", raising=False)
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "secret")

        with pytest.raises(ValueError, match="R2 credentials not set"):
            R2Client(bucket="b", endpoint_url="http://localhost")

    def test_missing_secret_key_raises(self, monkeypatch):
        """Raises ValueError when SOW_R2_SECRET_ACCESS_KEY is unset."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "access")
        monkeypatch.delenv("SOW_R2_SECRET_ACCESS_KEY", raising=False)

        with pytest.raises(ValueError, match="R2 credentials not set"):
            R2Client(bucket="b", endpoint_url="http://localhost")

    def test_missing_both_keys_raises(self, monkeypatch):
        """Raises ValueError when both credential vars are unset."""
        monkeypatch.delenv("SOW_R2_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("SOW_R2_SECRET_ACCESS_KEY", raising=False)

        with pytest.raises(ValueError, match="R2 credentials not set"):
            R2Client(bucket="b", endpoint_url="http://localhost")


class TestUploadAudio:
    """Tests for R2Client.upload_audio."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_upload_calls_s3_with_correct_key(self, mock_boto_client, r2_env, tmp_path):
        """upload_file is called with the right bucket and S3 key."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        audio_file = tmp_path / "song.mp3"
        audio_file.write_bytes(b"fake audio")

        url = client.upload_audio(audio_file, "abc123def456")

        assert url == "s3://sow-audio/abc123def456/audio.mp3"
        mock_s3.upload_file.assert_called_once_with(
            str(audio_file), "sow-audio", "abc123def456/audio.mp3"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_upload_returns_s3_url(self, mock_boto_client, r2_env, tmp_path):
        """Returned URL follows the s3://bucket/prefix/audio.mp3 format."""
        mock_boto_client.return_value = MagicMock()
        client = R2Client(bucket="my-bucket", endpoint_url="https://r2.example.com")

        audio_file = tmp_path / "x.mp3"
        audio_file.write_bytes(b"x")

        url = client.upload_audio(audio_file, "111111111111")
        assert url == "s3://my-bucket/111111111111/audio.mp3"


class TestDownloadAudio:
    """Tests for R2Client.download_audio."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_calls_s3_with_correct_key(
        self, mock_boto_client, r2_env, tmp_path
    ):
        """download_file is called with the right parameters."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        dest = tmp_path / "downloaded.mp3"
        result = client.download_audio("abc123def456", dest)

        assert result == dest
        mock_s3.download_file.assert_called_once_with(
            "sow-audio", "abc123def456/audio.mp3", str(dest)
        )


class TestAudioExists:
    """Tests for R2Client.audio_exists."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_returns_true_when_object_exists(self, mock_boto_client, r2_env):
        """head_object succeeding means the audio file exists."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.audio_exists("abc123def456")

        assert result is True
        mock_s3.head_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/audio.mp3"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_returns_false_on_client_error(self, mock_boto_client, r2_env):
        """head_object raising ClientError (404) means the file is absent."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.audio_exists("abc123def456")

        assert result is False
