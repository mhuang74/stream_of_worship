# LRC Pipeline Redesign: Qwen3 ASR + MVSEP + Canonical Snap

## Context

The LRC generation worker (`services/analysis/src/sow_analysis/workers/lrc.py`) currently uses a 3-path pipeline:

1. **YouTube Transcript** (primary) — fetch captions, LLM-correct against official lyrics
2. **Whisper ASR** (fallback) — local `faster_whisper` transcription + LLM alignment
3. **Qwen3 ForcedAligner** (optional refinement) — local Docker service for timestamp polishing

This redesign restructures the pipeline to:

1. **YouTube Transcript** (still 1st priority, unchanged)
2. **Qwen3 ASR via DashScope API** (new 2nd priority) — cloud ASR with context biasing + canonical-line snap
3. **Whisper ASR** (3rd priority, unchanged) — local fallback
4. **Remove Qwen3 ForcedAligner** from automatic pipeline (keep service as optional manual tool)

### Why this redesign

| Problem | Current behavior | New behavior |
|---------|-----------------|--------------|
| Chinese character accuracy | Whisper misrecognizes CJK characters frequently | Qwen3-ASR-Flash is SOTA on Chinese singing voice + supports context biasing with canonical lyrics |
| Repeat handling | LLM alignment often consolidates repeated choruses | ASR reports what was actually sung, in order — repeats preserved automatically |
| Timestamp quality | Whisper phrase timestamps are coarse; Qwen3 ForcedAligner requires local GPU | Qwen3-ASR-Flash returns word/character-level timestamps natively |
| ForcedAligner results | Require manual adjustment; not directly usable | Remove from automatic pipeline; Admin CLI Lyrics Editor handles manual adjustment |
| Vocal stem quality for ASR | Full mix causes "heard guitar as word" errors | Dry vocals via MVSEP (cloud) or local fallback |

---

## Architecture

### Pipeline Overview

