"""Tests for R2 storage client."""

import json
from unittest.mock import ANY, MagicMock, patch

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity


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
            config=ANY,
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
    def test_download_calls_s3_with_correct_key(self, mock_boto_client, r2_env, tmp_path):
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


class TestDownloadFile:
    """Tests for R2Client.download_file (generic file download)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_file_downloads_by_s3_key(self, mock_boto_client, r2_env, tmp_path):
        """Verify download_file uses correct key."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        dest = tmp_path / "subdir" / "lyrics.lrc"
        result = client.download_file("abc123def456/lrc/lyrics.lrc", dest)

        assert result == dest
        mock_s3.download_file.assert_called_once_with(
            "sow-audio", "abc123def456/lrc/lyrics.lrc", str(dest)
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_file_creates_parent_directories(self, mock_boto_client, r2_env, tmp_path):
        """Verify mkdir parents."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        dest = tmp_path / "deep" / "nested" / "path" / "file.mp3"
        client.download_file("abc123def456/audio.mp3", dest)

        assert dest.parent.exists()
        assert dest.parent.is_dir()

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_file_returns_dest_path(self, mock_boto_client, r2_env, tmp_path):
        """Verify download_file returns destination path."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        dest = tmp_path / "output.mp3"
        result = client.download_file("abc123def456/stems/vocals.mp3", dest)

        assert result == dest


class TestFileExists:
    """Tests for R2Client.file_exists (generic file existence check)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_file_exists_returns_true_when_exists(self, mock_boto_client, r2_env):
        """Verify head_object success."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.file_exists("abc123def456/lrc/lyrics.lrc")

        assert result is True
        mock_s3.head_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/lrc/lyrics.lrc"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_file_exists_returns_false_when_missing(self, mock_boto_client, r2_env):
        """Verify ClientError handling."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.file_exists("abc123def456/lrc/nonexistent.lrc")

        assert result is False


class TestDeleteFile:
    """Tests for R2Client.delete_file."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_file_calls_delete_object(self, mock_boto_client, r2_env):
        """delete_file calls delete_object with correct key."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        client.delete_file("abc123def456/audio.mp3")

        mock_s3.delete_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/audio.mp3"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_file_raises_on_error(self, mock_boto_client, r2_env):
        """ClientError is raised when deletion fails."""
        mock_s3 = MagicMock()
        mock_s3.delete_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access Denied"}},
            "DeleteObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        with pytest.raises(ClientError):
            client.delete_file("abc123def456/audio.mp3")


class TestLrcExists:
    """Tests for R2Client.lrc_exists."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_lrc_exists_returns_url_when_found(self, mock_boto_client, r2_env):
        """Returns S3 URL when LRC file exists."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.lrc_exists("abc123def456")

        assert result == "s3://sow-audio/abc123def456/lyrics.lrc"
        mock_s3.head_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/lyrics.lrc"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_lrc_exists_returns_none_on_404(self, mock_boto_client, r2_env):
        """Returns None when LRC file not found (404 error)."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.lrc_exists("abc123def456")

        assert result is None

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_lrc_exists_raises_on_permission_error(self, mock_boto_client, r2_env):
        """Raises ClientError on non-404 errors (permission denied)."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access Denied"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        with pytest.raises(ClientError):
            client.lrc_exists("abc123def456")


class TestAnalysisExists:
    """Tests for R2Client.analysis_exists."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_analysis_exists_returns_url_when_found(self, mock_boto_client, r2_env):
        """Returns S3 URL when analysis.json exists."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.analysis_exists("abc123def456")

        assert result == "s3://sow-audio/abc123def456/analysis.json"
        mock_s3.head_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/analysis.json"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_analysis_exists_returns_none_on_404(self, mock_boto_client, r2_env):
        """Returns None when analysis.json not found (404 error)."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.analysis_exists("abc123def456")

        assert result is None

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_analysis_exists_raises_on_permission_error(self, mock_boto_client, r2_env):
        """Raises ClientError on non-404 errors (permission denied)."""
        from unittest.mock import MagicMock

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access Denied"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        with pytest.raises(ClientError):
            client.analysis_exists("abc123def456")


class TestDownloadAnalysisJson:
    """Tests for R2Client.download_analysis_json."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_analysis_json_parses_and_returns_dict(self, mock_boto_client, r2_env):
        """Downloads and parses analysis.json into dictionary."""
        import io

        mock_s3 = MagicMock()
        analysis = {
            "tempo_bpm": 120.0,
            "musical_key": "C",
            "musical_mode": "major",
            "duration_seconds": 180.5,
        }
        mock_response = {"Body": io.BytesIO(json.dumps(analysis).encode())}
        mock_s3.get_object.return_value = mock_response
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.download_analysis_json("abc123def456")

        assert result["tempo_bpm"] == 120.0
        assert result["musical_key"] == "C"
        mock_s3.get_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123def456/analysis.json"
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_download_analysis_json_raises_on_404(self, mock_boto_client, r2_env):
        """Raises ClientError when file not found (including 404)."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "GetObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        with pytest.raises(ClientError):
            client.download_analysis_json("abc123def456")


class TestGetLrcIdentity:
    """Tests for R2Client.get_lrc_identity (stale-session detection)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_returns_identity_when_exists(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {
            "ETag": '"abc123etag"',
            "LastModified": "2024-01-01T00:00:00",
        }
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        identity = client.get_lrc_identity("abc123def456")

        assert identity.exists is True
        assert identity.etag == "abc123etag"
        assert identity.last_modified is not None

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_returns_not_exists_on_404(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        identity = client.get_lrc_identity("abc123def456")

        assert identity.exists is False
        assert identity.etag is None


class TestDownloadLrcContent:
    """Tests for R2Client.download_lrc_content."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_downloads_content(self, mock_boto_client, r2_env):
        import io

        mock_s3 = MagicMock()
        lrc_content = "[00:10.00]Hello\n[00:20.00]World\n"
        mock_response = {"Body": io.BytesIO(lrc_content.encode("utf-8"))}
        mock_s3.get_object.return_value = mock_response
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.download_lrc_content("abc123def456")

        assert result == lrc_content

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_returns_none_on_404(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "GetObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.download_lrc_content("abc123def456")

        assert result is None


class TestUploadBytes:
    """Tests for R2Client.upload_bytes (backup upload)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_uploads_bytes_to_key(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.upload_bytes(
            "abc123def456/backups/lyrics.20260102.lrc",
            b"content",
            content_type="text/plain",
        )

        assert result == "s3://sow-audio/abc123def456/backups/lyrics.20260102.lrc"
        mock_s3.put_object.assert_called_once_with(
            Bucket="sow-audio",
            Key="abc123def456/backups/lyrics.20260102.lrc",
            Body=b"content",
            ContentType="text/plain",
        )
