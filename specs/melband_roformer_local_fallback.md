# MelBand Roformer for Local Stem Separation Fallback

## Context

With MVSEP Cloud API as the default stem separation backend (see `specs/mvsep_api_stem_separation_v2.md`), the local audio-separator pipeline is now a **fallback only** — used when MVSEP is unavailable, disabled, or fails per-job. The local fallback currently uses BS-Roformer (~370MB model, ~1.5GB runtime memory), which is heavier than necessary for a fallback path. Switching to MelBand Roformer reduces local fallback memory by ~60% with a modest SDR trade-off that is acceptable for a fallback role.

## Motivation

| Metric | BS-Roformer (current) | MelBand Roformer (proposed) | Savings |
|---|---|---|---|
| Startup memory | ~1.5 GB (eager validation) | ~0 MB (lazy, no validation) | ~100% |
| Per-call runtime peak | ~1.5 GB | ~600 MB | ~60% |
| Model file on disk | ~370 MB | ~150 MB | ~60% |
| SDR (vocals) | 12.97 | 11.44 | -1.53 |
| Startup time | ~30-60s (model validation) | ~0s (deferred) | ~100% |

The 1.53 SDR gap is acceptable because:
1. MVSEP cloud (using BS-Roformer sep_type=40, add_opt1=81) handles the vast majority of jobs
2. Local fallback is only triggered on MVSEP failures (rare)
3. MelBand Roformer is `audio-separator`'s own default model (v0.44.1+), indicating the library author's recommendation for the quality/memory trade-off

## Design Decisions

- **MelBand Roformer `ep_3005`** chosen over Kimberley Jensen variant (`vocals_mel_band_roformer.ckpt`) because `ep_3005` outputs stems named "vocals" and "instrumental" — matching existing filename-matching logic with zero code changes. The Kim variant outputs "other" instead of "instrumental", requiring logic changes.
- **Rename `SOW_BS_ROFORMER_MODEL` → `SOW_VOCAL_SEPARATION_MODEL`** — the config key is now model-agnostic since it no longer defaults to BS-Roformer. Existing deployments must update their `.env` if they override the default.
- **Lazy initialization** — startup validation (`AudioSeparatorWrapper.initialize()`) is removed from the startup sequence. Instead, models are validated and loaded on first use via `_ensure_ready()`. Since MVSEP is the primary path and local is only a fallback, there's no reason to spend ~30-60s validating models at startup that may never be used. The first local fallback job pays a one-time validation penalty.
- **MVSEP path unchanged** — MVSEP continues using BS-Roformer (sep_type=40, add_opt1=81). Only the local fallback model changes.
- **Job stage rename** — `stage1_bs_roformer` → `stage1_vocal_separation` (model-agnostic naming).

## Files to Modify

| # | File | Change Summary |
|---|---|---|
| 1 | `services/analysis/src/sow_analysis/config.py` | Rename `SOW_BS_ROFORMER_MODEL` → `SOW_VOCAL_SEPARATION_MODEL`, change default to `model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt` |
| 2 | `services/analysis/src/sow_analysis/workers/separator_wrapper.py` | Rename `bs_roformer_model` param/attr → `vocal_model`, update all references and log messages |
| 3 | `services/analysis/src/sow_analysis/main.py` | Update constructor arg from `bs_roformer_model=cfg.SOW_BS_ROFORMER_MODEL` → `vocal_model=cfg.SOW_VOCAL_SEPARATION_MODEL` |
| 4 | `services/analysis/src/sow_analysis/workers/stem_separation.py` | Update comments and job stage name `stage1_bs_roformer` → `stage1_vocal_separation` |
| 5 | `services/analysis/src/sow_analysis/workers/queue.py` | Update comments referencing BS-Roformer |
| 6 | `services/analysis/docker-compose.yml` | Rename env var, update default |
| 7 | `services/analysis/start-dev.sh` | Change `BS_MODEL` → `VOCAL_MODEL`, update download to use MelBand Roformer filename |
| 8 | `services/analysis/.env.example` | Update model section with new env var name, default, description, and inline download script |
| 9 | `services/analysis/README.md` | Update env var names, model download scripts, job stage names |
| 10 | `services/analysis/DEVELOPER.md` | Update model loading log expectations and model references |

