"""Cloudflare R2 storage client.

Provides upload and download of audio assets to Cloudflare R2, which
exposes an S3-compatible API.  Credentials are read from environment
variables so they never appear in config files.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MAX_BACKUPS_PER_PREFIX = 5
RECORDING_HASH_PREFIX_RE = re.compile(r"^[0-9a-f]{12}$")


class StaleObjectError(Exception):
    """Raised when the official LRC object was modified after the operation started."""


class BackupFailedError(Exception):
    """Raised when copying the current official LRC to backup fails."""


@dataclass
class R2ObjectIdentity:
    """Identity snapshot of an R2 object for stale-session detection.

    Attributes:
        exists: Whether the object existed at snapshot time
        etag: ETag header value when available (MD5 of object data for non-multipart uploads)
        last_modified: LastModified header value when available
    """

    exists: bool
    etag: Optional[str] = None
    last_modified: Optional[str] = None


@dataclass
class R2PrefixSummary:
    """Summary of objects under an R2 prefix."""

    prefix: str
    object_count: int
    total_bytes: int
    last_modified: Optional[str] = None


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
            config=Config(
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 2},
                max_pool_connections=32,
            ),
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

    @staticmethod
    def validate_recording_hash_prefix(hash_prefix: object) -> str:
        """Validate and normalize a full recording hash prefix."""
        if not isinstance(hash_prefix, str):
            raise ValueError("Recording hash prefix must be a string")
        normalized = hash_prefix.strip().rstrip("/").lower()
        if not RECORDING_HASH_PREFIX_RE.fullmatch(normalized):
            raise ValueError("Recording hash prefix must be exactly 12 hex characters")
        return normalized

    @classmethod
    def recording_prefix_key(cls, hash_prefix: str) -> str:
        """Return the exact R2 prefix for a recording hash."""
        return f"{cls.validate_recording_hash_prefix(hash_prefix)}/"

    @staticmethod
    def _last_modified_to_str(value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def list_prefix(self, prefix: str) -> R2PrefixSummary:
        """List objects under an exact recording prefix using 100-object pages."""
        prefix_key = self.recording_prefix_key(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        object_count = 0
        total_bytes = 0
        latest: object = None

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix_key,
            PaginationConfig={"PageSize": 100},
        ):
            for obj in page.get("Contents", []):
                object_count += 1
                total_bytes += int(obj.get("Size") or 0)
                modified = obj.get("LastModified")
                if modified is not None and (latest is None or modified > latest):
                    latest = modified

        return R2PrefixSummary(
            prefix=prefix_key.rstrip("/"),
            object_count=object_count,
            total_bytes=total_bytes,
            last_modified=self._last_modified_to_str(latest),
        )

    def delete_prefix(self, prefix: str) -> R2PrefixSummary:
        """Delete all objects under an exact recording prefix in 100-object batches."""
        prefix_key = self.recording_prefix_key(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        total_bytes = 0
        latest: object = None

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix_key,
            PaginationConfig={"PageSize": 100},
        ):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
                total_bytes += int(obj.get("Size") or 0)
                modified = obj.get("LastModified")
                if modified is not None and (latest is None or modified > latest):
                    latest = modified

        for start in range(0, len(keys), 100):
            batch = keys[start : start + 100]
            if not batch:
                continue
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )

        return R2PrefixSummary(
            prefix=prefix_key.rstrip("/"),
            object_count=len(keys),
            total_bytes=total_bytes,
            last_modified=self._last_modified_to_str(latest),
        )

    def list_stems(self, prefix: str) -> R2PrefixSummary:
        """List objects under ``{prefix}/stems/`` for a recording.

        Args:
            prefix: 12-character recording hash prefix

        Returns:
            R2PrefixSummary scoped to the stems subdirectory
        """
        stems_key = f"{self.validate_recording_hash_prefix(prefix)}/stems/"
        paginator = self._client.get_paginator("list_objects_v2")
        object_count = 0
        total_bytes = 0
        latest: object = None

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=stems_key,
            PaginationConfig={"PageSize": 100},
        ):
            for obj in page.get("Contents", []):
                object_count += 1
                total_bytes += int(obj.get("Size") or 0)
                modified = obj.get("LastModified")
                if modified is not None and (latest is None or modified > latest):
                    latest = modified

        return R2PrefixSummary(
            prefix=stems_key.rstrip("/"),
            object_count=object_count,
            total_bytes=total_bytes,
            last_modified=self._last_modified_to_str(latest),
        )

    def delete_stems(self, prefix: str) -> R2PrefixSummary:
        """Delete all objects under ``{prefix}/stems/`` in 100-object batches.

        Args:
            prefix: 12-character recording hash prefix

        Returns:
            R2PrefixSummary with counts of deleted stems objects
        """
        stems_key = f"{self.validate_recording_hash_prefix(prefix)}/stems/"
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        total_bytes = 0
        latest: object = None

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=stems_key,
            PaginationConfig={"PageSize": 100},
        ):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
                total_bytes += int(obj.get("Size") or 0)
                modified = obj.get("LastModified")
                if modified is not None and (latest is None or modified > latest):
                    latest = modified

        for start in range(0, len(keys), 100):
            batch = keys[start : start + 100]
            if not batch:
                continue
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )

        return R2PrefixSummary(
            prefix=stems_key.rstrip("/"),
            object_count=len(keys),
            total_bytes=total_bytes,
            last_modified=self._last_modified_to_str(latest),
        )

    def scan_recording_prefixes(
        self,
        blacklist: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> list[R2PrefixSummary]:
        """Scan bucket objects and summarize top-level hash-like recording prefixes."""
        blacklist = blacklist or []
        paginator = self._client.get_paginator("list_objects_v2")
        summaries: dict[str, R2PrefixSummary] = {}
        latest_values: dict[str, object] = {}

        for page in paginator.paginate(Bucket=self.bucket, PaginationConfig={"PageSize": 1000}):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if any(key.startswith(blocked) for blocked in blacklist):
                    continue
                prefix = key.split("/", 1)[0]
                if not RECORDING_HASH_PREFIX_RE.fullmatch(prefix):
                    continue
                current = summaries.setdefault(
                    prefix,
                    R2PrefixSummary(prefix=prefix, object_count=0, total_bytes=0),
                )
                current.object_count += 1
                current.total_bytes += int(obj.get("Size") or 0)
                modified = obj.get("LastModified")
                if modified is not None and (
                    prefix not in latest_values or modified > latest_values[prefix]
                ):
                    latest_values[prefix] = modified
                    current.last_modified = self._last_modified_to_str(modified)

        result = sorted(summaries.values(), key=lambda summary: summary.prefix)
        if limit is not None:
            result = result[:limit]
        return result

    def upload_lrc(self, file_path: Path, hash_prefix: str) -> str:
        """Upload an LRC file to R2 under the hash-prefix directory.

        The file is stored at ``{hash_prefix}/lyrics.lrc`` inside the bucket.

        Args:
            file_path: Local path to the LRC file
            hash_prefix: 12-character hash prefix (R2 directory name)

        Returns:
            S3-style URL of the uploaded object
        """
        s3_key = f"{hash_prefix}/lyrics.lrc"
        self._client.upload_file(str(file_path), self.bucket, s3_key)
        return f"s3://{self.bucket}/{s3_key}"

    def upload_stem(self, file_path: Path, hash_prefix: str, stem_name: str) -> str:
        """Upload a stem file to R2.

        The file is stored at ``{hash_prefix}/stems/{stem_name}.flac`` inside the bucket.

        Args:
            file_path: Local path to stem file
            hash_prefix: 12-character hash prefix (R2 directory name)
            stem_name: Stem name (e.g., 'vocals_dry')

        Returns:
            S3-style URL of the uploaded object
        """
        s3_key = f"{hash_prefix}/stems/{stem_name}.flac"
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

        Raises:
            ClientError: On non-404 errors (permission, credential, network).
        """
        s3_key = f"{hash_prefix}/audio.mp3"
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise

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

        Raises:
            ClientError: On non-404 errors (permission, credential, network).
        """
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise

    def lrc_exists(self, hash_prefix: str) -> Optional[str]:
        """Check whether an LRC file exists in R2.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            S3 URL of the LRC file if it exists, None if not found.

        Raises:
            ClientError: On non-404 errors (permission, credential, network).
        """
        s3_key = f"{hash_prefix}/lyrics.lrc"
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return f"s3://{self.bucket}/{s3_key}"
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return None
            raise

    def get_lrc_identity(self, hash_prefix: str) -> R2ObjectIdentity:
        """Get identity snapshot of the transcribed LRC object for stale-session detection.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            R2ObjectIdentity with exists flag, ETag, and LastModified
        """
        s3_key = f"{hash_prefix}/lyrics.lrc"
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=s3_key)
            etag = resp.get("ETag", "").strip('"')
            lm = resp.get("LastModified")
            last_modified = (
                lm.isoformat() if lm and hasattr(lm, "isoformat") else str(lm) if lm else None
            )
            return R2ObjectIdentity(
                exists=True,
                etag=etag,
                last_modified=last_modified,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return R2ObjectIdentity(exists=False)
            raise

    def download_lrc_content(self, hash_prefix: str) -> Optional[str]:
        """Download transcribed LRC content from R2 as a string.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            LRC file content as UTF-8 string, or None if not found
        """
        s3_key = f"{hash_prefix}/lyrics.lrc"
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=s3_key)
            return resp["Body"].read().decode("utf-8")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return None
            raise

    def upload_bytes(self, s3_key: str, data: bytes, content_type: str = "text/plain") -> str:
        """Upload raw bytes to R2 under an arbitrary key.

        Args:
            s3_key: Full S3 key (path within bucket)
            data: Raw bytes to upload
            content_type: MIME type for the object

        Returns:
            S3-style URL of the uploaded object
        """
        self._client.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{self.bucket}/{s3_key}"

    def analysis_exists(self, hash_prefix: str) -> Optional[str]:
        """Check whether an analysis.json file exists in R2.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            S3 URL of the analysis.json if it exists, None if not found.

        Raises:
            ClientError: On non-404 errors (permission, credential, network).
        """
        s3_key = f"{hash_prefix}/analysis.json"
        try:
            self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return f"s3://{self.bucket}/{s3_key}"
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return None
            raise

    def download_analysis_json(self, hash_prefix: str) -> dict:
        """Download and parse analysis.json from R2.

        Args:
            hash_prefix: 12-character hash prefix

        Returns:
            Parsed analysis result dictionary with keys:
            duration_seconds, tempo_bpm, musical_key, musical_mode,
            key_confidence, loudness_db, beats, downbeats, sections,
            embeddings_shape, stems_url

        Raises:
            ClientError: On any R2 error (including 404).
            json.JSONDecodeError: If the file is not valid JSON.
        """
        s3_key = f"{hash_prefix}/analysis.json"
        response = self._client.get_object(Bucket=self.bucket, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)

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

    def delete_file(self, s3_key: str) -> None:
        """Delete a file from R2 by its S3 key.

        Args:
            s3_key: Full S3 key (path within bucket)

        Raises:
            ClientError: If deletion fails
        """
        self._client.delete_object(Bucket=self.bucket, Key=s3_key)

    def upload_official_lrc(
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
            expected_etag: ETag captured before upload (None if object didn't exist)
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
            head_resp = self._client.head_object(Bucket=self.bucket, Key=official_key)
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
            timestamp_ms = int(time.time() * 1000)
            backup_key = f"{hash_prefix}/lyrics.backup.{timestamp_ms}.lrc"
            copy_source = {"Bucket": self.bucket, "Key": official_key}
            try:
                self._client.copy_object(CopySource=copy_source, Bucket=self.bucket, Key=backup_key)
            except Exception as e:
                raise BackupFailedError(f"Failed to backup existing lyrics.lrc: {e}")

        # 4. Upload new lyrics.lrc
        self._client.upload_file(str(lrc_path), self.bucket, official_key)

        # 5. Prune old backups
        backup_prefix = f"{hash_prefix}/lyrics.backup."
        try:
            backup_keys = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=backup_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.startswith(backup_prefix) and key.endswith(".lrc"):
                        backup_keys.append(key)

            if len(backup_keys) > MAX_BACKUPS_PER_PREFIX:

                def _extract_ts(key: str) -> int:
                    try:
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
                    self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            # Pruning failure is non-fatal
            pass

        return official_url

    # ------------------------------------------------------------------
    # Backup / restore oriented methods
    # ------------------------------------------------------------------

    def iter_objects(self) -> Iterator[dict]:
        """Iterate over all objects in the bucket using list_objects_v2 paginator.

        Yields:
            dict with keys: key, size, etag, last_modified
        """
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, PaginationConfig={"PageSize": 1000}):
            for obj in page.get("Contents", []):
                yield {
                    "key": obj["Key"],
                    "size": int(obj.get("Size") or 0),
                    "etag": (obj.get("ETag") or "").strip('"'),
                    "last_modified": self._last_modified_to_str(obj.get("LastModified")),
                }

    def get_object_stream(self, s3_key: str) -> dict:
        """Stream an object from R2 via get_object.

        Args:
            s3_key: Full S3 key (path within bucket)

        Returns:
            dict with keys: body (streaming body), content_length, etag,
            last_modified, content_type, cache_control, content_disposition,
            content_encoding, metadata

        Raises:
            ClientError: On any R2 error (including 404).
        """
        resp = self._client.get_object(Bucket=self.bucket, Key=s3_key)
        return {
            "body": resp["Body"],
            "content_length": int(resp.get("ContentLength") or 0),
            "etag": (resp.get("ETag") or "").strip('"'),
            "last_modified": self._last_modified_to_str(resp.get("LastModified")),
            "content_type": resp.get("ContentType"),
            "cache_control": resp.get("CacheControl"),
            "content_disposition": resp.get("ContentDisposition"),
            "content_encoding": resp.get("ContentEncoding"),
            "metadata": resp.get("Metadata") or {},
        }

    def head_object(self, s3_key: str) -> Optional[dict]:
        """Head an object to get metadata without downloading body.

        Args:
            s3_key: Full S3 key (path within bucket)

        Returns:
            dict with keys: size, etag, last_modified, content_type,
            cache_control, content_disposition, content_encoding, metadata;
            or None on 404/NoSuchKey.

        Raises:
            ClientError: On non-404 errors.
        """
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=s3_key)
            return {
                "size": int(resp.get("ContentLength") or 0),
                "etag": (resp.get("ETag") or "").strip('"'),
                "last_modified": self._last_modified_to_str(resp.get("LastModified")),
                "content_type": resp.get("ContentType"),
                "cache_control": resp.get("CacheControl"),
                "content_disposition": resp.get("ContentDisposition"),
                "content_encoding": resp.get("ContentEncoding"),
                "metadata": resp.get("Metadata") or {},
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return None
            raise

    def upload_fileobj(
        self, fileobj, s3_key: str, extra_args: Optional[dict] = None
    ) -> str:
        """Upload a file-like object to R2 with optional metadata/content headers.

        Args:
            fileobj: A readable file-like object
            s3_key: Full S3 key (path within bucket)
            extra_args: Optional dict of extra args for upload_fileobj
                (ContentType, CacheControl, ContentDisposition,
                ContentEncoding, Metadata, etc.)

        Returns:
            S3-style URL of the uploaded object
        """
        from boto3.s3.transfer import TransferConfig
        self._client.upload_fileobj(
            Fileobj=fileobj,
            Bucket=self.bucket,
            Key=s3_key,
            ExtraArgs=extra_args or {},
            Config=TransferConfig(use_threads=False),
        )
        return f"s3://{self.bucket}/{s3_key}"
