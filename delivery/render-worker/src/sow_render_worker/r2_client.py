from typing import Any

import boto3
from botocore.config import Config as BotoConfig


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


class R2Client:
    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        region: str = "auto",
    ):
        self._bucket_name = bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
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
    ) -> str:
        file_config = FILE_TYPE_CONFIGS.get(file_type, FILE_TYPE_CONFIGS["audio"])

        params: dict[str, Any] = {
            "Bucket": self._bucket_name,
            "Key": key,
            "ResponseContentType": content_type or file_config["content_type"],
        }
        if content_disposition:
            params["ResponseContentDisposition"] = content_disposition

        return self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in_seconds,
        )

    def get_audio_signed_url(
        self,
        hash_prefix: str,
        expires_in_seconds: int = 3600,
    ) -> str:
        key = f"{hash_prefix}/audio.mp3"
        return self.generate_signed_url(key, "audio", expires_in_seconds)

    def get_lrc_signed_url(
        self,
        hash_prefix: str,
        expires_in_seconds: int = 3600,
    ) -> str:
        key = f"{hash_prefix}/lyrics.lrc"
        return self.generate_signed_url(key, "lrc", expires_in_seconds)


def create_r2_client_from_env() -> R2Client:
    import os

    endpoint_url = os.environ.get("SOW_R2_ENDPOINT_URL")
    access_key_id = os.environ.get("SOW_R2_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")
    bucket_name = os.environ.get("SOW_R2_BUCKET")

    if not all([endpoint_url, access_key_id, secret_access_key, bucket_name]):
        raise ValueError(
            "R2 credentials not configured. "
            "Set SOW_R2_ENDPOINT_URL, SOW_R2_ACCESS_KEY_ID, SOW_R2_SECRET_ACCESS_KEY, and SOW_R2_BUCKET environment variables."
        )

    return R2Client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket_name=bucket_name,
    )
