# LRC Pipeline V2: Cloud-First with Per-Step Local Fallback

## Context

The LRC generation worker (`services/analysis/src/sow_analysis/workers/lrc.py`) currently uses faster-whisper for ASR and a separate qwen3 Docker service for forced alignment refinement. The v1 spec (`qwen3_asr_onnx_lrc_enhancement.md`) proposed adding ONNX Qwen3-ASR as a local-only second ASR option.

This v2 plan rethinks the architecture around three goals:

1. **Cloud-first**: Each major pipeline step (stem separation, ASR, forced alignment) defaults to a cloud API, with local models as independent per-step fallbacks.
2. **Service consolidation**: Merge the separate qwen3 forced-alignment service into the analysis service, eliminating the HTTP boundary and simplifying deployment.
3. **POC-driven ASR selection**: Evaluate both ONNX and PyTorch Qwen3-ASR backends before committing to a local ASR strategy.

### Why v2 instead of v1

The v1 spec had several structural issues:

| Issue | v1 approach | v2 approach |
|-------|------------|-------------|
| Cloud migration | Mode 3 stub only | Per-step cloud/local with independent fallback |
| ASR local backend | ONNX only (no timestamps) | POC both ONNX and PyTorch, pick based on quality |
| Timestamp quality | Proportional estimation + forced aligner fix | PyTorch backend has per-char timestamps; cloud ASR has them too |
| Qwen3 forced aligner | Separate Docker service | Merged into analysis service |
| Configuration | Single `SOW_LYRICS_ASR_MODE` | Separate env vars per step |
| Self-hosted alignment | Not addressed | RunPod/serverless GPU hosting plan |

## Architecture

### Pipeline Overview

```
MP3 (from R2)
  │
  ▼
[1] Stem Separation (cloud=MVSEP, local=audio-separator)
    → vocals stem FLAC (cached in R2 or local cache)
  │
  ▼
[2] ASR Transcription (cloud=DashScope, local=Qwen3-ASR-PyTorch|ONNX)
    → List of segments {text, start, end} with timestamps
  │
  ▼
[3] Canonical-line snap (fuzzy matching, always local)
    → List of LRCLine with corrected text + ASR timestamps
    → Fallback: LLM alignment if snap coverage < 70%
  │
  ▼
[4] Forced Alignment (cloud=self-hosted RunPod, local=Qwen3ForcedAligner)
    → Refined character-level timestamps
  │
  ▼
[5] Write LRC file
```

Each step [1], [2], [4] independently selects cloud or local based on its own environment variable. Step [3] (canonical snap) is always local (pure Python, no ML). If a cloud step fails (timeout, error, API down), that step falls back to local automatically.

### Per-Step Mode Selection

| Env Var | Values | Default | Cloud Provider |
|---------|--------|---------|----------------|
| `SOW_STEM_MODE` | `cloud`, `local` | `cloud` | MVSEP |
| `SOW_ASR_MODE` | `cloud`, `local-pytorch`, `local-onnx` | `cloud` | DashScope Qwen3-ASR-Flash |
| `SOW_ALIGN_MODE` | `cloud`, `local` | `cloud` | Self-hosted (RunPod/serverless GPU) |

When a cloud step fails, the fallback is:
- `SOW_STEM_MODE=cloud` → MVSEP; on failure → local audio-separator
- `SOW_ASR_MODE=cloud` → DashScope; on failure → local Qwen3-ASR (PyTorch or ONNX per secondary env var)
- `SOW_ALIGN_MODE=cloud` → self-hosted RunPod endpoint; on failure → local Qwen3ForcedAligner

### Phase Structure

This plan is split into phases because some decisions depend on POC results:

**Phase 0 (POC)**: Evaluate ONNX vs PyTorch Qwen3-ASR for transcription quality and completeness.
**Phase 1**: Merge qwen3 forced alignment service into analysis service.
**Phase 2**: Add cloud ASR (DashScope) client and per-step mode dispatch.
**Phase 3**: Add cloud forced alignment (self-hosted RunPod) client.
**Phase 4**: Canonical-line snap production hardening.
**Phase 5**: End-to-end integration and validation.

Phase 0 must complete before Phase 2's local ASR backend is finalized. Phases 1, 3, 4 can proceed in parallel after Phase 0.

---

## Phase 0: POC — Evaluate Local ASR Backends

### Goal

Determine which local Qwen3-ASR backend (ONNX or PyTorch) produces the most complete and accurate Chinese worship song transcriptions. This is the current bottleneck — transcription quality drives LRC quality more than timestamp precision.

### What exists

- `poc/gen_lrc_qwen3_asr_local.py` — MLX backend (Apple Silicon only), 1793 lines
- `poc/gen_lrc_qwen3_asr.py` — DashScope cloud API, 422 lines
- Daumee/Qwen3-ASR-0.6B-ONNX-CPU — ONNX model, no existing POC script

