from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from sow_render_worker.r2_client import R2Client, create_r2_client_from_env


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


def infer_content_type(key: str) -> str:
    ext = Path(key).suffix.lower()
    return CONTENT_TYPE_MAP.get(ext, "application/octet-stream")


class R2Uploader:
    def __init__(self, r2_client: R2Client | None = None):
        client = r2_client or create_r2_client_from_env()
        self._client = client.client
        self._bucket_name = client.bucket_name

    def upload_file(
        self,
        key: str,
        file_path: str,
        content_type: str | None = None,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        file_path_obj = Path(file_path)
        body = file_path_obj.read_bytes()
        self._put_object(key, body, content_type, cache_control, metadata)
        return key

    def upload_buffer(
        self,
        key: str,
        buffer: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        self._put_object(key, buffer, content_type, cache_control, metadata)
        return key

    def upload_render_artifacts(
        self,
        render_job_id: str,
        artifacts: RenderArtifacts,
    ) -> UploadArtifactsResult:
        result = UploadArtifactsResult()

        if artifacts.mp3_path:
            key = f"renders/{render_job_id}/output.mp3"
            self.upload_file(
                key,
                artifacts.mp3_path,
                content_type="audio/mpeg",
                cache_control="public, max-age=3600",
                metadata={
                    "render-job-id": render_job_id,
                    "content-type": "audio",
                },
            )
            result.mp3_r2_key = key

        if artifacts.mp4_path:
            key = f"renders/{render_job_id}/output.mp4"
            self.upload_file(
                key,
                artifacts.mp4_path,
                content_type="video/mp4",
                cache_control="public, max-age=3600",
                metadata={
                    "render-job-id": render_job_id,
                    "content-type": "video",
                },
            )
            result.mp4_r2_key = key

        if artifacts.chapters is not None:
            key = f"renders/{render_job_id}/chapters.json"
            json_content = json.dumps(
                asdict(artifacts.chapters), indent=2, ensure_ascii=False
            )
            buffer = json_content.encode("utf-8")

            self.upload_buffer(
                key,
                buffer,
                content_type="application/json",
                cache_control="public, max-age=3600",
                metadata={
                    "render-job-id": render_job_id,
                    "content-type": "chapters",
                },
            )
            result.chapters_r2_key = key

        return result

    def delete_render_artifacts(self, render_job_id: str) -> None:
        keys = [
            f"renders/{render_job_id}/output.mp3",
            f"renders/{render_job_id}/output.mp4",
            f"renders/{render_job_id}/chapters.json",
        ]

        for key in keys:
            try:
                self._client.delete_object(Bucket=self._bucket_name, Key=key)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to delete {key}: {e}")

    def _put_object(
        self,
        key: str,
        body: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        ct = content_type or infer_content_type(key)
        cc = cache_control or DEFAULT_CACHE_CONTROL

        put_kwargs: dict[str, Any] = {
            "Bucket": self._bucket_name,
            "Key": key,
            "Body": body,
            "ContentType": ct,
            "CacheControl": cc,
        }

        if metadata:
            put_kwargs["Metadata"] = metadata

        self._client.put_object(**put_kwargs)
