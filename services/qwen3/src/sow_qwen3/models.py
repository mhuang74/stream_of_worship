"""Pydantic models for API requests and responses."""

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    """Output format options for alignment results."""

    LRC = "lrc"
    JSON = "json"


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
