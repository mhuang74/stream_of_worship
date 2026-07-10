"""HTTP client for the analysis service API.

Provides AnalysisClient for communicating with the FastAPI analysis service
over HTTP. Handles authentication, job submission, polling, and result parsing.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

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
        lrc_url: R2 URL for LRC file
        lrc_source: Source of LRC generation ("youtube_transcript", "qwen3_asr", or "whisper_asr")
    """

    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    key_algorithm_version: Optional[str] = None
    key_score_margin: Optional[float] = None
    key_window_agreement: Optional[float] = None
    key_candidates: Optional[Union[str, List[Dict[str, Any]]]] = None
    key_detected_at: Optional[str] = None
    loudness_db: Optional[float] = None
    beats: Optional[List[float]] = None
    downbeats: Optional[List[float]] = None
    sections: Optional[List[Dict[str, Any]]] = None
    embeddings_shape: Optional[List[int]] = None
    stems_url: Optional[str] = None
    lrc_url: Optional[str] = None
    lrc_source: Optional[str] = None


@dataclass
class LineEmbeddingResult:
    """Embedding for a single lyric line from the service.

    Attributes:
        line_index: Index of the line in the lyrics.
        line_text: Text of the lyric line.
        embedding: Embedding vector.
    """

    line_index: int = 0
    line_text: str = ""
    embedding: List[float] = field(default_factory=list)


