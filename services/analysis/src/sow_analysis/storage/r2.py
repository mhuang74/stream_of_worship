"""R2/S3-compatible storage client."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Tuple

import boto3
from botocore.exceptions import ClientError


def parse_s3_url(s3_url: str) -> Tuple[str, str]:
    """Parse s3://bucket/key to (bucket, key).

    Args:
        s3_url: URL in format s3://bucket/path/to/key

    Returns:
        Tuple of (bucket, key)

    Raises:
        ValueError: If URL is not a valid s3:// URL
    """
    match = re.match(r"s3://([^/]+)/(.*)", s3_url)
    if not match:
        raise ValueError(f"Invalid S3 URL format: {s3_url}")
    return match.group(1), match.group(2)


class R2Client:
    """R2/S3 storage client for audio and result files.

    Credentials read from SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY
    (same env-var names as the CLI R2Client).
    """

    def __init__(self, bucket: str, endpoint_url: str):
        """Initialize R2 client.

        Args:
            bucket: R2 bucket name
            endpoint_url: R2 endpoint URL
        """
        access_key = os.environ.get("SOW_R2_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY", "")

        if not access_key or not secret_key:
            raise ValueError(
                "SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY must be set"
            )

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    async def download_audio(self, s3_url: str, local_path: Path) -> None:
        """Download audio from R2 to local path.

        Args:
            s3_url: s3://bucket/{hash}/audio.mp3 format
            local_path: Where to save the file
        """
        bucket, key = parse_s3_url(s3_url)
        loop = asyncio.get_event_loop()

        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        await loop.run_in_executor(
            None, self.s3.download_file, bucket, key, str(local_path)
        )

    async def upload_stems(self, hash_prefix: str, stems_dir: Path) -> str:
        """Upload stem files to R2.

        Uploads: bass.wav, drums.wav, other.wav, vocals.wav

        Args:
            hash_prefix: Content hash prefix for the path
            stems_dir: Directory containing stem files

        Returns:
            s3://bucket/{hash}/stems/ URL
        """
        loop = asyncio.get_event_loop()

        for stem in ("bass", "drums", "other", "vocals"):
            key = f"{hash_prefix}/stems/{stem}.wav"
            stem_path = stems_dir / f"{stem}.wav"
            if stem_path.exists():
                await loop.run_in_executor(
                    None,
                    self.s3.upload_file,
                    str(stem_path),
                    self.bucket,
                    key,
                )

        return f"s3://{self.bucket}/{hash_prefix}/stems/"

    async def upload_analysis_result(self, hash_prefix: str, result: dict) -> str:
        """Upload analysis.json to R2.

        Args:
            hash_prefix: Content hash prefix for the path
            result: Analysis result dictionary

        Returns:
            s3://bucket/{hash}/analysis.json URL
        """
        key = f"{hash_prefix}/analysis.json"
        loop = asyncio.get_event_loop()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(result, f)
            tmp = f.name

        try:
            await loop.run_in_executor(
                None, self.s3.upload_file, tmp, self.bucket, key
            )
        finally:
            os.unlink(tmp)

        return f"s3://{self.bucket}/{key}"

    async def upload_lrc(self, hash_prefix: str, lrc_path: Path) -> str:
        """Upload lyrics.lrc to R2.

        Args:
            hash_prefix: Content hash prefix for the path
            lrc_path: Path to the LRC file

        Returns:
            s3://bucket/{hash}/lyrics.lrc URL
        """
        key = f"{hash_prefix}/lyrics.lrc"
        loop = asyncio.get_event_loop()

        await loop.run_in_executor(
            None, self.s3.upload_file, str(lrc_path), self.bucket, key
        )

        return f"s3://{self.bucket}/{key}"

    async def check_exists(self, s3_url: str) -> bool:
        """Check if an object exists in R2.

        Args:
            s3_url: s3://bucket/key URL

        Returns:
            True if object exists, False otherwise
        """
        bucket, key = parse_s3_url(s3_url)
        loop = asyncio.get_event_loop()

        try:
            await loop.run_in_executor(
                None, lambda: self.s3.head_object(Bucket=bucket, Key=key)
            )
            return True
        except ClientError:
            return False
