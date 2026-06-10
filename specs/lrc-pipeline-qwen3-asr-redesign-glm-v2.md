# LRC Pipeline Redesign: Qwen3 ASR + MVSEP + Canonical Snap (v2)

## Context

The LRC generation worker (`services/analysis/src/sow_analysis/workers/lrc.py`) currently uses a 3-path pipeline:

1. **YouTube Transcript** (primary) — fetch captions, LLM-correct against official lyrics
2. **Whisper ASR** (fallback) — local `faster_whisper` transcription + LLM alignment
3. **Qwen3 ForcedAligner** (optional refinement) — local Docker service for timestamp polishing

This redesign restructures the pipeline to:

1. **YouTube Transcript** (still 1st priority, unchanged)
2. **Qwen3 ASR via DashScope API** (new 2nd priority) — cloud ASR with context biasing + canonical-line snap + LLM text correction
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

### Changes from v1 spec

| Area | v1 | v2 | Rationale |
|------|----|----|-----------|
| LLM alignment | Conditional (coverage < 70%) | Always runs after snap | Both snap and LLM fix text to canonical; LLM catches what snap missed + reorders segments. Neither modifies timestamps. |
| Coverage check | Exact match on list (O(n)) | Removed | Inaccurate metric; LLM always runs so no coverage gate needed |
| LLM prompt | Reused Whisper-style `_build_alignment_prompt()` | New `_build_qwen3_asr_alignment_prompt()` | Qwen3 ASR segments have different characteristics than Whisper phrases |
| DashScope retries | None | 3 retries with exponential backoff (1s, 2s, 4s) for 429/5xx/network errors | Prevents transient failures from falling through to Whisper unnecessarily |
| Circuit breaker | None | `_disabled` flag on non-retriable errors (401/403/invalid key) | Prevents every LRC job from wasting ~120s on a bad API key |
| DashScope concurrency | No local semaphore | `asyncio.Semaphore(3)` | Prevents burst of concurrent API calls from multiple LRC jobs hitting QPS limits |
| Vocal stem resolution | Duplicated between Qwen3 ASR and Whisper paths | Refactored to shared `_resolve_vocal_stem()` helper | Eliminates ~80 lines of duplication; resolved stem reused if Qwen3 ASR fails and falls through to Whisper |
| filetrans context biasing | Implied for all models | Documented limitation: filetrans does NOT support context biasing | POC confirms filetrans ignores system-message context. Canonical snap + LLM compensate. |
| filetrans polling timeout | Not specified | `SOW_DASHSCOPE_ASR_FILETRANS_MAX_POLL_SECONDS = 600` (10 min) | Prevents indefinite polling for async tasks |
| Audio duration detection | Not specified | `ffprobe` subprocess | Always available in Docker; no new Python dependency |
| Cache invalidation | No TTL or version | `cache_version` field, bumped manually on model changes | Simple and sufficient; avoids stale results when DashScope silently updates models |
| API key env var | `SOW_DASHSCOPE_ASR_API_KEY` | `SOW_DASHSCOPE_API_KEY` | Shared key for all DashScope services (ASR, future uses). ForcedAligner keeps `SOW_QWEN3_API_KEY` unchanged. |
| Base URL env var | `SOW_DASHSCOPE_ASR_BASE_URL` | `SOW_DASHSCOPE_ASR_BASE_URL` (separate from `SOW_QWEN3_BASE_URL`) | Different services may use different regional endpoints |

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
  │   ├─ [2a] Resolve vocal stem (shared helper)
  │   │   _resolve_vocal_stem() → check R2 → submit child job if needed
  │   │   Fall back to full mix if stem separation fails
  │   │
  │   ├─ [2b] Qwen3-ASR-Flash transcription (DashScope cloud API)
  │   │   Auto-select: qwen3-asr-flash (≤5min, sync) or qwen3-asr-flash-filetrans (>5min, async)
  │   │   Context biasing with canonical lyrics (sync model only, up to 10k chars)
  │   │   ⚠️ KNOWN LIMITATION: filetrans model does NOT support context biasing
  │   │   enable_words=True for word-level timestamps
  │   │   → segments [{text, start, end}] + word-level timestamps
  │   │
  │   ├─ [2c] Canonical-line snap (local, deterministic)
  │   │   reconstruct_lines_from_words() → sequential_canonical_snap()
  │   │   Replace ASR text with closest canonical line (fuzzy match ≥ 0.60)
  │   │   Preserve ASR timestamps and repeat structure
  │   │
  │   ├─ [2d] LLM text correction (always runs)
  │   │   _llm_align() with Qwen3-ASR-specific prompt
  │   │   Catches what snap missed + reorders/reassigns segments to canonical lines
  │   │   Preserves ASR timestamps (text correction only, no timestamp modification)
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
| MVSEP integration | Reuse existing STEM_SEPARATION child job via shared helper | Same pattern as current Whisper path; child job handles MVSEP vs local fallback |
| Qwen3 ForcedAligner | Remove from automatic pipeline; keep service as optional manual tool | Results require manual adjustment; Admin CLI Lyrics Editor handles this |
| Canonical-line snap + LLM | Snap first (fixes text), then LLM always (catches misses + reorders) | Deterministic snap handles easy cases; LLM handles edge cases. Neither modifies timestamps. |
| LLM prompt | New `_build_qwen3_asr_alignment_prompt()` | Qwen3 ASR segments differ from Whisper phrases; dedicated prompt produces better alignment |
| ASR model selection | Auto-select by duration: flash ≤5min, filetrans >5min | Simpler for short songs; filetrans handles long songs |
| filetrans context biasing | Not supported; canonical snap + LLM compensate | DashScope filetrans API limitation; documented as known limitation |
| ASR caching | Cache raw DashScope API response keyed by audio_hash + ASR params + cache_version | Matches Whisper cache pattern; cache_version enables manual invalidation on model updates |
| Vocal stem source | Shared `_resolve_vocal_stem()` helper, resolved once before path split | Eliminates duplication; resolved stem reused if Qwen3 ASR fails and falls through to Whisper |
| DashScope retries | 3 retries with exponential backoff (1s, 2s, 4s) for 429/5xx/network errors | Prevents transient failures from falling through to Whisper |
| DashScope circuit breaker | `_disabled` flag on non-retriable errors (401/403/invalid key) | Prevents wasting time on bad API keys; mirrors MVSEP pattern |
| DashScope concurrency | `asyncio.Semaphore(3)` | Prevents burst of concurrent API calls from multiple LRC jobs |
| Whisper fallback | Keep as 3rd priority | Safety net if both YouTube and Qwen3 ASR fail |
| Audio duration detection | ffprobe subprocess | Always available in Docker; no new Python dependency |
| API key | `SOW_DASHSCOPE_API_KEY` (shared for all DashScope services) | Single key for DashScope account; ForcedAligner keeps its own `SOW_QWEN3_API_KEY` |