```
Audio (from R2)
  │
  ├─ Path 1: YouTube Transcript (unchanged, 1st priority)
  │   youtube_url → fetch captions → LLM correct → LRC
  │   On failure → fall through to Path 2
  │
  ├─ Path 2: Qwen3 ASR (NEW, 2nd priority)
  │   │
  │   ├─ [2a] Obtain clean vocal stem
  │   │   Check R2 for vocals_dry.flac → if missing, submit STEM_SEPARATION child job
  │   │   Fall back to full mix if stem separation fails
  │   │
  │   ├─ [2b] Qwen3-ASR-Flash transcription (DashScope cloud API)
  │   │   Auto-select: qwen3-asr-flash (≤5min, sync) or qwen3-asr-flash-filetrans (>5min, async)
  │   │   Context biasing with canonical lyrics (up to 10k chars)
  │   │   enable_words=True for word-level timestamps
  │   │   → segments [{text, start, end}] + word-level timestamps
  │   │
  │   ├─ [2c] Canonical-line snap (local, deterministic)
  │   │   reconstruct_lines_from_words() → sequential_canonical_snap()
  │   │   Replace ASR text with closest canonical line (fuzzy match ≥ threshold)
  │   │   Preserve ASR timestamps and repeat structure
  │   │
  │   ├─ [2d] LLM alignment fallback (if snap coverage < 70%)
  │   │   Use existing _llm_align() with Whisper-style prompt
  │   │
  │   └─ [2e] Write LRC
  │       lrc_source = "qwen3_asr"
  │
  └─ Path 3: Whisper ASR (unchanged, 3rd priority)
      Download audio → Whisper transcription → LLM alignment → Write LRC
      lrc_source = "whisper_asr"
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| DashScope API call origin | Direct from Analysis Service | Mirrors existing LLM client pattern; no new service to deploy |
| MVSEP integration | Reuse existing STEM_SEPARATION child job | Same pattern as current Whisper path; child job handles MVSEP vs local fallback |
| Qwen3 ForcedAligner | Remove from automatic pipeline; keep service as optional manual tool | Results require manual adjustment; Admin CLI Lyrics Editor handles this |
| Canonical-line snap | Snap first, LLM fallback if coverage < 70% | Deterministic, no LLM cost; LLM catches edge cases |
| ASR model selection | Auto-select by duration: flash ≤5min, filetrans >5min | Simpler for short songs; filetrans handles long songs |
| ASR caching | Cache raw DashScope API response keyed by audio_hash + ASR params | Matches Whisper cache pattern; re-extract segments on each access |
| Vocal stem source | Check R2 first, then submit STEM_SEPARATION child job | Same as current Whisper path; avoids redundant separation |
| Whisper fallback | Keep as 3rd priority | Safety net if both YouTube and Qwen3 ASR fail |

---

## Detailed Implementation Plan

### Phase 1: Add Qwen3 ASR Client Service

**Goal**: Create a production-grade async client for the DashScope Qwen3-ASR-Flash API.

#### 1.1 New file: `services/analysis/src/sow_analysis/services/qwen3_asr_client.py`

Port from POC `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` (functions `call_qwen3_asr`, `_call_qwen3_asr_filetrans`, `extract_segments`, `extract_word_timestamps`, `extract_asr_text`).

```python
class Qwen3AsrClient:
    """Async client for DashScope Qwen3-ASR-Flash API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1",
        model: str = "qwen3-asr-flash",
        filetrans_model: str = "qwen3-asr-flash-filetrans",
        timeout: float = 120.0,
    ): ...

    async def transcribe(
        self,
        audio_path: Path,
        language: str = "zh",
        context: Optional[str] = None,
        enable_words: bool = True,
    ) -> AsrResult:
        """Transcribe audio file.

        Auto-selects model based on audio duration:
        - ≤5min: qwen3-asr-flash (synchronous)
        - >5min: qwen3-asr-flash-filetrans (async, requires OSS upload)
        """
        ...

    async def transcribe_sync(
        self,
        audio_path: Path,
        language: str = "zh",
        context: Optional[str] = None,
        enable_words: bool = True,
    ) -> AsrResult:
        """Synchronous transcription via qwen3-asr-flash (≤5min audio)."""
        ...

    async def transcribe_filetrans(
        self,
        audio_path: Path,
        language: str = "zh",
        context: Optional[str] = None,
        enable_words: bool = True,
    ) -> AsrResult:
        """Async file transcription via qwen3-asr-flash-filetrans (>5min audio).

        Uploads to DashScope OSS, submits async task, polls until complete.
        """
        ...


@dataclass
class AsrSegment:
    """A transcription segment with timing."""
    text: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class AsrWord:
    """A word-level timestamp entry."""
    text: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class AsrResult:
    """Result from Qwen3-ASR transcription."""
    segments: List[AsrSegment]
    words: List[AsrWord]
    raw_response: dict  # For caching
    text: str  # Concatenated sentence texts
```

Key implementation details:
- Use `httpx.AsyncClient` for HTTP calls (consistent with `mvsep_client.py` and `qwen3_client.py`)
- **Do NOT use the `dashscope` Python SDK** — it's synchronous and adds a heavy dependency. Instead, make direct HTTP calls to the DashScope REST API
- For `filetrans`: upload audio to DashScope OSS via their upload endpoint, then submit async task and poll
- Context biasing: pass as system message with `{"text": context}` content
- ASR options: `enable_itn=False`, `enable_words=True`, `language="zh"`
- Parse response: extract `sentences` (segment-level) and `words` (word-level) from `audio_transcription_results`
- For `filetrans`: fetch `transcription_url` and parse the JSON response
- Error handling: raise `Qwen3AsrError` on API errors, with specific subclasses for timeout, rate limit, invalid key

#### 1.2 New file: `services/analysis/src/sow_analysis/services/qwen3_asr_oss.py`

Port `_upload_to_oss()` from POC. Handles uploading audio to DashScope OSS for the filetrans path.

```python
async def upload_to_oss(
    audio_path: Path,
    api_key: str,
    model: str,
    base_url: str,
) -> str:
    """Upload audio to DashScope OSS and return the oss:// URL."""
    ...
