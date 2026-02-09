"""Cloudflare R2 storage client.

Provides upload and download of audio assets to Cloudflare R2, which
exposes an S3-compatible API.  Credentials are read from environment
variables so they never appear in config files.
"""

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


class R2Client:
    """Client for Cloudflare R2 (S3-compatible) storage.

    Credentials are read from environment variables at construction time:
        SOW_R2_ACCESS_KEY_ID
        SOW_R2_SECRET_ACCESS_KEY

    Attributes:
        bucket: R2 bucket name
        endpoint_url: R2 endpoint URL
        region: R2 region (typically "auto")
    """

    def __init__(self, bucket: str, endpoint_url: str, region: str = "auto"):
        """Initialize the R2 client.

        Args:
            bucket: R2 bucket name
            endpoint_url: R2 endpoint URL
            region: R2 region

        Raises:
            ValueError: If either credential environment variable is unset
        """
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region

        access_key = os.environ.get("SOW_R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")

        if not access_key or not secret_key:
            raise ValueError(
                "R2 credentials not set. "
                "Set SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY environment variables."
            )

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def upload_audio(self, file_path: Path, hash_prefix: str) -> str:
        """Upload an audio file to R2 under the hash-prefix directory.

        The file is stored at ``{hash_prefix}/audio.mp3`` inside the bucket.

        Args:
            file_path: Local path to the audio file
            hash_prefix: 12-character hash prefix (R2 directory name)

        Returns:
            S3-style URL of the uploaded object
        """
        s3_key = f"{hash_prefix}/audio.mp3"
        self._client.upload_file(str(file_path), self.bucket, s3_key)
        return f"s3://{self.bucket}/{s3_key}"

    def download_audio(self, hash_prefix: str, dest_path: Path) -> Path:
        """Download an audio file from R2.

        Args:
            hash_prefix: 12-character hash prefix
            dest_path: Local path to save the downloaded file

        Returns:
            *dest_path* after the download completes
        """
        s3_key = f"{hash_prefix}/audio.mp3"
        self._client.download_file(self.bucket, s3_key, str(dest_path))
        return dest_path

    def audio_exists(self, hash_prefix: str) -> bool:
        """Check whether an audio file exists in R2.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            True if ``{hash_prefix}/audio.mp3`` exists in the bucket
        """
        try:
            s3_key = f"{hash_prefix}/audio.mp3"
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError:
            return False

    def download_file(self, s3_key: str, dest_path: Path) -> Path:
        """Download a file from R2 by its S3 key.

        Args:
            s3_key: Full S3 key (path within bucket)
            dest_path: Local path to save the downloaded file

        Returns:
            *dest_path* after the download completes
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, s3_key, str(dest_path))
        return dest_path

    def file_exists(self, s3_key: str) -> bool:
        """Check whether a file exists in R2 by its S3 key.

        Args:
            s3_key: Full S3 key (path within bucket)

        Returns:
            True if the object exists in the bucket
        """
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError:
            return False

    @staticmethod
    def parse_s3_url(s3_url: str) -> tuple[str, str]:
        """Parse S3 URL into bucket and key.

        Args:
            s3_url: S3 URL like "s3://bucket/abc123/lyrics.lrc"

        Returns:
            Tuple of (bucket, key)

        Raises:
            ValueError: If URL format is invalid
        """
        if not s3_url.startswith("s3://"):
            raise ValueError(f"Invalid S3 URL format: {s3_url}")
        parts = s3_url[5:].split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URL format: {s3_url}")
        return parts[0], parts[1]