### Files NOT Modified

- `services/analysis/src/sow_analysis/services/mvsep_client.py` — MVSEP continues using BS-Roformer (sep_type=40, add_opt1=81); no change
- `services/analysis/tests/` — Mock-based tests don't reference the model filename directly; no changes needed
- `services/analysis/.env` — User's local config; migration documented below
- `src/stream_of_worship/admin/commands/audio.py` — Admin CLI is architecturally separate per AGENTS.md; documented as follow-up

## Implementation Steps

### Step 1: Update `config.py`

```python
# Before (line 44):
SOW_BS_ROFORMER_MODEL: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# After:
SOW_VOCAL_SEPARATION_MODEL: str = "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
```

### Step 2: Update `separator_wrapper.py`

Rename parameter and attribute, add lazy initialization:

| Line | Before | After |
|---|---|---|
| 28 | `bs_roformer_model: Name of BS-Roformer model file` | `vocal_model: Name of vocal separation model file` |
| 36 | `bs_roformer_model: Optional[str] = None,` | `vocal_model: Optional[str] = None,` |
| 41 | `self.bs_roformer_model = bs_roformer_model or settings.SOW_BS_ROFORMER_MODEL` | `self.vocal_model = vocal_model or settings.SOW_VOCAL_SEPARATION_MODEL` |
| 59 | `"Validating BS-Roformer model: ..."` | `"Validating vocal separation model: ..."` |
| 65 | `bs_sep.load_model(model_filename=self.bs_roformer_model)` | `bs_sep.load_model(model_filename=self.vocal_model)` |
| 67 | `"BS-Roformer model validated: ..."` | `"Vocal separation model validated: ..."` |
| 94 | `"Stage 1: Extract vocals and instrumental using BS-Roformer"` | `"Stage 1: Extract vocals and instrumental"` |
| 131 | `sep.load_model(model_filename=self.bs_roformer_model)` | `sep.load_model(model_filename=self.vocal_model)` |

**Add lazy initialization** — replace the `if not self._ready: raise RuntimeError(...)` check in `separate_stems()` and `remove_reverb()` with a call to `_ensure_ready()`:

```python
async def _ensure_ready(self) -> None:
    """Lazily initialize models on first use. Thread-safe via lock."""
    if self._ready:
        return
    async with self._init_lock:
        if self._ready:
            return
        await self.initialize()
        if not self._ready:
            raise RuntimeError("Model validation failed. Check that model files exist.")
```

Add `_init_lock` to `__init__`:

```python
self._init_lock = asyncio.Lock()
```

Then in `separate_stems()` and `remove_reverb()`, replace:

```python
if not self._ready:
    raise RuntimeError("Models not ready. Call initialize() first.")
```

with:

```python
await self._ensure_ready()
```

The `initialize()` method and `is_ready` property remain unchanged for backward compatibility.

### Step 3: Update `main.py`

Remove the eager background initialization task. The wrapper is set on the queue immediately (not ready yet), and will self-initialize on first use:

```python
# Before (lines 42-63): _init_separator_wrapper background task
# Before (lines 113-116): bg_separator_task = asyncio.create_task(...)

# After: Instantiate and set immediately, no background task needed
if AudioSeparatorWrapper is not None:
    separator_wrapper = AudioSeparatorWrapper(
        model_dir=settings.SOW_AUDIO_SEPARATOR_MODEL_DIR,
        vocal_model=settings.SOW_VOCAL_SEPARATION_MODEL,
        dereverb_model=settings.SOW_DEREVERB_MODEL,
        output_format="FLAC",
    )
    job_queue.set_separator_wrapper(separator_wrapper)
    logger.info("Audio separator wrapper created (lazy init on first use)")
else:
    logger.warning("AudioSeparatorWrapper not available (audio-separator not installed)")
```