```

#### 1.3 Update: `services/analysis/src/sow_analysis/services/__init__.py`

Add exports:
```python
from .qwen3_asr_client import Qwen3AsrClient, Qwen3AsrError, AsrResult, AsrSegment, AsrWord
```

#### 1.4 Update: `services/analysis/src/sow_analysis/config.py`

Add new settings:
```python
# Qwen3 ASR (DashScope cloud API)
SOW_DASHSCOPE_ASR_API_KEY: str = ""
SOW_DASHSCOPE_ASR_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/api/v1"
SOW_DASHSCOPE_ASR_MODEL: str = "qwen3-asr-flash"
SOW_DASHSCOPE_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
SOW_DASHSCOPE_ASR_TIMEOUT: float = 120.0
SOW_DASHSCOPE_ASR_MAX_SYNC_DURATION: float = 300.0  # 5 minutes
SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS: int = 10000
```

#### 1.5 Update: `services/analysis/pyproject.toml`

Add `dashscope` is NOT needed (we use direct HTTP). No new dependencies required — `httpx` is already present.

#### 1.6 New test: `services/analysis/tests/services/test_qwen3_asr_client.py`

Test cases:
- `test_transcribe_sync_success` — mock HTTP response, verify AsrResult parsing
- `test_transcribe_filetrans_success` — mock OSS upload + async task + polling
- `test_auto_select_sync_model` — audio ≤5min uses sync model
- `test_auto_select_filetrans_model` — audio >5min uses filetrans model
- `test_context_biasing` — context string passed in system message
- `test_enable_words` — word-level timestamps extracted
- `test_api_error_raises` — non-200 response raises Qwen3AsrError
- `test_filetrans_polling` — polls until SUCCEEDED status
- `test_filetrans_failure` — FAILED status raises Qwen3AsrError
- `test_empty_response` — no segments raises Qwen3AsrError

---

### Phase 2: Add Canonical-Line Snap Service

**Goal**: Port the POC's `reconstruct_lines_from_words()` + `sequential_canonical_snap()` into a production-grade, independently testable service module.

#### 2.1 New file: `services/analysis/src/sow_analysis/services/canonical_snap.py`

Port from POC `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` (functions `reconstruct_lines_from_words`, `build_aligned_text`, `sequential_canonical_snap`, `verify_asr_quality`, plus helpers `_normalize_for_matching`, `_count_cjk_chars`, `_strip_cjk_spaces`, `detect_chinese_script`).

```python
def detect_chinese_script(text: str) -> str:
    """Detect whether Chinese text is traditional or simplified."""
    ...

def canonical_line_snap(
    asr_segments: List[AsrSegment],
    lyrics_lines: List[str],
    asr_words: Optional[List[AsrWord]] = None,
    threshold: float = 0.60,
) -> List[LRCLine]:
    """Snap ASR segments to canonical lyrics using sequential fuzzy matching.

    If word-level timestamps are available, first reconstruct lines from words
    using reconstruct_lines_from_words(), then snap to canonical.

    Args:
        asr_segments: ASR transcription segments with timestamps
        lyrics_lines: Official canonical lyric lines
        asr_words: Optional word-level timestamps for better line reconstruction
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        List of LRCLine with canonical text + ASR timestamps
    """
    ...

def reconstruct_lines_from_words(
    asr_words: List[AsrWord],
    canonical_lines: List[str],
    start_canonical_idx: int = 0,
) -> tuple[list[tuple[int, list[int]]], int]:
    """Map word-level ASR timestamps to canonical lyric lines.

    Uses fuzzy matching with lookahead and backtracking to handle
    repeated choruses and verse reordering.
    """
    ...

def verify_asr_quality(
    asr_text: str,
    canonical_lyrics: List[str],
) -> tuple[float, str]:
    """Compute overall fuzzy match score between ASR text and canonical lyrics.

    Returns (score, label) where label is "high"/"moderate"/"low".
    Diagnostic only — does not affect pipeline behavior.
    """
    ...
```

Key differences from POC:
- Input: `List[AsrSegment]` / `List[AsrWord]` instead of raw tuples
- Output: `List[LRCLine]` instead of raw tuples
- Logging via `logger` instead of `typer.echo`
- No file I/O, no diagnostic file writing
- Standalone module, no ASR code mixed in

Dependencies to add to `services/analysis/pyproject.toml`:
```toml
"rapidfuzz>=3.0.0",  # Fuzzy matching
"zhconv>=1.4.0",     # Chinese script conversion
```

#### 2.2 New test: `services/analysis/tests/services/test_canonical_snap.py`

Test cases:
- `test_detect_chinese_script_simplified` — Returns "zh-hans"
- `test_detect_chinese_script_traditional` — Returns "zh-hant"
- `test_canonical_snap_exact_match` — ASR text matches canonical exactly
- `test_canonical_snap_partial_match` — Fuzzy match replaces ASR text
- `test_canonical_snap_below_threshold` — Low-scoring segments keep ASR text
- `test_canonical_snap_preserves_repeats` — Chorus appears multiple times with different timestamps
- `test_canonical_snap_empty_input` — Empty → empty
- `test_reconstruct_lines_from_words_basic` — Words mapped to canonical lines
- `test_reconstruct_lines_wrap_around` — Chorus repeat wraps to earlier canonical line
- `test_verify_asr_quality_high` — Score ≥ 0.8 → "high"
- `test_verify_asr_quality_moderate` — Score 0.5-0.8 → "moderate"
- `test_verify_asr_quality_low` — Score < 0.5 → "low"

---

### Phase 3: Add Qwen3 ASR Cache

**Goal**: Cache raw DashScope API responses to avoid redundant API calls and costs.

#### 3.1 New file: `services/analysis/src/sow_analysis/services/qwen3_asr_cache.py`

Follow the existing Whisper cache pattern from `services/analysis/src/sow_analysis/workers/queue.py` (lines ~400-450).

```python
@dataclass
class CachedAsrResult:
    """Cached Qwen3 ASR transcription result."""
    raw_response: dict
    cached_at: float
    audio_hash: str
    params_hash: str


