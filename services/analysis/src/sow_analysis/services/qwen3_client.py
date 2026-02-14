"""Qwen3 Service HTTP client for lyrics alignment."""

import logging
from enum import Enum
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OutputFormat(str, Enum):
    """Output format options for alignment results."""

    LRC = "lrc"
    JSON = "json"


class Qwen3ClientError(Exception):
    """Exception raised when Qwen3 client requests fail."""

    pass


class AlignRequest(BaseModel):
    """Request for lyrics alignment to audio."""

    audio_url: str = Field(..., description="Audio file URL (R2/S3)")
    lyrics_text: str = Field(..., description="Lyrics text to align, one line per newline")
    language: str = Field(default="Chinese", description="Language hint")
    format: OutputFormat = Field(default=OutputFormat.LRC, description="Output format")


class LyricLine(BaseModel):
    """A single aligned lyric line with timestamps."""

    start_time: float = Field(..., description="Line start time in seconds")
    end_time: float = Field(..., description="Line end time in seconds")
    text: str = Field(..., description="Lyric line text")


class AlignResponse(BaseModel):
    """Response from lyrics alignment."""

    lrc_content: str | None = Field(None, description="LRC format output")
    json_data: List[LyricLine] | None = Field(None, description="JSON format output")
    line_count: int = Field(..., description="Number of aligned lines")
    duration_seconds: float = Field(..., description="Audio duration")


class Qwen3Client:
    """HTTP client for Qwen3 Alignment Service."""

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        """Initialize Qwen3 client.

        Args:
            base_url: Base URL of Qwen3 service (e.g., "http://qwen3:8000")
            api_key: Optional API key for authentication
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def align(
        self,
        audio_url: str,
        lyrics_text: str,
        language: str = "Chinese",
        format: OutputFormat = OutputFormat.LRC,
    ) -> AlignResponse:
        """Request lyrics alignment from Qwen3 service.

        Args:
            audio_url: Audio file URL (R2/S3)
            lyrics_text: Lyrics text to align
            language: Language hint (default: "Chinese")
            format: Output format (default: LRC)

        Returns:
            AlignResponse containing the aligned lyrics

        Raises:
            Qwen3ClientError: If the HTTP request fails or returns an error
        """
        url = f"{self.base_url}/api/v1/align"
        request_body = AlignRequest(
            audio_url=audio_url,
            lyrics_text=lyrics_text,
            language=language,
            format=format,
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.info(f"Requesting alignment from Qwen3 service: {url}")
        logger.debug(f"Request body: {request_body.model_dump_json()}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=request_body.model_dump(),
                    headers=headers,
                    timeout=300.0,  # 5 minute timeout for alignment
                )
                response.raise_for_status()

                response_data = response.json()
                align_response = AlignResponse(**response_data)

                logger.info(
                    f"Alignment successful: {align_response.line_count} lines, "
                    f"{align_response.duration_seconds:.2f}s duration"
                )

                return align_response

        except httpx.HTTPStatusError as e:
            logger.error(f"Qwen3 service returned error status: {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise Qwen3ClientError(
                f"Qwen3 service error: {e.response.status_code} - {e.response.text}"
            ) from e
        except httpx.TimeoutException as e:
            logger.error("Qwen3 service request timed out")
            raise Qwen3ClientError("Qwen3 service request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Qwen3 service request failed: {e}")
            raise Qwen3ClientError(f"Failed to connect to Qwen3 service: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error calling Qwen3 service: {e}")
            raise Qwen3ClientError(f"Qwen3 service error: {e}") from e
