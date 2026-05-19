from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import ClientError

from sow_render_worker.r2_client import R2Client, R2Config, create_r2_client_from_env


CONTENT_TYPE_MAP: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".json": "application/json",
    ".lrc": "text/plain; charset=utf-8",
    ".txt": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

DEFAULT_CACHE_CONTROL = "public, max-age=3600"


@dataclass(frozen=True)
class UploadOptions:
    content_type: str | None = None
    cache_control: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class UploadResult:
    key: str
    size_bytes: int
    etag: str | None
    uploaded_at: datetime


@dataclass
class RenderArtifacts:
    mp3_path: str | None = None
    mp4_path: str | None = None
    chapters: Any = None


@dataclass
class UploadArtifactsResult:
    mp3_r2_key: str | None = None
    mp4_r2_key: str | None = None
    chapters_r2_key: str | None = None
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


UploadProgressCallback = Callable[[str, int, int], None]


def infer_content_type(key: str) -> str:
    ext = Path(key).suffix.lower()
    return CONTENT_TYPE_MAP.get(ext, "application/octet-stream")


class R2Uploader:
    def __init__(self, config: R2Config | None = None):
        r2_client = R2Client(config) if config else create_r2_client_from_env()
        self._client = r2_client.client
        self._bucket_name = r2_client.bucket_name

    def upload_file(
        self,
        key: str,
        file_path: str,
        options: UploadOptions | None = None,
    ) -> UploadResult:
        options = options or UploadOptions()
        file_path_obj = Path(file_path)
        body = file_path_obj.read_bytes()
        size_bytes = file_path_obj.stat().st_size
        return self._put_object(key, body, size_bytes, options)

    def upload_buffer(
        self,
        key: str,
        buffer: bytes,
        options: UploadOptions | None = None,
    ) -> UploadResult:
        options = options or UploadOptions()
        return self._put_object(key, buffer, len(buffer), options)

    def upload_render_artifacts(
        self,
        render_job_id: str,
        artifacts: RenderArtifacts,
        progress_callback: UploadProgressCallback | None = None,
    ) -> UploadArtifactsResult:
        result = UploadArtifactsResult()

        if artifacts.mp3_path:
            key = f"renders/{render_job_id}/output.mp3"
            file_path_obj = Path(artifacts.mp3_path)
            size = file_path_obj.stat().st_size

            if progress_callback:
                progress_callback("mp3", 0, size)

            self.upload_file(
                key,
                artifacts.mp3_path,
                UploadOptions(
                    content_type="audio/mpeg",
                    cache_control="public, max-age=3600",
                    metadata={
                        "render-job-id": render_job_id,
                        "content-type": "audio",
                    },
                ),
            )

            if progress_callback:
                progress_callback("mp3", size, size)

            result.mp3_r2_key = key

        if artifacts.mp4_path:
            key = f"renders/{render_job_id}/output.mp4"
            file_path_obj = Path(artifacts.mp4_path)
            size = file_path_obj.stat().st_size

            if progress_callback:
                progress_callback("mp4", 0, size)

            self.upload_file(
                key,
                artifacts.mp4_path,
                UploadOptions(
                    content_type="video/mp4",
                    cache_control="public, max-age=3600",
                    metadata={
                        "render-job-id": render_job_id,
                        "content-type": "video",
                    },
                ),
            )

            if progress_callback:
                progress_callback("mp4", size, size)

            result.mp4_r2_key = key

        if artifacts.chapters is not None:
            key = f"renders/{render_job_id}/chapters.json"
            json_content = json.dumps(
                _chapters_to_dict(artifacts.chapters), indent=2, ensure_ascii=False
            )
            buffer = json_content.encode("utf-8")

            if progress_callback:
                progress_callback("chapters", 0, len(buffer))

            self.upload_buffer(
                key,
                buffer,
                UploadOptions(
                    content_type="application/json",
                    cache_control="public, max-age=3600",
                    metadata={
                        "render-job-id": render_job_id,
                        "content-type": "chapters",
                    },
                ),
            )

            if progress_callback:
                progress_callback("chapters", len(buffer), len(buffer))

            result.chapters_r2_key = key

        return result

    def file_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket_name, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise

    def delete_file(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket_name, Key=key)

    def delete_render_artifacts(self, render_job_id: str) -> None:
        keys = [
            f"renders/{render_job_id}/output.mp3",
            f"renders/{render_job_id}/output.mp4",
            f"renders/{render_job_id}/chapters.json",
        ]

        for key in keys:
            try:
                if self.file_exists(key):
                    self.delete_file(key)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to delete {key}: {e}")

    def _put_object(
        self,
        key: str,
        body: bytes,
        size_bytes: int,
        options: UploadOptions,
    ) -> UploadResult:
        content_type = options.content_type or infer_content_type(key)
        cache_control = options.cache_control or DEFAULT_CACHE_CONTROL

        put_kwargs: dict[str, Any] = {
            "Bucket": self._bucket_name,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "CacheControl": cache_control,
        }

        if options.metadata:
            put_kwargs["Metadata"] = options.metadata

        response = self._client.put_object(**put_kwargs)

        return UploadResult(
            key=key,
            size_bytes=size_bytes,
            etag=response.get("ETag"),
            uploaded_at=datetime.now(timezone.utc),
        )


def _chapters_to_dict(chapters: Any) -> Any:
    if hasattr(chapters, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(chapters)
    if isinstance(chapters, dict):
        return chapters
    return chapters