def get_asr_cache_path(cache_dir: Path, content_hash: str) -> Path:
    """Get cache file path for Qwen3 ASR result.

    Keyed on content_hash (audio only), same as Whisper cache.
    """
    return cache_dir / f"{content_hash[:32]}_qwen3_asr.json"


def save_asr_cache(cache_dir: Path, content_hash: str, raw_response: dict, params: dict) -> None:
    """Save raw ASR response to cache."""
    ...


def load_asr_cache(cache_dir: Path, content_hash: str, params: dict) -> Optional[dict]:
    """Load cached raw ASR response.

    Returns None on cache miss or parameter mismatch.
    """
    ...
```

Cache key: `content_hash[:32]` (audio hash only, same as Whisper cache)
Cache file: `{cache_dir}/{hash_prefix}_qwen3_asr.json`
Cache schema:
```json
{
  "cache_version": 1,
  "raw_response": { ... },
  "params": {
    "model": "qwen3-asr-flash",
    "language": "zh",
    "context_hash": "abc12345",
    "enable_words": true
  },
  "cached_at": 1713456789.0
}
```

Parameter validation on load: if any ASR-affecting parameter differs, treat as cache miss.

---

### Phase 4: Modify LRC Worker

**Goal**: Wire the new Qwen3 ASR path into the LRC worker as 2nd priority (between YouTube and Whisper).

#### 4.1 Modify: `services/analysis/src/sow_analysis/workers/lrc.py`

Add new function `_run_qwen3_asr_transcription()`:

```python
async def _run_qwen3_asr_transcription(
    audio_path: Path,
    lyrics_text: Optional[str] = None,
    content_hash: Optional[str] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
) -> tuple[List[WhisperPhrase], List[AsrWord], dict]:
    """Run Qwen3-ASR-Flash transcription via DashScope cloud API.

    Args:
        audio_path: Path to audio file (preferably dry vocals stem)
        lyrics_text: Optional lyrics for context biasing
        content_hash: Audio content hash for caching
        local_model_semaphore: Not used (cloud API), kept for interface consistency

    Returns:
        Tuple of (segments as WhisperPhrase list, word-level timestamps, raw API response)

    Raises:
        Qwen3AsrError: If DashScope API call fails
    """
    ...
```

Implementation:
1. Check cache (if `content_hash` provided and `force_whisper=False`)
2. Build context string from `lyrics_text` (truncate to `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS`)
3. Instantiate `Qwen3AsrClient` from settings
4. Call `client.transcribe(audio_path, language="zh", context=context, enable_words=True)`
5. Parse response into `List[WhisperPhrase]` (segments) and `List[AsrWord]` (word-level)
6. Save to cache if `content_hash` provided
7. Return segments, words, and raw response

Add new function `_qwen3_asr_to_lrc()`:

```python
async def _qwen3_asr_to_lrc(
    audio_path: Path,
    lyrics_text: str,
    content_hash: Optional[str] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
) -> tuple[List[LRCLine], List[WhisperPhrase]]:
    """Generate LRC via Qwen3 ASR + canonical-line snap.

    Pipeline: Qwen3-ASR transcription → canonical-line snap → LLM fallback if needed

    Returns:
        Tuple of (LRC lines, ASR segments as WhisperPhrase list)
    """
    # Step 1: Run Qwen3 ASR
    asr_phrases, asr_words, raw_response = await _run_qwen3_asr_transcription(
        audio_path, lyrics_text, content_hash, local_model_semaphore
    )

    # Step 2: Canonical-line snap
    lyrics_lines = [line for line in lyrics_text.split("\n") if line.strip()]
    lrc_lines = canonical_line_snap(
        asr_segments=[AsrSegment(text=p.text, start=p.start, end=p.end) for p in asr_phrases],
        lyrics_lines=lyrics_lines,
        asr_words=asr_words,
        threshold=0.60,
    )

    # Step 3: Coverage check — fall back to LLM if snap coverage < 70%
    expected_lines = len(lyrics_lines)
    matched_lines = len([l for l in lrc_lines if l.text in lyrics_lines])
    coverage = matched_lines / max(expected_lines, 1)

    if coverage < 0.70:
        logger.warning(
            f"Canonical snap coverage {coverage:.0%} < 70%, falling back to LLM alignment"
        )
        lrc_lines = await _llm_align(lyrics_text, asr_phrases, llm_model="")

    return lrc_lines, asr_phrases
