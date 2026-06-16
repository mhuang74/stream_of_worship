"""R2/S3-compatible storage client."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# Legacy stem name mappings for backward compatibility
STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}

MAX_BACKUPS_PER_PREFIX = 5


class StaleObjectError(Exception):
    """Raised when the official LRC object was modified after the job started."""


class BackupFailedError(Exception):
    """Raised when copying the current official LRC to backup fails."""


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
            raise ValueError("SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY must be set")

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

        await loop.run_in_executor(None, self.s3.download_file, bucket, key, str(local_path))

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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(result, f)
            tmp = f.name

        try:
            await loop.run_in_executor(None, self.s3.upload_file, tmp, self.bucket, key)
        finally:
            os.unlink(tmp)

        return f"s3://{self.bucket}/{key}"

    async def upload_lrc(
        self,
        hash_prefix: str,
        lrc_path: Path,
        object_name: str = "lyrics.lrc",
    ) -> str:
        """Upload lyrics.lrc to R2.

        Args:
            hash_prefix: Content hash prefix for the path
            lrc_path: Path to the LRC file
            object_name: Filename to store under the hash prefix

        Returns:
            s3://bucket/{hash}/{object_name} URL
        """
        key = f"{hash_prefix}/{object_name}"
        loop = asyncio.get_event_loop()

        await loop.run_in_executor(None, self.s3.upload_file, str(lrc_path), self.bucket, key)

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
            await loop.run_in_executor(None, lambda: self.s3.head_object(Bucket=bucket, Key=key))
            return True
        except ClientError:
            return False

    async def check_stem_exists(
        self, hash_prefix: str, stem_name: str, extension: str = "flac"
    ) -> Optional[str]:
        """Check if a stem exists in R2, trying new name then legacy fallback.

        Args:
            hash_prefix: Content hash prefix for the path
            stem_name: Stem name (e.g., "vocals_dry", "vocals", "instrumental")
            extension: File extension (default: "flac")

        Returns:
            S3 URL if found, None otherwise.
        """
        primary_key = f"{hash_prefix}/stems/{stem_name}.{extension}"
        primary_url = f"s3://{self.bucket}/{primary_key}"
        if await self.check_exists(primary_url):
            return primary_url

        legacy_name = STEM_LEGACY_NAMES.get(stem_name)
        if legacy_name:
            legacy_key = f"{hash_prefix}/stems/{legacy_name}.{extension}"
            legacy_url = f"s3://{self.bucket}/{legacy_key}"
            if await self.check_exists(legacy_url):
                return legacy_url

        return None

    async def upload_clean_stems(
        self,
        hash_prefix: str,
        vocals_dry: Path,
        instrumental: Optional[Path] = None,
        vocals: Optional[Path] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Upload clean stems to R2.

        Uploads vocals_dry.flac (Stage 2 output) and optionally vocals.flac
        (Stage 1 output) and instrumental.flac to the stems directory.

        Args:
            hash_prefix: Content hash prefix for the path
            vocals_dry: Path to the dry (de-reverb) vocals FLAC file
            instrumental: Optional path to the instrumental FLAC file
            vocals: Optional path to the Stage 1 vocals FLAC file

        Returns:
            Tuple of (vocals_dry_url, vocals_url or None, instrumental_url or None).
            Order matches separate_stems() return: (vocals_dry, vocals, instrumental).
        """
        loop = asyncio.get_event_loop()

        # Upload vocals_dry.flac (Stage 2 output)
        vocals_dry_url: Optional[str] = None
        if vocals_dry and vocals_dry.exists():
            dry_key = f"{hash_prefix}/stems/vocals_dry.flac"
            await loop.run_in_executor(
                None,
                self.s3.upload_file,
                str(vocals_dry),
                self.bucket,
                dry_key,
            )
            vocals_dry_url = f"s3://{self.bucket}/{dry_key}"

        # Upload vocals.flac (Stage 1 output) if provided
        vocals_url: Optional[str] = None
        if vocals and vocals.exists():
            vocals_key = f"{hash_prefix}/stems/vocals.flac"
            await loop.run_in_executor(
                None,
                self.s3.upload_file,
                str(vocals),
                self.bucket,
                vocals_key,
            )
            vocals_url = f"s3://{self.bucket}/{vocals_key}"

        # Upload instrumental.flac if provided
        instrumental_url: Optional[str] = None
        if instrumental and instrumental.exists():
            instrumental_key = f"{hash_prefix}/stems/instrumental.flac"
            await loop.run_in_executor(
                None,
                self.s3.upload_file,
                str(instrumental),
                self.bucket,
                instrumental_key,
            )
            instrumental_url = f"s3://{self.bucket}/{instrumental_key}"

        return vocals_dry_url, vocals_url, instrumental_url

    async def copy_object(self, source_s3_url: str, dest_s3_url: str) -> None:
        """Copy an object within R2.

        Args:
            source_s3_url: Source s3://bucket/key URL
            dest_s3_url: Destination s3://bucket/key URL
        """
        src_bucket, src_key = parse_s3_url(source_s3_url)
        dest_bucket, dest_key = parse_s3_url(dest_s3_url)
        loop = asyncio.get_running_loop()

        copy_source = {"Bucket": src_bucket, "Key": src_key}
        await loop.run_in_executor(
            None,
            lambda: self.s3.copy_object(
                CopySource=copy_source, Bucket=dest_bucket, Key=dest_key
            ),
        )

    async def head_object(self, s3_url: str) -> dict:
        """HEAD an object in R2 and return response metadata.

        Args:
            s3_url: s3://bucket/key URL

        Returns:
            dict with headers including ETag, LastModified, etc.

        Raises:
            ClientError: If object does not exist or other error.
        """
        bucket, key = parse_s3_url(s3_url)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.s3.head_object(Bucket=bucket, Key=key)
        )

    async def list_objects(self, prefix: str) -> list:
        """List objects in R2 with the given prefix.

        Args:
            prefix: Key prefix to list under

        Returns:
            List of object key strings.
        """
        loop = asyncio.get_event_loop()
        keys = []
        paginator = self.s3.get_paginator("list_objects_v2")
        pages = await loop.run_in_executor(
            None,
            lambda: list(paginator.paginate(Bucket=self.bucket, Prefix=prefix)),
        )
        for page in pages:
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    async def delete_object(self, key: str) -> None:
        """Delete an object from R2 by key.

        Args:
            key: Object key to delete
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self.s3.delete_object(Bucket=self.bucket, Key=key)
        )

    async def upload_official_lrc(
        self,
        hash_prefix: str,
        lrc_path: Path,
        expected_etag: Optional[str] = None,
        skip_backup: bool = False,
    ) -> str:
        """Upload the official lyrics.lrc with backup and ETag protection.

        1. HEAD lyrics.lrc to get current ETag.
        2. If expected_etag is provided and current ETag != expected_etag:
             raise StaleObjectError
        3. If lyrics.lrc exists and not skip_backup:
             copy to lyrics.backup.{timestamp_ms}.lrc
             If copy fails: raise BackupFailedError
        4. Upload new lyrics.lrc
        5. Prune old backups: list lyrics.backup.*.lrc, delete oldest if count > MAX_BACKUPS_PER_PREFIX
        6. Return s3://{bucket}/{hash_prefix}/lyrics.lrc

        Args:
            hash_prefix: Content hash prefix for the path
            lrc_path: Path to the LRC file to upload
            expected_etag: ETag captured at job start (None if object didn't exist)
            skip_backup: If True, skip backup even if it would fail

        Returns:
            S3 URL of the uploaded official LRC

        Raises:
            StaleObjectError: If ETag mismatch detected
            BackupFailedError: If backup copy fails and skip_backup=False
        """
        official_key = f"{hash_prefix}/lyrics.lrc"
        official_url = f"s3://{self.bucket}/{official_key}"

        # 1. HEAD lyrics.lrc to get current ETag
        current_etag: Optional[str] = None
        exists = False
        try:
            head_resp = await self.head_object(official_url)
            current_etag = head_resp.get("ETag", "").strip('"')
            exists = True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code not in ("404", "NoSuchKey"):
                raise

        # 2. ETag stale-object check
        if expected_etag is not None:
            if current_etag != expected_etag:
                raise StaleObjectError(
                    "lyrics.lrc was modified by another process after this job started"
                )

        # 3. Backup existing official LRC
        if exists and not skip_backup:
            timestamp_ms = int(asyncio.get_event_loop().time() * 1000)
            backup_key = f"{hash_prefix}/lyrics.backup.{timestamp_ms}.lrc"
            backup_url = f"s3://{self.bucket}/{backup_key}"
            try:
                await self.copy_object(official_url, backup_url)
            except Exception as e:
                raise BackupFailedError(f"Failed to backup existing lyrics.lrc: {e}")

        # 4. Upload new lyrics.lrc
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.s3.upload_file, str(lrc_path), self.bucket, official_key
        )

        # 5. Prune old backups
        backup_prefix = f"{hash_prefix}/lyrics.backup."
        try:
            backup_keys = await self.list_objects(backup_prefix)
            backup_keys = [k for k in backup_keys if k.startswith(backup_prefix) and k.endswith(".lrc")]
            if len(backup_keys) > MAX_BACKUPS_PER_PREFIX:
                # Sort by timestamp embedded in key name
                def _extract_ts(key: str) -> int:
                    try:
                        # key format: {hash_prefix}/lyrics.backup.{timestamp_ms}.lrc
                        parts = key.split("lyrics.backup.")
                        if len(parts) == 2:
                            ts_str = parts[1].split(".lrc")[0]
                            return int(ts_str)
                    except (ValueError, IndexError):
                        pass
                    return 0

                backup_keys.sort(key=_extract_ts)
                to_delete = backup_keys[:-MAX_BACKUPS_PER_PREFIX]
                for key in to_delete:
                    await self.delete_object(key)
        except Exception:
            # Pruning failure is non-fatal
            pass

        return official_url