---

## Detailed Implementation Plan

### Phase 1: Add Qwen3 ASR Client Service

**Goal**: Create a production-grade async client for the DashScope Qwen3-ASR-Flash API with retry logic, circuit breaker, and concurrency control.

#### 1.1 New file: `services/analysis/src/sow_analysis/services/qwen3_asr_client.py`

Port from POC `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` (functions `call_qwen3_asr`, `_call_qwen3_asr_filetrans`, `extract_segments`, `extract_word_timestamps`, `extract_asr_text`).

```python
class Qwen3AsrClient:
    """Async client for DashScope Qwen3-ASR-Flash API.

    Features:
    - Auto-selects sync vs filetrans model based on audio duration
    - 3 retries with exponential backoff for transient errors
    - Circuit breaker: disables on non-retriable errors (401/403/invalid key)
    - Concurrency control via external semaphore
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1",
        model: str = "qwen3-asr-flash",
        filetrans_model: str = "qwen3-asr-flash-filetrans",
        timeout: float = 120.0,
        max_retries: int = 3,
        filetrans_max_poll_seconds: float = 600.0,
    ): ...

    @property
    def is_available(self) -> bool:
        """Whether the client is available (not disabled by circuit breaker)."""
        return not self._disabled

    async def transcribe(
        self,
        audio_path: Path,
        language: str = "zh",
        context: Optional[str] = None,
        enable_words: bool = True,
    ) -> AsrResult:
        """Transcribe audio file.

        Auto-selects model based on audio duration (detected via ffprobe):
        - ≤5min: qwen3-asr-flash (synchronous, supports context biasing)
        - >5min: qwen3-asr-flash-filetrans (async, NO context biasing support)
        """
        ...

    async def transcribe_sync(
        self,
        audio_path: Path,
        language: str = "zh",
        context: Optional[str] = None,
        enable_words: bool = True,
    ) -> AsrResult:
        """Synchronous transcription via qwen3-asr-flash (≤5min audio).

        Supports context biasing via system message.
        """
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
        ⚠️ Does NOT support context biasing (filetrans API limitation).
        """
        ...

    async def _get_audio_duration(self, audio_path: Path) -> float:
        """Get audio duration in seconds using ffprobe."""
        ...

    async def _call_with_retry(self, request_fn, *args, **kwargs):
        """Execute request with 3 retries and exponential backoff.

        Retries on: 429 (rate limit), 5xx (server error), httpx.TimeoutException,
        httpx.NetworkError.
        Does NOT retry on: 401/403 (auth errors) — triggers circuit breaker instead.
        Backoff: 1s, 2s, 4s.
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


class Qwen3AsrError(Exception):
    """Base error for Qwen3 ASR client."""
    pass


class Qwen3AsrNonRetriableError(Qwen3AsrError):
    """Non-retriable error (401/403/invalid key). Triggers circuit breaker."""
    pass


class Qwen3AsrTimeoutError(Qwen3AsrError):
    """Timeout error (HTTP timeout or filetrans polling timeout)."""
    pass
```