```

Modify `generate_lrc()`:

```python
async def generate_lrc(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Optional[Path] = None,
    cached_phrases: Optional[List[WhisperPhrase]] = None,
    youtube_url: Optional[str] = None,
    content_hash: Optional[str] = None,
    vocals_stem_url: Optional[str] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
) -> tuple[Path, int, List[WhisperPhrase]]:

    # Path 1: YouTube transcript (unchanged, 1st priority)
    if youtube_url:
        result = await try_youtube_transcript_lrc(youtube_url, lyrics_text, options, output_path)
        if result is not None:
            return result

    # Path 2: Qwen3 ASR (NEW, 2nd priority)
    if options.use_qwen3_asr:
        try:
            lrc_lines, asr_phrases = await _qwen3_asr_to_lrc(
                audio_path=audio_path,
                lyrics_text=lyrics_text,
                content_hash=content_hash,
                local_model_semaphore=local_model_semaphore,
            )
            line_count = _write_lrc(lrc_lines, output_path)
            return output_path, line_count, asr_phrases
        except Qwen3AsrError as e:
            logger.warning(f"Qwen3 ASR path failed: {e}. Falling back to Whisper.")

    # Path 3: Whisper ASR (unchanged, 3rd priority)
    # ... existing Whisper + LLM alignment code ...
```

#### 4.2 Modify: `services/analysis/src/sow_analysis/models.py`

Update `LrcOptions`:

```python
class LrcOptions(BaseModel):
    """Options for LRC generation jobs."""

    whisper_model: str = "large-v3"
    llm_model: str = ""
    use_vocals_stem: bool = True
    language: str = "zh"
    force: bool = False
    force_whisper: bool = False

    # NEW: Qwen3 ASR options
    use_qwen3_asr: bool = True  # Use Qwen3-ASR-Flash via DashScope (2nd priority after YouTube)
    qwen3_asr_context_max_chars: int = 10000  # Max chars for context biasing
    snap_threshold: float = 0.60  # Minimum fuzzy score for canonical-line snap

    # DEPRECATED: Qwen3 forced alignment (removed from automatic pipeline)
    use_qwen3: bool = False  # Changed default from True to False
    max_qwen3_duration: int = 300  # Kept for backward compat, no longer used in auto pipeline
```

#### 4.3 Modify: `services/analysis/src/sow_analysis/workers/queue.py`

Update `_process_lrc_job()` to:

1. **Pass `use_qwen3_asr` option** to `generate_lrc()` (from request options or default)
2. **Handle Qwen3 ASR path for vocal stems**: When `use_qwen3_asr=True` and `use_vocals_stem=True`, the existing stem resolution logic (check R2 → submit child STEM_SEPARATION job) applies identically. No changes needed to the stem resolution flow.
3. **Update job stages**: Add new stages for Qwen3 ASR path:

```python
# New stages to add:
"trying_qwen3_asr"           # Attempting Qwen3 ASR path
"qwen3_asr_transcribing"      # Running Qwen3-ASR-Flash transcription
"qwen3_asr_snapping"          # Running canonical-line snap
"qwen3_asr_done"              # Qwen3 ASR path succeeded
```

4. **Remove Qwen3 ForcedAligner refinement step**: Remove the `if options.use_qwen3 and content_hash:` block from `generate_lrc()` in `lrc.py`. The `_qwen3_refine()` function and `_parse_qwen3_lrc()` are kept in the file but no longer called automatically.

5. **Update `lrc_source` tracking**: Add `"qwen3_asr"` as a valid source value.

---

### Phase 5: Update Job Queue Orchestrator

**Goal**: Wire the Qwen3 ASR path into the job queue with proper stage tracking and vocal stem handling.

#### 5.1 Modify: `services/analysis/src/sow_analysis/workers/queue.py`

In `_process_lrc_job()`, after the YouTube path fails and before the Whisper path:

```python
# Path 2: Qwen3 ASR (2nd priority)
if options.use_qwen3_asr:
    await job_store.update_job(job_id, stage="trying_qwen3_asr")

    # Resolve vocal stem (same logic as Whisper path)
    effective_audio_path = audio_path
    effective_vocals_stem_url = None

    if options.use_vocals_stem:
        # Check R2 for existing vocals_dry.flac
        vocals_url = await get_vocals_dry_url(content_hash, r2_client)
        if vocals_url:
            effective_vocals_stem_url = vocals_url
            await job_store.update_job(job_id, stage="using_vocals_stem")
        else:
            # Submit child STEM_SEPARATION job
            await job_store.update_job(job_id, stage="submitting_stem_separation_child")
            child_id = await submit_stem_separation_child(...)
            await job_store.update_job(job_id, stage=f"awaiting_stem_separation:{child_id}")
            # Poll for completion (same as existing Whisper path)
            ...

    try:
        result = await generate_lrc(
            audio_path=effective_audio_path,
            lyrics_text=lyrics_text,
            options=options,
            output_path=lrc_path,
            youtube_url=None,  # Already tried and failed
            content_hash=content_hash,
            vocals_stem_url=effective_vocals_stem_url,
            local_model_semaphore=local_model_semaphore,
        )
        # Success — upload and finalize
        ...
    except Qwen3AsrError as e:
        logger.warning(f"Qwen3 ASR path failed: {e}. Falling back to Whisper.")
        # Fall through to Whisper path below