### Tasks

#### 0a. ONNX ASR POC script

Create `poc/gen_lrc_qwen3_asr_onnx.py`:

- Adapt Daumee's `onnx_inference.py` into a runnable POC script
- Port the existing POC's `extract_segments()`, `canonical_line_snap()`, and diagnostic output
- Add caching (following `specs/cache_raw_asr_output_qwen3_local.md` pattern)
- Key difference: ONNX model returns text only, no per-character timestamps
- Handle timestamp estimation: proportional splitting + punctuation-based phrase boundaries
- Test against the same songs used in the MLX POC for direct comparison

#### 0b. PyTorch ASR POC script

Create `poc/gen_lrc_qwen3_asr_pytorch.py`:

- Use `qwen_asr.Qwen3ASRModel` (already in `qwen-asr>=0.0.6` package)
- This backend produces per-character timestamps (same as MLX backend)
- Port `extract_segments()`, `canonical_line_snap()`, diagnostics
- Add caching with same schema
- Test against the same song set

#### 0c. Comparative evaluation

Run both POC scripts on 10-20 worship songs and compare:

| Metric | ONNX | PyTorch | MLX (existing) |
|--------|------|---------|-----------------|
| Transcription completeness (% of lyrics captured) | ? | ? | baseline |
| Character accuracy (% correct chars) | ? | ? | baseline |
| Timestamp availability | None (estimated only) | Per-character | Per-character |
| Inference speed | ~3x realtime CPU | ~2x realtime CPU | ~1.5x realtime Apple Silicon |
| Memory usage | ~2.5GB model | ~2.5GB model + PyTorch | ~2.5GB model + MLX |
| GPU required | No | Optional (CPU works) | No (Apple Silicon) |

**Decision criteria**: If PyTorch transcription quality is comparable to ONNX, PyTorch wins because it provides per-character timestamps. ONNX is the fallback for resource-constrained environments (no PyTorch, CPU-only).

**Expected outcome**: PyTorch Qwen3-ASR as the primary local backend, ONNX as a secondary option for lightweight deployments.

---

## Phase 1: Merge Qwen3 Forced Alignment into Analysis Service

### Goal

Move the `Qwen3ForcedAligner` from its own Docker service (`services/qwen3/`) into the analysis service, then deprecate and remove the separate service.

### Why merge

1. **PyTorch already present**: The analysis container already has PyTorch for demucs/whisper. The qwen3 forced aligner also uses PyTorch. No new dependency.
2. **Eliminate HTTP overhead**: Current flow: analysis worker → httpx POST → qwen3 container → download audio from R2 → run alignment → return LRC. Merged: analysis worker → direct function call → run alignment. No network, no R2 re-download, no timeout.
3. **Simpler deployment**: One container instead of two. One Dockerfile, one docker-compose service.
4. **Shared audio cache**: No need for the qwen3 service to re-download audio from R2 — the analysis worker already has the audio locally.

### Risks of merging

| Risk | Mitigation |
|------|-----------|
| Memory pressure: demucs + whisper + qwen3 aligner all loaded | Lazy-load qwen3 model; add concurrency semaphore (existing pattern in qwen3 service) |
| GPU contention between workers | CPU-only forced aligner is adequate for production; GPU is optional |
| Loss of independent scaling | Accept trade-off; analysis service can scale horizontally |
| Qwen3 model loading time (~10s on first request) | Lazy-load on first request with health check; warn on cold start |

### Files to modify

#### 1a. Move alignment code into analysis service

**New file**: `services/analysis/src/sow_analysis/services/forced_aligner.py`

Port from `services/qwen3/src/sow_qwen3/workers/aligner.py`:
- `Qwen3AlignerWrapper` → `ForcedAlignerService`
- Remove R2 audio download (analysis worker already has local audio)
- Keep concurrency semaphore (`MAX_CONCURRENT`, default 2)
- Lazy-load model on first call
- Config from `settings.SOW_QWEN3_*` (reuse existing env vars)

**New file**: `services/analysis/src/sow_analysis/services/align_mapper.py`

Port `map_segments_to_lines()` from `services/qwen3/src/sow_qwen3/routes/align.py`:
- Pure function: takes alignment segments + lyrics lines → `List[LRCLine]`
- No HTTP dependency, no FastAPI dependency

#### 1b. Remove qwen3 HTTP client, use direct function call

**Modify**: `services/analysis/src/sow_analysis/workers/lrc.py`

Replace `_qwen3_refine()`:
- Current: instantiates `Qwen3Client`, calls `client.align(audio_url, ...)`, parses LRC response
- New: instantiates `ForcedAlignerService`, calls `service.align(audio_path, lyrics_text, ...)`, gets `List[LRCLine]` directly
- No more R2 URL construction, no HTTP timeout handling, no LRC text parsing

