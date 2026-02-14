---
phase: 01-qwen3-service-foundation
plan: 03
type: execute
wave: 3
depends_on: ["02"]
files_modified: [services/qwen3/src/sow_qwen3/models.py, services/qwen3/src/sow_qwen3/storage/audio.py, services/qwen3/src/sow_qwen3/routes/align.py, services/qwen3/src/sow_qwen3/main.py]
autonomous: true

must_haves:
  truths:
    - "POST /api/v1/align accepts audio_url and lyrics_text"
    - "Service validates audio duration and rejects files >5 minutes with HTTP 400"
    - "Service downloads audio from R2/S3 URL before alignment"
    - "Service returns both LRC and JSON formats (configurable via request flag)"
    - "Timestamps mapped to original lyric lines (line-level, not character-level)"
  artifacts:
    - path: "services/qwen3/src/sow_qwen3/models.py"
      provides: "Pydantic request/response models for align API"
      contains: "AlignRequest", "AlignResponse", "OutputFormat"
    - path: "services/qwen3/src/sow_qwen3/storage/audio.py"
      provides: "Audio download from R2/S3"
      contains: "download_audio", "get_audio_duration"
    - path: "services/qwen3/src/sow_qwen3/routes/align.py"
      provides: "POST /api/v1/align endpoint"
      contains: "align_lyrics", "POST /api/v1/align"
  key_links:
    - from: "services/qwen3/src/sow_qwen3/routes/align.py"
      to: "services/qwen3/src/sow_qwen3/models.py"
      via: "request/response models"
      pattern: "AlignRequest|AlignResponse"
    - from: "services/qwen3/src/sow_qwen3/routes/align.py"
      to: "services/qwen3/src/sow_qwen3/storage/audio.py"
      via: "audio download"
      pattern: "download_audio"
    - from: "services/qwen3/src/sow_qwen3/routes/align.py"
      to: "services/qwen3/src/sow_qwen3/workers/aligner.py"
      via: "model.align call"
      pattern: "aligner\.align"
    - from: "services/qwen3/src/sow_qwen3/routes/align.py"
      to: "services/qwen3/src/sow_qwen3/main.py"
      via: "router include"
      pattern: "app.include_router.*align"
---

<objective>
Implement the align API endpoint with audio download, duration validation, forced alignment, and LRC/JSON output.

Purpose: Accept audio URL and lyrics text, download audio, validate 5-minute limit, run Qwen3ForcedAligner, map character-level timestamps to original lyric lines, and return results in LRC or JSON format. This is the core service functionality.

Output: POST /api/v1/align endpoint with complete alignment workflow.
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-RESEARCH.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-CONTEXT.md

# Reference for segments-to-lines mapping and LRC formatting
@/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py

# Reference for Pydantic models
@/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/models.py

# Reference for R2 client (for audio download)
@/home/mhuang/Development/stream_of_worship/src/stream_of_worship/admin/services/r2.py

# Config and aligner from previous plans
@services/qwen3/src/sow_qwen3/config.py
@services/qwen3/src/sow_qwen3/workers/aligner.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create Pydantic models in models.py</name>
  <files>services/qwen3/src/sow_qwen3/models.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/models.py with request/response models:

from pydantic import BaseModel, Field
from enum import Enum

class OutputFormat(str, Enum):
    LRC = "lrc"
    JSON = "json"

class AlignRequest(BaseModel):
    audio_url: str = Field(..., description="Audio file URL (R2/S3)")
    lyrics_text: str = Field(..., description="Lyrics text to align, one line per newline")
    language: str = Field(default="Chinese", description="Language hint")
    format: OutputFormat = Field(default=OutputFormat.LRC, description="Output format")

class LyricLine(BaseModel):
    start_time: float = Field(..., description="Line start time in seconds")
    end_time: float = Field(..., description="Line end time in seconds")
    text: str = Field(..., description="Lyric line text")

class AlignResponse(BaseModel):
    lrc_content: str | None = Field(None, description="LRC format output")
    json_data: list[LyricLine] | None = Field(None, description="JSON format output")
    line_count: int = Field(..., description="Number of aligned lines")
    duration_seconds: float = Field(..., description="Audio duration")

Following Analysis Service models.py pattern.

DO NOT include: JobStatus, JobType, JobResponse (Analysis Service specific).
  </action>
  <verify>
