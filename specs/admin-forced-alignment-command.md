# Plan: Admin CLI `audio align-lrc` Command — Qwen3 Forced Alignment

## Overview

Add a new `sow-admin audio align-lrc` command that triggers the Qwen3 ForcedAligner service to re-align timestamps on an existing LRC file. This is a separate command from `audio lrc` to keep concerns clean: `lrc` generates LRC from scratch (YouTube/Whisper/Qwen3-ASR pipeline), while `align-lrc` uses the Qwen3 ForcedAligner to refine timestamps on an already-generated LRC.

## Current Architecture

### Qwen3 ForcedAligner Service
- **Location**: `services/qwen3/` — standalone FastAPI service in its own Docker container
- **Endpoint**: `POST /api/v1/align` — accepts `AlignRequest(audio_url, lyrics_text, language, format)` → returns `AlignResponse(lrc_content, json_data, line_count, duration_seconds)`
- **Limit**: 5-minute audio duration max
- **Docker**: Exposed on host port 8001, internal port 8000, reachable as `http://qwen3:8000` from analysis container

### Analysis Service
- **Location**: `services/analysis/` — main job processing service
- **Existing client**: `services/analysis/src/sow_analysis/services/qwen3_client.py` — `Qwen3Client` class with `align()` method that calls the Qwen3 service's `/api/v1/align` endpoint
- **Config**: `SOW_QWEN3_BASE_URL` (default: `http://qwen3:8000`), `SOW_QWEN3_API_KEY`
- **Job types**: `analyze`, `lrc`, `stem_separation`, `embedding`

### Admin CLI
- **Location**: `src/stream_of_worship/admin/commands/audio.py`
- **Analysis client**: `src/stream_of_worship/admin/services/analysis.py` — `AnalysisClient` class
- **Pattern**: CLI option → helper function → `AnalysisClient.submit_*()` → HTTP POST → job polling

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Command | New `audio align-lrc` | Clean separation from `lrc` pipeline; no option conflicts |
| API endpoint | New `POST /api/v1/jobs/forced-alignment` | Dedicated endpoint, own job type, no risk to existing LRC pipeline |
| Duration limit | Reject at CLI with error | Fail fast before submitting job |
| Input | `song_id` (looks up recording + lyrics from DB) | Consistent with `audio lrc` pattern |
| Output | Overwrites existing LRC on R2 | Refinement use case — replace timestamps |

## Implementation Plan

### Phase 1: Analysis Service — New Job Type & Endpoint

#### 1a. Add `FORCED_ALIGNMENT` job type

**File**: `services/analysis/src/sow_analysis/models.py`

```python
class JobType(str, Enum):
    ANALYZE = "analyze"
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"
    FORCED_ALIGNMENT = "forced_alignment"  # NEW
```

Add new request/options/result models:

```python
class ForcedAlignmentOptions(BaseModel):
    """Options for forced alignment jobs."""
    model_config = ConfigDict(extra="allow")
    language: str = "Chinese"  # Language hint for Qwen3 ForcedAligner
    force: bool = False  # Re-run even if cached

class ForcedAlignmentJobRequest(BaseModel):
    """Request to submit a forced alignment job."""
    audio_url: str
    content_hash: str
    lyrics_text: str
    song_title: str = ""
    options: ForcedAlignmentOptions = Field(default_factory=ForcedAlignmentOptions)
```

Add `lrc_source` value for forced alignment results — extend `JobResult.lrc_source` to include `"forced_alignment"`.

#### 1b. Add API route

**File**: `services/analysis/src/sow_analysis/routes/jobs.py`

Add new endpoint:

```python
@router.post("/jobs/forced-alignment", response_model=JobResponse)
async def submit_forced_alignment_job(
    request: ForcedAlignmentJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
```

#### 1c. Add job processing in queue

**File**: `services/analysis/src/sow_analysis/workers/queue.py`

Add `_process_forced_alignment_job()` method to `JobQueue`:

1. Download audio from R2 to temp dir
2. Call `Qwen3Client.align()` with `audio_url`, `lyrics_text`, `language`
3. Parse `AlignResponse.lrc_content` → write to temp `.lrc` file
4. Upload LRC to R2 (same pattern as LRC job: `{hash_prefix}/lyrics.{lang}.v2.lrc`)
5. Set `job.result = JobResult(lrc_url=..., line_count=..., lrc_source="forced_alignment")`
6. Handle `Qwen3ClientError` → fail job with descriptive error

Wire into `_process_job_with_semaphore()`:
```python
if job.type == JobType.FORCED_ALIGNMENT:
    await self._process_forced_alignment_job(job)
```

#### 1d. Update Job dataclass union type

