# Phase 6: LRC Generation Implementation Plan

## Overview

Implement the LRC (timestamped lyrics) generation worker in the Analysis Service using Whisper for transcription and OpenRouter LLM for alignment with scraped lyrics.

**Scope:** Analysis Service worker only (not CLI command - that depends on Phase 5)

## Architecture

```
POST /jobs/lrc → Queue → _process_lrc_job → generate_lrc()
                                              ↓
                                    1. Download audio from R2
                                    2. (Optional) Download vocals.wav stem
                                    3. Run Whisper transcription (word timestamps)
                                    4. LLM alignment via OpenRouter
                                    5. Generate .lrc file
                                    6. Upload to R2 → {hash}/lyrics.lrc
                                    7. Return lrc_url + line_count
```

## Existing Infrastructure (from Phase 4)

- `workers/lrc.py` - Stub raising `LRCWorkerNotImplementedError`
- `LrcJobRequest` model: `audio_url`, `content_hash`, `lyrics_text`, `options`
- `LrcOptions` model: `whisper_model = "large-v3"`
- `JobResult` with `lrc_url` and `line_count` fields
- `POST /jobs/lrc` endpoint fully wired
- `CacheManager.get_lrc_result()` / `save_lrc_result()` ready
- `R2Client.upload_lrc()` ready

## Files to Modify

### 1. `services/analysis/src/sow_analysis/models.py`

Extend `LrcOptions`:
```python
class LrcOptions(BaseModel):
    whisper_model: str = "large-v3"
    llm_model: str = "openai/gpt-4o-mini"  # NEW
    use_vocals_stem: bool = True           # NEW: prefer vocals stem for transcription
    language: str = "zh"                   # NEW: Whisper language hint
    force: bool = False                    # NEW: re-generate even if cached
```

### 2. `services/analysis/src/sow_analysis/config.py`

Add LLM + Whisper settings (generic, works with OpenRouter, nano-gpt.com, synthetic.new, etc.):
```python
# LLM Configuration (OpenAI-compatible API)
SOW_LLM_API_KEY: str = ""
SOW_LLM_BASE_URL: str = "https://openrouter.ai/api/v1"  # Default, can be changed

# Whisper Configuration
WHISPER_DEVICE: str = "cpu"
WHISPER_CACHE_DIR: Path = Path("/cache/whisper")
```

**Supported providers (all OpenAI-compatible):**
- OpenRouter: `https://openrouter.ai/api/v1`
- nano-gpt.com: `https://nano-gpt.com/api/v1`
- synthetic.new: `https://api.synthetic.new/v1`
- OpenAI direct: `https://api.openai.com/v1`

### 3. `services/analysis/pyproject.toml`

Add dependencies:
```toml
"openai-whisper>=20231117",  # Whisper transcription
"openai>=1.10.0",            # OpenAI client (works with OpenRouter)
```

### 4. `services/analysis/src/sow_analysis/workers/lrc.py` (Main Implementation)

**Replace stub with full implementation:**

```python
# Core classes
@dataclass
class WhisperWord:
    word: str
    start: float  # seconds
    end: float

@dataclass
class LRCLine:
    time_seconds: float
    text: str

    def format(self) -> str:
        """[mm:ss.xx] text"""
        minutes = int(self.time_seconds // 60)
        seconds = self.time_seconds % 60
        return f"[{minutes:02d}:{seconds:05.2f}] {self.text}"

# Main functions
async def _run_whisper_transcription(audio_path, model_name, language) -> List[WhisperWord]
async def _llm_align(lyrics_text, whisper_words, llm_model) -> List[LRCLine]
def _write_lrc(lines, output_path) -> int
async def generate_lrc(audio_path, lyrics_text, options, ...) -> tuple[Path, int]
```

**Key implementation details:**

1. **Whisper transcription:**
   - Use `run_in_executor` for sync whisper.transcribe()
   - Enable `word_timestamps=True`
   - Extract word-level timings from segments

2. **LLM alignment prompt:**
   - Provide scraped lyrics (gold standard text)
   - Provide Whisper words with timestamps
   - Ask LLM to return JSON: `[{"time_seconds": X, "text": "phrase"}]`
   - Retry up to 3 times on parse failure
   - Strip markdown code blocks from response

