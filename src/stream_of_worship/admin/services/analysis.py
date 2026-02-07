"""HTTP client for the analysis service API.

Provides AnalysisClient for communicating with the FastAPI analysis service
over HTTP. Handles authentication, job submission, polling, and result parsing.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests


class AnalysisServiceError(Exception):
    """Error communicating with the analysis service."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AnalysisResult:
    """Analysis results from the service.

    Attributes:
        duration_seconds: Audio duration
        tempo_bpm: Detected tempo
        musical_key: Detected key (e.g., "G", "C#")
        musical_mode: Detected mode (major/minor)
        key_confidence: Key detection confidence
        loudness_db: Loudness in dB
        beats: List of beat timestamps
        downbeats: List of downbeat timestamps
        sections: List of section objects
        embeddings_shape: Embedding dimensions
        stems_url: R2 URL for stems directory
    """

    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    loudness_db: Optional[float] = None
    beats: Optional[List[float]] = None
    downbeats: Optional[List[float]] = None
    sections: Optional[List[Dict[str, Any]]] = None
    embeddings_shape: Optional[List[int]] = None
    stems_url: Optional[str] = None


@dataclass
class JobInfo:
    """Information about an analysis job.

    Attributes:
        job_id: Unique job identifier
        status: Job status (queued|processing|completed|failed)
        job_type: Type of job (e.g., "analysis")
        progress: Progress percentage (0.0-1.0)
        stage: Current processing stage
        error_message: Error message if failed
        result: Analysis results if completed
        created_at: ISO timestamp when job was created
        updated_at: ISO timestamp when job was last updated
    """

    job_id: str
    status: str
    job_type: str
    progress: float = 0.0
    stage: str = ""
    error_message: Optional[str] = None
    result: Optional[AnalysisResult] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AnalysisClient:
    """HTTP client for the analysis service API.

    Communicates with the FastAPI analysis service for audio analysis jobs.
    Uses Bearer token authentication via SOW_ANALYSIS_API_KEY environment variable.

    Attributes:
        base_url: Base URL of the analysis service
        timeout: Request timeout in seconds
    """

    def __init__(self, base_url: str, timeout: int = 30):
        """Initialize the analysis client.

        Args:
            base_url: Base URL of the analysis service
            timeout: Request timeout in seconds

        Raises:
            ValueError: If SOW_ANALYSIS_API_KEY environment variable is not set
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self._api_key = os.environ.get("SOW_ANALYSIS_API_KEY")
        if not self._api_key:
            raise ValueError(
                "SOW_ANALYSIS_API_KEY environment variable is not set. "
                "Set it to your analysis service API key."
            )

    def _auth_headers(self) -> Dict[str, str]:
        """Get authentication headers.

        Returns:
            Dictionary with Authorization header
        """
        return {"Authorization": f"Bearer {self._api_key}"}

    def health_check(self) -> Dict[str, Any]:
        """Check if the analysis service is healthy.

        Returns:
            Health check response from the service

        Raises:
            AnalysisServiceError: If the service is unreachable
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/health",
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            raise AnalysisServiceError(f"Health check failed: {e}")

    def submit_analysis(
        self,
        audio_url: str,
        content_hash: str,
        generate_stems: bool = True,
        force: bool = False,
    ) -> JobInfo:
        """Submit an audio file for analysis.

        Args:
            audio_url: R2 URL of the audio file
            content_hash: SHA-256 hash of the audio content
            generate_stems: Whether to generate stem separation
            force: Whether to force re-analysis

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        payload = {
            "audio_url": audio_url,
            "content_hash": content_hash,
            "options": {
                "generate_stems": generate_stems,
                "force": force,
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/analyze",
                json=payload,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid API key", status_code=401
                )

            response.raise_for_status()
            data = response.json()
            return self._parse_job_response(data)

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e.response, "status_code"):
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"Analysis submission failed (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Analysis submission failed: {e}")

    def submit_lrc(
        self,
        audio_url: str,
        content_hash: str,
        lyrics_text: str,
        whisper_model: str = "large-v3",
        language: str = "zh",
        use_vocals_stem: bool = True,
        force: bool = False,
    ) -> JobInfo:
        """Submit an audio file for LRC generation.

        Args:
            audio_url: R2 URL of the audio file
            content_hash: SHA-256 hash of the audio content
            lyrics_text: Raw lyrics text to align
            whisper_model: Whisper model to use
            language: Language hint for transcription
            use_vocals_stem: Whether to use vocals stem for better alignment
            force: Whether to force re-generation

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        payload = {
            "audio_url": audio_url,
            "content_hash": content_hash,
            "lyrics_text": lyrics_text,
            "options": {
                "whisper_model": whisper_model,
                "language": language,
                "use_vocals_stem": use_vocals_stem,
                "force": force,
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/lrc",
                json=payload,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid API key", status_code=401
                )

            response.raise_for_status()
            data = response.json()
            return self._parse_job_response(data)

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e.response, "status_code"):
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"LRC submission failed (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"LRC submission failed: {e}")

    def get_job(self, job_id: str) -> JobInfo:
        """Get information about a job.

        Args:
            job_id: Unique job identifier

        Returns:
            JobInfo for the job

        Raises:
            AnalysisServiceError: If job not found or request fails
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/jobs/{job_id}",
                headers=self._auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 404:
                raise AnalysisServiceError(
                    f"Job not found: {job_id}", status_code=404
                )

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid API key", status_code=401
                )

            response.raise_for_status()
            data = response.json()
            return self._parse_job_response(data)

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e.response, "status_code"):
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"Failed to get job status (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Failed to get job status: {e}")

    def wait_for_completion(
        self,
        job_id: str,
        poll_interval: float = 3.0,
        timeout: float = 600.0,
        callback: Optional[Callable[[JobInfo], None]] = None,
    ) -> JobInfo:
        """Poll a job until it completes or fails.

        Args:
            job_id: Unique job identifier
            poll_interval: Seconds between polls
            timeout: Maximum seconds to wait
            callback: Optional callback called with JobInfo each iteration

        Returns:
            Final JobInfo (completed or failed)

        Raises:
            AnalysisServiceError: On timeout or request failure
        """
        start_time = time.time()

        while True:
            job = self.get_job(job_id)

            if callback:
                callback(job)

            if job.status in ("completed", "failed"):
                return job

            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise AnalysisServiceError(
                    f"Timed out waiting for job {job_id} after {timeout}s"
                )

            time.sleep(poll_interval)

    @staticmethod
    def _parse_job_response(data: Dict[str, Any]) -> JobInfo:
        """Parse a job response from JSON.

        Args:
            data: JSON response from the API

        Returns:
            Parsed JobInfo with optional AnalysisResult
        """
        result = None
        if data.get("result"):
            result_data = data["result"]
            result = AnalysisResult(
                duration_seconds=result_data.get("duration_seconds"),
                tempo_bpm=result_data.get("tempo_bpm"),
                musical_key=result_data.get("musical_key"),
                musical_mode=result_data.get("musical_mode"),
                key_confidence=result_data.get("key_confidence"),
                loudness_db=result_data.get("loudness_db"),
                beats=result_data.get("beats"),
                downbeats=result_data.get("downbeats"),
                sections=result_data.get("sections"),
                embeddings_shape=result_data.get("embeddings_shape"),
                stems_url=result_data.get("stems_url"),
            )

        return JobInfo(
            job_id=data["job_id"],
            status=data["status"],
            job_type=data.get("job_type", "analysis"),
            progress=data.get("progress", 0.0),
            stage=data.get("stage", ""),
            error_message=data.get("error_message"),
            result=result,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
