import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from sow_render_worker.chapters import Chapter, ChaptersManifest
from sow_render_worker.r2_client import R2Client
from sow_render_worker.uploader import (
    CONTENT_TYPE_MAP,
    DEFAULT_CACHE_CONTROL,
    R2Uploader,
    RenderArtifacts,
    UploadArtifactsResult,
    infer_content_type,
)


def _make_r2_client_mock() -> MagicMock:
    mock_r2 = MagicMock(spec=R2Client)
    mock_s3_client = MagicMock()
    mock_r2.client = mock_s3_client
    mock_r2.bucket_name = "test-bucket"
    return mock_r2


def _make_uploader() -> R2Uploader:
    mock_r2 = _make_r2_client_mock()
    uploader = R2Uploader(r2_client=mock_r2)
    uploader._client = mock_r2.client
    uploader._bucket_name = "test-bucket"
    return uploader


class TestInferContentType:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("file.mp3", "audio/mpeg"),
            ("file.MP3", "audio/mpeg"),
            ("file.mp4", "video/mp4"),
            ("file.json", "application/json"),
            ("file.lrc", "text/plain; charset=utf-8"),
            ("file.txt", "text/plain"),
            ("file.jpg", "image/jpeg"),
            ("file.jpeg", "image/jpeg"),
            ("file.png", "image/png"),
            ("file.gif", "image/gif"),
            ("file.webp", "image/webp"),
        ],
    )
    def test_known_extensions(self, key, expected):
        assert infer_content_type(key) == expected

    def test_unknown_extension_returns_octet_stream(self):
        assert infer_content_type("file.xyz") == "application/octet-stream"

    def test_no_extension_returns_octet_stream(self):
        assert infer_content_type("noext") == "application/octet-stream"

    def test_nested_path(self):
        assert infer_content_type("renders/abc123/output.mp3") == "audio/mpeg"


class TestContentTypeMap:
    def test_all_expected_extensions_present(self):
        expected_keys = {".mp3", ".mp4", ".json", ".lrc", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".webp"}
        assert set(CONTENT_TYPE_MAP.keys()) == expected_keys