Key implementation details:
- Use `httpx.AsyncClient` for HTTP calls (consistent with `mvsep_client.py` and `qwen3_client.py`)
- **Do NOT use the `dashscope` Python SDK** — it's synchronous and adds a heavy dependency. Instead, make direct HTTP calls to the DashScope REST API
- For `filetrans`: upload audio to DashScope OSS via their upload endpoint, then submit async task and poll
- Context biasing: pass as system message with `{"text": context}` content (sync model only)
- ASR options: `enable_itn=False`, `enable_words=True`, `language="zh"`
- Parse response: extract `sentences` (segment-level) and `words` (word-level) from `audio_transcription_results`
- For `filetrans`: fetch `transcription_url` and parse the JSON response
- Error handling: raise `Qwen3AsrError` on API errors, with `Qwen3AsrNonRetriableError` for auth errors and `Qwen3AsrTimeoutError` for timeouts
- Circuit breaker: set `self._disabled = True` on `Qwen3AsrNonRetriableError`; `is_available` property returns False
- Retry: 3 retries with exponential backoff (1s, 2s, 4s) for 429/5xx/network errors; no retry for 401/403
- Audio duration detection: use `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 <path>` subprocess call
- Filetrans polling: poll every 3s with `filetrans_max_poll_seconds` timeout (default 600s)

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
from .qwen3_asr_client import Qwen3AsrClient, Qwen3AsrError, Qwen3AsrNonRetriableError, Qwen3AsrTimeoutError, AsrResult, AsrSegment, AsrWord
```

#### 1.4 Update: `services/analysis/src/sow_analysis/config.py`

Add new settings:
```python
# DashScope API (shared key for ASR and future DashScope services)
SOW_DASHSCOPE_API_KEY: str = ""

