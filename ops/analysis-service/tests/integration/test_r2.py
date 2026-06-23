"""Tests for R2 storage client."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from sow_analysis.storage.r2 import R2Client, parse_s3_url


class TestParseS3Url:
    """Test S3 URL parsing."""

    def test_valid_s3_url(self):
        """Test parsing valid S3 URL."""
        bucket, key = parse_s3_url("s3://my-bucket/path/to/file.mp3")
        assert bucket == "my-bucket"
        assert key == "path/to/file.mp3"

    def test_s3_url_with_hash(self):
        """Test parsing S3 URL with hash prefix."""
        bucket, key = parse_s3_url("s3://sow-audio/abc123/audio.mp3")
        assert bucket == "sow-audio"
        assert key == "abc123/audio.mp3"

    def test_invalid_url_format(self):
        """Test parsing invalid URL raises error."""
        with pytest.raises(ValueError, match="Invalid S3 URL format"):
            parse_s3_url("https://example.com/file.mp3")

    def test_missing_bucket(self):
        """Test URL without bucket raises error."""
        with pytest.raises(ValueError, match="Invalid S3 URL format"):
            parse_s3_url("s3:///just-a-path")


class TestR2Client:
    """Test R2Client class."""

    @pytest.fixture(autouse=True)
    def mock_env(self):
        """Set up mock environment variables."""
        with patch.dict(
            os.environ,
            {
                "SOW_R2_ACCESS_KEY_ID": "test-access-key",
                "SOW_R2_SECRET_ACCESS_KEY": "test-secret-key",
            },
        ):
            yield

    @pytest.fixture
    def mock_boto3(self):
        """Create mock boto3 client."""
        with patch("sow_analysis.storage.r2.boto3") as mock:
            mock_client = MagicMock()
            mock.client.return_value = mock_client
            yield mock_client

    def test_init_without_credentials(self):
        """Test initialization without credentials raises error."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="SOW_R2_ACCESS_KEY_ID"):
                R2Client("bucket", "https://r2.example.com")

    def test_init_with_credentials(self, mock_boto3):
        """Test initialization with credentials."""
        client = R2Client("my-bucket", "https://r2.example.com")
        assert client.bucket == "my-bucket"

    @pytest.mark.asyncio
    async def test_download_audio(self, mock_boto3):
        """Test downloading audio file."""
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "audio.mp3"

            # Create the file that the mock would have downloaded
            def mock_download(bucket, key, filepath):
                Path(filepath).write_text("fake audio data")

            mock_boto3.download_file.side_effect = mock_download

            await client.download_audio("s3://my-bucket/hash/audio.mp3", local_path)

            assert local_path.exists()
            mock_boto3.download_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_stems(self, mock_boto3):
        """Test uploading stem files."""
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            stems_dir = Path(tmp)
            for stem in ("bass", "drums", "other", "vocals"):
                (stems_dir / f"{stem}.wav").write_text(f"fake {stem}")

            url = await client.upload_stems("abc123", stems_dir)

            assert url == "s3://my-bucket/abc123/stems/"
            assert mock_boto3.upload_file.call_count == 4

    @pytest.mark.asyncio
    async def test_upload_analysis_result(self, mock_boto3):
        """Test uploading analysis result."""
        client = R2Client("my-bucket", "https://r2.example.com")

        result = {"tempo_bpm": 120.0, "key": "C major"}
        url = await client.upload_analysis_result("abc123", result)

        assert url == "s3://my-bucket/abc123/analysis.json"
        mock_boto3.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_lrc(self, mock_boto3):
        """Test uploading LRC file."""
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_lrc("abc123", lrc_path)

            assert url == "s3://my-bucket/abc123/lyrics.lrc"
            mock_boto3.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_exists_true(self, mock_boto3):
        """Test checking if object exists (True)."""
        client = R2Client("my-bucket", "https://r2.example.com")

        exists = await client.check_exists("s3://my-bucket/file.mp3")

        assert exists is True
        mock_boto3.head_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_exists_false(self, mock_boto3):
        """Test checking if object exists (False)."""
        mock_boto3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        exists = await client.check_exists("s3://my-bucket/missing.mp3")

        assert exists is False

    @pytest.mark.asyncio
    async def test_upload_official_lrc_new_object(self, mock_boto3):
        """upload_official_lrc uploads to lyrics.lrc when object doesn't exist."""
        mock_boto3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_official_lrc("abc123", lrc_path)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.upload_file.assert_called_once()
        mock_boto3.copy_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_official_lrc_creates_backup(self, mock_boto3):
        """upload_official_lrc copies existing object to backup before overwrite."""
        mock_boto3.head_object.return_value = {"ETag": '"oldetag"'}
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_official_lrc("abc123", lrc_path)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.copy_object.assert_called_once()
        copy_call = mock_boto3.copy_object.call_args
        assert "lyrics.backup." in copy_call.kwargs["Key"]

    @pytest.mark.asyncio
    async def test_upload_official_lrc_stale_etag_raises(self, mock_boto3):
        """upload_official_lrc raises StaleObjectError when ETag mismatches."""
        from sow_analysis.storage.r2 import StaleObjectError

        mock_boto3.head_object.return_value = {"ETag": '"newetag"'}
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with pytest.raises(StaleObjectError):
                await client.upload_official_lrc("abc123", lrc_path, expected_etag="oldetag")

    @pytest.mark.asyncio
    async def test_upload_official_lrc_backup_failure_raises(self, mock_boto3):
        """upload_official_lrc raises BackupFailedError when copy_object fails."""
        from sow_analysis.storage.r2 import BackupFailedError

        mock_boto3.head_object.return_value = {"ETag": '"etag"'}
        mock_boto3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with pytest.raises(BackupFailedError):
                await client.upload_official_lrc("abc123", lrc_path)

    @pytest.mark.asyncio
    async def test_upload_official_lrc_skip_backup(self, mock_boto3):
        """upload_official_lrc proceeds even if backup would fail when skip_backup=True."""
        mock_boto3.head_object.return_value = {"ETag": '"etag"'}
        mock_boto3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_official_lrc("abc123", lrc_path, skip_backup=True)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_official_lrc_prunes_old_backups(self, mock_boto3):
        """upload_official_lrc deletes oldest backups when count exceeds MAX_BACKUPS_PER_PREFIX."""
        mock_boto3.head_object.return_value = {"ETag": '"etag"'}

        # Mock paginator to return 6 backup objects
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
        mock_boto3.get_paginator.return_value = mock_paginator

        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            await client.upload_official_lrc("abc123", lrc_path)

        mock_boto3.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="abc123/lyrics.backup.1000.lrc"
        )
