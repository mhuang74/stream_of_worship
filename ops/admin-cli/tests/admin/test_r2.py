"""Tests for R2 storage client."""

import json
from datetime import datetime
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


class TestPrefixMaintenance:
    """Tests for recording-prefix maintenance helpers."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_validate_recording_hash_prefix_is_strict(self, mock_boto_client, r2_env):
        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        assert client.validate_recording_hash_prefix("abc123def456") == "abc123def456"
        assert client.validate_recording_hash_prefix(" ABC123DEF456/ ") == "abc123def456"
        with pytest.raises(ValueError):
            client.validate_recording_hash_prefix("abc123")
        with pytest.raises(ValueError):
            client.validate_recording_hash_prefix("abc123def45g")
        with pytest.raises(ValueError):
            client.validate_recording_hash_prefix("renders")
        with pytest.raises(ValueError):
            client.validate_recording_hash_prefix(None)

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_list_prefix_uses_100_object_pages(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "abc123def456/audio.mp3",
                        "Size": 100,
                        "LastModified": datetime(2024, 1, 1),
                    },
                    {
                        "Key": "abc123def456/lyrics.lrc",
                        "Size": 20,
                        "LastModified": datetime(2024, 1, 2),
                    },
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.list_prefix("abc123def456")

        assert summary.object_count == 2
        assert summary.total_bytes == 120
        paginator.paginate.assert_called_once_with(
            Bucket="sow-audio",
            Prefix="abc123def456/",
            PaginationConfig={"PageSize": 100},
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_prefix_batches_100_objects_and_missing_is_success(
        self, mock_boto_client, r2_env
    ):
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": f"abc123def456/file-{idx}",
                        "Size": 1,
                        "LastModified": datetime(2024, 1, 1),
                    }
                    for idx in range(205)
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.delete_prefix("abc123def456")

        assert summary.object_count == 205
        assert mock_s3.delete_objects.call_count == 3

        paginator.paginate.return_value = [{}]
        empty = client.delete_prefix("abc123def456")
        assert empty.object_count == 0

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_scan_recording_prefixes_filters_non_hash_and_blacklist(self, mock_boto_client, r2_env):
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "abc123def456/audio.mp3",
                        "Size": 100,
                        "LastModified": datetime(2024, 1, 1),
                    },
                    {
                        "Key": "renders/abc123def456/output.mp4",
                        "Size": 999,
                        "LastModified": datetime(2024, 1, 2),
                    },
                    {
                        "Key": "not-a-hash/file.txt",
                        "Size": 50,
                        "LastModified": datetime(2024, 1, 3),
                    },
                    {
                        "Key": "def123abc456/audio.mp3",
                        "Size": 200,
                        "LastModified": datetime(2024, 1, 4),
                    },
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summaries = client.scan_recording_prefixes(blacklist=["renders/"])

        assert [summary.prefix for summary in summaries] == ["abc123def456", "def123abc456"]
        assert [summary.total_bytes for summary in summaries] == [100, 200]
        paginator.paginate.assert_called_once_with(
            Bucket="sow-audio",
            PaginationConfig={"PageSize": 1000},
        )

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


class TestStemsMaintenance:
    """Tests for R2Client.list_stems and R2Client.delete_stems."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_list_stems_uses_stems_prefix(self, mock_boto_client, r2_env):
        """list_stems paginates with Prefix={hash}/stems/."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "abc123def456/stems/bass.wav",
                        "Size": 49_140_000,
                        "LastModified": datetime(2024, 1, 1),
                    },
                    {
                        "Key": "abc123def456/stems/drums.wav",
                        "Size": 49_140_000,
                        "LastModified": datetime(2024, 1, 2),
                    },
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.list_stems("abc123def456")

        assert summary.object_count == 2
        assert summary.total_bytes == 98_280_000
        assert summary.prefix == "abc123def456/stems"
        paginator.paginate.assert_called_once_with(
            Bucket="sow-audio",
            Prefix="abc123def456/stems/",
            PaginationConfig={"PageSize": 100},
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_list_stems_returns_empty_when_no_stems(self, mock_boto_client, r2_env):
        """list_stems returns zero-count summary when no stems exist."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{}]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.list_stems("abc123def456")

        assert summary.object_count == 0
        assert summary.total_bytes == 0
        assert summary.last_modified is None

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_list_stems_tracks_latest_last_modified(self, mock_boto_client, r2_env):
        """list_stems picks the most recent LastModified across all stem objects."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        latest = datetime(2024, 3, 8, 17, 46, 27)
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "abc123def456/stems/bass.wav",
                        "Size": 100,
                        "LastModified": datetime(2024, 2, 7, 20, 15, 10),
                    },
                    {
                        "Key": "abc123def456/stems/vocals_clean.wav",
                        "Size": 200,
                        "LastModified": latest,
                    },
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.list_stems("abc123def456")

        assert summary.last_modified == latest.isoformat()

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_stems_deletes_only_stems_objects(self, mock_boto_client, r2_env):
        """delete_stems collects keys under {hash}/stems/ and batch-deletes them."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        keys = [
            f"abc123def456/stems/{stem}.wav"
            for stem in ("bass", "drums", "other", "vocals", "vocals_clean")
        ]
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": key,
                        "Size": 49_000_000,
                        "LastModified": datetime(2024, 1, 1),
                    }
                    for key in keys
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.delete_stems("abc123def456")

        assert summary.object_count == 5
        assert summary.total_bytes == 245_000_000
        mock_s3.delete_objects.assert_called_once()
        delete_call = mock_s3.delete_objects.call_args
        assert delete_call.kwargs["Bucket"] == "sow-audio"
        deleted_keys = [obj["Key"] for obj in delete_call.kwargs["Delete"]["Objects"]]
        assert deleted_keys == keys

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_stems_returns_empty_when_no_stems(self, mock_boto_client, r2_env):
        """delete_stems returns zero-count summary when no stems exist."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{}]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.delete_stems("abc123def456")

        assert summary.object_count == 0
        mock_s3.delete_objects.assert_not_called()

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_delete_stems_batches_100_objects(self, mock_boto_client, r2_env):
        """delete_stems splits deletes into 100-object batches."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": f"abc123def456/stems/stem-{idx}.wav",
                        "Size": 1,
                        "LastModified": datetime(2024, 1, 1),
                    }
                    for idx in range(205)
                ]
            }
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        summary = client.delete_stems("abc123def456")

        assert summary.object_count == 205
        assert mock_s3.delete_objects.call_count == 3


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


