from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from sow_render_worker.r2_client import (
    FILE_TYPE_CONFIGS,
    R2Client,
    R2Config,
    SignedUrlResult,
    create_r2_client_from_env,
)


def _make_config(**overrides) -> R2Config:
    defaults = {
        "account_id": "testaccount123",
        "access_key_id": "test-access-key",
        "secret_access_key": "test-secret-key",
        "bucket_name": "test-bucket",
        "region": "auto",
    }
    defaults.update(overrides)
    return R2Config(**defaults)


def _make_r2_client(**config_overrides) -> R2Client:
    with patch("sow_render_worker.r2_client.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_boto3.Config.return_value = MagicMock()
        client = R2Client(_make_config(**config_overrides))
        client._client = mock_client
    return client


class TestR2Config:
    def test_endpoint_url(self):
        config = _make_config()
        assert config.endpoint_url == "https://testaccount123.r2.cloudflarestorage.com"

    def test_endpoint_url_custom_account(self):
        config = _make_config(account_id="myaccount")
        assert config.endpoint_url == "https://myaccount.r2.cloudflarestorage.com"

    def test_default_region(self):
        config = _make_config()
        assert config.region == "auto"


class TestR2ClientInit:
    def test_creates_boto3_client_with_correct_params(self):
        with patch("sow_render_worker.r2_client.boto3") as mock_boto3, \
             patch("sow_render_worker.r2_client.BotoConfig") as mock_boto_config:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            config = _make_config()
            client = R2Client(config)

            mock_boto3.client.assert_called_once_with(
                "s3",
                endpoint_url="https://testaccount123.r2.cloudflarestorage.com",
                region_name="auto",
                aws_access_key_id="test-access-key",
                aws_secret_access_key="test-secret-key",
                config=mock_boto_config.return_value,
            )
            assert client.bucket_name == "test-bucket"

    def test_bucket_name_property(self):
        client = _make_r2_client()
        assert client.bucket_name == "test-bucket"

    def test_client_property(self):
        client = _make_r2_client()
        assert client.client is not None


class TestGenerateSignedUrl:
    def test_generates_url_with_default_params(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/audio.mp3"

        result = client.generate_signed_url("abc123/audio.mp3")

        assert isinstance(result, SignedUrlResult)
        assert result.url == "https://signed-url.example.com/audio.mp3"
        assert result.cache_control == "public, max-age=3600"
        assert isinstance(result.expires_at, datetime)

        call_args = client._client.generate_presigned_url.call_args
        assert call_args[0][0] == "get_object"
        params = call_args[1]["Params"]
        assert params["Bucket"] == "test-bucket"
        assert params["Key"] == "abc123/audio.mp3"
        assert params["ResponseContentType"] == "audio/mpeg"
        assert call_args[1]["ExpiresIn"] == 3600

    def test_generates_url_with_custom_file_type(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/lyrics.lrc"

        result = client.generate_signed_url("abc123/lyrics.lrc", file_type="lrc")

        assert result.cache_control == "public, max-age=86400"
        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentType"] == "text/plain; charset=utf-8"

    def test_generates_url_with_custom_expires(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        result = client.generate_signed_url("key", expires_in_seconds=7200)

        call_args = client._client.generate_presigned_url.call_args
        assert call_args[1]["ExpiresIn"] == 7200

    def test_generates_url_with_custom_content_type(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        client.generate_signed_url("key", content_type="video/mp4")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentType"] == "video/mp4"

    def test_generates_url_with_content_disposition(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        client.generate_signed_url("key", content_disposition='attachment; filename="audio.mp3"')

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentDisposition"] == 'attachment; filename="audio.mp3"'

    def test_expires_at_is_in_future(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        before = datetime.now(timezone.utc)
        result = client.generate_signed_url("key", expires_in_seconds=3600)
        after = datetime.now(timezone.utc)

        assert before + __import__("datetime").timedelta(seconds=3600) <= result.expires_at.replace(
            tzinfo=timezone.utc
        ) or result.expires_at.replace(tzinfo=timezone.utc) <= after + __import__("datetime").timedelta(
            seconds=3600
        )

    def test_unknown_file_type_falls_back_to_audio(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        result = client.generate_signed_url("key", file_type="unknown")

        assert result.cache_control == "public, max-age=3600"
        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentType"] == "audio/mpeg"


class TestGetAudioSignedUrl:
    def test_constructs_audio_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/audio.mp3"

        result = client.get_audio_signed_url("abc123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "abc123/audio.mp3"
        assert result.cache_control == "public, max-age=3600"

    def test_custom_expires(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/audio.mp3"

        client.get_audio_signed_url("abc123", expires_in_seconds=1800)

        call_args = client._client.generate_presigned_url.call_args
        assert call_args[1]["ExpiresIn"] == 1800


class TestGetLrcSignedUrl:
    def test_constructs_lrc_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/lyrics.lrc"

        result = client.get_lrc_signed_url("abc123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "abc123/lyrics.lrc"
        assert result.cache_control == "public, max-age=86400"


class TestGetVideoSignedUrl:
    def test_constructs_video_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/video.mp4"

        result = client.get_video_signed_url("job-123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "renders/job-123/output.mp4"
        assert result.cache_control == "public, max-age=3600"


class TestGetRenderedAudioSignedUrl:
    def test_constructs_rendered_audio_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/audio.mp3"

        result = client.get_rendered_audio_signed_url("job-123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "renders/job-123/output.mp3"


class TestGetChaptersSignedUrl:
    def test_constructs_chapters_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/chapters.json"

        result = client.get_chapters_signed_url("job-123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "renders/job-123/chapters.json"
        assert result.cache_control == "public, max-age=3600"


class TestFileExists:
    def test_returns_true_when_object_exists(self):
        client = _make_r2_client()
        client._client.head_object.return_value = {"ContentLength": 1024}

        assert client.file_exists("abc123/audio.mp3") is True

        client._client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="abc123/audio.mp3"
        )

    def test_returns_false_on_404(self):
        client = _make_r2_client()
        client._client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        assert client.file_exists("nonexistent/key") is False

    def test_returns_false_on_no_such_key(self):
        client = _make_r2_client()
        client._client.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "No such key"}},
            "HeadObject",
        )

        assert client.file_exists("nonexistent/key") is False

    def test_raises_on_other_client_error(self):
        client = _make_r2_client()
        client._client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadObject",
        )

        with pytest.raises(ClientError):
            client.file_exists("forbidden/key")


class TestGetObjectSize:
    def test_returns_content_length(self):
        client = _make_r2_client()
        client._client.head_object.return_value = {"ContentLength": 2048}

        assert client.get_object_size("abc123/audio.mp3") == 2048

    def test_returns_none_when_missing(self):
        client = _make_r2_client()
        client._client.head_object.return_value = {}

        assert client.get_object_size("abc123/audio.mp3") is None

    def test_returns_none_on_client_error(self):
        client = _make_r2_client()
        client._client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        assert client.get_object_size("nonexistent/key") is None


class TestParseS3Url:
    def test_parses_valid_url(self):
        bucket, key = R2Client.parse_s3_url("s3://my-bucket/path/to/file.mp3")
        assert bucket == "my-bucket"
        assert key == "path/to/file.mp3"

    def test_parses_nested_key(self):
        bucket, key = R2Client.parse_s3_url("s3://bucket/a/b/c/d.json")
        assert bucket == "bucket"
        assert key == "a/b/c/d.json"

    def test_rejects_non_s3_url(self):
        with pytest.raises(ValueError, match="Invalid S3 URL format"):
            R2Client.parse_s3_url("https://bucket.s3.amazonaws.com/key")

    def test_rejects_no_key(self):
        with pytest.raises(ValueError, match="Invalid S3 URL format"):
            R2Client.parse_s3_url("s3://bucket/")

    def test_rejects_no_bucket(self):
        with pytest.raises(ValueError, match="Invalid S3 URL format"):
            R2Client.parse_s3_url("s3:///key")


class TestFileTypeConfigs:
    def test_audio_config(self):
        assert FILE_TYPE_CONFIGS["audio"]["content_type"] == "audio/mpeg"
        assert FILE_TYPE_CONFIGS["audio"]["cache_control"] == "public, max-age=3600"

    def test_video_config(self):
        assert FILE_TYPE_CONFIGS["video"]["content_type"] == "video/mp4"
        assert FILE_TYPE_CONFIGS["video"]["cache_control"] == "public, max-age=3600"

    def test_lrc_config(self):
        assert FILE_TYPE_CONFIGS["lrc"]["content_type"] == "text/plain; charset=utf-8"
        assert FILE_TYPE_CONFIGS["lrc"]["cache_control"] == "public, max-age=86400"

    def test_json_config(self):
        assert FILE_TYPE_CONFIGS["json"]["content_type"] == "application/json"
        assert FILE_TYPE_CONFIGS["json"]["cache_control"] == "public, max-age=3600"


class TestCreateR2ClientFromEnv:
    def test_creates_client_from_env_vars(self):
        env = {
            "R2_ACCOUNT_ID": "envaccount",
            "R2_ACCESS_KEY_ID": "env-access-key",
            "R2_SECRET_ACCESS_KEY": "env-secret-key",
            "R2_BUCKET_NAME": "env-bucket",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("sow_render_worker.r2_client.boto3") as mock_boto3:
                mock_boto3.client.return_value = MagicMock()
                mock_boto3.Config.return_value = MagicMock()

                client = create_r2_client_from_env()

                assert client.bucket_name == "env-bucket"
                mock_boto3.client.assert_called_once()
                call_kwargs = mock_boto3.client.call_args[1]
                assert call_kwargs["endpoint_url"] == "https://envaccount.r2.cloudflarestorage.com"

    def test_uses_r2_bucket_as_fallback(self):
        env = {
            "R2_ACCOUNT_ID": "envaccount",
            "R2_ACCESS_KEY_ID": "env-access-key",
            "R2_SECRET_ACCESS_KEY": "env-secret-key",
            "R2_BUCKET": "fallback-bucket",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("sow_render_worker.r2_client.boto3") as mock_boto3:
                mock_boto3.client.return_value = MagicMock()
                mock_boto3.Config.return_value = MagicMock()

                client = create_r2_client_from_env()
                assert client.bucket_name == "fallback-bucket"

    def test_raises_on_missing_env_vars(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="R2 credentials not configured"):
                create_r2_client_from_env()

    def test_raises_on_partial_env_vars(self):
        env = {
            "R2_ACCOUNT_ID": "account",
            "R2_ACCESS_KEY_ID": "key",
        }
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValueError, match="R2 credentials not configured"):
                create_r2_client_from_env()