**Remove**: `services/analysis/src/sow_analysis/services/qwen3_client.py`

The httpx-based HTTP client is no longer needed.

#### 1c. Update config

**Modify**: `services/analysis/src/sow_analysis/config.py`

Keep existing qwen3 settings but rename for clarity:

```python
# Qwen3 Forced Aligner (merged from separate service)
SOW_QWEN3_MODEL_PATH: Optional[Path] = None  # Auto-detect from SOW_QWEN3_MODEL_ROOT
SOW_QWEN3_DEVICE: str = "auto"  # auto, cpu, cuda, mps
SOW_QWEN3_DTYPE: str = "float32"  # float32 (CPU), bfloat16 (GPU)
SOW_QWEN3_MAX_CONCURRENT: int = 2  # Semaphore limit
```

Remove `SOW_QWEN3_BASE_URL` and `SOW_QWEN3_API_KEY` (no longer HTTP).

#### 1d. Update Docker

**Modify**: `services/analysis/Dockerfile`

No changes needed — PyTorch and `qwen-asr` package are either already present or will be added as dependencies.

**Modify**: `services/analysis/pyproject.toml`

Add to `service` extra:
```toml
"qwen-asr>=0.0.6",  # Qwen3ForcedAligner + Qwen3ASRModel (Phase 2)
```

**Modify**: `services/analysis/docker-compose.yml`

Remove `qwen3` and `qwen3-dev` service definitions. Remove `SOW_QWEN3_R2_*` env forwarding (no longer needed). Add qwen3 model volume mount to analysis service:

```yaml
services:
  analysis:
    volumes:
      - ${SOW_QWEN3_MODEL_ROOT}:/models/qwen3:ro
  analysis-dev:
    volumes:
      - ${SOW_QWEN3_MODEL_ROOT}:/models/qwen3:ro
```

#### 1e. Deprecate separate qwen3 service

**Remove**: `services/qwen3/` directory entirely after merge is validated.

Add a brief deprecation note in the analysis service docs.

### Phase 1 Verification

1. Run existing LRC worker tests with `use_qwen3=True` — should work identically
2. Test forced alignment on a 3-minute song — compare timestamps with old qwen3 service output
3. Verify concurrent alignment requests respect semaphore limit
4. Verify graceful degradation when model path is invalid (logs warning, skips alignment)

---

## Phase 2: Cloud ASR + Per-Step Mode Dispatch

### Goal

Add DashScope Qwen3-ASR-Flash as the primary ASR, with local Qwen3-ASR (PyTorch or ONNX, per Phase 0 results) as fallback. Implement per-step mode dispatch with `SOW_ASR_MODE` environment variable.

### DashScope ASR Client

**New file**: `services/analysis/src/sow_analysis/services/qwen3_asr_client.py`

Mirrors the structure of the existing (now-removed) `qwen3_client.py`:

```python
class Qwen3AsrClient:
    """Async client for DashScope Qwen3-ASR-Flash API."""

    async def transcribe(
        self,
        audio_url: str,  # R2 URL or DashScope file URL
        language: str = "zh",
        context: str = "",  # Canonical lyrics for biasing
        enable_words: bool = True,  # Per-character timestamps
    ) -> AsrResult:
        ...

    async def transcribe_file(
        self,
        audio_path: Path,
        language: str = "zh",
        context: str = "",
        enable_words: bool = True,
    ) -> AsrResult:
        """Upload to DashScope then transcribe (for >5min audio)."""
        ...
```

Config:
```python
SOW_DASHSCOPE_ASR_API_KEY: str = ""  # Required for cloud ASR
SOW_DASHSCOPE_ASR_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/api/v1"
SOW_DASHSCOPE_ASR_MODEL: str = "qwen3-asr-flash"  # Or qwen3-asr-flash-filetrans for >5min
SOW_ASR_MODE: str = "cloud"  # "cloud", "local-pytorch", "local-onnx"
SOW_ASR_LOCAL_BACKEND: str = "pytorch"  # "pytorch" or "onnx" (used when SOW_ASR_MODE starts with "local")
```

**Data flow for DashScope ASR:**

1. Upload audio to DashScope (or provide R2 URL if accessible)
2. Call `qwen3-asr-flash` with `context=` (canonical lyrics for biasing), `enable_words=True`
3. Response includes per-character/word-level timestamps
4. Parse into `List[WhisperPhrase]` (same data structure as Whisper output)
5. Downstream canonical-line snap and forced alignment work identically regardless of ASR source

**Fallback on failure:** If DashScope returns error/timeout, and `SOW_ASR_MODE=cloud`, automatically try local ASR backend. Log the fallback event prominently.

### Local ASR Integration

Based on Phase 0 results, integrate the winning local backend:

#### If PyTorch wins (expected):

**New file**: `services/analysis/src/sow_analysis/services/qwen3_asr_local.py`