# Qwen3 ASR (DashScope cloud API)
SOW_DASHSCOPE_ASR_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/api/v1"
SOW_DASHSCOPE_ASR_MODEL: str = "qwen3-asr-flash"
SOW_DASHSCOPE_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
SOW_DASHSCOPE_ASR_TIMEOUT: float = 120.0
SOW_DASHSCOPE_ASR_MAX_SYNC_DURATION: float = 300.0  # 5 minutes
SOW_DASHSCOPE_ASR_FILETRANS_MAX_POLL_SECONDS: float = 600.0  # 10 minutes
SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS: int = 10000
SOW_DASHSCOPE_ASR_MAX_CONCURRENT: int = 3  # Semaphore limit for concurrent DashScope API calls
SOW_DASHSCOPE_ASR_CACHE_VERSION: int = 1  # Bump when DashScope model changes significantly
```

Note: `SOW_QWEN3_API_KEY` and `SOW_QWEN3_BASE_URL` remain unchanged (used by `qwen3_client.py` for manual ForcedAligner calls).

#### 1.5 Update: `services/analysis/pyproject.toml`

Add `dashscope` is NOT needed (we use direct HTTP). No new dependencies required — `httpx` is already present.

#### 1.6 New test: `services/analysis/tests/services/test_qwen3_asr_client.py`

Test cases:
- `test_transcribe_sync_success` — mock HTTP response, verify AsrResult parsing
- `test_transcribe_filetrans_success` — mock OSS upload + async task + polling
- `test_auto_select_sync_model` — audio ≤5min uses sync model
- `test_auto_select_filetrans_model` — audio >5min uses filetrans model
- `test_context_biasing_sync_only` — context string passed in system message for sync model
- `test_no_context_biasing_filetrans` — context NOT passed for filetrans model (API limitation)
- `test_enable_words` — word-level timestamps extracted
- `test_api_error_raises` — non-200 response raises Qwen3AsrError
- `test_auth_error_circuit_breaker` — 401/403 sets `_disabled=True`, subsequent calls skip
- `test_retry_on_429` — 429 retried with backoff, succeeds on 2nd attempt
- `test_retry_on_5xx` — 500 retried with backoff, succeeds on 3rd attempt
- `test_no_retry_on_401` — 401 raises Qwen3AsrNonRetriableError immediately
- `test_filetrans_polling` — polls until SUCCEEDED status
- `test_filetrans_failure` — FAILED status raises Qwen3AsrError
- `test_filetrans_polling_timeout` — exceeds max poll seconds raises Qwen3AsrTimeoutError
- `test_empty_response` — no segments raises Qwen3AsrError
- `test_is_available_property` — True initially, False after circuit breaker triggers
- `test_duration_detection_ffprobe` — ffprobe returns correct duration

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

    Returns None on cache miss, parameter mismatch, or cache_version mismatch.
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
Cache version validation: if `cache_version` in file < `SOW_DASHSCOPE_ASR_CACHE_VERSION`, treat as cache miss. Bump `SOW_DASHSCOPE_ASR_CACHE_VERSION` in config when DashScope model changes significantly.

---

### Phase 4: Add Shared Vocal Stem Resolution Helper

**Goal**: Extract vocal stem resolution logic into a shared helper to eliminate duplication between Qwen3 ASR and Whisper paths.

#### 4.1 New function in: `services/analysis/src/sow_analysis/workers/queue.py`

```python
async def _resolve_vocal_stem(
    job_id: str,
    request: LrcJobRequest,
    temp_path: Path,
    r2_client,
    job_store,
    submit_fn,
) -> tuple[Path, Optional[str]]:
    """Resolve vocal stem for LRC transcription.

    Checks R2 for existing vocals_dry.flac, then falls back to submitting
    a child STEM_SEPARATION job if not found.

    Args:
        job_id: Current LRC job ID
        request: LRC job request
        temp_path: Temporary directory for downloads
        r2_client: R2 client for checking/downloading stems
        job_store: Job store for updating job stages
        submit_fn: Function to submit child jobs (self.submit)

    Returns:
        Tuple of (transcription_path, vocals_stem_url).
        transcription_path is the stem path if available, or the original audio path.
        vocals_stem_url is the R2 URL if available, or None.
    """
    ...
```

Implementation:
1. Check R2 for existing `vocals_dry.flac` via `get_vocals_dry_url()`
2. If found: download to `temp_path`, update stage to `"using_vocals_stem"`, return stem path
3. If not found: submit child `STEM_SEPARATION` job, update stage to `"awaiting_stem_separation:{child_id}"`
4. Poll child job every 3s with 2-hour timeout
5. On child completion: download `vocals_dry_url` or `vocals_url`, update stage to `"using_vocals_dry_stem"`, return stem path
6. On child failure/timeout: log warning, return original audio path (fall back to full mix)

This replaces the duplicated logic currently at `queue.py:700-803` and the proposed duplication in Phase 5 of v1.

---

### Phase 5: Modify LRC Worker

**Goal**: Wire the new Qwen3 ASR path into the LRC worker as 2nd priority (between YouTube and Whisper).

#### 5.1 Modify: `services/analysis/src/sow_analysis/workers/lrc.py`

Add new function `_run_qwen3_asr_transcription()`:

```python
async def _run_qwen3_asr_transcription(
    audio_path: Path,
    lyrics_text: Optional[str] = None,
    content_hash: Optional[str] = None,
    force: bool = False,
) -> tuple[List[WhisperPhrase], List[AsrWord], dict]:
    """Run Qwen3-ASR-Flash transcription via DashScope cloud API.

    Args:
        audio_path: Path to audio file (preferably dry vocals stem)
        lyrics_text: Optional lyrics for context biasing (sync model only)
        content_hash: Audio content hash for caching
        force: If True, bypass cache

    Returns:
        Tuple of (segments as WhisperPhrase list, word-level timestamps, raw API response)

    Raises:
        Qwen3AsrError: If DashScope API call fails
        Qwen3AsrNonRetriableError: If API key is invalid (circuit breaker triggered)
    """
    ...
