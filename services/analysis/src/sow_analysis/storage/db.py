"""SQLite-backed persistent job store for the analysis service."""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..models import (
    AnalyzeJobRequest,
    Job,
    JobStatus,
    JobType,
    LrcJobRequest,
    Section,
)
from .cache import CacheManager

logger = logging.getLogger(__name__)


class JobStore:
    """SQLite-backed persistent job store."""

    def __init__(self, db_path: Path):
        """Initialize with path to SQLite database file.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._cache_manager: Optional[CacheManager] = None

    def set_cache_manager(self, cache_manager: CacheManager) -> None:
        """Set the cache manager for job reconstruction.

        Args:
            cache_manager: CacheManager instance for accessing caching utilities
        """
        self._cache_manager = cache_manager

    async def initialize(self) -> None:
        """Create tables if not exist. Called once at startup."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                type            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'queued',
                progress        REAL NOT NULL DEFAULT 0.0,
                stage           TEXT NOT NULL DEFAULT '',
                error_message   TEXT,

                request_json    TEXT NOT NULL,
                result_json     TEXT,

                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,

                content_hash    TEXT NOT NULL,

                CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
                CHECK (type IN ('analyze', 'lrc'))
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
            """
        )
        await self._db.commit()
        logger.info(f"JobStore initialized with database at {self.db_path}")

    async def insert_job(self, job: Job) -> None:
        """Insert a new job record.

        Args:
            job: Job to insert
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        request_json = job.request.model_dump_json()
        result_json = job.result.model_dump_json() if job.result else None

        await self._db.execute(
            """
            INSERT INTO jobs (
                id, type, status, progress, stage, error_message,
                request_json, result_json, created_at, updated_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.type.value,
                job.status.value,
                job.progress,
                job.stage,
                job.error_message,
                request_json,
                result_json,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                job.request.content_hash,
            ),
        )
        await self._db.commit()
        logger.debug(f"Inserted job {job.id} into database")

    async def update_job(self, job_id: str, **fields: Any) -> None:
        """Update specific fields on a job.

        Args:
            job_id: Job ID to update
            **fields: Fields to update (e.g., status, progress, stage, error_message, result_json)
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        if not fields:
            return

        # Build dynamic update query
        set_clauses = []
        values = []

        for key, value in fields.items():
            set_clauses.append(f"{key} = ?")
            values.append(value)

        # Always update updated_at timestamp
        set_clauses.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())

        values.append(job_id)

        await self._db.execute(
            f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        await self._db.commit()
        logger.debug(f"Updated job {job_id}: {fields}")

    def _row_to_job(self, row: tuple) -> Job:
        """Convert database row to Job instance.

        Args:
            row: Database row tuple

        Returns:
            Job instance
        """
        (
            job_id,
            job_type_str,
            status_str,
            progress,
            stage,
            error_message,
            request_json,
            result_json,
            created_at_str,
            updated_at_str,
            content_hash,
        ) = row

        # Parse enums
        job_type = JobType(job_type_str)
        status = JobStatus(status_str)

        # Deserialize request
        if job_type == JobType.ANALYZE:
            request = AnalyzeJobRequest.model_validate_json(request_json)
        else:  # LRC
            request = LrcJobRequest.model_validate_json(request_json)

        # Deserialize result if present
        result = None
        if result_json:
            from ..models import JobResult
            result = JobResult.model_validate_json(result_json)

        # Parse timestamps
        created_at = datetime.fromisoformat(created_at_str)
        updated_at = datetime.fromisoformat(updated_at_str)

        return Job(
            id=job_id,
            type=job_type,
            status=status,
            request=request,
            result=result,
            error_message=error_message,
            created_at=created_at,
            updated_at=updated_at,
            progress=progress,
            stage=stage,
        )

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve a single job by ID.

        Args:
            job_id: Job ID to look up

        Returns:
            Job instance or None if not found
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        async with self._db.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_job(row)

    async def get_interrupted_jobs(self) -> list[Job]:
        """Return jobs with status QUEUED or PROCESSING (for restart recovery).

        Returns:
            List of jobs that were interrupted
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        async with self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'processing')"
        ) as cursor:
            rows = await cursor.fetchall()

        jobs = [self._row_to_job(row) for row in rows]
        logger.info(f"Found {len(jobs)} interrupted jobs in database")
        return jobs

    async def purge_old_jobs(self, max_age_days: int = 7) -> int:
        """Delete completed/failed jobs older than max_age_days.

        Args:
            max_age_days: Maximum age in days to keep completed/failed jobs

        Returns:
            Number of jobs deleted
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_str = cutoff_date.isoformat()

        cursor = await self._db.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('completed', 'failed')
            AND created_at < ?
            """,
            (cutoff_str,),
        )
        await self._db.commit()

        deleted_count = cursor.rowcount
        if deleted_count > 0:
            logger.info(f"Purged {deleted_count} jobs older than {max_age_days} days")

        return deleted_count

    async def list_jobs(
        self, status: Optional[JobStatus] = None, job_type: Optional[JobType] = None, limit: int = 100
    ) -> list[Job]:
        """List jobs with optional filtering.

        Args:
            status: Filter by job status
            job_type: Filter by job type
            limit: Maximum number of jobs to return

        Returns:
            List of jobs matching filters
        """
        if not self._db:
            raise RuntimeError("JobStore not initialized")

        # Build query with optional filters
        query = "SELECT * FROM jobs"
        conditions = []
        values = []

        if status:
            conditions.append("status = ?")
            values.append(status.value)

        if job_type:
            conditions.append("type = ?")
            values.append(job_type.value)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)

        async with self._db.execute(query, values) as cursor:
            rows = await cursor.fetchall()

        return [self._row_to_job(row) for row in rows]

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("JobStore database connection closed")