```python
class Qwen3AsrLocalService:
    """Local Qwen3-ASR via PyTorch (qwen-asr package)."""

    def __init__(self, model_path: Path, device: str = "auto"):
        ...

    async def transcribe(
        self,
        audio_path: Path,
        language: str = "Chinese",
        context: str = "",  # Context biasing (PyTorch supports it)
    ) -> AsrResult:
        """Returns per-character timestamps via Qwen3ASRModel."""
        ...
```

- Uses `qwen_asr.Qwen3ASRModel` (already available via `qwen-asr>=0.0.6` added in Phase 1)
- Supports context biasing (unlike ONNX)
- Returns per-character timestamps (no estimation needed)
- Runs in executor (CPU-bound), with concurrency semaphore

#### If ONNX is also supported:

**New file**: `services/analysis/src/sow_analysis/services/onnx_asr_pipeline.py`

(As described in v1 spec, adapted from Daumee's `onnx_inference.py`)

- 400-line self-contained module
- No PyTorch dependency
- Returns text only (no timestamps) — requires proportional estimation
- `SOW_ASR_MODE=local-onnx` activates this path

### Mode Dispatch in LRC Worker

**Modify**: `services/analysis/src/sow_analysis/workers/lrc.py`

```python
async def generate_lrc(...):
    # Resolve stem mode
    stem_mode = settings.SOW_STEM_MODE  # "cloud" or "local"

    # Resolve ASR mode
    asr_mode = options.asr_mode or settings.SOW_ASR_MODE  # "cloud", "local-pytorch", "local-onnx"

    # Resolve align mode
    align_mode = settings.SOW_ALIGN_MODE  # "cloud" or "local"

    # Step 1: Stem separation (cloud or local)
    vocals_path = await _get_vocals_stem(audio_path, stem_mode, ...)

    # Step 2: ASR transcription (cloud or local)
    asr_result = await _run_asr(vocals_path or audio_path, asr_mode, lyrics_text, ...)

    # Step 3: Canonical-line snap (always local)
    lrc_lines = _canonical_line_snap(asr_result.phrases, lyrics_lines, ...)
    if coverage < 0.70:
        lrc_lines = await _llm_align(asr_result.phrases, lyrics_text, ...)

    # Step 4: Forced alignment (cloud or local)
    if align_mode != "skip":
        lrc_lines = await _run_forced_alignment(
            audio_path, lyrics_text, lrc_lines, align_mode, ...
        )

    # Step 5: Write LRC
    _write_lrc(lrc_lines, output_path)
```

### Cache Strategy

Each ASR backend has its own cache namespace:

```
{CACHE_DIR}/asr/
  dashscope_{params_hash8}.json   # Cloud ASR cache
  pytorch_{params_hash8}.json     # Local PyTorch ASR cache
  onnx_{params_hash8}.json       # Local ONNX ASR cache
```

Cache key includes all ASR-affecting parameters:
- `asr_mode`, `backend`, `model`, `language`, `use_vocals`
- `context_max_chars`, `context_text_hash8` (context biasing changes output)
- `audio_duration_bucket` (rounded to nearest 10s, for cache invalidation on different audio slices)

Cache schema follows v2 pattern from `specs/cache_raw_asr_output_qwen3_local.md`:
```json
{
  "cache_version": 3,
  "backend": "dashscope",
  "params": { ... },
  "wall_time": 12.5,
  "timestamp": 1713456789.0,
  "raw": { "text": "...", "segments": [...] }
}
```

**Extraction runs on every access** (fresh or cached), so tuning `extract_segments` or snap thresholds doesn't require re-running the model.

### Phase 2 Verification

1. Mock DashScope client: verify cloud → local fallback on API error
2. Mock local ASR: verify local-only path works end-to-end
3. Test cache: same params → cache hit; different params → cache miss
4. Test `SOW_ASR_MODE=cloud` with real DashScope key on 2-3 songs
5. Compare cloud ASR output vs local PyTorch ASR output on same songs

---

## Phase 3: Cloud Forced Alignment (Self-Hosted RunPod)

### Goal

Deploy the `Qwen3ForcedAligner` on a serverless GPU platform (RunPod, Modal, etc.) and add a cloud client to the analysis service.

### Why self-host instead of using an existing cloud API

No existing cloud service offers Qwen3 forced alignment. Options:
1. **RunPod Serverless**: Deploy the aligner as a serverless function. Cold start ~30-60s with model loading, warm start ~1s. GPU cost ~$0.0002/GPU-sec.
2. **Modal**: Similar to RunPod, Python-native deployment. Auto-scales to zero.
3. **Keep local**: If cloud alignment is too expensive or unreliable, `SOW_ALIGN_MODE=local` uses the merged local aligner from Phase 1.

### Architecture

```
Analysis Worker
  │
  ├─ SOW_ALIGN_MODE=cloud
  │   │
  │   ▼
  │   [Cloud Forced Aligner Client]
  │     POST https://<runpod-endpoint>/align
  │     Body: { audio_url, lyrics_text, language }
  │     → { lrc_lines: [{start, end, text}] }
  │     │
  │     On failure → fallback to local ForcedAlignerService
  │
  ├─ SOW_ALIGN_MODE=local
  │   │
  │   ▼
  │   [Local ForcedAlignerService] (from Phase 1)
  │     Direct function call, no HTTP
  │     Lazy-loaded PyTorch model
  │
  └─ SOW_ALIGN_MODE=skip
      Skip forced alignment entirely (for quick testing)
```

### Cloud Forced Aligner Client

**New file**: `services/analysis/src/sow_analysis/services/cloud_align_client.py`

```python
class CloudAlignClient:
    """Client for self-hosted Qwen3ForcedAligner on serverless GPU."""

    async def align(
        self,
        audio_url: str,  # R2 URL accessible by the cloud endpoint
        lyrics_text: str,
        language: str = "Chinese",
    ) -> List[LRCLine]:
        ...

    @property
    def is_healthy(self) -> bool:
        """Check if cloud endpoint is reachable."""
        ...
```

Config:
```python
SOW_CLOUD_ALIGN_BASE_URL: str = ""  # e.g. https://api.runpod.ai/v2/<endpoint-id>
SOW_CLOUD_ALIGN_API_KEY: str = ""  # RunPod API key
SOW_CLOUD_ALIGN_TIMEOUT: int = 300  # seconds (includes cold start)
```

### Serverless Deployment Package

**New directory**: `services/cloud-align/`

Minimal serverless function:

```
services/cloud-align/
  Dockerfile         # GPU base image + qwen-asr + FastAPI
  main.py            # POST /align handler
  requirements.txt   # qwen-asr, fastapi, uvicorn, boto3
  README.md          # Deploy instructions for RunPod/Modal
```

This is a thin HTTP wrapper around `Qwen3ForcedAligner` — essentially the same logic that was in the old qwen3 service, but optimized for serverless (model loaded at container start, not on demand).

### Fallback Logic

```python
async def _run_forced_alignment(audio_path, lyrics_text, lrc_lines, align_mode, ...):
    if align_mode == "cloud":
        try:
            result = await cloud_client.align(audio_url, lyrics_text)
            return result
        except (ConnectionError, TimeoutError, CloudAlignError) as e:
            logger.warning(f"Cloud alignment failed: {e}. Falling back to local.")
            align_mode = "local"  # Fall through

    if align_mode == "local":
        result = await local_aligner.align(audio_path, lyrics_text)
        return result

    # align_mode == "skip"
    return lrc_lines
```

### Phase 3 Verification

1. Deploy cloud-align to RunPod (or test with local Docker)
2. Test cloud alignment on 3-5 songs, compare output with local aligner
3. Test fallback: kill cloud endpoint, verify local aligner takes over
4. Measure cold start latency and warm request latency
5. Estimate per-song cost for the full catalog (~685 songs)

---

## Phase 4: Canonical-Line Snap Production Hardening

### Goal

Port the POC's `canonical_line_snap()` algorithm into a production-grade, independently testable service module.

### Current state

- POC implementation: `poc/gen_lrc_qwen3_asr_local.py` (1793 lines, includes both DP and greedy algorithms)
- POC handles: Chinese script detection, fragment merging, force-anchoring, DP consensus walk with chorus repeats, dedup
- Not yet production-grade: mixed concerns, POC-only diagnostics, no unit test coverage

### New file: `services/analysis/src/sow_analysis/services/canonical_snap.py`

Extract the snap algorithm into its own service module:

```python
def detect_chinese_script(text: str) -> str: ...
def _normalize_text(text: str) -> str: ...
def _is_filler(text: str) -> bool: ...
def _text_to_pinyin(text: str) -> str: ...
def _score(asr_text: str, canonical_line: str, target_script: str, use_pinyin: bool) -> float: ...
def _combined_score(asr: str, canonical: str, target_script: str, asr_char_count: int) -> float: ...
def canonical_line_snap(
    asr_phrases: List[WhisperPhrase],
    lyrics_lines: List[str],
    threshold: float = 0.60,
    algo: str = "dp",
) -> List[LRCLine]: ...
```

Dependencies to add to analysis `pyproject.toml`:
```toml
"rapidfuzz>=3.0.0",  # Fuzzy matching
"zhconv>=1.4.0",     # Chinese script conversion
"pypinyin>=0.52.0",  # Pinyin-based matching
```

### Key differences from POC

| POC | Production |
|-----|-----------|
| Input: `list[dict]` | Input: `List[WhisperPhrase]` |
| Output: raw tuples | Output: `List[LRCLine]` |
| Diagnostic file writing | No file I/O, logging only |
| `typer.echo` | `logger.info/warning` |
| Mixed with ASR code | Standalone module |

### Test file: `services/analysis/tests/services/test_canonical_snap.py`

1. `test_detect_chinese_script_simplified` — Returns "zh-hans"
2. `test_detect_chinese_script_traditional` — Returns "zh-hant"
3. `test_is_filler` — Filler words detected
4. `test_score_exact_match` — Score = 1.0
5. `test_score_partial_match` — Score between 0 and 1
6. `test_canonical_snap_dp_algo` — DP algorithm with chorus repeats
7. `test_canonical_snap_greedy_algo` — Greedy algorithm
8. `test_canonical_snap_empty_input` — Empty → empty
9. `test_canonical_snap_below_threshold` — Low-scoring segments keep ASR text
10. `test_canonical_snap_preserves_repeats` — Chorus appears multiple times
11. `test_canonical_snap_filler_handling` — Filler segments skipped

---

## Phase 5: End-to-End Integration and Validation

### LRC Worker Rewrite

The `generate_lrc()` function is restructured around the per-step dispatch:

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
) -> tuple[Path, int, List[WhisperPhrase]]:

    stem_mode = settings.SOW_STEM_MODE
    asr_mode = options.asr_mode or settings.SOW_ASR_MODE
    align_mode = settings.SOW_ALIGN_MODE

    # --- Step 1: Vocals stem ---
    vocals_path = await _get_vocals_stem(audio_path, stem_mode, vocals_stem_url, ...)

    # --- Step 2: ASR ---
    asr_phrases = await _run_asr(
        vocals_path or audio_path,
        asr_mode=asr_mode,
        lyrics_text=lyrics_text,
        ...
    )

    # --- Step 3: Canonical-line snap ---
    lyrics_lines = lyrics_text.strip().splitlines()
    lrc_lines = canonical_line_snap(asr_phrases, lyrics_lines, threshold=options.snap_threshold, algo=options.snap_algo)

    # Coverage check
    expected_lines = len([l for l in lyrics_lines if l.strip()])
    coverage = len(lrc_lines) / max(expected_lines, 1)

    if coverage < 0.70:
        logger.warning(f"Snap coverage {coverage:.0%} < 70%, falling back to LLM alignment")
        lrc_lines = await _llm_align(asr_phrases, lyrics_text, ...)

    # --- Step 4: Forced alignment ---
    lrc_lines = await _run_forced_alignment(
        audio_path, lyrics_text, lrc_lines, align_mode, ...
    )

    # --- Step 5: Write LRC ---
    _write_lrc(lrc_lines, output_path or _default_lrc_path(audio_path))
    return (output_path, len(lrc_lines), asr_phrases)