```

Implementation:
1. Check cache (if `content_hash` provided and `force=False`)
2. Build context string from `lyrics_text` (truncate to `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS`)
3. Get or create `Qwen3AsrClient` instance (check `is_available` first — circuit breaker)
4. Acquire `_dashscope_asr_semaphore` (concurrency control)
5. Call `client.transcribe(audio_path, language="zh", context=context, enable_words=True)`
6. Parse response into `List[WhisperPhrase]` (segments) and `List[AsrWord]` (word-level)
7. Save to cache if `content_hash` provided
8. Return segments, words, and raw response

Add new function `_build_qwen3_asr_alignment_prompt()`:

```python
def _build_qwen3_asr_alignment_prompt(
    lyrics_text: str,
    asr_phrases: List[WhisperPhrase],
) -> str:
    """Build LLM prompt for aligning Qwen3 ASR segments to canonical lyrics.

    Differences from Whisper alignment prompt:
    - Qwen3 ASR segments may be more granular than Whisper phrases
    - Qwen3 ASR text may already be partially canonical (due to context biasing + snap)
    - Prompt instructs LLM to fix remaining text mismatches and reorder/reassign
      segments to canonical lines, preserving ASR timestamps
    - Explicitly instructs: do NOT modify timestamps, only fix text and reorder
    """
    ...
```

Add new function `_qwen3_asr_to_lrc()`:

```python
async def _qwen3_asr_to_lrc(
    audio_path: Path,
    lyrics_text: str,
    content_hash: Optional[str] = None,
    force: bool = False,
) -> tuple[List[LRCLine], List[WhisperPhrase]]:
    """Generate LRC via Qwen3 ASR + canonical-line snap + LLM text correction.

    Pipeline: Qwen3-ASR transcription → canonical-line snap → LLM text correction

    Both snap and LLM fix text to canonical lyrics. Neither modifies timestamps.
    Snap handles deterministic fuzzy matching; LLM catches what snap missed + reorders segments.

    Returns:
        Tuple of (LRC lines, ASR segments as WhisperPhrase list)
    """
    # Step 1: Run Qwen3 ASR
    asr_phrases, asr_words, raw_response = await _run_qwen3_asr_transcription(
        audio_path, lyrics_text, content_hash, force
    )

    # Step 2: Canonical-line snap (deterministic text correction)
    lyrics_lines = [line for line in lyrics_text.split("\n") if line.strip()]
    lrc_lines = canonical_line_snap(
        asr_segments=[AsrSegment(text=p.text, start=p.start, end=p.end) for p in asr_phrases],
        lyrics_lines=lyrics_lines,
        asr_words=asr_words,
        threshold=0.60,
    )

    # Step 3: LLM text correction (always runs — catches snap misses + reorders segments)
    lrc_lines = await _llm_align(
        lyrics_text,
        asr_phrases,
        llm_model="",
        prompt_builder=_build_qwen3_asr_alignment_prompt,
    )

    return lrc_lines, asr_phrases
```

Note: `_llm_align()` needs a minor refactor to accept an optional `prompt_builder` parameter (defaulting to `_build_alignment_prompt` for backward compatibility with the Whisper path).

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
                force=options.force,
            )
            line_count = _write_lrc(lrc_lines, output_path)
            return output_path, line_count, asr_phrases
        except Qwen3AsrNonRetriableError as e:
            logger.error(f"Qwen3 ASR non-retriable error: {e}. Falling back to Whisper.")
        except Qwen3AsrError as e:
            logger.warning(f"Qwen3 ASR path failed: {e}. Falling back to Whisper.")

    # Path 3: Whisper ASR (unchanged, 3rd priority)
    # ... existing Whisper + LLM alignment code ...
```

#### 5.2 Modify: `services/analysis/src/sow_analysis/models.py`

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

#### 5.3 Modify: `services/analysis/src/sow_analysis/workers/queue.py`

Update `_process_lrc_job()` to:

1. **Resolve vocal stem once** using shared `_resolve_vocal_stem()` helper before the path split
2. **Pass resolved stem** to both Qwen3 ASR and Whisper paths
3. **Add DashScope ASR semaphore**: `self._dashscope_asr_semaphore = asyncio.Semaphore(settings.SOW_DASHSCOPE_ASR_MAX_CONCURRENT)`
4. **Update job stages**: Add new stages for Qwen3 ASR path:

```python
# New stages to add:
"trying_qwen3_asr"           # Attempting Qwen3 ASR path
"qwen3_asr_transcribing"      # Running Qwen3-ASR-Flash transcription
"qwen3_asr_snapping"          # Running canonical-line snap
"qwen3_asr_llm_correcting"   # Running LLM text correction
"qwen3_asr_done"              # Qwen3 ASR path succeeded
```

5. **Remove Qwen3 ForcedAligner refinement step**: Remove the `if options.use_qwen3 and content_hash:` block from `generate_lrc()` in `lrc.py`. The `_qwen3_refine()` function and `_parse_qwen3_lrc()` are kept in the file but no longer called automatically.