# Path 3: Whisper ASR (3rd priority, existing code)
...
```

---

### Phase 6: Remove Qwen3 ForcedAligner from Automatic Pipeline

**Goal**: Disable the automatic Qwen3 ForcedAligner refinement step while keeping the service available for manual use.

#### 6.1 Modify: `services/analysis/src/sow_analysis/workers/lrc.py`

- Change `use_qwen3` default from `True` to `False` in `LrcOptions`
- Remove the Qwen3 refinement block from `generate_lrc()` (lines 747-801)
- Keep `_qwen3_refine()` and `_parse_qwen3_lrc()` functions in the file (they may be used manually)
- Add a comment marking them as "available for manual use via Admin CLI"

#### 6.2 Keep: `services/qwen3/` directory

- The Qwen3 ForcedAligner service remains deployed and available
- Admin CLI can still call it manually if needed
- No code changes to the qwen3 service itself

#### 6.3 Keep: `services/analysis/src/sow_analysis/services/qwen3_client.py`

- The HTTP client remains available for manual/admin use
- No longer called automatically from the LRC pipeline

---

### Phase 7: Update API and Admin CLI

**Goal**: Expose the new Qwen3 ASR options through the API and admin CLI.

#### 7.1 Modify: `services/analysis/src/sow_analysis/routes.py`

Update the LRC job submission endpoint to accept new options:
- `use_qwen3_asr` (bool, default True)
- `qwen3_asr_context_max_chars` (int, default 10000)
- `snap_threshold` (float, default 0.60)

#### 7.2 Modify: Admin CLI LRC commands

Update `src/stream_of_worship/admin/services/analysis.py` to:
- Pass `use_qwen3_asr=True` by default when submitting LRC jobs
- Add `--no-qwen3-asr` flag to disable Qwen3 ASR and fall back to Whisper
- Add `--snap-threshold` flag for canonical-line snap threshold tuning

---

### Phase 8: Testing and Validation

#### 8.1 Unit tests

| Test file | Tests |
|-----------|-------|
| `tests/services/test_qwen3_asr_client.py` | DashScope API client: sync/filetrans, auto-select, context biasing, error handling |
| `tests/services/test_canonical_snap.py` | Snap algorithm: exact/partial match, repeats, threshold, wrap-around |
| `tests/services/test_qwen3_asr_cache.py` | Cache: save/load, parameter validation, cache miss |
| `tests/workers/test_lrc_qwen3_asr.py` | LRC worker: Qwen3 ASR path, fallback to Whisper, snap + LLM fallback |

#### 8.2 Integration tests

- Submit LRC job with `use_qwen3_asr=True` → verify Qwen3 ASR path is used
- Submit LRC job with `use_qwen3_asr=False` → verify Whisper path is used
- Submit LRC job with YouTube URL → verify YouTube path is tried first
- Submit LRC job with YouTube URL that fails → verify Qwen3 ASR is tried second
- Submit LRC job with both YouTube and Qwen3 ASR failing → verify Whisper fallback

#### 8.3 End-to-end validation

1. **Benchmark on 10 known-good songs**: Regenerate LRC via Qwen3 ASR path, compare against hand-corrected versions. Target: ≥95% character match, ≤0.5s avg timestamp delta.
2. **Fallback testing**: Force each path to fail independently and verify correct fallback.
3. **Cost validation**: Measure DashScope API cost per song (~$0.008 expected).
4. **Vocal stem quality**: Compare LRC quality with dry vocals vs full mix.

---

## New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SOW_DASHSCOPE_ASR_API_KEY` | str | `""` | **Required** for Qwen3 ASR path. DashScope API key from Alibaba Cloud Model Studio. |
| `SOW_DASHSCOPE_ASR_BASE_URL` | str | `https://dashscope-intl.aliyuncs.com/api/v1` | Regional endpoint: `intl` (Singapore), `cn` (Beijing), `us` (Virginia) |
| `SOW_DASHSCOPE_ASR_MODEL` | str | `qwen3-asr-flash` | Sync model for ≤5min audio |
| `SOW_DASHSCOPE_ASR_FILETRANS_MODEL` | str | `qwen3-asr-flash-filetrans` | Async model for >5min audio |
| `SOW_DASHSCOPE_ASR_TIMEOUT` | float | `120.0` | HTTP timeout in seconds |
| `SOW_DASHSCOPE_ASR_MAX_SYNC_DURATION` | float | `300.0` | Max duration for sync model (seconds) |
| `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS` | int | `10000` | Max chars for context biasing string |

