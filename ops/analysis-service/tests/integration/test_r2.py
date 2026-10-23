"""Tests for R2 storage client."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from sow_analysis.storage.cache import BEAT_GRID_SCHEMA_VERSION
from sow_analysis.storage.r2 import R2Client, parse_s3_url

LRC_PREFIX = "abc123"
OFFICIAL_KEY = f"{LRC_PREFIX}/lyrics.lrc"
BASE_MS = 1_792_712_640_000


def _utc(*args) -> datetime:
    """Timezone-aware UTC datetime from positional datetime() arguments."""
    return datetime(*args, tzinfo=timezone.utc)


def _backup_key(timestamp_ms: int) -> str:
    return f"{LRC_PREFIX}/lyrics.backup.{timestamp_ms}.lrc"


def _not_found_error() -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


def _objects(pairs) -> list[dict]:
    """Paginator ``Contents`` entries from ``(key, LastModified)`` pairs."""
    return [{"Key": key, "LastModified": ts} for key, ts in pairs]


def _paginate(contents):
    """Mocked list_objects_v2 paginator returning one page."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": contents}] if contents else [{}]
    return paginator


def _head_routing(occupied=(), official_exists=True):
    """HEAD answers per key: official 200, ``occupied`` backups 200, else 404."""
    occupied = set(occupied)

    def _head(**kwargs):
        key = kwargs["Key"]
        if key == OFFICIAL_KEY:
            if not official_exists:
                raise _not_found_error()
            return {"ETag": '"etag"'}
        if key in occupied:
            return {"ETag": '"occupied"'}
        raise _not_found_error()

    return _head


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
        mock_boto3.head_object.side_effect = _head_routing(official_exists=False)
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_official_lrc(LRC_PREFIX, lrc_path)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.upload_file.assert_called_once()
        mock_boto3.copy_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_official_lrc_creates_backup_with_epoch_ms_key(self, mock_boto3):
        """Backup key is exactly {hash_prefix}/lyrics.backup.{epoch_ms}.lrc."""
        mock_boto3.head_object.side_effect = _head_routing()
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with patch(
                "sow_analysis.storage.r2.time.time",
                return_value=BASE_MS / 1000 + 0.5,
            ):
                url = await client.upload_official_lrc(LRC_PREFIX, lrc_path)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.copy_object.assert_called_once()
        copy_call = mock_boto3.copy_object.call_args
        assert copy_call.kwargs["Key"] == _backup_key(BASE_MS + 500)
        assert copy_call.kwargs["CopySource"] == {
            "Bucket": "my-bucket",
            "Key": OFFICIAL_KEY,
        }

    @pytest.mark.asyncio
    async def test_upload_official_lrc_stale_etag_raises(self, mock_boto3):
        """upload_official_lrc raises StaleObjectError when ETag mismatches."""
        from sow_analysis.storage.r2 import StaleObjectError

        mock_boto3.head_object.side_effect = _head_routing()
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with pytest.raises(StaleObjectError):
                await client.upload_official_lrc(LRC_PREFIX, lrc_path, expected_etag="oldetag")

    @pytest.mark.asyncio
    async def test_upload_official_lrc_backup_failure_raises(self, mock_boto3):
        """upload_official_lrc raises BackupFailedError when copy_object fails."""
        from sow_analysis.storage.r2 import BackupFailedError

        mock_boto3.head_object.side_effect = _head_routing()
        mock_boto3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with pytest.raises(BackupFailedError):
                await client.upload_official_lrc(LRC_PREFIX, lrc_path)

    @pytest.mark.asyncio
    async def test_upload_official_lrc_skip_backup(self, mock_boto3):
        """upload_official_lrc proceeds even if backup would fail when skip_backup=True."""
        mock_boto3.head_object.side_effect = _head_routing()
        mock_boto3.copy_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError"}}, "CopyObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            url = await client.upload_official_lrc(LRC_PREFIX, lrc_path, skip_backup=True)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        mock_boto3.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_objects_returns_key_and_last_modified(self, mock_boto3):
        """list_objects() exposes LastModified so callers can order by it."""
        ts = _utc(2026, 10, 22, 23, 9, 5)
        mock_boto3.get_paginator.return_value = _paginate(
            [{"Key": f"{LRC_PREFIX}/lyrics.backup.1.lrc", "LastModified": ts}]
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        assert await client.list_objects(f"{LRC_PREFIX}/lyrics.backup.") == [
            (f"{LRC_PREFIX}/lyrics.backup.1.lrc", ts)
        ]

    @pytest.mark.asyncio
    async def test_upload_official_lrc_prunes_old_backups_by_last_modified(self, mock_boto3):
        """Incident regression: prune orders by LastModified, not key integers."""
        newest = _utc(2026, 10, 22, 23, 44, 0)
        newest_ms = int(newest.timestamp() * 1000)
        oldest_key = _backup_key(1792710546483)
        manual_key = _backup_key(357735493)
        mock_boto3.head_object.side_effect = _head_routing()
        mock_boto3.get_paginator.return_value = _paginate(
            _objects(
                [
                    (_backup_key(1792711476891), _utc(2026, 10, 22, 23, 24, 35)),
                    (manual_key, _utc(2026, 10, 22, 23, 27, 44)),
                    (oldest_key, _utc(2026, 10, 22, 23, 9, 5)),
                    (_backup_key(358288869), _utc(2026, 10, 22, 23, 36, 58)),
                    (_backup_key(358431311), _utc(2026, 10, 22, 23, 39, 20)),
                    (_backup_key(newest_ms), newest),
                ]
            )
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with patch("sow_analysis.storage.r2.time.time", return_value=newest.timestamp()):
                url = await client.upload_official_lrc(LRC_PREFIX, lrc_path)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        deleted = [c.kwargs["Key"] for c in mock_boto3.delete_object.call_args_list]
        assert deleted == [oldest_key]
        assert manual_key not in deleted

    @pytest.mark.asyncio
    async def test_prune_delete_failure_is_logged_and_continues(self, mock_boto3, caplog):
        """A failed delete is logged, and the remaining keys are still deleted."""
        pairs = [(_backup_key(1000 + i), _utc(2026, 1, 1, 0, 7 - i)) for i in range(8)]
        mock_boto3.head_object.side_effect = _head_routing()
        mock_boto3.get_paginator.return_value = _paginate(_objects(pairs))
        failing_key = _backup_key(1006)

        def _delete(**kwargs):
            if kwargs["Key"] == failing_key:
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "boom"}}, "DeleteObject"
                )

        mock_boto3.delete_object.side_effect = _delete
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with caplog.at_level(logging.WARNING):
                url = await client.upload_official_lrc(LRC_PREFIX, lrc_path, skip_backup=True)

        assert url == "s3://my-bucket/abc123/lyrics.lrc"
        attempted = [c.kwargs["Key"] for c in mock_boto3.delete_object.call_args_list]
        assert attempted == [_backup_key(1007), failing_key, _backup_key(1005)]
        assert failing_key in caplog.text

    @pytest.mark.asyncio
    async def test_collision_guard_raises_after_ten_occupied_slots(self, mock_boto3):
        """Backup creation never overwrites an occupied key."""
        from sow_analysis.storage.r2 import BackupFailedError

        occupied = {_backup_key(BASE_MS + i) for i in range(10)}
        mock_boto3.head_object.side_effect = _head_routing(occupied=occupied)
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with patch("sow_analysis.storage.r2.time.time", return_value=BASE_MS / 1000):
                with pytest.raises(BackupFailedError, match="free backup key"):
                    await client.upload_official_lrc(LRC_PREFIX, lrc_path)

        mock_boto3.copy_object.assert_not_called()
        mock_boto3.upload_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_collision_guard_uses_next_free_millisecond(self, mock_boto3):
        occupied = {_backup_key(BASE_MS), _backup_key(BASE_MS + 1)}
        mock_boto3.head_object.side_effect = _head_routing(occupied=occupied)
        client = R2Client("my-bucket", "https://r2.example.com")

        with tempfile.TemporaryDirectory() as tmp:
            lrc_path = Path(tmp) / "lyrics.lrc"
            lrc_path.write_text("[00:00.00]Line 1")

            with patch("sow_analysis.storage.r2.time.time", return_value=BASE_MS / 1000):
                await client.upload_official_lrc(LRC_PREFIX, lrc_path)

        copied_key = mock_boto3.copy_object.call_args.kwargs["Key"]
        assert copied_key == _backup_key(BASE_MS + 2)
        assert copied_key not in occupied


