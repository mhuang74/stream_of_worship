"""Audio download from R2/S3 and duration validation."""

import hashlib
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from pydub import AudioSegment

from ..config import settings

logger = logging.getLogger(__name__)


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds
    """
    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0


def validate_audio_duration(audio_path: Path, max_seconds: float = 300.0) -> float:
    """Validate audio duration against limit.

    Args:
        audio_path: Path to audio file
        max_seconds: Maximum allowed duration (default: 5 minutes)

    Returns:
        Audio duration in seconds

    Raises:
        ValueError: If audio duration exceeds limit
    """
    duration = get_audio_duration(audio_path)
    if duration > max_seconds:
        raise ValueError(
            f"Audio duration ({duration:.1f}s) exceeds {max_seconds/60:.0f} minute limit"
        )
    return duration


def download_audio(url: str, cache_dir: Path) -> Path:
    """Download audio from R2/S3 URL to cache directory.

    Supports S3 URLs in format: s3://{bucket}/{key}
    or full HTTPS URLs that can be parsed to extract bucket/key.

    Args:
        url: Audio file URL (R2/S3 URL)
        cache_dir: Cache directory for downloaded files

    Returns:
        Path to downloaded audio file

    Raises:
        ValueError: If R2 credentials not configured or URL is invalid
        RuntimeError: If download fails
    """
    # Check R2 credentials
    if not settings.R2_ACCESS_KEY_ID or not settings.R2_SECRET_ACCESS_KEY:
        raise ValueError(
            "R2 credentials not configured. "
            "Set SOW_QWEN3_R2_ACCESS_KEY_ID and SOW_QWEN3_R2_SECRET_ACCESS_KEY環境変数"
        )

    # Parse URL to get bucket and key
    if url.startswith("s3://"):
        # Parse s3://bucket/key format
        parsed = urlparse(url)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
    else:
        # Try to parse as HTTPS URL - assume we can extract from R2 endpoint
        # For now, only support s3:// URLs
        raise ValueError(f"Only s3:// URLs are supported, got: {url}")

    # Create cache directory
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Use hash of URL as filename to ensure uniqueness
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    local_path = cache_dir / f"audio_{url_hash}.mp3"

    # Check cache
    if local_path.exists():
        logger.info(f"Using cached audio: {local_path}")
        return local_path

    # Create S3 client
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )

    # Download file
    logger.info(f"Downloading audio from s3://{bucket}/{key} to {local_path}")
    try:
        s3.download_file(bucket, key, str(local_path))
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "NoSuchKey":
            raise RuntimeError(f"Audio file not found in R2: s3://{bucket}/{key}") from e
        raise RuntimeError(f"Failed to download audio: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error downloading audio: {e}") from e

    logger.info(f"Audio downloaded successfully: {local_path}")
    return local_path
