# Plan: Two-Pronged LRC Generation (YouTube Primary + Whisper Fallback)

## Context

The current LRC generation pipeline uses Whisper transcription + LLM alignment exclusively. This can produce inaccurate timestamps, especially for Chinese worship songs. YouTube often has human-curated subtitles with accurate timing. By trying YouTube transcripts first and falling back to Whisper, we get more robust LRC generation with better timecodes.

**Scope:** YouTube transcript as primary path, existing Whisper+LLM as fallback. Qwen3 forced alignment deferred to a separate task (will be a separate Docker microservice).

## Changes

### 1. Add `youtube_url` to `LrcJobRequest` (analysis service)

**File:** `services/analysis/src/sow_analysis/models.py`

- Add `youtube_url: str = ""` optional field to `LrcJobRequest`

### 2. Add YouTube transcript module (analysis service)

**New file:** `services/analysis/src/sow_analysis/workers/youtube_transcript.py`

Port logic from `poc/gen_lrc_youtube.py` into a reusable async module:

- `extract_video_id(youtube_url: str) -> Optional[str]` — parse YouTube URL formats
- `async def fetch_youtube_transcript(video_id: str, languages: list[str]) -> list[TranscriptSnippet]` — download captions via `youtube-transcript-api`, try multiple language codes (`["zh-Hant", "zh-Hans", "zh", "en"]`)
- `build_correction_prompt(transcript_text: str, official_lyrics: list[str]) -> str` — LLM prompt to correct YouTube captions against official lyrics (adapted from POC)
- `parse_lrc_response(response: str) -> list[LRCLine]` — parse LLM output into LRC lines
- `async def youtube_transcript_to_lrc(youtube_url: str, lyrics_text: str, llm_model: str) -> list[LRCLine]` — end-to-end: extract video ID → fetch transcript → LLM correction → parse. Raises `YouTubeTranscriptError` on failure.

Reuses `LRCLine` dataclass from `lrc.py`.

### 3. Modify `generate_lrc` to try YouTube first (analysis service)

**File:** `services/analysis/src/sow_analysis/workers/lrc.py`

Add `youtube_url: Optional[str] = None` parameter to `generate_lrc()`.

New flow:
1. **If `youtube_url` provided:** Try `youtube_transcript_to_lrc()` first
   - On success: write LRC via existing `_write_lrc()`, return `(path, count, [])`
   - On failure: log warning, fall through to Whisper path
2. **Whisper fallback:** Existing logic unchanged

### 4. Update queue to pass `youtube_url` (analysis service)

**File:** `services/analysis/src/sow_analysis/workers/queue.py`

In `_process_lrc_job()`, pass `request.youtube_url` through to `generate_lrc()`.

### 5. Add `youtube-transcript-api` dependency

**File:** `services/analysis/pyproject.toml`

Add `"youtube-transcript-api>=1.0.0"` to dependencies.

### 6. Update admin `AnalysisClient.submit_lrc()` to pass `youtube_url`

**File:** `src/stream_of_worship/admin/services/analysis.py`

- Add `youtube_url: str = ""` parameter to `submit_lrc()`
- Include `"youtube_url": youtube_url` in the request payload

### 7. Update admin `_submit_lrc_job()` to look up and pass `youtube_url`

**File:** `src/stream_of_worship/admin/commands/audio.py`

- In `_submit_lrc_job()`: look up `recording.youtube_url` (already stored in DB from download) and pass it to `client.submit_lrc()`
- In `lrc_recording()` command: same — pass `recording.youtube_url` through

## File Summary

| File | Action |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | Add `youtube_url` to `LrcJobRequest` |
| `services/analysis/src/sow_analysis/workers/youtube_transcript.py` | **New** — YouTube transcript fetch + LLM correction |
| `services/analysis/src/sow_analysis/workers/lrc.py` | Add YouTube-first path in `generate_lrc()` |
| `services/analysis/src/sow_analysis/workers/queue.py` | Pass `youtube_url` through to `generate_lrc()` |
| `services/analysis/pyproject.toml` | Add `youtube-transcript-api` dependency |
| `src/stream_of_worship/admin/services/analysis.py` | Add `youtube_url` param to `submit_lrc()` |
| `src/stream_of_worship/admin/commands/audio.py` | Pass `recording.youtube_url` in LRC submissions |

## Verification

1. **Unit test:** `extract_video_id()` with various YouTube URL formats
2. **Unit test:** `build_correction_prompt()` produces valid prompt
3. **Unit test:** `generate_lrc()` falls back to Whisper when `youtube_url` is None
4. **Unit test:** `generate_lrc()` falls back to Whisper when YouTube transcript fails
5. **Run existing tests:** `cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/ -v`