```

### LrcOptions updates

```python
class LrcOptions(BaseModel):
    # Existing fields
    use_qwen3: bool = True
    max_qwen3_duration: int = 300
    force: bool = False
    force_whisper: bool = False
    use_vocals_stem: bool = True

    # New fields
    asr_mode: str = ""  # ""=use env var, "cloud", "local-pytorch", "local-onnx"
    stem_mode: str = ""  # ""=use env var, "cloud", "local"
    align_mode: str = ""  # ""=use env var, "cloud", "local", "skip"
    snap_threshold: float = 0.60
    snap_algo: str = "dp"  # "greedy" or "dp"
    asr_context_max_chars: int = 10000  # Context biasing (cloud + PyTorch only)
```

### YouTube transcript path

The YouTube transcript path is **deprecated but preserved**. It is no longer called by default but remains accessible:

- `SOW_ASR_MODE=legacy` activates the old Whisper + YouTube + LLM path
- This ensures backward compatibility during migration
- Remove in a follow-up after the new pipeline proves out

### End-to-End Validation

1. **Benchmark on 10 known-good songs**: Regenerate LRC via new pipeline (cloud), compare against hand-corrected versions. Target: ≥95% character match, ≤0.5s avg timestamp delta.

2. **Per-step fallback testing**: Force each step to fail independently and verify correct fallback:
   - Kill MVSEP → local audio-separator takes over
   - Remove DashScope key → local PyTorch ASR takes over
   - Kill cloud align endpoint → local forced aligner takes over
   - All three fail → best-effort with whatever works (Whisper ASR + LLM alignment + estimated timestamps)

3. **Cost validation**: Run full catalog (~685 songs) through cloud pipeline, measure:
   - DashScope ASR cost: ~$0.008/song expected
   - MVSEP stem separation cost: existing, already measured
   - Cloud alignment cost: depends on RunPod pricing

4. **Performance**: Measure end-to-end LRC generation time for cloud vs local pipeline

---

## New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| **Per-step mode** | | | |
| `SOW_STEM_MODE` | str | `cloud` | `cloud`=MVSEP, `local`=audio-separator |
| `SOW_ASR_MODE` | str | `cloud` | `cloud`=DashScope, `local-pytorch`, `local-onnx` |
| `SOW_ALIGN_MODE` | str | `cloud` | `cloud`=self-hosted, `local`=in-process, `skip`=off |
| **DashScope ASR** | | | |
| `SOW_DASHSCOPE_ASR_API_KEY` | str | `""` | Required for cloud ASR |
| `SOW_DASHSCOPE_ASR_BASE_URL` | str | `https://dashscope-intl.aliyuncs.com/api/v1` | Regional endpoint |
| `SOW_DASHSCOPE_ASR_MODEL` | str | `qwen3-asr-flash` | Or `qwen3-asr-flash-filetrans` for >5min |
| **Cloud alignment** | | | |
| `SOW_CLOUD_ALIGN_BASE_URL` | str | `""` | RunPod/Modal endpoint URL |
| `SOW_CLOUD_ALIGN_API_KEY` | str | `""` | Serverless platform API key |
| `SOW_CLOUD_ALIGN_TIMEOUT` | int | `300` | Timeout in seconds |
| **Qwen3 forced aligner (merged)** | | | |
| `SOW_QWEN3_MODEL_ROOT` | str | — | Host path for model mount |
| `SOW_QWEN3_DEVICE` | str | `auto` | `auto`, `cpu`, `cuda`, `mps` |
| `SOW_QWEN3_DTYPE` | str | `float32` | `float32` (CPU), `bfloat16` (GPU) |
| `SOW_QWEN3_MAX_CONCURRENT` | int | `2` | Concurrency semaphore |
| **Local ASR** | | | |
| `SOW_ASR_LOCAL_BACKEND` | str | `pytorch` | `pytorch` or `onnx` (when SOW_ASR_MODE starts with `local`) |
| `SOW_QWEN3_ASR_ONNX_MODEL_ROOT` | str | — | Host path for ONNX model mount (if ONNX backend used) |
| `SOW_QWEN3_ASR_ONNX_QUANTIZE` | str | `int8` | ONNX decoder quantization |
| `SOW_QWEN3_ASR_ONNX_THREADS` | int | `0` | ONNX threads (0=all) |
| **Legacy** | | | |
| `SOW_LYRICS_ASR_MODE` | int | `1` | Deprecated. 1=Whisper (legacy). Use `SOW_ASR_MODE` instead. |