## Modified Environment Variables

| Variable | Change | Rationale |
|----------|--------|-----------|
| `SOW_QWEN3_BASE_URL` | Still used by qwen3_client.py for manual ForcedAligner calls | No longer used in automatic pipeline |

## Deprecated Options

| Option | Status | Replacement |
|--------|--------|-------------|
| `LrcOptions.use_qwen3` | Deprecated (default changed to False) | Manual ForcedAligner via Admin CLI |
| `LrcOptions.max_qwen3_duration` | Deprecated (no longer used in auto pipeline) | N/A |

---

## New Dependencies

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| `rapidfuzz` | >=3.0.0 | Fuzzy matching for canonical-line snap | `services/analysis/pyproject.toml` |
| `zhconv` | >=1.4.0 | Chinese script conversion (simplified/traditional) | `services/analysis/pyproject.toml` |

Note: `httpx` is already present in the analysis service. The `dashscope` SDK is NOT added — we use direct HTTP calls to the DashScope REST API.

---

## Files Changed Summary

| File | Action | Phase |
|------|--------|-------|
| `services/analysis/src/sow_analysis/services/qwen3_asr_client.py` | **NEW** | 1 |
| `services/analysis/src/sow_analysis/services/qwen3_asr_oss.py` | **NEW** | 1 |
| `services/analysis/src/sow_analysis/services/__init__.py` | MODIFY | 1 |
| `services/analysis/src/sow_analysis/config.py` | MODIFY | 1 |
| `services/analysis/tests/services/test_qwen3_asr_client.py` | **NEW** | 1 |
| `services/analysis/src/sow_analysis/services/canonical_snap.py` | **NEW** | 2 |
| `services/analysis/tests/services/test_canonical_snap.py` | **NEW** | 2 |
| `services/analysis/src/sow_analysis/services/qwen3_asr_cache.py` | **NEW** | 3 |
| `services/analysis/tests/services/test_qwen3_asr_cache.py` | **NEW** | 3 |
| `services/analysis/src/sow_analysis/workers/lrc.py` | MODIFY | 4, 6 |
| `services/analysis/src/sow_analysis/models.py` | MODIFY | 4 |
| `services/analysis/src/sow_analysis/workers/queue.py` | MODIFY | 5 |
| `services/analysis/src/sow_analysis/routes.py` | MODIFY | 7 |
| `src/stream_of_worship/admin/services/analysis.py` | MODIFY | 7 |
| `services/analysis/pyproject.toml` | MODIFY | 2 |

---

## Job Stages (Updated)

| Stage | Description |
|-------|-------------|
| `starting` | Job begins processing |
| `trying_youtube_transcript` | Attempting YouTube transcript path |
| `youtube_transcript_done` | YouTube path succeeded |
| **`trying_qwen3_asr`** | **Attempting Qwen3 ASR path (NEW)** |
| **`qwen3_asr_transcribing`** | **Running Qwen3-ASR-Flash transcription (NEW)** |
| **`qwen3_asr_snapping`** | **Running canonical-line snap (NEW)** |
| **`qwen3_asr_done`** | **Qwen3 ASR path succeeded (NEW)** |
| `downloading` | Downloading audio from R2 |
| `using_vocals_stem` | Using vocals stem for transcription |
| `submitting_stem_separation_child` | Auto-submitting child stem separation job |
| `awaiting_stem_separation:{child_id}` | Polling for child job completion |
| `using_vocals_dry_stem` | Using dry vocals from child job |
| `transcription_cached` | Using cached Whisper transcription |
| `transcribing` | Running Whisper transcription |
| `uploading` | Uploading LRC to R2 |
| `cached` | Returned from cache (no processing) |
| `complete` | Job completed successfully |
| `lrc_error` | LRC worker error |
| `error` | Unexpected error |
| `cancelled` | Job was cancelled |