class TestUploadOfficialLrc:
    """Tests for R2Client.upload_official_lrc."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_uploads_new_object_without_backup(self, mock_boto_client, r2_env, tmp_path):
        """When lyrics.lrc doesn't exist, upload without backup."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        result = client.upload_official_lrc("abc123", lrc_path)

        assert result == "s3://sow-audio/abc123/lyrics.lrc"
        mock_s3.upload_file.assert_called_once()
        mock_s3.copy_object.assert_not_called()

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_creates_backup_before_overwrite(self, mock_boto_client, r2_env, tmp_path):
        """When lyrics.lrc exists, copy to backup before overwrite."""
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"oldetag"'}
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        result = client.upload_official_lrc("abc123", lrc_path)

        assert result == "s3://sow-audio/abc123/lyrics.lrc"
        mock_s3.copy_object.assert_called_once()
        copy_call = mock_s3.copy_object.call_args
        assert "lyrics.backup." in copy_call.kwargs["Key"]

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_stale_etag_raises(self, mock_boto_client, r2_env, tmp_path):
        """When expected_etag doesn't match current ETag, raise StaleObjectError."""
        from stream_of_worship.admin.services.r2 import StaleObjectError

        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"newetag"'}
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        with pytest.raises(StaleObjectError):
            client.upload_official_lrc("abc123", lrc_path, expected_etag="oldetag")

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_backup_failure_raises(self, mock_boto_client, r2_env, tmp_path):
        """When copy_object fails and skip_backup=False, raise BackupFailedError."""
        from stream_of_worship.admin.services.r2 import BackupFailedError

        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"etag"'}
        mock_s3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        with pytest.raises(BackupFailedError):
            client.upload_official_lrc("abc123", lrc_path)

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_skip_backup_ignores_copy_failure(self, mock_boto_client, r2_env, tmp_path):
        """When skip_backup=True, upload proceeds even if backup would fail."""
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"etag"'}
        mock_s3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        result = client.upload_official_lrc("abc123", lrc_path, skip_backup=True)

        assert result == "s3://sow-audio/abc123/lyrics.lrc"
        mock_s3.upload_file.assert_called_once()

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_prunes_old_backups(self, mock_boto_client, r2_env, tmp_path):
        """When backup count exceeds MAX_BACKUPS_PER_PREFIX, oldest are deleted."""
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"etag"'}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "abc123/lyrics.backup.1000.lrc"},
                    {"Key": "abc123/lyrics.backup.2000.lrc"},
                    {"Key": "abc123/lyrics.backup.3000.lrc"},
                    {"Key": "abc123/lyrics.backup.4000.lrc"},
                    {"Key": "abc123/lyrics.backup.5000.lrc"},
                    {"Key": "abc123/lyrics.backup.6000.lrc"},
                ]
            }
        ]
        mock_s3.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        lrc_path = tmp_path / "lyrics.lrc"
        lrc_path.write_text("[00:00.00]Line 1")

        client.upload_official_lrc("abc123", lrc_path)

        mock_s3.delete_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123/lyrics.backup.1000.lrc"
        )