---

## Dependencies Added

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| `qwen-asr` | >=0.6.0 | Qwen3ForcedAligner + Qwen3ASRModel (local ASR) | `services/analysis/pyproject.toml` |
| `rapidfuzz` | >=3.0.0 | Fuzzy matching for canonical snap | `services/analysis/pyproject.toml` |
| `zhconv` | >=1.4.0 | Chinese script conversion | `services/analysis/pyproject.toml` |
| `pypinyin` | >=0.52.0 | Pinyin matching for homophones | `services/analysis/pyproject.toml` |

Note: `onnxruntime`, `numpy`, and `librosa` already present in analysis service.

---

## Operational Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **ONNX ASR no timestamps** — proportional estimation too inaccurate | High (if ONNX chosen) | LRC timestamps off by seconds | POC evaluation in Phase 0; PyTorch backend preferred; ForcedAligner polish step |
| **Cloud ASR rate limits or outage** | Medium | Pipeline stalls | Automatic local fallback; configurable timeout; retry with backoff |
| **Cloud alignment cold start** (RunPod serverless) | High | 30-60s delay on first request | Pre-warm endpoint; local fallback for latency-sensitive cases; health check before calling |
| **Memory pressure in merged service** (demucs + whisper + qwen3) | Medium | OOM kills container | Lazy model loading; per-worker semaphores; monitor memory; set Docker resource limits |
| **Canonical snap < 70% coverage** | Medium | Falls back to LLM (lower quality) | Tune threshold; handle edge cases (short songs, heavily repeated choruses); log coverage for monitoring |
| **Cloud forced alignment cost** (RunPod GPU) | Medium | ~$0.02-0.05/song GPU time | Profile cost during Phase 3; consider batch processing; local alignment for backfill |
| **Qwen3 model version drift** between cloud and local | Low | Different alignment results | Pin model version in both deployments; version in cache key |
| **DashScope audio upload size limit** | Low | Very long songs fail | Use `filetrans` variant for >5min; fallback to local for >12min |
| **Stem separation failure cascades** to ASR and alignment | Low | Full mix used instead (lower quality) | Log clearly; both ASR and alignment degrade gracefully on full mix |
| **Cache key collision** across cloud/local backends | Low | Stale cached ASR output | Separate cache namespaces per backend; params hash includes all ASR-affecting params |