@dataclass
class EmbeddingResult:
    """Embedding results from the service.

    Attributes:
        song_id: Song ID.
        embedding: Song-level embedding vector.
        line_embeddings: List of line-level embeddings.
        model_version: Model version string.
        content_hash: Content hash for staleness detection.
    """

    song_id: str = ""
    embedding: List[float] = field(default_factory=list)
    line_embeddings: List[LineEmbeddingResult] = field(default_factory=list)
    model_version: str = "text-embedding-3-small"
    content_hash: str = ""


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
    result: Optional[Union[AnalysisResult, EmbeddingResult]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AnalysisClient:
    """HTTP client for the analysis service API.

    Communicates with the FastAPI analysis service for audio analysis jobs.
    Uses Bearer token authentication via SOW_ANALYSIS_API_KEY environment variable.
    Admin operations (cancel) require SOW_ADMIN_API_KEY.

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

        self._admin_api_key = os.environ.get("SOW_ADMIN_API_KEY")

    def _auth_headers(self) -> Dict[str, str]:
        """Get authentication headers.

        Returns:
            Dictionary with Authorization header
        """
        return {"Authorization": f"Bearer {self._api_key}"}

    def _admin_auth_headers(self) -> Dict[str, str]:
        """Get admin authentication headers.

        Returns:
            Dictionary with Authorization header using admin API key

        Raises:
            ValueError: If SOW_ADMIN_API_KEY is not set
        """
        if not self._admin_api_key:
            raise ValueError(
                "SOW_ADMIN_API_KEY environment variable is not set. "
                "Set it to your admin API key for cancel operations."
            )
        return {"Authorization": f"Bearer {self._admin_api_key}"}

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

    def submit_fast_analysis(
        self,
        audio_url: str,
        content_hash: str,
        force: bool = False,
        sample_rate: int = 22050,
        hop_length: int = 512,
        start_bpm: float = 80.0,
        lrc_content: Optional[str] = None,
    ) -> JobInfo:
        """Submit an audio file for fast analysis (librosa-only).

        Produces only the fast-tier subset: duration, tempo, key, mode, key
        confidence, loudness. Full-only fields (beats, downbeats, sections,
        embeddings_shape, stems_url) will be None/absent on the result.

        Args:
            audio_url: R2 URL of the audio file
            content_hash: SHA-256 hash of the audio content
            force: Whether to force re-analysis (bypass cache)
            sample_rate: Target sample rate for librosa
            hop_length: Hop length for tempo estimation
            start_bpm: Initial tempo guess for the log-normal prior (default 80)
            lrc_content: Optional LRC lyrics text for CPS-based prod-v5 prior.
                When provided and the service has BPM_ALGORITHM_VERSION=v5_cps_prior,
                a lognormal prior is derived from the CPS value.

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        payload = {
            "audio_url": audio_url,
            "content_hash": content_hash,
            "options": {
                "force": force,
                "sample_rate": sample_rate,
                "hop_length": hop_length,
                "start_bpm": start_bpm,
                **({"lrc_content": lrc_content} if lrc_content is not None else {}),
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/fast-analyze",
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
                    f"Fast analysis submission failed (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Fast analysis submission failed: {e}")

    def submit_lrc(
        self,
        audio_url: str,
        content_hash: str,
        lyrics_text: str,
        song_title: str = "",
        whisper_model: str = "large-v3",
        language: str = "auto",
        use_vocals_stem: bool = True,
        force: bool = False,
        force_whisper: bool = False,
        youtube_url: str = "",
        use_qwen3_asr: bool = True,
        force_qwen3_asr: bool = False,
    ) -> JobInfo:
        """Submit an audio file for LRC generation.

        Args:
            audio_url: R2 URL of the audio file
            content_hash: SHA-256 hash of the audio content
            lyrics_text: Raw lyrics text to align
            song_title: Song title used for auto language detection
            whisper_model: Whisper model to use
            language: Language mode for transcription ("auto", "zh", or "en")
            use_vocals_stem: Whether to use vocals stem for better alignment
            force: Whether to force re-generation
            force_whisper: Bypass Whisper transcription cache
            youtube_url: YouTube URL for transcript-based LRC (primary path)
            use_qwen3_asr: Whether to use DashScope Qwen3 ASR before Whisper fallback
            force_qwen3_asr: Bypass only the Qwen3 ASR cache

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        payload = {
            "audio_url": audio_url,
            "content_hash": content_hash,
            "lyrics_text": lyrics_text,
            "song_title": song_title,
            "youtube_url": youtube_url,
            "options": {
                "whisper_model": whisper_model,
                "language": language,
                "use_vocals_stem": use_vocals_stem,
                "force": force,
                "force_whisper": force_whisper,
                "use_qwen3_asr": use_qwen3_asr,
                "force_qwen3_asr": force_qwen3_asr,
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

    def submit_embedding(
        self,
        song_id: str,
        title: str,
        composer: str = "",
        lyrics_raw: str = "",
        lyrics_lines: Optional[List[str]] = None,
    ) -> JobInfo:
        """Submit a song for embedding generation.

        Args:
            song_id: Song ID
            title: Song title
            composer: Song composer
            lyrics_raw: Raw lyrics text
            lyrics_lines: List of lyric lines (parsed from JSON)

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        import hashlib

        content = f"{title}\0{composer}\0{lyrics_raw}\0{'|'.join(lyrics_lines or [])}"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        payload = {
            "song_id": song_id,
            "title": title,
            "composer": composer,
            "lyrics_raw": lyrics_raw,
            "lyrics_lines": lyrics_lines or [],
            "content_hash": content_hash,
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/embedding",
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
                    f"Embedding submission failed (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Embedding submission failed: {e}")

    def submit_forced_alignment(
        self,
        audio_url: str,
        content_hash: str,
        lyrics_text: str,
        song_title: str = "",
        language: str = "auto",
        force: bool = False,
        use_vocals_stem: bool = True,
    ) -> JobInfo:
        """Submit an audio file for forced alignment.

        Args:
            audio_url: R2 URL of the audio file
            content_hash: SHA-256 hash of the audio content
            lyrics_text: Raw lyrics text to align
            song_title: Song title
            language: Language mode ("auto", "zh", or "en")
            force: Whether to force re-alignment
            use_vocals_stem: Whether to use vocals stem for better accuracy

        Returns:
            JobInfo for the submitted job

        Raises:
            AnalysisServiceError: If submission fails
        """
        payload = {
            "audio_url": audio_url,
            "content_hash": content_hash,
            "lyrics_text": lyrics_text,
            "song_title": song_title,
            "options": {
                "language": language,
                "force": force,
                "use_vocals_stem": use_vocals_stem,
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/forced-alignment",
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
                    f"Forced alignment submission failed (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Forced alignment submission failed: {e}")

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
                raise AnalysisServiceError(f"Job not found: {job_id}", status_code=404)

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
                raise AnalysisServiceError(f"Timed out waiting for job {job_id} after {timeout}s")

            time.sleep(poll_interval)

    def list_jobs(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
    ) -> List[JobInfo]:
        """List jobs with optional filtering.

        Args:
            status: Filter by status (queued, processing, completed, failed, cancelled)
            job_type: Filter by job type (analyze, lrc, stem_separation)

        Returns:
            List of JobInfo objects

        Raises:
            AnalysisServiceError: If request fails
        """
        params = {}
        if status:
            params["status"] = status
        if job_type:
            params["job_type"] = job_type

        try:
            response = requests.get(
                f"{self.base_url}/api/v1/jobs",
                params=params,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid API key", status_code=401
                )

            response.raise_for_status()
            data = response.json()
            return [self._parse_job_response(job) for job in data]

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"Failed to list jobs (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Failed to list jobs: {e}")

    def cancel_job(self, job_id: str) -> JobInfo:
        """Cancel a specific job by ID.

        Args:
            job_id: Unique job identifier

        Returns:
            JobInfo for the cancelled job

        Raises:
            AnalysisServiceError: If job not found or request fails
            ValueError: If SOW_ADMIN_API_KEY is not set
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/{job_id}/cancel",
                headers=self._admin_auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 404:
                raise AnalysisServiceError(f"Job not found: {job_id}", status_code=404)

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid admin API key", status_code=401
                )

            if response.status_code == 503:
                raise AnalysisServiceError(
                    "Admin API key not configured on server", status_code=503
                )

            response.raise_for_status()
            data = response.json()
            return self._parse_job_response(data)

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"Failed to cancel job (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Failed to cancel job: {e}")

    def cancel_all_jobs(self) -> Dict[str, Any]:
        """Cancel all queued and processing jobs.

        Returns:
            Dict with 'cancelled_count' and 'cancelled_job_ids' keys

        Raises:
            AnalysisServiceError: If request fails
            ValueError: If SOW_ADMIN_API_KEY is not set
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/jobs/clear-queue",
                headers=self._admin_auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code == 401:
                raise AnalysisServiceError(
                    "Authentication failed: Invalid admin API key", status_code=401
                )

            if response.status_code == 503:
                raise AnalysisServiceError(
                    "Admin API key not configured on server", status_code=503
                )

            response.raise_for_status()
            return response.json()

        except requests.exceptions.ConnectionError as e:
            raise AnalysisServiceError(
                f"Cannot connect to analysis service at {self.base_url}: {e}"
            )
        except requests.exceptions.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                status = e.response.status_code
                raise AnalysisServiceError(
                    f"Failed to cancel all jobs (HTTP {status}): {e}",
                    status_code=status,
                )
            raise AnalysisServiceError(f"Failed to cancel all jobs: {e}")

    @staticmethod
    def _parse_job_response(data: Dict[str, Any]) -> JobInfo:
        """Parse a job response from JSON.

        Args:
            data: JSON response from the API

        Returns:
            Parsed JobInfo with optional AnalysisResult or EmbeddingResult
        """
        result = None
        if data.get("result"):
            result_data = data["result"]
            job_type = data.get("job_type", "analysis")

            if job_type == "embedding":
                line_embeddings = [
                    LineEmbeddingResult(
                        line_index=le.get("line_index", 0),
                        line_text=le.get("line_text", ""),
                        embedding=le.get("embedding", []),
                    )
                    for le in result_data.get("line_embeddings", [])
                ]
                result = EmbeddingResult(
                    song_id=result_data.get("song_id", ""),
                    embedding=result_data.get("embedding", []),
                    line_embeddings=line_embeddings,
                    model_version=result_data.get("model_version", "text-embedding-3-small"),
                    content_hash=result_data.get("content_hash", ""),
                )
            else:
                result = AnalysisResult(
                    duration_seconds=result_data.get("duration_seconds"),
                    tempo_bpm=result_data.get("tempo_bpm"),
                    musical_key=result_data.get("musical_key"),
                    musical_mode=result_data.get("musical_mode"),
                    key_confidence=result_data.get("key_confidence"),
                    key_algorithm_version=result_data.get("key_algorithm_version"),
                    key_score_margin=result_data.get("key_score_margin"),
                    key_window_agreement=result_data.get("key_window_agreement"),
                    key_candidates=result_data.get("key_candidates"),
                    key_detected_at=result_data.get("key_detected_at"),
                    loudness_db=result_data.get("loudness_db"),
                    beats=result_data.get("beats"),
                    downbeats=result_data.get("downbeats"),
                    sections=result_data.get("sections"),
                    embeddings_shape=result_data.get("embeddings_shape"),
                    stems_url=result_data.get("stems_url"),
                    lrc_url=result_data.get("lrc_url"),
                    lrc_source=result_data.get("lrc_source"),
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
