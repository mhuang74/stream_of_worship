# Handover: YouTube LRC Generation Prototype

## Status: COMPLETE (as of 2026-02-12)

**All implementation tasks have been completed.**

### Part 1: Persist YouTube URL in Recording (COMPLETE)

All database and model changes have been implemented:

| File | Change | Status |
|------|--------|--------|
| `src/stream_of_worship/admin/db/schema.py` | Added `youtube_url TEXT` column after `r2_lrc_url` in CREATE_RECORDINGS_TABLE | Done |
| `src/stream_of_worship/admin/db/models.py` | Added `youtube_url: Optional[str] = None` to Recording dataclass; updated `from_row()` (youtube_url=row[9], all subsequent indices shifted +1); updated `to_dict()` | Done |
| `src/stream_of_worship/admin/db/client.py` | Updated `insert_recording()` SQL to include youtube_url (26 columns total); added ALTER TABLE migration in `initialize_schema()` with try/except for idempotency | Done |
| `src/stream_of_worship/admin/commands/audio.py` | Capture `youtube_url=video_info.get("webpage_url")` in download command; display youtube_url in `show_recording()` after r2_audio_url | Done |

**Verification needed:**
- Run `sow-admin db init` on existing DB to verify migration works
- Run `sow-admin audio download <song_id> --force` to verify URL is captured
- Run `sow-admin audio show <song_id>` to verify URL is displayed

---

### Part 2: New Script `poc/gen_lrc_youtube.py` (COMPLETE)

**Script created:** `/poc/gen_lrc_youtube.py`

**Dependency added:** New `transcription_youtube` extras in `pyproject.toml` with `youtube-transcript-api>=0.6.0`, `openai>=1.0.0`, and `typer>=0.12.0`

**CLI Options:**
- `song_id` (required): Look up song metadata, lyrics, and youtube_url from DB
- `--youtube-url` / `-u`: Override YouTube URL (skip DB lookup)
- `--output` / `-o`: Output file (default: stdout)
- `--lang`: Subtitle language (default: `"en-US"`)
- `--model`: LLM model override (default: `SOW_LLM_MODEL` env var)

**Usage:**
```bash
# Install dependencies
uv sync --extra transcription_youtube

# Run with song_id (looks up youtube_url from DB)
SOW_LLM_API_KEY=... SOW_LLM_BASE_URL=... SOW_LLM_MODEL=... \
uv run python poc/gen_lrc_youtube.py <song_id>

# Run with explicit YouTube URL
SOW_LLM_API_KEY=... SOW_LLM_BASE_URL=... SOW_LLM_MODEL=... \
uv run python poc/gen_lrc_youtube.py <song_id> --youtube-url https://youtu.be/VIDEO_ID

# Save to file
uv run python poc/gen_lrc_youtube.py <song_id> -o output.lrc --lang en-US
```

**Task:** Create a new POC script that downloads YouTube transcripts via `youtube-transcript-api`, corrects them against official lyrics via LLM, and outputs LRC format.

#### Step 1: Add Dependency
```bash
uv add --extra admin youtube-transcript-api
```

#### Step 2: Create `poc/gen_lrc_youtube.py`

Use `poc/gen_lrc_whisper.py` as reference. The script needs:

**CLI Interface (typer):**
- `song_id` (required): Look up song metadata, lyrics, and youtube_url from DB
- `--youtube-url` / `-u`: Override YouTube URL (skip DB lookup)
- `--output` / `-o`: Output file (default: stdout)
- `--lang`: Subtitle language (default: `"en"`)
- `--model`: LLM model override (default: `SOW_LLM_MODEL` env var)

**Step-by-step implementation:**

1. **Resolve YouTube URL & Extract Video ID:**
   - Load `AppConfig`, init `ReadOnlyClient`, `CatalogService`
   - Look up `catalog.get_song_with_recording(song_id)` for lyrics + recording
   - YouTube URL from: `--youtube-url` CLI flag > `recording.youtube_url` from DB
   - Extract video ID from URL (parse `v=` param or `youtu.be/` path)
   - Error if no URL available

2. **Download Transcript via `youtube-transcript-api`:**
   ```python
   from youtube_transcript_api import YouTubeTranscriptApi

   ytt_api = YouTubeTranscriptApi()
   transcript = ytt_api.fetch(video_id, languages=[lang])
   # transcript.snippets: list of {text, start, duration}
   # transcript.is_generated: bool
   ```
   Format transcript into timestamped text for the LLM prompt.

3. **Build LLM Correction Prompt:**
   See `report/lyrics_correction_prompt_2.txt` for improved prompt.
   Key points:
   - Compare auto-generated subtitle transcription against published Chinese lyrics
   - Correct each transcribed line to matching Chinese lyrics while preserving timecodes
   - Preserve number of lines and timecodes exactly
   - Output ONLY corrected lines in LRC format: `[mm:ss.xx] 中文歌词`

4. **Call LLM:**
   - OpenAI-compatible client (same pattern as `services/analysis/src/sow_analysis/workers/lrc.py`)
   - Read `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_MODEL` from env
   - `temperature=0.1` for deterministic output
   - CLI `--model` flag overrides env var

5. **Format as LRC and Output:**
   - Parse LLM response lines (expected `[mm:ss.xx] text`)
   - Strip blank lines, validate timestamp format via regex
   - Write to `--output` or stdout

**Verification:**
```bash
SOW_LLM_API_KEY=... SOW_LLM_BASE_URL=... SOW_LLM_MODEL=... \
uv run python poc/gen_lrc_youtube.py <song_id>
```

Expected output: Valid LRC with Chinese lyrics and reasonable timestamps.

---

## Reference Files

- Plan: `specs/test_youtube_lrc_driver_plan.md`
- LLM Prompt: `report/lyrics_correction_prompt_2.txt`
- Reference Script: `poc/gen_lrc_whisper.py`
- LLM Worker Pattern: `services/analysis/src/sow_analysis/workers/lrc.py`

## Key Technical Notes

1. **Schema Order:** The `youtube_url` column is at the END of the table (index 25) for both new and migrated databases due to SQLite's ALTER TABLE behavior.

2. **Backwards Compatibility:** The `Recording.from_row()` method handles both old (25 columns) and new (26 columns) database schemas. Old databases will have `youtube_url=None`.

3. **Existing Songs:** Songs downloaded before this change won't have `youtube_url` populated. Users must either re-download with `sow-admin audio download --force` or use the `--youtube-url` flag.

2. **Idempotent Migration:** The ALTER TABLE in `client.py` is wrapped in try/except to handle existing databases gracefully.

3. **LLM Environment Variables:** The script should use the same pattern as other LRC scripts:
   - `SOW_LLM_API_KEY`
   - `SOW_LLM_BASE_URL`
   - `SOW_LLM_MODEL`

4. **YouTube Video ID Extraction:** Handle both formats:
   - `https://www.youtube.com/watch?v=VIDEO_ID`
   - `https://youtu.be/VIDEO_ID`

---

## Verification Checklist

### Part 1 - Database Changes
- [ ] Run `sow-admin db init` on existing DB to verify migration works (youtube_url column added idempotently)
- [ ] Run `sow-admin audio download <song_id> --force` to verify URL is captured
- [ ] Run `sow-admin audio show <song_id>` to verify URL is displayed

### Part 2 - POC Script
- [ ] Install dependencies: `uv sync --extra transcription_youtube`
- [ ] Run script with valid song_id and environment variables set
- [ ] Verify output is valid LRC format with `[mm:ss.xx]` timestamps
- [ ] Verify lyrics are in Chinese (matching song lyrics)
- [ ] Verify timecodes match the song structure
