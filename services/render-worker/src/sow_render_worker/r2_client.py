from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


FILE_TYPE_CONFIGS: dict[str, dict[str, str]] = {
    "audio": {
        "content_type": "audio/mpeg",
        "cache_control": "public, max-age=3600",
    },
    "video": {
        "content_type": "video/mp4",
        "cache_control": "public, max-age=3600",
    },
    "lrc": {
        "content_type": "text/plain; charset=utf-8",
        "cache_control": "public, max-age=86400",
    },
    "json": {
        "content_type": "application/json",
        "cache_control": "public, max-age=3600",
    },
}


@dataclass(frozen=True)
class SignedUrlResult:
    url: str
    expires_at: datetime
    cache_control: str


@dataclass
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    region: str = "auto"

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class R2Client:
    def __init__(self, config: R2Config):
        self._config = config
        self._bucket_name = config.bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
        )

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @property
    def client(self):
        return self._client

    def generate_signed_url(
        self,
        key: str,
        file_type: str = "audio",
        expires_in_seconds: int = 3600,
        content_type: str | None = None,
        content_disposition: str | None = None,
    ) -> SignedUrlResult:
        file_config = FILE_TYPE_CONFIGS.get(file_type, FILE_TYPE_CONFIGS["audio"])

        params: dict[str, Any] = {
            "Bucket": self._bucket_name,
            "Key": key,
            "ResponseContentType": content_type or file_config["content_type"],
        }
        if content_disposition:
            params["ResponseContentDisposition"] = content_disposition

        url = self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in_seconds,
        )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

        return SignedUrlResult(
            url=url,
            expires_at=expires_at,
            cache_control=file_config["cache_control"],
        )

    def get_audio_signed_url(
        self,
        hash_prefix: str,
        expires_in_seconds: int = 3600,
    ) -> SignedUrlResult:
        key = f"{hash_prefix}/audio.mp3"
        return self.generate_signed_url(key, "audio", expires_in_seconds)

    def get_lrc_signed_url(
        self,
        hash_prefix: str,
        expires_in_seconds: int = 3600,
    ) -> SignedUrlResult:
        key = f"{hash_prefix}/lyrics.lrc"
        return self.generate_signed_url(key, "lrc", expires_in_seconds)

    def get_video_signed_url(
        self,
        render_job_id: str,
        expires_in_seconds: int = 3600,
    ) -> SignedUrlResult:
        key = f"renders/{render_job_id}/output.mp4"
        return self.generate_signed_url(key, "video", expires_in_seconds)

    def get_rendered_audio_signed_url(
        self,
        render_job_id: str,
        expires_in_seconds: int = 3600,
    ) -> SignedUrlResult:
        key = f"renders/{render_job_id}/output.mp3"
        return self.generate_signed_url(key, "audio", expires_in_seconds)

    def get_chapters_signed_url(
        self,
        render_job_id: str,
        expires_in_seconds: int = 3600,
    ) -> SignedUrlResult:
        key = f"renders/{render_job_id}/chapters.json"
        return self.generate_signed_url(key, "json", expires_in_seconds)

    def file_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket_name, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise

    def get_object_size(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self._bucket_name, Key=key)
            return response.get("ContentLength")
        except ClientError:
            return None

    @staticmethod
    def parse_s3_url(s3_url: str) -> tuple[str, str]:
        if not s3_url.startswith("s3://"):
            raise ValueError(f"Invalid S3 URL format: {s3_url}")

        rest = s3_url[5:]
        slash_idx = rest.find("/")
        if slash_idx < 1:
            raise ValueError(f"Invalid S3 URL format: {s3_url}")

        bucket = rest[:slash_idx]
        key = rest[slash_idx + 1 :]

        if not key:
            raise ValueError(f"Invalid S3 URL format: {s3_url}")

        return bucket, key


def create_r2_client_from_env() -> R2Client:
    import os

    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket_name = os.environ.get("R2_BUCKET_NAME") or os.environ.get("R2_BUCKET")

    if not all([account_id, access_key_id, secret_access_key, bucket_name]):
        raise ValueError(
            "R2 credentials not configured. "
            "Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME environment variables."
        )

    return R2Client(
        R2Config(
            account_id=account_id,  # type: ignore[arg-type]
            access_key_id=access_key_id,  # type: ignore[arg-type]
            secret_access_key=secret_access_key,  # type: ignore[arg-type]
            bucket_name=bucket_name,  # type: ignore[arg-type]
        )
    )