**File**: `services/analysis/src/sow_analysis/models.py`

Update `Job.request` type union to include `ForcedAlignmentJobRequest`.

### Phase 2: Admin CLI — New `align-lrc` Command

#### 2a. Add `AnalysisClient.submit_forced_alignment()` method

**File**: `src/stream_of_worship/admin/services/analysis.py`

```python
def submit_forced_alignment(
    self,
    audio_url: str,
    content_hash: str,
    lyrics_text: str,
    song_title: str = "",
    language: str = "Chinese",
    force: bool = False,
) -> JobInfo:
```

Payload:
```json
{
    "audio_url": "...",
    "content_hash": "...",
    "lyrics_text": "...",
    "song_title": "...",
    "options": {
        "language": "Chinese",
        "force": false
    }
}
```

POST to `{base_url}/api/v1/jobs/forced-alignment`.

#### 2b. Add `audio align-lrc` CLI command

**File**: `src/stream_of_worship/admin/commands/audio.py`

New command following existing `lrc` pattern:

```python
@app.command("align-lrc")
def align_lrc_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to force-align LRC for"),
    language: str = typer.Option("Chinese", "--lang", help="Language hint: Chinese, English"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-alignment"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for alignment to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

Helper function `_submit_forced_alignment_single()`:
1. Look up recording by song_id → get `r2_audio_url`, `content_hash`
2. Look up song → get `lyrics_raw`
3. Validate recording has audio URL and lyrics
4. Check recording duration (if available) — reject if > 5 minutes
5. Submit via `analysis_client.submit_forced_alignment()`
6. Update DB: `lrc_status="processing"`, `lrc_job_id=job_id`
7. If `--wait`, poll until complete, then update DB with result

Helper function `_submit_forced_alignment_batch()` for `--stdin` mode.

#### 2c. Duration validation

The Admin CLI should check audio duration before submitting. Options:
- **Option A**: Use `ffprobe` or `mutagen` locally to check duration from the recording metadata (if stored in DB)
- **Option B**: Check `duration_seconds` field on the recording (if available from prior analysis)
- **Recommended**: Option B — check if the recording has `duration_seconds` from prior analysis. If not available, submit anyway and let the Qwen3 service reject it server-side.

### Phase 3: Docker Compose

No changes needed — the `qwen3` service is already defined in `services/analysis/docker-compose.yml` and the `SOW_QWEN3_BASE_URL` config is already present.

### Phase 4: Testing

#### 4a. Analysis Service tests

**File**: `services/analysis/tests/test_forced_alignment.py`

- Test `ForcedAlignmentJobRequest` model validation
- Test `POST /api/v1/jobs/forced-alignment` endpoint (auth, validation)
- Test `_process_forced_alignment_job()` with mocked `Qwen3Client`
- Test error handling: Qwen3 service unavailable, audio too long, alignment failure

#### 4b. Admin CLI tests

- Test `align-lrc` command with missing song_id
- Test `align-lrc` command with song that has no recording
- Test `align-lrc` command with song > 5 minutes
- Test `--wait` mode with mocked analysis client

## File Change Summary

| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | Add `FORCED_ALIGNMENT` job type, `ForcedAlignmentOptions`, `ForcedAlignmentJobRequest` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/forced-alignment` endpoint |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `_process_forced_alignment_job()`, wire into job dispatcher |
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_forced_alignment()` method |
| `src/stream_of_worship/admin/commands/audio.py` | Add `align-lrc` command + helpers |

## Request Flow

```
sow-admin audio align-lrc <song_id> --wait
  │
  ├─ DB lookup: recording (r2_audio_url, content_hash, duration_seconds)
  ├─ DB lookup: song (lyrics_raw)
  ├─ Validate: duration <= 5 min (if available)
  │
  ├─ AnalysisClient.submit_forced_alignment()
  │   └─ POST /api/v1/jobs/forced-alignment
  │       └─ JobQueue.submit(FORCED_ALIGNMENT, request)
  │           └─ Job(QUEUED) → asyncio.Queue
  │
  ├─ Poll: GET /api/v1/jobs/{job_id} (every 30s)
  │
  └─ On completion:
      └─ _process_forced_alignment_job()
          ├─ Download audio from R2
          ├─ Qwen3Client.align(audio_url, lyrics_text, language)
          │   └─ POST http://qwen3:8000/api/v1/align
          │       └─ Qwen3ForcedAligner.align() → character-level segments
          │       └─ map_segments_to_lines() → LRC timestamps
          ├─ Write LRC file
          ├─ Upload LRC to R2
          └─ JobResult(lrc_url, line_count, lrc_source="forced_alignment")
```

## Open Questions

None — all resolved via user Q&A.
