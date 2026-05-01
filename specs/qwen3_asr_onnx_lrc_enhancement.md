# Enhance LRC Worker with Qwen3 ASR (ONNX) Support

## Context

The LRC generation worker (`services/analysis/src/sow_analysis/workers/lrc.py`) currently uses **faster-whisper** (large-v3) as its sole ASR transcription backend. Qwen3-ASR produces significantly better Chinese song lyric transcriptions than Whisper, but is not yet integrated into the production worker.

The POC (`poc/gen_lrc_qwen3_asr_local.py`) has demonstrated three Qwen3-ASR backends (MLX, PyTorch, DashScope cloud), but none are wired into the analysis service. This spec adds a second ASR option — **Qwen3-ASR-0.6B via ONNX Runtime** — running locally inside the existing analysis Docker container, selected by the `SOW_LYRICS_ASR_MODE` environment variable.

### Why ONNX (not PyTorch or MLX)?

- **MLX** (`mlx-qwen3-asr`) is Apple Silicon only — won't run in Docker on Linux.
- **PyTorch** (`qwen_asr.Qwen3ASRModel`) requires a full PyTorch stack, increasing Docker image size significantly and competing for GPU/CPU with the existing allin1/demucs/faster-whisper stack.
- **ONNX Runtime** is already a dependency in the analysis container (used by `audio-separator` for stem separation). The `Daumee/Qwen3-ASR-0.6B-ONNX-CPU` model runs ~3x realtime on CPU with no PyTorch, no GPU, ~2.5GB model files. This is the lightest-weight path to production.

## ASR Mode Selection

Environment variable `SOW_LYRICS_ASR_MODE` (int, default `1`):

| Value | Mode | Description |
|-------|------|-------------|
| `1` | Whisper (current) | Existing `faster-whisper` transcription → LLM alignment. No behavior change. |
| `2` | Qwen3 ASR local ONNX | `Daumee/Qwen3-ASR-0.6B-ONNX-CPU` via onnxruntime inside analysis container. Canonical-line snap for character correction, LLM alignment as fallback. |
| `3` | Qwen3 ASR via API | DashScope cloud API. **Stub only** in this phase — raises `NotImplementedError` with a clear message. Full implementation is a separate spec. |

Mode 1 remains the default to avoid breaking existing deployments. Operators opt into mode 2 by setting `SOW_LYRICS_ASR_MODE=2`.

## Architecture

### Mode 2 Pipeline

```
Audio (vocals stem preferred, full mix fallback)
  │
  ▼
[1] Qwen3-ASR-0.6B ONNX (local, CPU)
    - input: vocals stem FLAC or full mix MP3
    - language: Chinese
    - NO context biasing (ONNX model doesn't support it)
    → raw dict: {text, language, timing}
    → extract_segments() → list of {start, end, text} phrases
  │
  ▼
[2] Canonical-line snap (fuzzy matching)
    - Port from POC: canonical_line_snap()
    - For each ASR segment: fuzzy match against canonical lyrics
    - If best match ≥ threshold: replace ASR text with canonical
    - Else: keep ASR text
    → list of (timestamp, text) tuples
  │
  ▼
[3] Coverage check
    - If snap coverage ≥ 70% of Whisper-phrase count: use snap result
    - If snap coverage < 70%: fall back to LLM alignment
    - (When falling back, Whisper phrases are NOT re-run; we use
       the ONNX ASR phrases as input to LLM alignment instead)
  │
  ▼
[4] Optional Qwen3-ForcedAligner refinement (unchanged)
    - If options.use_qwen3 and content_hash and duration ≤ limit
  │
  ▼
[5] Write LRC file (unchanged)
```

### Mode 1 Pipeline (unchanged)

```
YouTube transcript (if URL available) → LLM correction
  ↓ fallback
Whisper transcription → LLM alignment → Qwen3-ForcedAligner refinement → Write LRC
```

## Files to Modify

### 1. `services/analysis/src/sow_analysis/config.py`

Add new settings fields:

