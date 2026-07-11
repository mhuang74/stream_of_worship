# Analysis Service — Log Declutter (v1)

## Goal

Reduce noise in console / `docker logs` output from the Analysis Service. Three classes of clutter to address:

1. Uvicorn access-log lines for job-status polling
2. Large "LLM Prompt" / "LLM Response" trace blocks
3. Large "Scraped Lyrics" / "Final Lyrics" trace blocks (plus Whisper phrases)

All large content dumps move to `DEBUG` and disappear from console by default; a new env var `SOW_LOG_LEVEL=DEBUG` re-enables them.

---

## 1. Silence uvicorn access logs for `GET /api/v1/jobs*`

**Problem trace:**
```
INFO:     100.121.214.94:55084 - "GET /api/v1/jobs/job_d4921796b0e9 HTTP/1.1" 200 OK
```

This is uvicorn's `uvicorn.access` logger. Uvicorn emits one such line per HTTP request. Polling `GET /api/v1/jobs/{id}` and `GET /api/v1/jobs` floods the console during render wait loops.

**Fix — add a `logging.Filter` in `logging_config.py`:**

```python
class _JobStatusAccessFilter(logging.Filter):
    """Drop uvicorn access-log records for GET /api/v1/jobs* (status + list polls)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn access record args: (host, method, path, http_version, status)
        if not isinstance(args, tuple) or len(args) < 5:
            return True  # not a uvicorn access record; let it through
        method = str(args[1]).upper()
        path = str(args[2])
        if method != "GET":
            return True
        # Match /api/v1/jobs and /api/v1/jobs/{id}; ignore query string
        return not path.startswith("/api/v1/jobs")
```

Attach in `configure_logging()` after handler setup, before `suppress_external` block:

```python
logging.getLogger("uvicorn.access").addFilter(_JobStatusAccessFilter())
```

**Behavior:**
- `GET /api/v1/jobs/job_xxx` → suppressed (status poll)
- `GET /api/v1/jobs` → suppressed (list poll)
- `POST /api/v1/jobs/analyze`, `POST /jobs/cancel`, etc. → still logged (submissions are meaningful events)
- `GET /api/v1/health`, `GET /` → still logged (unchanged)
- Filter is **always active**, independent of `SOW_LOG_LEVEL`. Status polls are pure noise even in DEBUG.

---

## 2. Move LLM Prompt/Response blocks to DEBUG

### 2a. `workers/lrc.py` — `_llm_align()` (lines 604–613, 666–671)