class TestIterObjects:
    """Tests for R2Client.iter_objects (backup-oriented listing)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_iter_objects_paginates_all_objects(self, mock_boto_client, r2_env):
        """iter_objects yields all objects across pages with key/size/etag/last_modified."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "abc123/audio.mp3",
                        "Size": 100,
                        "ETag": '"etag1"',
                        "LastModified": datetime(2024, 1, 1),
                    },
                    {
                        "Key": "abc123/lyrics.lrc",
                        "Size": 20,
                        "ETag": '"etag2"',
                        "LastModified": datetime(2024, 1, 2),
                    },
                ]
            },
            {
                "Contents": [
                    {
                        "Key": "def456/audio.mp3",
                        "Size": 200,
                        "ETag": '"etag3"',
                        "LastModified": datetime(2024, 1, 3),
                    },
                ]
            },
        ]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        objects = list(client.iter_objects())

        assert len(objects) == 3
        assert objects[0] == {
            "key": "abc123/audio.mp3",
            "size": 100,
            "etag": "etag1",
            "last_modified": datetime(2024, 1, 1).isoformat(),
        }
        assert objects[2]["key"] == "def456/audio.mp3"
        assert objects[2]["size"] == 200

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_iter_objects_empty_bucket(self, mock_boto_client, r2_env):
        """iter_objects yields nothing for an empty bucket."""
        mock_s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{}]
        mock_s3.get_paginator.return_value = paginator
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        objects = list(client.iter_objects())

        assert objects == []


class TestGetObjectStream:
    """Tests for R2Client.get_object_stream."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_get_object_stream_returns_body_and_metadata(self, mock_boto_client, r2_env):
        """get_object_stream returns body stream and all metadata fields."""
        import io

        mock_s3 = MagicMock()
        body = io.BytesIO(b"test data")
        mock_s3.get_object.return_value = {
            "Body": body,
            "ContentLength": 9,
            "ETag": '"abc123etag"',
            "LastModified": datetime(2024, 1, 1),
            "ContentType": "audio/mpeg",
            "CacheControl": "max-age=3600",
            "ContentDisposition": "attachment",
            "ContentEncoding": "gzip",
            "Metadata": {"custom": "value"},
        }
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.get_object_stream("abc123/audio.mp3")

        assert result["body"] is body
        assert result["content_length"] == 9
        assert result["etag"] == "abc123etag"
        assert result["content_type"] == "audio/mpeg"
        assert result["cache_control"] == "max-age=3600"
        assert result["content_disposition"] == "attachment"
        assert result["content_encoding"] == "gzip"
        assert result["metadata"] == {"custom": "value"}
        mock_s3.get_object.assert_called_once_with(
            Bucket="sow-audio", Key="abc123/audio.mp3"
        )


class TestHeadObject:
    """Tests for R2Client.head_object (backup-oriented)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_head_object_returns_metadata_when_found(self, mock_boto_client, r2_env):
        """head_object returns size, etag, last_modified, and metadata fields."""
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {
            "ContentLength": 500,
            "ETag": '"etag123"',
            "LastModified": datetime(2024, 1, 1),
            "ContentType": "audio/mpeg",
            "CacheControl": None,
            "ContentDisposition": None,
            "ContentEncoding": None,
            "Metadata": {"key": "val"},
        }
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.head_object("abc123/audio.mp3")

        assert result is not None
        assert result["size"] == 500
        assert result["etag"] == "etag123"
        assert result["content_type"] == "audio/mpeg"
        assert result["metadata"] == {"key": "val"}

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_head_object_returns_none_on_404(self, mock_boto_client, r2_env):
        """head_object returns None on 404/NoSuchKey."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        result = client.head_object("abc123/nonexistent")

        assert result is None

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_head_object_raises_on_non_404(self, mock_boto_client, r2_env):
        """head_object raises ClientError on non-404 errors."""
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access Denied"}},
            "HeadObject",
        )
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")

        with pytest.raises(ClientError):
            client.head_object("abc123/audio.mp3")


class TestUploadFileobj:
    """Tests for R2Client.upload_fileobj (backup-oriented upload)."""

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_upload_fileobj_with_extra_args(self, mock_boto_client, r2_env):
        """upload_fileobj passes ExtraArgs for metadata preservation."""
        import io
        from unittest.mock import ANY

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        fileobj = io.BytesIO(b"test data")
        extra_args = {
            "ContentType": "audio/mpeg",
            "Metadata": {"custom": "val"},
        }

        result = client.upload_fileobj(fileobj, "abc123/audio.mp3", extra_args=extra_args)

        assert result == "s3://sow-audio/abc123/audio.mp3"
        mock_s3.upload_fileobj.assert_called_once_with(
            Fileobj=fileobj,
            Bucket="sow-audio",
            Key="abc123/audio.mp3",
            ExtraArgs=extra_args,
            Config=ANY,
        )

    @patch("stream_of_worship.admin.services.r2.boto3.client")
    def test_upload_fileobj_without_extra_args(self, mock_boto_client, r2_env):
        """upload_fileobj works without extra_args."""
        import io
        from unittest.mock import ANY

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        client = R2Client(bucket="sow-audio", endpoint_url="https://r2.example.com")
        fileobj = io.BytesIO(b"test data")

        result = client.upload_fileobj(fileobj, "abc123/audio.mp3")

        assert result == "s3://sow-audio/abc123/audio.mp3"
        mock_s3.upload_fileobj.assert_called_once_with(
            Fileobj=fileobj,
            Bucket="sow-audio",
            Key="abc123/audio.mp3",
            ExtraArgs={},
            Config=ANY,
        )