```python
# ASR Mode Configuration
SOW_LYRICS_ASR_MODE: int = 1  # 1=Whisper, 2=Qwen3-ASR ONNX local, 3=Qwen3-ASR API (future)

# Qwen3 ASR ONNX Configuration
SOW_QWEN3_ASR_ONNX_MODEL_DIR: Path = Path("/models/qwen3-asr-onnx")
SOW_QWEN3_ASR_ONNX_QUANTIZE: str = "int8"  # "int8" or "none" (FP32)
SOW_QWEN3_ASR_ONNX_THREADS: int = 0  # 0 = all threads
SOW_QWEN3_ASR_ONNX_CHUNK_SEC: int = 30  # Long-audio chunk target
SOW_QWEN3_ASR_ONNX_MAX_TOKENS: int = 512  # Max decode tokens per chunk
```

### 2. `services/analysis/src/sow_analysis/models.py`

Extend `LrcOptions`:

```python
class LrcOptions(BaseModel):
    # ... existing fields unchanged ...
    asr_mode: int = 0  # 0=use env var (SOW_LYRICS_ASR_MODE), 1=Whisper, 2=Qwen3-ASR ONNX, 3=Qwen3-ASR API
    snap_threshold: float = 0.60  # Minimum fuzzy score for canonical-line snap
    snap_algo: str = "dp"  # "greedy" or "dp" for canonical_line_snap algorithm
    asr_context_max_chars: int = 0  # 0=disabled for ONNX; used by API mode in future
```

The `asr_mode` field on `LrcOptions` defaults to `0` (meaning "use env var"), so the effective ASR mode is resolved at runtime as: `options.asr_mode if options.asr_mode > 0 else settings.SOW_LYRICS_ASR_MODE`.

### 3. `services/analysis/src/sow_analysis/workers/lrc.py`

Major changes — add ONNX transcription, canonical-line snap, and mode-based dispatch.

#### 3a. New exception classes

```python
class Qwen3AsrTranscriptionError(LRCWorkerError):
    """Raised when Qwen3 ASR ONNX transcription fails."""

class CanonicalSnapError(LRCWorkerError):
    """Raised when canonical-line snap produces no output."""
```

#### 3b. New function: `_run_qwen3_asr_onnx()`

Async function wrapping the ONNX ASR pipeline. Runs in executor (CPU-bound).

```python
async def _run_qwen3_asr_onnx(
    audio_path: Path,
    language: str = "zh",
) -> List[WhisperPhrase]:
```