PYTHONPATH=services/qwen3/src python3 -c "from sow_qwen3.models import AlignRequest, AlignResponse; assert hasattr(AlignRequest, 'lyrics_text') and hasattr(AlignResponse, 'lrc_content')"
  </verify>
  <done>
models.py exists with AlignRequest, AlignResponse, OutputFormat, LyricLine classes
  </done>
</task>

<task type="auto">
  <name>Task 2: Create audio.py for download and duration validation</name>
  <files>services/qwen3/src/sow_qwen3/storage/audio.py, services/qwen3/src/sow_qwen3/storage/__init__.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/storage/ directory with audio.py:

1. Create storage/__init__.py (empty or with exports)

2. Create storage/audio.py:

from pathlib import Path
import boto3
from botocore.client import Config
from pydub import AudioSegment

from ..config import settings

def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds."""
    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0

def download_audio(url: str, cache_dir: Path) -> Path:
    """Download audio from R2/S3 URL to cache directory.

    Args:
        url: Audio file URL (R2/S3 URL)
        cache_dir: Cache directory for downloaded files

    Returns:
        Path to downloaded audio file

    Raises:
        ValueError: If R2 credentials not configured or URL is invalid
        RuntimeError: If download fails
    """
    # Parse URL to get object key (expecting R2/S3 URL format)
    # For simplicity, implement full R2 download using boto3 with settings
    # Filename from URL: extract last path component, or use hash of URL

    # Create client
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )

    # Extract object key from URL (parse R2 URL format)
    # Example: https://<endpoint>/<bucket>/<key>
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = cache_dir / f"audio_{hash(url)}.mp3"

    # Download file
    # For initial implementation, download using R2 key extraction
    # Extract bucket and key from URL or use direct URL fetch
    # Follow Analysis Service AssetCache.download_audio pattern

    return local_path

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
        raise ValueError(f"Audio duration ({duration:.1f}s) exceeds {max_seconds/60:.0f} minute limit")
    return duration

Note: For initial implementation, simplify download to extract key from known R2 URL pattern.
The full URL parsing can be enhanced in later iterations if needed.

DO NOT include: AssetCache class (Analysis Service specific), keep simple functions.
  </action>
  <verify>
PYTHONPATH=services/qwen3/src python3 -c "from sow_qwen3.storage.audio import get_audio_duration, validate_audio_duration; assert callable(get_audio_duration) and callable(validate_audio_duration)"
  </verify>
  <done>
audio.py exists with get_audio_duration, download_audio, validate_audio_duration functions
  </done>
</task>

<task type="auto">
  <name>Task 3: Create align.py route with alignment endpoint</name>
  <files>services/qwen3/src/sow_qwen3/routes/align.py, services/qwen3/src/sow_qwen3/main.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/routes/align.py with alignment endpoint:

from typing import List, Tuple
import logging
import re
from fastapi import APIRouter, HTTPException, Header

from ..models import AlignRequest, AlignResponse, LyricLine, OutputFormat
from ..storage.audio import download_audio, validate_audio_duration
from ..workers.aligner import get_aligner
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# API key verification (optional, enabled if API_KEY set)
async def verify_api_key(authorization: str | None = Header(None)) -> str:
    """Verify Bearer token matches API_KEY."""
    if not settings.API_KEY:
        # No API key configured, skip verification
        return "unconfigured"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    if token != settings.API_KEY:
        raise HTTPException(401, "Invalid API key")
    return token

def normalize_text(text: str) -> str:
    """Normalize text by removing whitespace and common punctuation."""
    return re.sub(r"[\s。，！？、；：\"''""''""''（）【】「」『』 ]+", "", text)

def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"

def map_segments_to_lines(
    segments: List[Tuple[float, float, str]],
    original_lines: List[str],
) -> List[Tuple[float, float, str]]:
    """Map character-level alignment segments to original lyric lines.

    Follow POC gen_lrc_qwen3.py map_segments_to_lines logic.
    - Build aligned text and track character positions
    - Normalize for comparison
    - Find each line in normalized aligned text
    - Compute min/max timestamps for each line from overlapping segments
    - Handle empty lines and missing matches
    """
    # Full implementation from POC
    # Return list of (start_time, end_time, text) tuples

@router.post("/api/v1/align", response_model=AlignResponse)
async def align_lyrics(
    request: AlignRequest,
    _api_key: str = Header(None)  # Use default to allow optional; actual verification in function
) -> AlignResponse:
    """Align lyrics to audio timestamps."""
    # Verify API key if configured
    if settings.API_KEY:
        if not _api_key or not _api_key.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid Authorization header")
        token = _api_key[7:]
        if token != settings.API_KEY:
            raise HTTPException(401, "Invalid API key")

    # Get aligner
    aligner = get_aligner()
    if aligner is None or not aligner.is_ready:
        raise HTTPException(503, "Model not loaded")

    # Split lyrics into lines
    lyrics_lines = [line.rstrip() for line in request.lyrics_text.splitlines()]
    # Remove trailing empty lines
    while lyrics_lines and not lyrics_lines[-1]:
        lyrics_lines.pop()

    if not lyrics_lines:
        raise HTTPException(400, "Lyrics are required for forced alignment")

    # Download audio
    try:
        audio_path = download_audio(request.audio_url, settings.CACHE_DIR)
    except Exception as e:
        logger.error(f"Failed to download audio: {e}")
        raise HTTPException(400, f"Failed to download audio: {str(e)}")

    # Validate duration (5 minute limit)
    try:
        duration_seconds = validate_audio_duration(audio_path)
    except ValueError as e:
        logger.error(f"Audio validation failed: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Failed to validate audio: {e}")
        raise HTTPException(500, f"Failed to validate audio: {str(e)}")

    # Run alignment
    try:
        results = await aligner.align(
            audio_path=audio_path,
            lyrics_text=request.lyrics_text,
            language=request.language,
        )
    except Exception as e:
        logger.error(f"Alignment failed: {e}")
        raise HTTPException(500, f"Alignment failed: {str(e)}")

    # Extract segments from results
    raw_segments = []
    for segment_list in results:
        for segment in segment_list:
            text = segment.text.strip()
            if text:
                raw_segments.append((segment.start_time, segment.end_time, text))

    # Map segments to lines
    line_alignments = map_segments_to_lines(raw_segments, lyrics_lines)

    # Build response based on format
    lrc_content = None
    json_data = None

    if request.format == OutputFormat.LRC:
        lrc_lines = []
        for start, _end, text in line_alignments:
            timestamp = format_timestamp(start)
            lrc_lines.append(f"{timestamp} {text}")
        lrc_content = "\n".join(lrc_lines)

    if request.format == OutputFormat.JSON or request.format == OutputFormat.JSON:
        json_data = [
            LyricLine(start_time=start, end_time=end, text=text)
            for start, end, text in line_alignments
        ]

    return AlignResponse(
        lrc_content=lrc_content,
        json_data=json_data,
        line_count=len(line_alignments),
        duration_seconds=duration_seconds,
    )

Update main.py:
- Import: from .routes import align
- Include router: app.include_router(align.router)

Following Analysis Service routes/jobs.py pattern but for alignment instead of job queue.

DO NOT include: JobRequest, JobResponse patterns (Analysis Service specific).
  </action>
  <verify>
cd services/qwen3 && PYTHONPATH=src python3 -c "from sow_qwen3.routes.align import align_lyrics; from sow_qwen3.models import AlignRequest; from fastapi.testclient import TestClient; from sow_qwen3.main import app; tc = TestClient(app); assert '/api/v1/align' in [route.path for route in app.routes]"
  </verify>
  <done>
align.py exists with POST /api/v1/align endpoint, main.py includes align router
  </done>
</task>

</tasks>

<verification>
- Verify align router is included: check app.routes contains /api/v1/align
- Verify API models import: AlignRequest, AlignResponse
- Verify audio functions import: download_audio, validate_audio_duration
- Verify semantics: 5-minute limit returns HTTP 400, nil lyrics returns HTTP 400, model not ready returns HTTP 503
</verification>

<success_criteria>
- POST /api/v1/align endpoint accepts audio_url and lyrics_text
- Audio duration >5 minutes returns HTTP 400 with error message
- Model not ready returns HTTP 503
- Returns LRC format when format="lrc" (default)
- Returns JSON format when format="json" with LyricLine objects
- Timestamps mapped to original lyric lines (preserves line structure)
  </success_criteria>

<output>
After completion, create `.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-03-SUMMARY.md`
</output>