6. **Update `lrc_source` tracking**: Add `"qwen3_asr"` as a valid source value.

---

### Phase 6: Update Job Queue Orchestrator

**Goal**: Wire the Qwen3 ASR path into the job queue with proper stage tracking and shared vocal stem handling.

#### 6.1 Modify: `services/analysis/src/sow_analysis/workers/queue.py`

In `_process_lrc_job()`, after the YouTube path fails and before the Whisper path:

```python
# Resolve vocal stem ONCE (shared between Qwen3 ASR and Whisper paths)
transcription_path = audio_path
vocals_stem_url = None

if options.use_vocals_stem:
    transcription_path, vocals_stem_url = await _resolve_vocal_stem(
        job_id=job.id,
        request=request,
        temp_path=temp_path,
        r2_client=self.r2_client,
        job_store=self.job_store,
        submit_fn=self.submit,
    )

# Path 2: Qwen3 ASR (2nd priority)
if options.use_qwen3_asr:
    await job_store.update_job(job.id, stage="trying_qwen3_asr")

    try:
        result = await generate_lrc(
            audio_path=transcription_path,
            lyrics_text=lyrics_text,
            options=options,
            output_path=lrc_path,
            youtube_url=None,  # Already tried and failed
            content_hash=content_hash,
            vocals_stem_url=vocals_stem_url,
            local_model_semaphore=self._local_model_semaphore,
        )
        # Success — upload and finalize
        lrc_source = "qwen3_asr"
        ...
    except Qwen3AsrNonRetriableError as e:
        logger.error(f"Qwen3 ASR non-retriable error: {e}. Falling back to Whisper.")
    except Qwen3AsrError as e:
        logger.warning(f"Qwen3 ASR path failed: {e}. Falling back to Whisper.")

# Path 3: Whisper ASR (3rd priority, existing code)
# transcription_path and vocals_stem_url already resolved above
...
```

---

### Phase 7: Remove Qwen3 ForcedAligner from Automatic Pipeline

**Goal**: Disable the automatic Qwen3 ForcedAligner refinement step while keeping the service available for manual use.

#### 7.1 Modify: `services/analysis/src/sow_analysis/workers/lrc.py`

- Change `use_qwen3` default from `True` to `False` in `LrcOptions`
- Remove the Qwen3 refinement block from `generate_lrc()` (lines 747-801)
- Keep `_qwen3_refine()` and `_parse_qwen3_lrc()` functions in the file (they may be used manually)
- Add a comment marking them as "available for manual use via Admin CLI"

#### 7.2 Keep: `services/qwen3/` directory

- The Qwen3 ForcedAligner service remains deployed and available
- Admin CLI can still call it manually if needed
- No code changes to the qwen3 service itself

#### 7.3 Keep: `services/analysis/src/sow_analysis/services/qwen3_client.py`

- The HTTP client remains available for manual/admin use
- No longer called automatically from the LRC pipeline
- Continues to use `SOW_QWEN3_API_KEY` and `SOW_QWEN3_BASE_URL` (unchanged)

---

### Phase 8: Update API and Admin CLI

**Goal**: Expose the new Qwen3 ASR options through the API and admin CLI.

#### 8.1 Modify: `services/analysis/src/sow_analysis/routes.py`

Update the LRC job submission endpoint to accept new options:
- `use_qwen3_asr` (bool, default True)
- `qwen3_asr_context_max_chars` (int, default 10000)
- `snap_threshold` (float, default 0.60)

#### 8.2 Modify: Admin CLI LRC commands

Update `src/stream_of_worship/admin/services/analysis.py` to:
- Pass `use_qwen3_asr=True` by default when submitting LRC jobs
- Add `--no-qwen3-asr` flag to disable Qwen3 ASR and fall back to Whisper
- Add `--snap-threshold` flag for canonical-line snap threshold tuning

---

### Phase 9: Testing and Validation

#### 9.1 Unit tests

| Test file | Tests |
|-----------|-------|
| `tests/services/test_qwen3_asr_client.py` | DashScope API client: sync/filetrans, auto-select, context biasing, retry, circuit breaker, error handling |
| `tests/services/test_canonical_snap.py` | Snap algorithm: exact/partial match, repeats, threshold, wrap-around |
| `tests/services/test_qwen3_asr_cache.py` | Cache: save/load, parameter validation, cache_version mismatch, cache miss |
| `tests/workers/test_lrc_qwen3_asr.py` | LRC worker: Qwen3 ASR path, snap + LLM always, fallback to Whisper |

