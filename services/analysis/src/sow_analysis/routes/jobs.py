"""Job submission and status endpoints."""

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from ..config import settings
from ..models import (
    AnalyzeJobRequest,
    JobResponse,
    JobStatus,
    JobType,
    LrcJobRequest,
)

if TYPE_CHECKING:
    from ..workers.queue import JobQueue

router = APIRouter()

# Global job queue reference - set in main.py
job_queue: Optional["JobQueue"] = None


def set_job_queue(queue: "JobQueue") -> None:
    """Set the global job queue reference.

    Args:
        queue: JobQueue instance
    """
    global job_queue
    job_queue = queue


async def verify_api_key(authorization: Optional[str] = Header(None)) -> str:
    """Verify Bearer token matches SOW_ANALYSIS_API_KEY.

    Args:
        authorization: Authorization header value

    Returns:
        Validated token

    Raises:
        HTTPException: If token is invalid
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = authorization[7:]

    if not settings.SOW_ANALYSIS_API_KEY:
        raise HTTPException(500, "SOW_ANALYSIS_API_KEY not configured on server")

    if token != settings.SOW_ANALYSIS_API_KEY:
        raise HTTPException(401, "Invalid API key")

    return token


def job_to_response(job) -> JobResponse:
    """Convert Job to JobResponse.

    Args:
        job: Job instance

    Returns:
        JobResponse model
    """
    from ..models import JobResult

    result = None
    if job.result:
        result = JobResult(
            duration_seconds=job.result.duration_seconds,
            tempo_bpm=job.result.tempo_bpm,
            musical_key=job.result.musical_key,
            musical_mode=job.result.musical_mode,
            key_confidence=job.result.key_confidence,
            loudness_db=job.result.loudness_db,
            beats=job.result.beats,
            downbeats=job.result.downbeats,
            sections=job.result.sections,
            embeddings_shape=job.result.embeddings_shape,
            stems_url=job.result.stems_url,
            lrc_url=job.result.lrc_url,
            line_count=job.result.line_count,
        )

    return JobResponse(
        job_id=job.id,
        status=JobStatus(job.status),
        job_type=JobType(job.type),
        created_at=job.created_at,
        updated_at=job.updated_at,
        progress=job.progress,
        stage=job.stage,
        error_message=job.error_message,
        result=result,
    )


@router.post("/jobs/analyze", response_model=JobResponse)
async def submit_analysis_job(
    request: AnalyzeJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Submit audio for analysis.

    Args:
        request: Analysis job request
        api_key: Validated API key

    Returns:
        Job response with status
    """
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")

    job = await job_queue.submit(JobType.ANALYZE, request)
    return job_to_response(job)


@router.post("/jobs/lrc", response_model=JobResponse)
async def submit_lrc_job(
    request: LrcJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Submit LRC generation job.

    Args:
        request: LRC job request
        api_key: Validated API key

    Returns:
        Job response with status
    """
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")

    job = await job_queue.submit(JobType.LRC, request)
    return job_to_response(job)


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    status: Optional[JobStatus] = None,
    job_type: Optional[JobType] = None,
    api_key: str = Depends(verify_api_key),
) -> list[JobResponse]:
    """List jobs with optional status/type filtering.

    Args:
        status: Filter by job status (optional)
        job_type: Filter by job type (optional)
        api_key: Validated API key

    Returns:
        List of job responses
    """
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")

    jobs = await job_queue.list_jobs(status, job_type)
    return [job_to_response(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Get job status and results.

    Args:
        job_id: Job ID to look up
        api_key: Validated API key

    Returns:
        Job response with status

    Raises:
        HTTPException: If job not found
    """
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")

    job = await job_queue.get_job(job_id)

    if not job:
        raise HTTPException(404, "Job not found")

    return job_to_response(job)