Implementation details:
- Import `onnxruntime` inside the function (lazy, like Whisper)
- Instantiate `OnnxAsrPipeline` (adapted from Daumee's `onnx_inference.py`) using `settings.SOW_QWEN3_ASR_ONNX_MODEL_DIR`, `settings.SOW_QWEN3_ASR_ONNX_QUANTIZE`, `settings.SOW_QWEN3_ASR_ONNX_THREADS`
- Call `pipeline.transcribe(str(audio_path), language="Chinese", max_new_tokens=settings.SOW_QWEN3_ASR_ONNX_MAX_TOKENS, chunk_sec=settings.SOW_QWEN3_ASR_ONNX_CHUNK_SEC)`
- The Daumee pipeline returns `{text, language, timing}` but **no per-character timestamps** — it only returns top-level text
- Adapt the POC's `extract_segments()` logic: since the Daumee ONNX pipeline does NOT return per-character timestamps (unlike the MLX backend), we need to handle this differently

**Critical difference from POC MLX backend:** The Daumee ONNX pipeline's `OnnxAsrPipeline.transcribe()` returns `{"text": str, "language": str, "timing": {...}}` but does NOT include per-character `segments`. This means we cannot extract phrase-level timestamps from the ONNX model directly.

**Resolution:** We have two options:

**Option A (Recommended): Accept whole-song text, use fixed-duration segment estimation.**
- The ONNX pipeline returns the full transcription text without timestamps
- Split the text into phrases using punctuation (same logic as POC's `extract_segments`)
- Estimate timestamps by dividing audio duration proportionally across phrases
- This is less precise than Whisper's timestamps but the text quality is better
- The Qwen3-ForcedAligner refinement step (step 4) can fix the timestamps afterward

**Option B: Use Whisper for timestamps, Qwen3-ASR for text only.**
- Run both Whisper (for timestamps) and Qwen3-ASR (for text)
- Align Qwen3-ASR text to Whisper phrases (like the existing LLM alignment step)
- More accurate timestamps but doubles compute cost

**We choose Option A** because:
1. The canonical-line snap already corrects the text, so we're primarily using Qwen3-ASR text as input to the snap algorithm
2. Qwen3-ForcedAligner refinement (already in the pipeline) fixes timestamps
3. Running two ASR models doubles cost and latency

However, there's a **third hybrid option** that's better:

**Option C (Best): Use ONNX ASR text + Whisper timestamps when cached_phrases available.**
- If `cached_phrases` (Whisper) are available, use Qwen3-ASR text quality + Whisper timestamp structure
- If no cached Whisper phrases, fall back to Option A (estimated timestamps + ForcedAligner)

Actually, re-examining the Daumee pipeline more carefully: while `_transcribe_chunk` doesn't return per-char timestamps, the full `transcribe` method also doesn't. The ONNX pipeline is text-only, no timestamps.

**Final decision: Option A with ForcedAligner polish.** The pipeline becomes:
1. ONNX ASR → text (no timestamps)
2. Split text into phrases via punctuation → estimate timestamps proportionally
3. Canonical-line snap → correct text, inherit estimated timestamps
4. Qwen3-ForcedAligner → refine timestamps (this is the key step that makes Option A viable)
5. If ForcedAligner is unavailable/skipped, the estimated timestamps are "good enough" (Taption-level)

#### 3c. New function: `_split_asr_text_to_phrases()`

Split ASR text into phrases using Chinese punctuation, estimating timestamps:

```python
def _split_asr_text_to_phrases(
    asr_text: str,
    audio_duration_seconds: float,
) -> List[WhisperPhrase]:
```

- Split `asr_text` on Chinese/ASCII punctuation (`。，、！？；：．. , ! ? ; :`)
- Distribute timestamps evenly across phrases (proportional to phrase character count)
- Each phrase gets `start` and `end` estimated from its proportional share of the audio duration
- These are *rough* timestamps — Qwen3-ForcedAligner will refine them

#### 3d. New function: `_canonical_line_snap()`

Port the POC's `canonical_line_snap()` algorithm to the production worker. This is the most complex piece.

```python
def _canonical_line_snap(
    asr_phrases: List[WhisperPhrase],
    lyrics_lines: List[str],
    threshold: float = 0.60,
    algo: str = "dp",
) -> List[LRCLine]:
```

Dependencies to add to analysis service:
- `rapidfuzz` — for fuzzy matching (`fuzz.token_set_ratio`, `fuzz.partial_ratio`)
- `zhconv` — for Chinese script conversion (zh-hans ↔ zh-hant)
- `pypinyin` — for pinyin-based matching (handles homophones)

These are lightweight pure-Python packages, no ML dependencies.

Implementation notes (ported from POC `canonical_line_snap`):
1. **Detect Chinese script** of canonical lyrics (simplified vs traditional) using `zhconv`
2. **Merge adjacent fragmented ASR phrases** — if two consecutive short phrases merge to a better score, combine them
3. **Force-anchor first 1-2 content segments** (skip filler like 嗯/啊)
4. **DP consensus walk** — for each remaining ASR segment, find the best-matching canonical line considering sequence position, with skip/wrap penalties for handling repeats and gaps
5. **Dedup consecutive identical snaps**
6. Return `List[LRCLine]` with timestamps from ASR and text from canonical lyrics

Key differences from POC version:
- Input is `List[WhisperPhrase]` instead of `list[dict]`
- Output is `List[LRCLine]` instead of raw tuples
- No diagnostic file writing (that's POC-only)
- Logging instead of typer.echo

#### 3e. Modify `generate_lrc()` — add mode-based dispatch

The `generate_lrc()` function gains an ASR mode switch:

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
    # Resolve effective ASR mode
    asr_mode = options.asr_mode if options.asr_mode > 0 else settings.SOW_LYRICS_ASR_MODE

    if asr_mode == 2:
        # Qwen3-ASR ONNX path
        return await _generate_lrc_qwen3_asr_onnx(...)
    elif asr_mode == 3:
        raise NotImplementedError(
            "Qwen3-ASR API mode (mode 3) is not yet implemented. "
            "Set SOW_LYRICS_ASR_MODE=1 or 2."
        )
    else:
        # Mode 1: existing Whisper path (unchanged)
        return await _generate_lrc_whisper(...)
```

#### 3f. New function: `_generate_lrc_qwen3_asr_onnx()`

The main orchestration function for mode 2:

```python
async def _generate_lrc_qwen3_asr_onnx(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    content_hash: Optional[str] = None,
    vocals_stem_url: Optional[str] = None,
) -> tuple[Path, int, List[WhisperPhrase]]:
```

Flow:
1. Run `_run_qwen3_asr_onnx()` → get raw ASR text
2. Calculate audio duration from the ASR timing data (or probe the audio file)
3. Call `_split_asr_text_to_phrases()` → `List[WhisperPhrase]` with estimated timestamps
4. Call `_canonical_line_snap()` → `List[LRCLine]`
5. Check coverage: if snap result has < 70% of expected line count, fall back to LLM alignment using the ONNX ASR phrases as input (instead of Whisper phrases)
6. If `options.use_qwen3 and content_hash`: run Qwen3-ForcedAligner refinement
7. Write LRC file
8. Return `(output_path, line_count, asr_phrases)`

**Important:** The Qwen3-ForcedAligner refinement step is **critical** for mode 2 because the ONNX ASR pipeline doesn't produce timestamps. Without ForcedAligner, the LRC will have only estimated timestamps (proportional splitting). The pipeline should log a prominent warning if ForcedAligner is disabled/skipped for mode 2.

### 4. `services/analysis/src/sow_analysis/services/onnx_asr_pipeline.py` (NEW)

Adapt the Daumee `onnx_inference.py` into a production-grade module. Key changes from the original:

- Remove CLI (`argparse`, `main`)
- Remove `print()` calls → use `logger`
- Class `OnnxAsrPipeline` preserved with same API:
  - `__init__(onnx_dir, num_threads, quantize)`
  - `transcribe(audio_path, language, max_new_tokens, chunk_sec) -> dict`
- Internal helpers unchanged: `load_audio`, `compute_mel_spectrogram`, `get_mel_filters`, `OnnxAsrPipeline._encode_audio`, `_build_prompt_ids`, `_embed_and_fuse`, `_transcribe_chunk`, `find_silence_split_points`
- `SimpleTokenizer` class — use `tokenizers` library (bundled with the ONNX model) or fall back to `transformers.AutoTokenizer`

This file is a ~400-line self-contained module with zero PyTorch dependency. It only requires `onnxruntime`, `numpy`, `librosa`, and a tokenizer.

### 5. `services/analysis/src/sow_analysis/services/canonical_snap.py` (NEW)

Extract the canonical-line snap algorithm into its own service module (not inline in `lrc.py`). This keeps `lrc.py` focused on orchestration and makes the snap algorithm independently testable.

Contents (ported from POC `canonical_line_snap` and helpers):
- `detect_chinese_script(text) -> str`
- `_normalize_text(text) -> str`
- `_is_filler(text) -> bool`
- `_text_to_pinyin(text) -> str`
- `_score(asr_text, canonical_line, target_script, use_pinyin) -> float`
- `_combined_score(asr, canonical, target_script, asr_char_count) -> float`
- `canonical_line_snap(asr_phrases, lyrics_lines, threshold, algo, ...) -> List[LRCLine]`

Dependencies: `rapidfuzz`, `zhconv`, `pypinyin`

### 6. `services/analysis/pyproject.toml`

Add new dependencies to the `service` extra:

```toml
service = [
    # ... existing ...
    "rapidfuzz>=3.0.0",
    "zhconv>=1.4.0",
    "pypinyin>=0.52.0",
    "librosa>=0.10.0",   # already present? verify
    "numpy>=1.24.0",     # already present via other deps
]
```

Note: `onnxruntime>=1.17.0` is already in the service extra. `librosa` is likely already a transitive dependency (used by demucs/whisper). Need to verify and only add if missing.

### 7. `services/analysis/Dockerfile`

Add model download step for the ONNX model files:

```dockerfile
# Download Qwen3-ASR ONNX model at build time
ARG SOW_QWEN3_ASR_ONNX_MODEL_DIR=/models/qwen3-asr-onnx
RUN mkdir -p ${SOW_QWEN3_ASR_ONNX_MODEL_DIR} && \
    pip install huggingface_hub && \
    python -c "from huggingface_hub import snapshot_download; snapshot_download('Daumee/Qwen3-ASR-0.6B-ONNX-CPU', local_dir='${SOW_QWEN3_ASR_ONNX_MODEL_DIR}')" && \
    pip uninstall -y huggingface_hub
```

Alternatively, mount the model from host (like the audio-separator models) to avoid inflating the Docker image:

```yaml
# docker-compose.yml addition
volumes:
  - ${SOW_QWEN3_ASR_ONNX_MODEL_ROOT}:/models/qwen3-asr-onnx:ro
```

**Recommended: Host mount** (consistent with how `audio-separator` models are handled). The model is ~2.5GB and the existing pattern mounts model dirs read-only from the host. The Dockerfile should NOT download the model; instead add a setup script or documentation for the operator to download it.

### 8. `services/analysis/docker-compose.yml`

Add volume mount for ONNX model directory:

```yaml
services:
  analysis:
    volumes:
      # ... existing ...
      - ${SOW_QWEN3_ASR_ONNX_MODEL_ROOT}:/models/qwen3-asr-onnx:ro
  analysis-dev:
    volumes:
      # ... existing ...
      - ${SOW_QWEN3_ASR_ONNX_MODEL_ROOT}:/models/qwen3-asr-onnx:ro
```

### 9. `services/analysis/src/sow_analysis/workers/lrc.py` — Existing function modifications

#### `_run_whisper_transcription()` — NO CHANGES
The existing Whisper transcription function stays untouched. Mode 1 behavior is preserved exactly.

#### `_llm_align()` — NO CHANGES
The existing LLM alignment function stays untouched. It's used as fallback for mode 2 when snap coverage is poor.

#### `generate_lrc()` — See 3e above
Add mode dispatch. The existing code path (YouTube → Whisper → LLM → Qwen3-ForcedAligner) moves into `_generate_lrc_whisper()` (private function), called when `asr_mode == 1`.

#### `_qwen3_refine()` — NO CHANGES
The Qwen3-ForcedAligner refinement step is used by both modes.

### 10. `scripts/download_qwen3_asr_onnx_model.sh` (NEW)

Helper script for operators to download the ONNX model to the host:

```bash
#!/usr/bin/env bash
# Download Daumee/Qwen3-ASR-0.6B-ONNX-CPU model files
# Usage: ./scripts/download_qwen3_asr_onnx_model.sh [target_dir]
set -euo pipefail
TARGET_DIR="${1:-$HOME/.cache/sow/qwen3-asr-onnx}"
mkdir -p "$TARGET_DIR"
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Daumee/Qwen3-ASR-0.6B-ONNX-CPU', local_dir='$TARGET_DIR')"
echo "Model downloaded to: $TARGET_DIR"
echo "Set SOW_QWEN3_ASR_ONNX_MODEL_ROOT=$TARGET_DIR before starting Docker"
```

## New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SOW_LYRICS_ASR_MODE` | int | `1` | ASR mode: 1=Whisper, 2=Qwen3-ASR ONNX, 3=API (future) |
| `SOW_QWEN3_ASR_ONNX_MODEL_DIR` | Path | `/models/qwen3-asr-onnx` | Directory containing ONNX model files |
| `SOW_QWEN3_ASR_ONNX_QUANTIZE` | str | `int8` | Decoder quantization: `int8` or `none` (FP32) |
| `SOW_QWEN3_ASR_ONNX_THREADS` | int | `0` | Number of threads (0=all) |
| `SOW_QWEN3_ASR_ONNX_CHUNK_SEC` | int | `30` | Target chunk length for long audio |
| `SOW_QWEN3_ASR_ONNX_MAX_TOKENS` | int | `512` | Max tokens to decode per chunk |
| `SOW_QWEN3_ASR_ONNX_MODEL_ROOT` | str | — | Host path to mount as model dir (docker-compose) |

## Dependencies Added

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| `rapidfuzz` | >=3.0.0 | Fuzzy string matching for canonical snap | `services/analysis/pyproject.toml` service extra |
| `zhconv` | >=1.4.0 | Chinese script conversion (zh-hans ↔ zh-hant) | `services/analysis/pyproject.toml` service extra |
| `pypinyin` | >=0.52.0 | Pinyin conversion for homophone matching | `services/analysis/pyproject.toml` service extra |

Note: `onnxruntime`, `numpy`, and `librosa` are already present in the analysis service.

## Tests

### New test file: `services/analysis/tests/workers/test_lrc_qwen3_asr_onnx.py`

Tests for mode 2 path with mocked ONNX pipeline:

1. **`test_asr_mode_dispatch_mode1`** — Mode 1 calls existing Whisper path
2. **`test_asr_mode_dispatch_mode2`** — Mode 2 calls ONNX path
3. **`test_asr_mode_dispatch_mode3_raises`** — Mode 3 raises NotImplementedError
4. **`test_qwen3_asr_onnx_returns_phrases`** — Mocked ONNX pipeline returns text → phrases are extracted
5. **`test_split_asr_text_to_phrases`** — Unit test for punctuation-based splitting
6. **`test_split_asr_text_empty`** — Edge case: empty ASR text
7. **`test_canonical_line_snap_basic`** — Snap produces LRC lines with canonical text
8. **`test_canonical_line_snap_below_threshold`** — Low-scoring segments keep ASR text
9. **`test_canonical_line_snap_preserves_repeats`** — Chorus repeated multiple times in output
10. **`test_mode2_fallback_to_llm_on_poor_snap`** — Snap coverage < 70% → LLM alignment used
11. **`test_mode2_forced_aligner_refinement`** — ForcedAligner runs after snap
12. **`test_mode2_forced_aligner_skipped_warning`** — ForcedAligner skipped → warning logged
13. **`test_mode2_vocals_stem_preferred`** — Uses vocals stem when available
14. **`test_mode2_falls_back_to_full_mix`** — No vocals stem → uses audio_path

### New test file: `services/analysis/tests/services/test_canonical_snap.py`

Unit tests for the canonical snap service module:

1. **`test_detect_chinese_script_simplified`** — Returns "zh-hans"
2. **`test_detect_chinese_script_traditional`** — Returns "zh-hant"
3. **`test_is_filler`** — 嗯, 啊, 呃 are filler; regular text is not
4. **`test_score_exact_match`** — Score = 1.0
5. **`test_score_no_match`** — Score near 0
6. **`test_score_partial_match`** — Partial match between 0 and 1
7. **`test_canonical_snap_dp_algo`** — DP algorithm produces correct output
8. **`test_canonical_snap_greedy_algo`** — Greedy algorithm produces correct output
9. **`test_canonical_snap_empty_input`** — Empty phrases/lyrics → empty output
10. **`test_canonical_snap_filler_handling`** — Filler segments are skipped

### New test file: `services/analysis/tests/services/test_onnx_asr_pipeline.py`

Tests for the ONNX pipeline wrapper (mostly smoke tests since real inference needs the model):

1. **`test_pipeline_init_missing_model_dir`** — Raises error when model dir doesn't exist
2. **`test_split_long_audio`** — `find_silence_split_points` returns boundaries for long audio
3. **`test_split_short_audio`** — No split points for short audio

## Cache Strategy

### ONNX ASR Transcription Caching

Follow the POC's cache pattern (from `specs/cache_raw_asr_output_qwen3_local.md`):

- Cache the raw ASR output dict (`{text, language, timing}`) — not the post-processed phrases
- Cache key includes all ASR-affecting parameters: `asr_mode`, `language`, `use_vocals`, `chunk_sec`
- Cache directory: `settings.CACHE_DIR / "qwen3-asr-onnx"` (inside Docker, maps to `analysis-cache` volume)
- Cache version: `3` (incrementing from POC's `2` to distinguish production schema)
- Phrase extraction (`_split_asr_text_to_phrases`) runs on every access (from fresh or cached raw), so tuning the punctuation set doesn't require re-running the model

This caching lives in `lrc.py` (the `_run_qwen3_asr_onnx` function), similar to how Whisper caching works with `cached_phrases`.

## Vocals Stem Handling (Mode 2)

Mode 2 prefers the vocals stem for cleaner ASR input (same as Whisper path):

1. If `vocals_stem_url` is provided → download from R2 to a temp file → transcribe
2. If `options.use_vocals_stem` is True but no URL → attempt to find vocals stem in cache
3. If no vocals stem available → fall back to `audio_path` (full mix)
4. Log which audio source was used

This mirrors the existing Whisper path's vocals stem handling.

## Mode 3 Stub

Mode 3 (Qwen3-ASR via API) is a future feature. For this spec:

- `generate_lrc()` raises `NotImplementedError("Qwen3-ASR API mode (mode 3) is not yet implemented.")` when `asr_mode == 3`
- Config fields for mode 3 are NOT added yet (they'll be in a separate spec)
- The existing high-level plan at `specs/transcription_via_Qwen3-ASR-Flash_highlevel_plan.md` remains the reference for mode 3 implementation

## Interaction with Existing Features

| Feature | Mode 1 (Whisper) | Mode 2 (Qwen3-ASR ONNX) |
|---------|-------------------|--------------------------|
| YouTube transcript primary path | Yes | No (skipped) |
| Whisper transcription | Yes | No |
| LLM alignment | Yes (always) | Fallback only (when snap coverage < 70%) |
| Canonical-line snap | No | Yes (primary text correction) |
| Qwen3-ForcedAligner refinement | Optional | **Strongly recommended** (critical for timestamp quality) |
| Context biasing (lyrics prompt) | Yes (Whisper initial_prompt) | No (ONNX model doesn't support it) |
| Vocals stem preference | Yes | Yes |
| `cached_phrases` | Yes | N/A (ONNX has its own cache) |
| `force` / `force_whisper` | Yes | `force` skips LRC cache; ONNX cache bypassed via separate flag |

## Migration / Rollout

1. Deploy analysis service with mode 1 (default) — no behavior change
2. Download ONNX model to host: `scripts/download_qwen3_asr_onnx_model.sh`
3. Set `SOW_QWEN3_ASR_ONNX_MODEL_ROOT` in `.env`
4. Test on a single song with `SOW_LYRICS_ASR_MODE=2`
5. Compare LRC output quality vs mode 1
6. Once validated, switch default to mode 2 (or leave as opt-in)

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| ONNX model produces poor Chinese text on worship songs | Low (POC validated) | Fallback to mode 1 via env var |
| Estimated timestamps (proportional splitting) are too inaccurate | Medium | Qwen3-ForcedAligner refinement fixes timestamps; without it, log a warning |
| Canonical snap misses lines (coverage < 70%) | Medium | Fall back to LLM alignment using ONNX ASR phrases |
| ONNX pipeline too slow for long songs | Low (3x realtime on CPU) | VAD-based chunking already in Daumee pipeline; configurable chunk size |
| `rapidfuzz`/`zhconv`/`pypinyin` not available in Docker | Low (pure Python) | Add to service extra in pyproject.toml |
| ONNX model dir not mounted → startup failure | Medium | Validate model dir exists in `_run_qwen3_asr_onnx`, raise clear error with setup instructions |

## Out of Scope

- Mode 3 (Qwen3-ASR via cloud API) implementation
- Replacing or removing the Whisper path (mode 1 stays indefinitely)
- Modifying the Qwen3-ForcedAligner service
- Changes to admin CLI or user app
- Retiring the YouTube transcript path
- GPU acceleration for ONNX (CPU-only for now; `CUDAExecutionProvider` can be added later)
- 1.7B ONNX model support
- Context biasing for ONNX mode