---

## Data Flow Issues and Resolutions

### Issue 1: ONNX ASR text-only output

**Problem**: The Daumee ONNX pipeline returns `{text, language, timing}` without per-character or per-phrase timestamps. The v1 spec proposed proportional timestamp estimation, but this produces timestamps that can be off by many seconds for songs with long instrumental breaks or uneven phrase lengths.

**Resolution**:
1. **Primary path**: Use PyTorch Qwen3-ASR or DashScope ASR (both produce per-character timestamps)
2. **ONNX fallback**: If ONNX must be used, apply Silero VAD on vocals to detect vocal activity boundaries, then map ASR text to VAD segments. This gives phrase boundaries aligned to actual vocal activity, much better than proportional splitting.
3. **Forced aligner polish**: After snap, forced alignment corrects any remaining timestamp drift.

### Issue 2: Vocals stem dependency chain

**Problem**: Both ASR and forced alignment prefer vocals stems. If stem separation fails, both downstream steps degrade to full mix. This creates a single point of failure.

**Resolution**:
1. Stem separation result is cached (both in R2 and locally), so re-separation is rarely needed
2. MVSEP has its own fallback to local audio-separator
3. ASR and alignment both work on full mix (just lower quality), not hard failure
4. Log the audio source used (vocals vs full mix) for monitoring