Remove the `_init_separator_wrapper` function entirely and the `bg_separator_task` variable/cancellation in the shutdown block.

### Step 4: Update `stem_separation.py`

| Line | Before | After |
|---|---|---|
| 4 | `"Uses pre-loaded AudioSeparatorWrapper for BS-Roformer + UVR-De-Echo processing."` | `"Uses pre-loaded AudioSeparatorWrapper for vocal separation + UVR-De-Echo processing."` |
| 161 | `"Downloads audio from R2, runs two-stage separation (BS-Roformer + UVR-De-Echo),"` | `"Downloads audio from R2, runs two-stage separation (vocal model + UVR-De-Echo),"` |
| 354 | `"produced by the two-stage BS-Roformer + UVR-De-Echo pipeline"` | `"produced by the two-stage vocal separation + UVR-De-Echo pipeline"` |

Also update the job stage name set in `_separate_with_mvsep_fallback`:

```python
# Before (in the fallback_local branch):
_set_job_stage(job, "fallback_local")
# ... then later in process_stem_separation the stage is set to "stage1_bs_roformer"

# After:
# In process_stem_separation, change:
job.stage = "stage1_bs_roformer"
# To:
job.stage = "stage1_vocal_separation"
```

### Step 5: Update `queue.py`

```python
# Before (line 110):
# Stem separation uses lock for serialization (high memory/CPU with BS-Roformer)

# After:
# Stem separation uses lock for serialization (high memory/CPU with vocal model)
```

**Remove separator readiness wait** — since the wrapper now self-initializes lazily via `_ensure_ready()`, the queue no longer needs to block on `_separator_ready.wait()`. Remove:

- `self._separator_ready` asyncio.Event
- `self._separator_init_failed` flag
- The `set_separator_wrapper()` method's event-set logic (simplify to just setting `self._separator_wrapper`)
- The `notify_separator_init_failed()` method
- The 300-second wait loop in `_process_stem_separation_job()` (lines ~977-1026) that blocks until the separator is ready

The stem separation job can now proceed immediately — if the wrapper isn't ready yet, `_ensure_ready()` will handle initialization inline on the first call.

### Step 6: Update `docker-compose.yml`

```yaml
# Before (line 16):
SOW_BS_ROFORMER_MODEL: ${SOW_BS_ROFORMER_MODEL:-model_bs_roformer_ep_317_sdr_12.9755.ckpt}

# After:
SOW_VOCAL_SEPARATION_MODEL: ${SOW_VOCAL_SEPARATION_MODEL:-model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt}
```

### Step 7: Update `start-dev.sh`

Replace all BS_MODEL references with VOCAL_MODEL:

| Line | Before | After |
|---|---|---|
| 30 | `BS_MODEL="model_bs_roformer_ep_317_sdr_12.9755.ckpt"` | `VOCAL_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"` |
| 33 | `BS_MODEL_PATH="$MODEL_DIR/$BS_MODEL"` | `VOCAL_MODEL_PATH="$MODEL_DIR/$VOCAL_MODEL"` |
| 38 | `if [[ ! -f "$BS_MODEL_PATH" ]]` | `if [[ ! -f "$VOCAL_MODEL_PATH" ]]` |
| 39 | `echo -e "  ${YELLOW}Missing: $BS_MODEL${NC}"` | `echo -e "  ${YELLOW}Missing: $VOCAL_MODEL${NC}"` |
| 42 | `echo -e "  ${GREEN}Found: $BS_MODEL${NC}"` | `echo -e "  ${GREEN}Found: $VOCAL_MODEL${NC}"` |
| 67 | `print("Downloading BS-Roformer model...")` | `print("Downloading MelBand Roformer model...")` |
| 69 | `sep1.load_model(model_filename="$BS_MODEL")` | `sep1.load_model(model_filename="$VOCAL_MODEL")` |
| 70 | `print(f"  ✓ BS-Roformer downloaded successfully")` | `print(f"  ✓ MelBand Roformer downloaded successfully")` |
| 72 | `print(f"  ✗ Failed to download BS-Roformer: {e}")` | `print(f"  ✗ Failed to download MelBand Roformer: {e}")` |

