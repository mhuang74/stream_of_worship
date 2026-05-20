from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.r2_client import (
    FILE_TYPE_CONFIGS,
    R2Client,
    create_r2_client_from_env,
)


def _make_r2_client(**overrides) -> R2Client:
    defaults = {
        "endpoint_url": "https://testaccount.r2.cloudflarestorage.com",
        "access_key_id": "test-access-key",
        "secret_access_key": "test-secret-key",
        "bucket_name": "test-bucket",
        "region": "auto",
    }
    defaults.update(overrides)
    with patch("sow_render_worker.r2_client.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_boto3.Config.return_value = MagicMock()
        client = R2Client(**defaults)
        client._client = mock_client
    return client


class TestR2ClientInit:
    def test_creates_boto3_client_with_correct_params(self):
        with patch("sow_render_worker.r2_client.boto3") as mock_boto3, \
             patch("sow_render_worker.r2_client.BotoConfig") as mock_boto_config:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            client = R2Client(
                endpoint_url="https://testaccount.r2.cloudflarestorage.com",
                access_key_id="test-access-key",
                secret_access_key="test-secret-key",
                bucket_name="test-bucket",
            )

            mock_boto3.client.assert_called_once_with(
                "s3",
                endpoint_url="https://testaccount.r2.cloudflarestorage.com",
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

        assert isinstance(result, str)
        assert result == "https://signed-url.example.com/audio.mp3"

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

        client.generate_signed_url("abc123/lyrics.lrc", file_type="lrc")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentType"] == "text/plain; charset=utf-8"

    def test_generates_url_with_custom_expires(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        client.generate_signed_url("key", expires_in_seconds=7200)

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

    def test_unknown_file_type_falls_back_to_audio(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/file"

        client.generate_signed_url("key", file_type="unknown")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["ResponseContentType"] == "audio/mpeg"


class TestGetAudioSignedUrl:
    def test_constructs_audio_key(self):
        client = _make_r2_client()
        client._client.generate_presigned_url.return_value = "https://signed-url.example.com/audio.mp3"

        client.get_audio_signed_url("abc123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "abc123/audio.mp3"

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

        client.get_lrc_signed_url("abc123")

        call_args = client._client.generate_presigned_url.call_args
        params = call_args[1]["Params"]
        assert params["Key"] == "abc123/lyrics.lrc"


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
            "R2_ENDPOINT_URL": "https://envaccount.r2.cloudflarestorage.com",
            "R2_ACCESS_KEY_ID": "env-access-key",
            "R2_SECRET_ACCESS_KEY": "env-secret-key",
            "R2_BUCKET": "env-bucket",
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

    def test_raises_on_missing_env_vars(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="R2 credentials not configured"):
                create_r2_client_from_env()

    def test_raises_on_partial_env_vars(self):
        env = {
            "R2_ENDPOINT_URL": "https://envaccount.r2.cloudflarestorage.com",
            "R2_ACCESS_KEY_ID": "key",
        }
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValueError, match="R2 credentials not configured"):
                create_r2_client_from_env()