#### 9.2 Integration tests

- Submit LRC job with `use_qwen3_asr=True` → verify Qwen3 ASR path is used
- Submit LRC job with `use_qwen3_asr=False` → verify Whisper path is used
- Submit LRC job with YouTube URL → verify YouTube path is tried first
- Submit LRC job with YouTube URL that fails → verify Qwen3 ASR is tried second
- Submit LRC job with both YouTube and Qwen3 ASR failing → verify Whisper fallback
- Submit LRC job with invalid `SOW_DASHSCOPE_API_KEY` → verify circuit breaker triggers, falls through to Whisper without 120s delay on subsequent jobs
- Submit LRC job with >5min audio → verify filetrans model is used (no context biasing)
- Submit concurrent LRC jobs → verify DashScope semaphore limits concurrency to 3

#### 9.3 End-to-end validation

1. **Benchmark on 10 known-good songs**: Regenerate LRC via Qwen3 ASR path, compare against hand-corrected versions. Target: ≥95% character match, ≤0.5s avg timestamp delta.
2. **Fallback testing**: Force each path to fail independently and verify correct fallback.
3. **Cost validation**: Measure DashScope API cost per song (~$0.008 expected).
4. **Vocal stem quality**: Compare LRC quality with dry vocals vs full mix.
5. **filetrans validation**: Test with >5min songs to verify filetrans works correctly without context biasing.

---

## New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SOW_DASHSCOPE_API_KEY` | str | `""` | **Required** for Qwen3 ASR path. DashScope API key from Alibaba Cloud Model Studio. Shared key for all DashScope services. |
| `SOW_DASHSCOPE_ASR_BASE_URL` | str | `https://dashscope-intl.aliyuncs.com/api/v1` | Regional endpoint: `intl` (Singapore), `cn` (Beijing), `us` (Virginia) |
| `SOW_DASHSCOPE_ASR_MODEL` | str | `qwen3-asr-flash` | Sync model for ≤5min audio |
| `SOW_DASHSCOPE_ASR_FILETRANS_MODEL` | str | `qwen3-asr-flash-filetrans` | Async model for >5min audio |
| `SOW_DASHSCOPE_ASR_TIMEOUT` | float | `120.0` | HTTP timeout in seconds |
| `SOW_DASHSCOPE_ASR_MAX_SYNC_DURATION` | float | `300.0` | Max duration for sync model (seconds) |
| `SOW_DASHSCOPE_ASR_FILETRANS_MAX_POLL_SECONDS` | float | `600.0` | Max polling time for async filetrans tasks (seconds) |
| `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS` | int | `10000` | Max chars for context biasing string |
| `SOW_DASHSCOPE_ASR_MAX_CONCURRENT` | int | `3` | Max concurrent DashScope API calls (semaphore limit) |
| `SOW_DASHSCOPE_ASR_CACHE_VERSION` | int | `1` | Cache version — bump when DashScope model changes significantly |

## Unchanged Environment Variables

| Variable | Notes |
|----------|-------|
| `SOW_QWEN3_API_KEY` | Still used by `qwen3_client.py` for manual ForcedAligner calls |
| `SOW_QWEN3_BASE_URL` | Still used by `qwen3_client.py` for manual ForcedAligner calls |

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

Note: `httpx` is already present in the analysis service. The `dashscope` SDK is NOT added — we use direct HTTP calls to the DashScope REST API. `ffprobe` is available in the Docker container (FFmpeg is already installed).

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
| `services/analysis/src/sow_analysis/workers/queue.py` | MODIFY | 4, 6 |
| `services/analysis/src/sow_analysis/workers/lrc.py` | MODIFY | 5, 7 |
| `services/analysis/src/sow_analysis/models.py` | MODIFY | 5 |
| `services/analysis/src/sow_analysis/routes.py` | MODIFY | 8 |
| `src/stream_of_worship/admin/services/analysis.py` | MODIFY | 8 |
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
| **`qwen3_asr_llm_correcting`** | **Running LLM text correction (NEW)** |
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
| **DashScope API error (429/5xx)** | **3 retries with exponential backoff (1s, 2s, 4s). If all retries fail, fall through to Whisper.** |
| **DashScope auth error (401/403)** | **Raise Qwen3AsrNonRetriableError, trigger circuit breaker, fall through to Whisper. Subsequent jobs skip Qwen3 ASR entirely.** |
| **DashScope timeout** | **Retry up to 3 times, then fall through to Whisper** |
| **Qwen3 ASR returns no segments** | **Fall through to Whisper path** |
| **Circuit breaker active** | **Skip Qwen3 ASR path entirely, go straight to Whisper** |
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
| **DashScope ASR API** | **3 concurrent** | **`_dashscope_asr_semaphore`** |
| **Canonical-line snap** | **No semaphore needed (pure Python)** | **N/A** |
| LLM alignment (cloud API) | No local semaphore | API rate limits |
| Embedding jobs (cloud API) | 5 concurrent | `_embedding_semaphore` |