class TestBeatGridR2:
    """Tests for beat_grid.json R2 upload/download."""

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

    @pytest.mark.asyncio
    async def test_upload_download_beat_grid_round_trip(self, mock_boto3):
        """Upload then download beat_grid.json returns the same payload."""
        payload = {
            "schema_version": BEAT_GRID_SCHEMA_VERSION,
            "source": "madmom",
            "content_hash": "a" * 64,
            "hash_prefix": "a" * 12,
            "beats": [[0.464, 1], [0.928, 2]],
            "downbeats": [0.464],
            "detected_at": "2026-08-12T12:00:00+00:00",
            "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
        }

        client = R2Client("my-bucket", "https://r2.example.com")

        # Upload writes to a temp file then calls s3.upload_file.
        # Capture the temp file content by intercepting upload_file.
        uploaded_content = {}

        def capture_upload(local_path, bucket, key):
            with open(local_path) as f:
                uploaded_content["json"] = f.read()

        mock_boto3.upload_file.side_effect = capture_upload

        url = await client.upload_beat_grid("abc123def456", payload)
        assert url == "s3://my-bucket/abc123def456/beat_grid.json"

        # Now simulate download: check_exists returns True, download_file writes the captured content.
        mock_boto3.head_object.return_value = {"ETag": '"etag"'}
        mock_boto3.upload_file.side_effect = None

        def mock_download(bucket, key, filepath):
            Path(filepath).write_text(uploaded_content["json"])

        mock_boto3.download_file.side_effect = mock_download

        result = await client.download_beat_grid("abc123def456")
        assert result is not None
        assert result["schema_version"] == BEAT_GRID_SCHEMA_VERSION
        assert result["source"] == "madmom"
        assert result["downbeats"] == [0.464]

    @pytest.mark.asyncio
    async def test_download_beat_grid_schema_mismatch_returns_none(self, mock_boto3):
        """Download with schema_version mismatch returns None."""
        client = R2Client("my-bucket", "https://r2.example.com")

        mock_boto3.head_object.return_value = {"ETag": '"etag"'}

        stale_payload = json.dumps({"schema_version": 99, "source": "madmom"})

        def mock_download(bucket, key, filepath):
            Path(filepath).write_text(stale_payload)

        mock_boto3.download_file.side_effect = mock_download

        result = await client.download_beat_grid("abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_beat_grid_missing_returns_none(self, mock_boto3):
        """Download when object doesn't exist returns None."""
        mock_boto3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadObject"
        )
        client = R2Client("my-bucket", "https://r2.example.com")

        result = await client.download_beat_grid("abc123def456")
        assert result is None