---

## Error Handling (Updated)

| Error | Handling |
|-------|----------|
| YouTube transcript not found | Fall through to Qwen3 ASR path |
| YouTube LLM correction fails | Fall through to Qwen3 ASR path |
| **DashScope API error** | **Fall through to Whisper path** |
| **DashScope timeout** | **Fall through to Whisper path** |
| **DashScope rate limit (429)** | **Fall through to Whisper path** |
| **Qwen3 ASR returns no segments** | **Fall through to Whisper path** |
| **Canonical snap coverage < 70%** | **Fall back to LLM alignment within Qwen3 ASR path** |
| Child stem separation fails | Fall back to full mix audio |
| Stem separation timeout (2h) | Fall back to full mix audio |
| Whisper transcription fails | Job fails with `WhisperTranscriptionError` |
| LLM alignment fails (3 retries) | Job fails with `LLMAlignmentError` |
| R2 upload fails | Job fails with error message |

---

## Concurrency Control (Updated)

| Resource | Limit | Controlled By |
|----------|-------|---------------|
| Local models (Whisper, audio-separator, demucs) | 1 concurrent | `_local_model_semaphore` |
| **DashScope ASR API** | **No local semaphore needed (cloud API)** | **API rate limits** |
| **Canonical-line snap** | **No semaphore needed (pure Python)** | **N/A** |
| LLM alignment (cloud API) | No local semaphore | API rate limits |
| Embedding jobs (cloud API) | 5 concurrent | `_embedding_semaphore` |

---

## Migration / Rollout

### Step 1: Phase 1 — Qwen3 ASR Client (3-4 days)
- Create `qwen3_asr_client.py` with direct HTTP calls to DashScope REST API
- Create `qwen3_asr_oss.py` for filetrans upload
- Add config settings and tests
- Validate: mock tests pass, can call DashScope API from analysis container

### Step 2: Phase 2 — Canonical Snap Service (2-3 days)
- Port snap algorithm from POC into `canonical_snap.py`
- Add `rapidfuzz` and `zhconv` dependencies
- Add unit tests
- Validate: snap algorithm produces correct output on test cases

### Step 3: Phase 3 — ASR Cache (1-2 days)
- Create `qwen3_asr_cache.py`
- Add cache tests
- Validate: cache hit/miss behavior correct

### Step 4: Phase 4+5 — Wire into LRC Worker + Job Queue (3-4 days)
- Add `_run_qwen3_asr_transcription()` and `_qwen3_asr_to_lrc()` to `lrc.py`
- Modify `generate_lrc()` to try Qwen3 ASR as 2nd priority
- Update `_process_lrc_job()` with new stages
- Update `LrcOptions` model
- Validate: end-to-end LRC generation via Qwen3 ASR path

### Step 5: Phase 6 — Remove ForcedAligner from Auto Pipeline (1 day)
- Change `use_qwen3` default to False
- Remove Qwen3 refinement block from `generate_lrc()`
- Validate: existing tests still pass, ForcedAligner still available manually

### Step 6: Phase 7 — API and Admin CLI Updates (1-2 days)
- Update API routes and admin CLI commands
- Validate: admin can submit LRC jobs with Qwen3 ASR options

### Step 7: Phase 8 — Testing and Validation (3-5 days)
- Run unit and integration tests
- Benchmark on 10 known-good songs
- Fallback testing
- Cost validation

**Total estimated time: 14-21 days**

---

## Out of Scope

- Retiring the YouTube transcript path (kept as 1st priority until Qwen3 ASR proves out)
- Retiring the Whisper path (kept as 3rd priority safety net)
- Removing the Qwen3 ForcedAligner service entirely (kept as optional manual tool)
- Building a lyric review UI (existing `upload-lrc` flow covers outliers)
- Local Qwen3-ASR (PyTorch/ONNX) as a fallback for when DashScope is unavailable (future work)
- Cloud forced alignment via RunPod (future work, per v2 spec Phase 3)
- Changes to the webapp or user app (they consume LRC output, not LRC generation)