Change every `logger.info` in the two blocks to `logger.debug`. Keep the surrounding `=` separators at debug too (they're part of the same block):

```python
# Log the full LLM prompt
logger.debug("=" * 80)
logger.debug(f"LLM PROMPT (sent to model: {effective_model})")
logger.debug("=" * 80)
for line in prompt.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
```

And the response block (lines 666–671):
```python
logger.debug("=" * 80)
logger.debug(f"LLM RESPONSE (attempt {attempt + 1}/{max_retries})")
logger.debug("=" * 80)
for line in response_text.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
```

**Keep at INFO** (these are operational, not content dumps):
- `logger.info(f"Using LLM model: {effective_model}")` (line 634)
- `logger.info(f"LLM alignment attempt {attempt + 1}/{max_retries}")` (line 647)
- `logger.info(f"LLM call completed in {attempt_elapsed:.2f}s")` (line 663)
- `logger.info(f"Successfully aligned {len(lines)} lyric lines …")` (line 682–684)

### 2b. `workers/youtube_transcript.py` (lines 791–808)

Change both blocks to `logger.debug`:
```python
# Log the prompt
logger.debug("=" * 80)
logger.debug("YOUTUBE TRANSCRIPT LLM PROMPT")
logger.debug("=" * 80)
for line in prompt.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
…
logger.debug("=" * 80)
logger.debug("YOUTUBE TRANSCRIPT LLM RESPONSE")
logger.debug("=" * 80)
for line in response_text.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
```

**Keep at INFO:** `logger.info(f"YouTube transcript -> LRC completed: {len(lrc_lines)} lines in {elapsed:.2f}s")` (line 817).

---

## 3. Move Lyrics dumps and LRC file dumps to DEBUG

### 3a. `workers/lrc.py` — Whisper transcribed phrases (lines 279–286)

```python
logger.debug("=" * 80)
logger.debug("WHISPER TRANSCRIBED PHRASES (with timecodes)")
logger.debug("=" * 80)
for phrase in phrases:
    start_ts = _format_timestamp(phrase.start)
    end_ts = _format_timestamp(phrase.end)
    logger.debug(f"{start_ts} - {end_ts}  {phrase.text}")
logger.debug("=" * 80)
```

**Keep at INFO:** `logger.info(f"Transcribed {len(phrases)} phrases")` (line 278) — it's one summary line, not a content dump.

### 3b. `workers/lrc.py` — "SCRAPED LYRICS (Input)" in `try_youtube_transcript_lrc()` (lines 876–881)

```python
logger.debug("=" * 80)
logger.debug("SCRAPED LYRICS (Input)")
logger.debug("=" * 80)
for line in lyrics_text.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
```

### 3c. `workers/lrc.py` — "FINAL LRC FILE CONTENTS (via YouTube transcript)" (lines 902–908)

```python
logger.debug("=" * 80)
logger.debug("FINAL LRC FILE CONTENTS (via YouTube transcript)")
logger.debug("=" * 80)
with open(output_path, "r", encoding="utf-8") as f:
    for lrc_line in f:
        logger.debug(lrc_line.rstrip("\n"))
logger.debug("=" * 80)
```

**Keep at INFO:** the success/failure banner lines around it (898–901 `"LRC GENERATION: YouTube transcript path SUCCEEDED"` and `"Wrote N lines …"`). Those are operational status, not content dumps.

### 3d. `workers/lrc.py` — `generate_lrc()` second "SCRAPED LYRICS" block (lines 975–983)

```python
logger.debug("=" * 80)
logger.debug("SCRAPED LYRICS (Input)")
logger.debug("=" * 80)
for line in lyrics_text.split("\n"):
    logger.debug(line)
logger.debug("=" * 80)
logger.debug("=" * 80)
logger.debug("LRC GENERATION: Using Whisper transcription directly")
logger.debug("=" * 80)
```

(The "Using Whisper transcription directly" banner stays together with the lyrics block since it's part of the same dump; demote as a unit.)

**Keep at INFO:** `logger.info(f"Starting Whisper LRC generation for {audio_path}")` (line 986) — operational.

### 3e. `workers/lrc.py` — "FINAL LRC FILE CONTENTS" in `generate_lrc()` (lines 1016–1022)

```python
logger.debug("=" * 80)
logger.debug("FINAL LRC FILE CONTENTS")
logger.debug("=" * 80)
with open(output_path, "r", encoding="utf-8") as f:
    for lrc_line in f:
        logger.debug(lrc_line.rstrip("\n"))
logger.debug("=" * 80)
```

**Keep at INFO:** `logger.info(f"Wrote {line_count} lines to {output_path} (total LRC time: {total_elapsed:.2f}s)")` (line 1013) — operational.

---

## 4. Env var — `SOW_LOG_LEVEL` (re-enable DEBUG)

### 4a. `config.py`

Add new field in `Settings` (place near top, before R2 settings, since it governs framework behavior):

```python
# Logging
SOW_LOG_LEVEL: str = "INFO"
# Root log level for the service. Default INFO hides large content dumps
# (LLM prompts/responses, scraped/final lyrics, Whisper phrases).
# Set to DEBUG to surface them in console / docker logs for troubleshooting.
```

Add a validator to normalize to upper-case and restrict to a known set:

```python
@field_validator("SOW_LOG_LEVEL")
@classmethod
def _validate_log_level(cls, v: str) -> str:
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
    upper = v.upper()
    if upper not in allowed:
        raise ValueError(f"SOW_LOG_LEVEL must be one of {allowed}, got: {v!r}")
    return upper
```

### 4b. `main.py`

Replace the hard-coded INFO:

```python
configure_logging(level=logging.INFO, suppress_external=True)
```

with:

```python
configure_logging(
    level=getattr(logging, settings.SOW_LOG_LEVEL, logging.INFO),
    suppress_external=True,
)
```

(`settings` is already imported on line 16.)

`configure_logging` already sets the root logger level and uses a single `StreamHandler`, so raising it to `DEBUG` will surface all the demoted traces automatically — no per-module wiring needed.

### 4c. `.env.example`

Add a "Logging Configuration" section (after the Processing Config section, around line 197):

```bash
# ========================================
# Logging Configuration (Optional)
# ========================================

SOW_LOG_LEVEL="INFO"
# Root log level for the Analysis Service.
# Default "INFO" keeps the console/docker log clean: hides large content
# dumps (LLM prompts/responses, scraped lyrics, final LRC contents,
# Whisper transcribed phrases).
# Set to "DEBUG" to reveal those traces for troubleshooting.
# Options: DEBUG, INFO, WARNING, ERROR
```

### 4d. `docker-compose.yml`

Add to `x-common-env` anchor:

```yaml
SOW_LOG_LEVEL: ${SOW_LOG_LEVEL:-INFO}
```

(Place near `NATTEN_LOG_LEVEL: error` for grouping.)

---

## Files Changed (summary)

| File | Change |
|---|---|
| `src/sow_analysis/logging_config.py` | Add `_JobStatusAccessFilter`; attach to `uvicorn.access` logger in `configure_logging()` |
| `src/sow_analysis/config.py` | Add `SOW_LOG_LEVEL` field + validator |
| `src/sow_analysis/main.py` | Read `settings.SOW_LOG_LEVEL` and pass to `configure_logging` |
| `src/sow_analysis/workers/lrc.py` | Demote 5 content-dump blocks to `logger.debug` (LLM prompt, LLM response, Whisper phrases, 2× scraped lyrics, 2× final LRC contents, "Using Whisper directly" banner) |
| `src/sow_analysis/workers/youtube_transcript.py` | Demote 2 blocks (`YOUTUBE TRANSCRIPT LLM PROMPT`, `YOUTUBE TRANSCRIPT LLM RESPONSE`) to `logger.debug` |
| `.env.example` | Document `SOW_LOG_LEVEL` |
| `docker-compose.yml` | Add `SOW_LOG_LEVEL: ${SOW_LOG_LEVEL:-INFO}` to `x-common-env` |

---

## Verification

- `docker compose up analysis-dev`, then from the webapp trigger an LRC render:
  - Console must **not** show Scraped/Final Lyrics, LLM Prompt/Response, Whisper phrases blocks.
  - Console must **not** show `GET /api/v1/jobs/{id}` access lines during polling.
  - Console **must** still show: `Wrote N lines to … (total LRC time: …s)`, `LLM call completed in 0.62s`, `Successfully aligned 47 lyric lines`, `GET /api/v1/jobs/analyze` (POST submit), `POST /jobs/lrc`.
- Set `SOW_LOG_LEVEL=DEBUG` in `/opt/sow/.env` (or compose env), `docker compose up analysis-dev`, repeat LRC job:
  - All 5 previously-hidden blocks now appear in console.
  - `GET /api/v1/jobs/{id}` access lines remain filtered out (filter is unconditional).
- Run unit tests: `cd ops/analysis-service && uv run --extra dev pytest tests/ -v` — ensure no test asserts on the renamed logger calls or relies on `.info` for the demoted blocks (search tests for `SCRAPED LYRICS` / `LLM PROMPT` / `caplog`).

---

## Out of scope

- Per-module log levels (e.g. silence just `audio_separator`, `urllib3`). Already handled in `configure_logging(suppress_external=True)`.
- Structured/JSON logging. Not needed; current formatter is fine.
- Reducing the startup configuration table (`Startup configuration:` in main.py:224). That's a one-time log, not runtime clutter.