class TestUploadFile:
    def test_uploads_file_with_default_options(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"abc123"'}

        file_path = tmp_path / "output.mp3"
        file_path.write_bytes(b"fake audio data")

        result = uploader.upload_file("renders/job1/output.mp3", str(file_path))

        assert result == "renders/job1/output.mp3"

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "renders/job1/output.mp3"
        assert call_kwargs["Body"] == b"fake audio data"
        assert call_kwargs["ContentType"] == "audio/mpeg"
        assert call_kwargs["CacheControl"] == "public, max-age=3600"

    def test_uploads_file_with_custom_options(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"def456"'}

        file_path = tmp_path / "output.mp4"
        file_path.write_bytes(b"fake video data")

        uploader.upload_file(
            "renders/job1/output.mp4",
            str(file_path),
            content_type="video/mp4",
            cache_control="no-cache",
            metadata={"render-job-id": "job1"},
        )

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "video/mp4"
        assert call_kwargs["CacheControl"] == "no-cache"
        assert call_kwargs["Metadata"] == {"render-job-id": "job1"}

    def test_uses_inferred_content_type_when_not_specified(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"ghi789"'}

        file_path = tmp_path / "data.json"
        file_path.write_bytes(b'{"key": "value"}')

        uploader.upload_file("data.json", str(file_path))

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "application/json"

    def test_no_metadata_key_when_metadata_is_none(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"abc"'}

        file_path = tmp_path / "output.mp3"
        file_path.write_bytes(b"data")

        uploader.upload_file("output.mp3", str(file_path))

        call_kwargs = uploader._client.put_object.call_args[1]
        assert "Metadata" not in call_kwargs


class TestUploadBuffer:
    def test_uploads_buffer_with_default_options(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"buf123"'}

        buffer = b"buffer content"
        result = uploader.upload_buffer("test/key.json", buffer)

        assert result == "test/key.json"

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["Body"] == b"buffer content"
        assert call_kwargs["ContentType"] == "application/json"

    def test_uploads_buffer_with_custom_options(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"buf456"'}

        buffer = b"custom buffer"
        uploader.upload_buffer(
            "test/key.mp3",
            buffer,
            content_type="audio/mpeg",
            cache_control="no-cache",
        )

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "audio/mpeg"
        assert call_kwargs["CacheControl"] == "no-cache"

    def test_empty_buffer(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"empty"'}

        uploader.upload_buffer("empty.txt", b"")

        call_kwargs = uploader._client.put_object.call_args[1]
        assert call_kwargs["Body"] == b""


class TestUploadRenderArtifacts:
    def test_uploads_all_artifacts(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        mp3_path = tmp_path / "output.mp3"
        mp3_path.write_bytes(b"fake mp3 audio data here")
        mp4_path = tmp_path / "output.mp4"
        mp4_path.write_bytes(b"fake mp4 video data here!!")

        artifacts = RenderArtifacts(
            mp3_path=str(mp3_path),
            mp4_path=str(mp4_path),
            chapters=ChaptersManifest(chapters=(), total_duration_seconds=180.0, generated_at=""),
        )

        result = uploader.upload_render_artifacts("job-123", artifacts)

        assert result.mp3_r2_key == "renders/job-123/output.mp3"
        assert result.mp4_r2_key == "renders/job-123/output.mp4"
        assert result.chapters_r2_key == "renders/job-123/chapters.json"
        assert isinstance(result.uploaded_at, datetime)

        calls = uploader._client.put_object.call_args_list
        assert len(calls) == 3

        mp3_call = calls[0]
        assert mp3_call[1]["Key"] == "renders/job-123/output.mp3"
        assert mp3_call[1]["ContentType"] == "audio/mpeg"
        assert mp3_call[1]["Metadata"]["render-job-id"] == "job-123"
        assert mp3_call[1]["Metadata"]["content-type"] == "audio"

        mp4_call = calls[1]
        assert mp4_call[1]["Key"] == "renders/job-123/output.mp4"
        assert mp4_call[1]["ContentType"] == "video/mp4"
        assert mp4_call[1]["Metadata"]["render-job-id"] == "job-123"
        assert mp4_call[1]["Metadata"]["content-type"] == "video"

        chapters_call = calls[2]
        assert chapters_call[1]["Key"] == "renders/job-123/chapters.json"
        assert chapters_call[1]["ContentType"] == "application/json"
        assert chapters_call[1]["Metadata"]["render-job-id"] == "job-123"
        assert chapters_call[1]["Metadata"]["content-type"] == "chapters"

    def test_uploads_only_mp3(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        mp3_path = tmp_path / "output.mp3"
        mp3_path.write_bytes(b"mp3 data")

        artifacts = RenderArtifacts(mp3_path=str(mp3_path))

        result = uploader.upload_render_artifacts("job-456", artifacts)

        assert result.mp3_r2_key == "renders/job-456/output.mp3"
        assert result.mp4_r2_key is None
        assert result.chapters_r2_key is None
        assert uploader._client.put_object.call_count == 1

    def test_uploads_only_mp4(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        mp4_path = tmp_path / "output.mp4"
        mp4_path.write_bytes(b"mp4 data")

        artifacts = RenderArtifacts(mp4_path=str(mp4_path))

        result = uploader.upload_render_artifacts("job-789", artifacts)

        assert result.mp3_r2_key is None
        assert result.mp4_r2_key == "renders/job-789/output.mp4"
        assert result.chapters_r2_key is None

    def test_uploads_only_chapters(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        artifacts = RenderArtifacts(chapters=ChaptersManifest(chapters=(), total_duration_seconds=0.0, generated_at=""))

        result = uploader.upload_render_artifacts("job-ch", artifacts)

        assert result.mp3_r2_key is None
        assert result.mp4_r2_key is None
        assert result.chapters_r2_key == "renders/job-ch/chapters.json"

        call_kwargs = uploader._client.put_object.call_args[1]
        body = call_kwargs["Body"]
        parsed = json.loads(body)
        assert parsed["chapters"] == []
        assert parsed["total_duration_seconds"] == 0.0

    def test_no_artifacts_returns_empty_result(self):
        uploader = _make_uploader()

        artifacts = RenderArtifacts()
        result = uploader.upload_render_artifacts("job-empty", artifacts)

        assert result.mp3_r2_key is None
        assert result.mp4_r2_key is None
        assert result.chapters_r2_key is None
        uploader._client.put_object.assert_not_called()

    def test_chapters_json_is_utf8_encoded(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        artifacts = RenderArtifacts(chapters=ChaptersManifest(chapters=(Chapter(position=1, song_title="中文標題", start_seconds=0.0, end_seconds=60.0),), total_duration_seconds=60.0, generated_at=""))
        uploader.upload_render_artifacts("job-cn", artifacts)

        call_kwargs = uploader._client.put_object.call_args[1]
        body = call_kwargs["Body"]
        parsed = json.loads(body)
        assert parsed["chapters"][0]["song_title"] == "中文標題"

    def test_chapters_dataclass_serialization(self):
        from dataclasses import dataclass

        @dataclass
        class FakeChapter:
            title: str
            start: float

        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"etag"'}

        artifacts = RenderArtifacts(chapters=FakeChapter(title="Test", start=0.0))
        uploader.upload_render_artifacts("job-dc", artifacts)

        call_kwargs = uploader._client.put_object.call_args[1]
        body = call_kwargs["Body"]
        parsed = json.loads(body)
        assert parsed["title"] == "Test"
        assert parsed["start"] == 0.0


class TestDeleteRenderArtifacts:
    def test_deletes_all_artifacts(self):
        uploader = _make_uploader()

        uploader.delete_render_artifacts("job-123")

        assert uploader._client.delete_object.call_count == 3
        deleted_keys = {
            call[1]["Key"] for call in uploader._client.delete_object.call_args_list
        }
        assert deleted_keys == {
            "renders/job-123/output.mp3",
            "renders/job-123/output.mp4",
            "renders/job-123/chapters.json",
        }

    def test_continues_on_individual_delete_failure(self):
        uploader = _make_uploader()
        uploader._client.delete_object.side_effect = [
            None,
            Exception("Network error"),
            None,
        ]

        uploader.delete_render_artifacts("job-partial")

        assert uploader._client.delete_object.call_count == 3

    def test_correct_key_prefix(self):
        uploader = _make_uploader()

        uploader.delete_render_artifacts("my-job-id")

        keys = [call[1]["Key"] for call in uploader._client.delete_object.call_args_list]
        assert all(k.startswith("renders/my-job-id/") for k in keys)


class TestRenderArtifacts:
    def test_defaults(self):
        artifacts = RenderArtifacts()
        assert artifacts.mp3_path is None
        assert artifacts.mp4_path is None
        assert artifacts.chapters is None

    def test_with_values(self, tmp_path):
        mp3 = tmp_path / "out.mp3"
        mp3.write_bytes(b"data")
        artifacts = RenderArtifacts(
            mp3_path=str(mp3),
            mp4_path=None,
            chapters=ChaptersManifest(chapters=(), total_duration_seconds=0.0, generated_at=""),
        )
        assert artifacts.mp3_path == str(mp3)
        assert artifacts.chapters == ChaptersManifest(chapters=(), total_duration_seconds=0.0, generated_at="")


class TestUploadArtifactsResult:
    def test_defaults(self):
        result = UploadArtifactsResult()
        assert result.mp3_r2_key is None
        assert result.mp4_r2_key is None
        assert result.chapters_r2_key is None
        assert isinstance(result.uploaded_at, datetime)


class TestR2UploaderInit:
    def test_creates_from_r2_client(self):
        mock_r2 = _make_r2_client_mock()

        uploader = R2Uploader(r2_client=mock_r2)

        assert uploader._client is mock_r2.client
        assert uploader._bucket_name == "test-bucket"

    def test_creates_from_env(self):
        with patch("sow_render_worker.uploader.create_r2_client_from_env") as mock_factory:
            mock_r2 = MagicMock()
            mock_r2.client = MagicMock()
            mock_r2.bucket_name = "env-bucket"
            mock_factory.return_value = mock_r2

            uploader = R2Uploader()

            mock_factory.assert_called_once()
            assert uploader._bucket_name == "env-bucket"


class TestKeyConstruction:
    def test_mp3_key_format(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"e"'}

        mp3_path = tmp_path / "output.mp3"
        mp3_path.write_bytes(b"data")

        artifacts = RenderArtifacts(mp3_path=str(mp3_path))
        result = uploader.upload_render_artifacts("abc-123", artifacts)

        assert result.mp3_r2_key == "renders/abc-123/output.mp3"

    def test_mp4_key_format(self, tmp_path):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"e"'}

        mp4_path = tmp_path / "output.mp4"
        mp4_path.write_bytes(b"data")

        artifacts = RenderArtifacts(mp4_path=str(mp4_path))
        result = uploader.upload_render_artifacts("xyz-456", artifacts)

        assert result.mp4_r2_key == "renders/xyz-456/output.mp4"

    def test_chapters_key_format(self):
        uploader = _make_uploader()
        uploader._client.put_object.return_value = {"ETag": '"e"'}

        artifacts = RenderArtifacts(chapters=ChaptersManifest(chapters=(), total_duration_seconds=0.0, generated_at=""))
        result = uploader.upload_render_artifacts("def-789", artifacts)

        assert result.chapters_r2_key == "renders/def-789/chapters.json"