### Issue 3: Context biasing availability varies by backend

**Problem**: DashScope and PyTorch backends support context biasing (passing canonical lyrics to bias ASR), but ONNX does not. This means ONNX ASR will produce more character errors.

**Resolution**:
1. ONNX path relies more heavily on canonical-line snap for text correction
2. POC comparison (Phase 0) will determine if the quality gap is acceptable
3. If ONNX without context biasing produces too many errors, mark ONNX as "fallback only, not for primary use"

### Issue 4: Cloud alignment needs audio URL accessible from serverless endpoint

**Problem**: The self-hosted forced aligner on RunPod needs to download audio. It can't access the analysis container's local filesystem.

**Resolution**:
1. Pass R2 URL to cloud align endpoint (R2 is publicly accessible or pre-signed URL)
2. Cloud align endpoint downloads from R2 (same pattern as old qwen3 service)
3. For local aligner (merged), pass local file path directly (no download needed)

### Issue 5: Cache inconsistency across mode switches

**Problem**: If an operator switches `SOW_ASR_MODE` from `cloud` to `local-pytorch`, cached ASR results from DashScope are structurally different from PyTorch output (different segment boundaries, different text).

**Resolution**:
1. Cache key includes `asr_mode` and `backend` — different modes never share cache
2. Each backend has its own cache subdirectory
3. Phrase extraction and snap run on every access regardless of cache status, so downstream tuning doesn't require cache invalidation

---

## Migration / Rollout

### Step 1: Phase 0 — POC Evaluation (1-2 weeks)
- Create ONNX and PyTorch POC scripts
- Run comparative evaluation on 10-20 songs
- Document findings; select local ASR backend

### Step 2: Phase 1 — Merge Qwen3 Service (1 week)
- Port forced aligner into analysis service
- Remove qwen3 HTTP client, use direct function call
- Remove `services/qwen3/` directory
- Validate: existing LRC tests pass, forced alignment output unchanged

### Step 3: Phase 4 — Canonical Snap Hardening (1 week, can parallel with Phase 1)
- Extract snap algorithm into standalone module
- Add unit tests
- Wire into LRC worker (replaces LLM alignment as primary text correction for non-legacy modes)

### Step 4: Phase 2 — Cloud ASR + Mode Dispatch (1-2 weeks)
- Add DashScope ASR client
- Add local Qwen3-ASR service (backend per Phase 0 results)
- Implement per-step mode dispatch in LRC worker
- Validate: cloud → local fallback works, ASR output quality acceptable

### Step 5: Phase 3 — Cloud Forced Alignment (1 week)
- Deploy cloud align to RunPod (or test with local Docker first)
- Add cloud align client
- Implement cloud → local fallback
- Validate: cloud alignment works, fallback on failure

### Step 6: Phase 5 — End-to-End Validation (1 week)
- Benchmark 10 known-good songs
- Fallback testing for each step
- Cost validation
- Switch defaults to cloud mode

### Step 7: Backfill (optional, ongoing)
- Regenerate LRCs for existing catalog using cloud pipeline
- Compare quality, flag outliers for manual review

---

## Out of Scope

- Retiring the Whisper/YouTube path (kept as `legacy` mode until new pipeline proves out)
- Building a lyric review UI (existing `upload-lrc` flow covers outliers)
- GPU acceleration for ONNX ASR (CPU-only; `CUDAExecutionProvider` can be added later)
- 1.7B ONNX model support (0.6B only for now)
- Cache eviction / size limits (acceptable at POC/catalog scale)
- Modal/RunPod deployment automation (manual deployment with documented steps)
- Changes to admin CLI or user app (they consume LRC output, not LRC generation)