3. **Vocals stem preference:**
   - If `use_vocals_stem=True` and vocals.wav exists → use it
   - Otherwise fallback to full audio
   - Cleaner audio = better transcription

### 5. `services/analysis/src/sow_analysis/workers/queue.py`

Update `_process_lrc_job`:
```python
async def _process_lrc_job(self, job: Job) -> None:
    # 1. Download audio from R2
    # 2. Check for vocals stem (optional)
    # 3. Call generate_lrc()
    # 4. Upload LRC to R2
    # 5. Set job.result with lrc_url, line_count
```

Add import: `from .lrc import generate_lrc, LRCWorkerError`

### 6. `services/analysis/src/sow_analysis/storage/r2.py`

May need to add `check_exists()` method if not present:
```python
async def check_exists(self, s3_url: str) -> bool:
    """Check if object exists at S3 URL."""
```

## Error Handling

| Error | Exception | Handling |
|-------|-----------|----------|
| Missing SOW_LLM_API_KEY | `LLMConfigError` | Job fails with clear message |
| Whisper returns no words | `WhisperTranscriptionError` | Job fails |
| LLM invalid JSON (3 retries) | `LLMAlignmentError` | Job fails |
| Missing vocals stem | N/A | Fallback to full audio |
| R2 download failure | `Exception` | Job fails |

## Test File

Create `tests/services/analysis/test_lrc_worker.py`:

- `TestLRCLine` - LRC format output
- `TestWhisperIntegration` - Mock whisper model
- `TestLLMAlignment` - Mock OpenAI client, test JSON parsing
- `TestGenerateLRC` - Full pipeline with mocks
- `TestLRCJobQueue` - Job processing integration

**Test strategy:**
- Mock `whisper.load_model()` and `.transcribe()`
- Mock `openai.OpenAI()` and chat completions
- Use tempfile for audio paths
- ~15-20 new tests

## docker-compose.yml Update

Add environment variables:
```yaml
environment:
  - SOW_LLM_API_KEY=${SOW_LLM_API_KEY}
  - SOW_LLM_BASE_URL=${SOW_LLM_BASE_URL:-https://openrouter.ai/api/v1}
```

## Verification

1. **Unit tests:**
   ```bash
   cd services/analysis
   pytest tests/test_lrc_worker.py -v
   ```

2. **Integration test (requires API key):**
   ```bash
   # Use any OpenAI-compatible provider
   export SOW_LLM_API_KEY="..."
   export SOW_LLM_BASE_URL="https://openrouter.ai/api/v1"  # or nano-gpt.com, synthetic.new

   # Start service
   uvicorn sow_analysis.main:app --reload

   # Submit LRC job
   curl -X POST http://localhost:8000/api/v1/jobs/lrc \
     -H "Authorization: Bearer $ANALYSIS_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "audio_url": "s3://sow-audio/abc123/audio.mp3",
       "content_hash": "abc123...",
       "lyrics_text": "第一行歌詞\n第二行歌詞"
     }'

   # Poll for completion
   curl http://localhost:8000/api/v1/jobs/{job_id}
   ```

3. **Docker build:**
   ```bash
   cd services/analysis
   docker compose build
   docker compose up -d
   curl http://localhost:8000/api/v1/health
   ```

4. **Verify R2 upload:**
   Check that `{hash_prefix}/lyrics.lrc` exists after job completes

## Implementation Order

1. Update `models.py` - Extend LrcOptions
2. Update `config.py` - Add SOW_LLM_* and WHISPER_* settings
3. Update `pyproject.toml` - Add whisper, openai deps
4. Implement `workers/lrc.py` - Full Whisper + LLM pipeline
5. Update `workers/queue.py` - Call real generate_lrc()
6. Add `storage/r2.py` check_exists if needed
7. Write tests - `test_lrc_worker.py`
8. Update `docker-compose.yml` - SOW_LLM_* env vars
9. Run tests and verify

## Estimated Test Count

- New tests: ~20 (test_lrc_worker.py)
- Total project tests: ~210 → ~230

## Dependencies Summary

| Package | Purpose |
|---------|---------|
| `openai-whisper>=20231117` | Audio transcription with word timestamps |
| `openai>=1.10.0` | OpenAI client for OpenRouter API |

Both work on CPU. Whisper `large-v3` is ~3GB model, cached in `/cache/whisper`.