### Step 8: Update `.env.example`

```bash
# ========================================
# Audio-Separator Model Configuration (Required for stem separation)
# ========================================

SOW_AUDIO_SEPARATOR_MODEL_ROOT="/home/user/.cache/audio-separator"
# Host path to pre-downloaded audio-separator models
# Models are downloaded once and bind-mounted into the container
#
# To download models, run this Python script on your host:
#
#   from audio_separator.separator import Separator
#   import os
#
#   model_dir = "/home/user/.cache/audio-separator"
#   os.makedirs(model_dir, exist_ok=True)
#
#   print("Downloading MelBand Roformer model...")
#   sep1 = Separator(output_dir=model_dir, output_format="FLAC")
#   sep1.load_model(model_filename="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt")
#
#   print("Downloading UVR-De-Echo model...")
#   sep2 = Separator(output_dir=model_dir, output_format="FLAC")
#   sep2.load_model(model_filename="UVR-De-Echo-Normal.pth")
#
#   print(f"Models downloaded to: {model_dir}")

# ========================================
# Processing Configuration (Optional)
# ========================================

SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS=1
# Maximum concurrent stem separation jobs (default: 1)
# Vocal separation is memory/CPU intensive, so serialized by default

# ========================================
# Stem Separation Model Selection (Optional)
# ========================================

SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
# Vocal separation model filename for Stage 1
# Default: model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt (MelBand Roformer, SDR 11.44)
# Alternative: model_bs_roformer_ep_317_sdr_12.9755.ckpt (BS-Roformer, SDR 12.97, ~2.5x more memory)
# Note: MVSEP cloud uses its own model selection (see MVSEP config section)

SOW_DEREVERB_MODEL="UVR-De-Echo-Normal.pth"
# UVR-De-Echo model filename for reverb removal (Stage 2)
# Options:
#   - UVR-De-Echo-Normal.pth (default, balanced)
#   - UVR-De-Echo-Aggressive.pth (stronger echo removal)
#   - UVR-DeEcho-DeReverb.pth (combined de-echo and de-reverb)
```

### Step 9: Update `README.md`

Key changes:
- Line 68: `SOW_BS_ROFORMER_MODEL="..."` → `SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"`
- Lines 160-162: `"Stage 1 (BS-Roformer)"` → `"Stage 1 (Vocal Separation)"`
- Line 181: `"stage1_bs_roformer"` → `"stage1_vocal_separation"` (in Job Stages table)
- Lines 223-232: Update model download script (replace BS-Roformer with MelBand Roformer)
- Lines 244-251: Update inline Python download script

### Step 10: Update `DEVELOPER.md`

Key changes:
- Line 51: `"Check for missing models (BS-Roformer and UVR-De-Echo)"` → `"(MelBand Roformer and UVR-De-Echo)"`
- Lines 170-172: Update expected log messages:
  ```
  Loading vocal separation model: model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt
  Loading UVR-De-Echo model: UVR-De-Echo-Normal.pth
  ```

## Model Caching & Docker Volume Mount

The Docker setup mounts the host model directory **read-only**:

```yaml
# docker-compose.yml
volumes:
  - ${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro
```

The container **cannot** auto-download models — they must be pre-downloaded on the host. The `start-dev.sh` script handles this by downloading missing models to `$SOW_AUDIO_SEPARATOR_MODEL_ROOT` (default: `$HOME/.cache/audio-separator`) before starting Docker.

After this change, the host directory will need to contain:
- `model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt` (~150 MB) — **new**
- `UVR-De-Echo-Normal.pth` (~60 MB) — already present

The old `model_bs_roformer_ep_317_sdr_12.9755.ckpt` (~370 MB) can be safely deleted from the host directory to free space, or left in place (it will not be loaded by the service).

## Job Stage Name Changes

Local-fallback job stages are renamed for model-agnostic naming:

| Before | After | Context |
|---|---|---|
| `stage1_bs_roformer` | `stage1_vocal_separation` | Running local vocal separation (Stage 1) |

MVSEP-prefixed stages are unchanged:
- `mvsep_stage1_submitting`, `mvsep_stage1_polling`, etc. — unchanged
- `fallback_local` — unchanged
- `fallback_local_stage2` — unchanged

Full progression with local fallback:
```
starting → checking_cache → downloading → stage1_vocal_separation → stage2_dereverb → renaming_outputs → caching → uploading → complete
```

## Code Logic Changes

### Filename Matching (No Change Needed)

MelBand Roformer `ep_3005` outputs stems named **"vocals"** and **"instrumental"** — matching the existing filename-matching logic in `separator_wrapper.py` (lines 144-148). No string-matching changes required.

### Lazy Initialization (New)

The `AudioSeparatorWrapper` gains `_ensure_ready()` for lazy initialization:
- Replaces the `if not self._ready: raise RuntimeError(...)` guard in `separate_stems()` and `remove_reverb()`
- Uses double-check locking via `asyncio.Lock` to prevent concurrent initialization
- The `initialize()` method is still called but deferred to first use
- The `is_ready` property and `initialize()` method remain public for backward compatibility

## Migration for Existing Deployments

1. **Download new model on host**: Run `start-dev.sh --no-start` to download MelBand Roformer to `$SOW_AUDIO_SEPARATOR_MODEL_ROOT`
2. **Update `.env`**: If `SOW_BS_ROFORMER_MODEL` was explicitly set, rename to `SOW_VOCAL_SEPARATION_MODEL`:
   ```bash
   # Before:
   # SOW_BS_ROFORMER_MODEL="model_bs_roformer_ep_317_sdr_12.9755.ckpt"
   
   # After:
   # SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
   ```
   If it was **not** explicitly set (commented out), no `.env` change needed — the new default takes effect.
3. **Optional cleanup**: Delete old model from host:
   ```bash
   rm ~/.cache/audio-separator/model_bs_roformer_ep_317_sdr_12.9755.ckpt
   ```
4. **Rebuild/restart Docker**: `docker compose up -d --build`

## Verification

1. **Model download**: `start-dev.sh --no-start` — verify MelBand Roformer downloads to `$MODEL_DIR`
2. **Lazy startup**: Start service, verify logs show **no** model validation messages (no "Validating vocal separation model..."). Only `Audio separator wrapper created (lazy init on first use)`.
3. **Local fallback (MVSEP disabled)**: Submit stem separation job with no `SOW_MVSEP_API_KEY`, verify:
   - First job shows lazy init logs: `"Validating vocal separation model..."` → `"Vocal separation model validated: ..."`
   - 3 FLAC files produced (`vocals_clean.flac`, `instrumental_clean.flac`, `vocals_reverb.flac`)
   - Subsequent jobs skip init (already ready)
4. **MVSEP primary path**: Submit job with valid `SOW_MVSEP_API_KEY`, verify:
   - MVSEP still uses BS-Roformer (sep_type=40, add_opt1=81)
   - Local models are **never** initialized (MVSEP handled everything)
5. **Cross-backend handoff**: Force MVSEP Stage 2 failure (e.g., invalid `SOW_MVSEP_DEREVERB_MODEL`), verify MVSEP Stage 1 vocals are passed to local `remove_reverb()`
6. **Full fallback**: Set invalid `SOW_MVSEP_API_KEY`, verify full local fallback with MelBand Roformer
7. **Existing tests**: `PYTHONPATH=src pytest tests/test_mvsep_fallback.py tests/test_mvsep_client.py -v`

## Follow-up (Out of Scope)

- **Admin CLI** (`src/stream_of_worship/admin/commands/audio.py` line 1361): Still hardcodes BS-Roformer as default. Separate change since Admin CLI is architecturally separate per AGENTS.md.
- **Configurable model via MVSEP settings**: Future option to let `SOW_MVSEP_VOCAL_MODEL` control both cloud and local model selection. Out of scope for this change.