---

## Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| filetrans model does NOT support context biasing | Songs >5min get no context biasing, potentially lower ASR accuracy | Canonical snap + LLM text correction compensate for missing context biasing |
| DashScope API availability | If DashScope is down, all Qwen3 ASR jobs fall through to Whisper | Whisper is the safety net; circuit breaker prevents retry storms |
| ffprobe required for duration detection | Must be available in Docker container | FFmpeg (which includes ffprobe) is already installed in the analysis Docker image |

---

## Migration / Rollout

### Step 1: Phase 1 — Qwen3 ASR Client (3-4 days)
- Create `qwen3_asr_client.py` with direct HTTP calls to DashScope REST API
- Create `qwen3_asr_oss.py` for filetrans upload
- Implement retry logic (3 retries, exponential backoff)
- Implement circuit breaker (`_disabled` flag on non-retriable errors)
- Implement concurrency semaphore
- Implement ffprobe duration detection
- Add config settings and tests
- Validate: mock tests pass, can call DashScope API from analysis container

### Step 2: Phase 2 — Canonical Snap Service (2-3 days)
- Port snap algorithm from POC into `canonical_snap.py`
- Add `rapidfuzz` and `zhconv` dependencies
- Add unit tests
- Validate: snap algorithm produces correct output on test cases

### Step 3: Phase 3 — ASR Cache (1-2 days)
- Create `qwen3_asr_cache.py`
- Add `cache_version` validation
- Add cache tests
- Validate: cache hit/miss behavior correct, version mismatch triggers cache miss

### Step 4: Phase 4 — Shared Vocal Stem Resolution (1-2 days)
- Extract `_resolve_vocal_stem()` helper from existing Whisper path logic
- Refactor existing Whisper path to use the shared helper
- Add tests for the shared helper
- Validate: existing Whisper path still works after refactor

### Step 5: Phase 5+6 — Wire into LRC Worker + Job Queue (3-4 days)
- Add `_run_qwen3_asr_transcription()` and `_qwen3_asr_to_lrc()` to `lrc.py`
- Add `_build_qwen3_asr_alignment_prompt()` for Qwen3-ASR-specific LLM prompt
- Refactor `_llm_align()` to accept optional `prompt_builder` parameter
- Modify `generate_lrc()` to try Qwen3 ASR as 2nd priority
- Update `_process_lrc_job()` with new stages and shared stem resolution
- Update `LrcOptions` model
- Validate: end-to-end LRC generation via Qwen3 ASR path

### Step 6: Phase 7 — Remove ForcedAligner from Auto Pipeline (1 day)
- Change `use_qwen3` default to False
- Remove Qwen3 refinement block from `generate_lrc()`
- Validate: existing tests still pass, ForcedAligner still available manually

### Step 7: Phase 8 — API and Admin CLI Updates (1-2 days)
- Update API routes and admin CLI commands
- Validate: admin can submit LRC jobs with Qwen3 ASR options

### Step 8: Phase 9 — Testing and Validation (3-5 days)
- Run unit and integration tests
- Benchmark on 10 known-good songs
- Fallback testing
- Cost validation
- filetrans validation with >5min songs

**Total estimated time: 16-23 days**

---

## Out of Scope

- Retiring the YouTube transcript path (kept as 1st priority until Qwen3 ASR proves out)
- Retiring the Whisper path (kept as 3rd priority safety net)
- Removing the Qwen3 ForcedAligner service entirely (kept as optional manual tool)
- Migrating `qwen3_client.py` to use `SOW_DASHSCOPE_API_KEY` (kept on `SOW_QWEN3_API_KEY` for now)
- Building a lyric review UI (existing `upload-lrc` flow covers outliers)
- Local Qwen3-ASR (PyTorch/ONNX) as a fallback for when DashScope is unavailable (future work)
- Cloud forced alignment via RunPod (future work, per v2 spec Phase 3)
- Changes to the webapp or user app (they consume LRC output, not LRC generation)
- Chunking long songs into ≤5min segments to use sync model with context biasing (future optimization)
