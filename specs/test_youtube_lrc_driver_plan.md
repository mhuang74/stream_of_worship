# Plan: YouTube Subtitle LRC Generation Prototype

## Context

Current LRC generation prototypes (`poc/gen_lrc_whisper.py`, `gen_lrc_whisperx.py`, `gen_lrc_qwen3.py`) all rely on local Whisper-based transcription. A simpler approach leverages YouTube's auto-generated subtitles (already timed) and corrects them against official Chinese lyrics via LLM — no local Whisper needed.

Three changes needed:
1. **Schema/model**: Persist YouTube URL from `sow-admin audio download` in recordings table
2. **Audio show**: Display YouTube URL in `sow-admin audio show`
3. **New POC script**: `poc/gen_lrc_youtube.py` — download transcript via `youtube-transcript-api`, correct via LLM, output LRC

---

## Part 1: Persist YouTube URL in Recording

### 1.1 Schema — `src/stream_of_worship/admin/db/schema.py`
- Add `youtube_url TEXT` column to `CREATE_RECORDINGS_TABLE`, after `r2_lrc_url`

### 1.2 Model — `src/stream_of_worship/admin/db/models.py`
- Add `youtube_url: Optional[str] = None` to `Recording` dataclass (after `r2_lrc_url`)
- Update `from_row()`: insert `youtube_url=row[9]`, shift all subsequent indices +1 (row[10]…row[25])
- Update `to_dict()`: add `"youtube_url"` key

### 1.3 DB Client — `src/stream_of_worship/admin/db/client.py`
- Update `insert_recording()` SQL: add `youtube_url` to column list and values tuple (26 columns total)

### 1.4 Audio Download Command — `src/stream_of_worship/admin/commands/audio.py`
- After `preview_video()` succeeds (~line 471), capture `video_info["webpage_url"]`
- Pass `youtube_url=webpage_url` when constructing `Recording(...)` (~line 550)

### 1.5 Audio Show Command — `src/stream_of_worship/admin/commands/audio.py`
- In `show_recording()` (~line 842), add a line after `r2_audio_url` display:
  ```python
  if recording.youtube_url:
      info_lines.append(f"[cyan]YouTube URL:[/cyan] {recording.youtube_url}")
  ```

### 1.6 Migration for Existing DBs
- Add `ALTER TABLE recordings ADD COLUMN youtube_url TEXT;` in `initialize_schema()` (wrapped in try/except for idempotency)

---

## Part 2: New Script `poc/gen_lrc_youtube.py`

### 2.1 New Dependency
- Add `youtube-transcript-api` to `pyproject.toml` under new `transcription_youtube` extras via `uv add`

### 2.2 CLI Interface (typer)
Same pattern as `gen_lrc_whisper.py`:

| Argument/Option | Description |
|----------------|-------------|
| `song_id` (required) | Look up song metadata, lyrics, and youtube_url from DB |
| `--youtube-url` / `-u` | Override YouTube URL (skip DB lookup) |
| `--output` / `-o` | Output file (default: stdout) |
| `--lang` | Subtitle language (default: `"en-US"`) |
| `--model` | LLM model override (default: `SOW_LLM_MODEL` env var) |

### 2.3 Step 1: Resolve YouTube URL & Extract Video ID
1. Load `AppConfig`, init `ReadOnlyClient`, `CatalogService`
2. Look up `catalog.get_song_with_recording(song_id)` for lyrics + recording
3. YouTube URL from: `--youtube-url` CLI flag > `recording.youtube_url` from DB
4. Extract video ID from URL (parse `v=` param or `youtu.be/` path)
5. Error if no URL available

### 2.4 Step 2: Download Transcript via `youtube-transcript-api`
```python
from youtube_transcript_api import YouTubeTranscriptApi

ytt_api = YouTubeTranscriptApi()
transcript = ytt_api.fetch(video_id, languages=[lang])
# transcript.snippets: list of {text, start, duration}
# transcript.is_generated: bool
```
Format transcript into timestamped text for the LLM prompt:
```
00:00:12
With all my heart, I will give thanks and enter Your court.

00:00:19
Offering my praise to You, Lord; It's the fruit of my lips.
```

### 2.5 Step 3: Build LLM Correction Prompt
Improved prompt based on `report/lyrics_correction_prompt_2.txt`:

```
You are a lyrics correction assistant for Chinese worship songs.

## Task
Compare the auto-generated subtitle transcription (which may be in the wrong language or contain errors) against the published Chinese lyrics. Correct each transcribed line to the matching Chinese lyrics while preserving the original timecodes.

## Rules
1. Each transcribed line corresponds to a phrase in the published lyrics. Replace the transcribed text with the correct Chinese lyrics for that phrase.
2. Songs often repeat sections (verse, chorus). The transcription reflects what was actually sung — keep all repeated phrases with their timecodes.
3. Preserve the number of lines and their timecodes exactly. Only correct the text content.
4. If a transcribed line doesn't match any published lyrics (e.g. instrumental, audience noise), remove that line entirely.

## Transcribed Subtitle (auto-generated)
```
{formatted subtitle with timestamps}
```

## Published Lyrics (official, one unique phrase per line)
```
{official lyrics from song.lyrics_list, joined by newlines}
```

## Output Format
Output ONLY corrected lines in LRC format, one per line:
[mm:ss.xx] 中文歌词

No blank lines, no commentary, no markdown.
```

### 2.6 Step 4: Call LLM
OpenAI-compatible client (same pattern as `services/analysis/src/sow_analysis/workers/lrc.py`):
- Read `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_MODEL` from env
- `temperature=0.1` for deterministic output
- CLI `--model` flag overrides env var

### 2.7 Step 5: Format as LRC and Output
- Parse LLM response lines (expected `[mm:ss.xx] text`)
- Strip blank lines, validate timestamp format via regex
- Write to `--output` or stdout

---

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add new `transcription_youtube` extras with `youtube-transcript-api` and `openai` |
| `src/stream_of_worship/admin/db/schema.py` | Add `youtube_url TEXT` column |
| `src/stream_of_worship/admin/db/models.py` | Add field, update `from_row()`, `to_dict()` |
| `src/stream_of_worship/admin/db/client.py` | Update `insert_recording()` SQL |
| `src/stream_of_worship/admin/commands/audio.py` | Capture URL in download, display in show |
| `poc/gen_lrc_youtube.py` | **New file** |

## Verification

1. **Schema**: `sow-admin db init` — adds column on existing DB without error
2. **Download**: `sow-admin audio download <song_id> --force` — verify `youtube_url` populated
3. **Show**: `sow-admin audio show <song_id>` — displays YouTube URL
4. **POC script**:
   ```bash
   SOW_LLM_API_KEY=... SOW_LLM_BASE_URL=... SOW_LLM_MODEL=... \
   uv run python poc/gen_lrc_youtube.py <song_id>
   ```
   Verify output is valid LRC with Chinese lyrics and reasonable timestamps
