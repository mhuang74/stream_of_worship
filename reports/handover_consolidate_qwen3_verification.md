# Handover: Consolidate Qwen3 ForcedAligner — Remaining Verification

**Date:** 2026-06-16  
**Branch:** `trigger_alignment_via_admin`  
**Commit:** `7f67d64` (pushed to origin)  
**Spec:** `specs/consolidate-qwen3-into-analysis-service-v3.md`  
**Impl Summary:** `reports/consolidate_qwen3_into_analysis_service_impl_summary.md`

---

## What Was Done

All 5 implementation phases are complete, committed, and pushed. See the impl summary for full details. In short:

- Qwen3 ForcedAligner merged from separate `services/qwen3/` Docker microservice into `services/analysis/` as an in-process worker
- New `FORCED_ALIGNMENT` job type with `ForcedAlignerWrapper` (lazy init, double-check locking, no internal semaphore)
- New `POST /api/v1/jobs/forced-alignment` endpoint
- New `sow-admin audio align-lrc` CLI command
- `services/qwen3/` directory deleted entirely
- All tests pass (53 new tests: 34 map_segments_to_lines + 19 forced_alignment)
- Documentation updated

---

## What Remains

The migration checklist from the spec has 3 verification items that require **running Docker services + populated DB + R2 credentials**:

### 1. Verify `docker compose up analysis` works without qwen3 service

```bash
cd services/analysis
docker compose up analysis
# Should start without errors. No qwen3 service needed.
# Check logs for "Forced Aligner model_path" in startup config.
```

**What to check:**
- Container starts and health check passes
- Startup logs show `SOW_FORCED_ALIGNER_MODEL_PATH` and `SOW_FORCED_ALIGNER_DEVICE` config values
- No errors about missing qwen3 service or `SOW_QWEN3_BASE_URL`
- The model is NOT loaded at startup (lazy — should only load on first forced alignment job)

**If it fails:**
- Check `.env` has `SOW_FORCED_ALIGNER_MODEL_PATH` and `SOW_FORCED_ALIGNER_DEVICE` set
- Check `docker-compose.yml` has the model volume mount if using a local model cache
- Check that no code still references `SOW_QWEN3_BASE_URL` or `SOW_QWEN3_API_KEY`

### 2. Verify `sow-admin audio align-lrc <song_id> --wait` works end-to-end

```bash
# First, ensure analysis service is running
cd services/analysis && docker compose up -d

# Then run the CLI command
PYTHONPATH=src uv run --python 3.11 --extra admin sow-admin audio align-lrc <song_id> --wait --lang zh
```

**What to check:**
- CLI looks up recording (r2_audio_url, content_hash) and song (lyrics_raw) from DB
- Submits forced alignment job to analysis service
- Analysis service downloads audio from R2
- If `--use-vocals-stem` (default), resolves vocals stem (may auto-trigger stem separation)
- Validates audio duration ≤ 300s
- Lazy-loads `ForcedAlignerWrapper` on first job (check logs for model loading)
- Runs alignment, maps segments to lines, generates LRC
- Service-level backup: if LRC already exists in R2, copies to backup key before overwriting
- Uploads LRC to R2
- CLI polls and reports completion with LRC URL and line count

**If it fails:**
- Check `ForcedAlignerWrapper` init: model path must be accessible inside Docker container
- If using HuggingFace model ID, ensure container has internet access on first run to download
- If using local model cache, ensure volume mount is correct in `docker-compose.yml`
- Check GPU/CUDA availability if `SOW_FORCED_ALIGNER_DEVICE=auto`
- For stem resolution issues, try `--no-vocals-stem` to skip stem separation

### 3. Verify `sow-admin audio lrc <song_id>` still works unchanged

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin sow-admin audio lrc <song_id> --wait
```

**What to check:**
- LRC job submits and completes as before
- No regressions from the `_resolve_lrc_transcription_audio` refactor (it's now a thin wrapper over `_resolve_transcription_audio`)
- Deprecated `--no-qwen3` flag is gone (should error if used)

---

## Key Files for Debugging

| File | Purpose |
|------|---------|
| `services/analysis/src/sow_analysis/workers/forced_aligner.py` | `ForcedAlignerWrapper` — lazy init, align(), cleanup() |
| `services/analysis/src/sow_analysis/workers/forced_alignment.py` | Utility functions: map_segments_to_lines, validate_audio_duration |
| `services/analysis/src/sow_analysis/workers/queue.py` | `_process_forced_alignment_job()`, `_resolve_transcription_audio()`, dispatch wiring |
| `services/analysis/src/sow_analysis/models.py` | `ForcedAlignmentJobRequest`, `ForcedAlignmentOptions`, `JobType.FORCED_ALIGNMENT` |
| `services/analysis/src/sow_analysis/config.py` | `SOW_FORCED_ALIGNER_MODEL_PATH`, `SOW_FORCED_ALIGNER_DEVICE` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | `POST /api/v1/jobs/forced-alignment` endpoint |
| `services/analysis/src/sow_analysis/storage/r2.py` | `copy_object()` for LRC backup |
| `services/analysis/docker-compose.yml` | Docker config (env vars, volume mounts) |
| `services/analysis/.env.example` | Required env vars |
| `src/stream_of_worship/admin/commands/audio.py` | `align-lrc` CLI command |
| `src/stream_of_worship/admin/services/analysis.py` | `submit_forced_alignment()` client method |

---

## Known Issues

1. **Pre-existing test failures** in `test_youtube_transcript.py` (4 tests) — `MockSettings` missing `SOW_YOUTUBE_PROXY` attribute. Unrelated to this change.

2. **`validate_audio_duration` uses lazy imports** — `soundfile` and `librosa` are imported inside the function body (for optional dependency handling). Tests must mock at the top-level module (`patch("soundfile.info")`) rather than at the usage site (`patch("sow_analysis.workers.forced_alignment.soundfile")`).

3. **`_process_forced_alignment_job` test hangs** when `use_vocals_stem=True` without mocking `_resolve_transcription_audio`, because it auto-submits a child `STEM_SEPARATION` job and waits in a polling loop. Tests must mock this method.

---

## Semaphore Interaction Notes

With `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1` (default), a forced alignment job acquires the global local-model semaphore **only during the `align()` call** (~seconds to minutes). Download, upload, stem resolution, and LRC formatting run without holding the semaphore. This prevents deadlock when stem separation is auto-triggered as a child job.

If you observe jobs stuck in QUEUED state, check:
- `_log_queue_state()` output in logs — shows active/queued counts per job type
- Whether the semaphore is held by another long-running job (Whisper, Demucs)
- Whether a stem separation child job is waiting for the same semaphore (shouldn't happen since forced alignment releases it before waiting)

---

## After Verification

Once all 3 verification items pass:
1. Update the migration checklist in `specs/consolidate-qwen3-into-analysis-service-v3.md` (bottom of file)
2. Update `reports/current_impl_status.md` if appropriate
3. Consider creating a PR from `trigger_alignment_via_admin` → `main`
