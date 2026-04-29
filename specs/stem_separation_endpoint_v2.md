# Spec Review & Revisions: stem_separation_endpoint.md

## Context

Reviewing `specs/stem_separation_endpoint.md`, which adds a new `/jobs/stem-separation` endpoint to the analysis service that runs BS-Roformer + UVR-De-Echo, and auto-triggers from the LRC worker when a clean vocal stem is missing in R2.

This document records the design flaws, data-flow inconsistencies, and operational issues found during review, plus the user-confirmed revisions to fold into the spec before implementation.

---

## Findings & resolutions

### 1. Naming conventions are fragmented (must standardize)

There are **7 distinct names** across the codebase for "the de-echoed vocal stem":

| # | Name | Where | Format |
|---|------|-------|--------|
| 1 | `clean_vocals.flac` | `docs/manually-fix-lrc.md`, `poc/score_lrc_quality.py:268-272`, `poc/utils.py:158-161`, `poc/experiment_lrc_signals.py:151`, `specs/experiment_with_lrc_eval_approaches.md` | FLAC |
| 2 | `vocals_clean.wav` | **`src/stream_of_worship/admin/commands/audio.py:1427-1563`**, `src/stream_of_worship/admin/services/r2.py:95,100` (admin **already produces this** today) | WAV |
| 3 | `vocals_clean.flac` | new spec under review | FLAC |
| 4 | `dry_vocals.flac` | `specs/transcription_via_Qwen3-ASR-Flash_highlevel_plan.md` | FLAC |
| 5 | `<song>_clean_vocals.flac` | `reports/handover_canonical_matching.md`, several specs | FLAC |
| 6 | `vocal.wav` (singular) | `docs/manually-fix-lrc.md` (incorrect — actual file is `vocals.wav`) | WAV |
| 7 | `dry_vocals_file` (variable) | `poc/gen_clean_vocal_stem.py:178` | FLAC |

**Resolution:** Adopt `vocals_clean.flac` and `instrumental_clean.flac` as canonical R2 names (matching the spec). The LRC worker's lookup chain falls back through `.flac` → `.wav` → legacy `vocals.wav` to absorb existing files.

### 2. SQLite schema blocks new JobType (spec under-claims)

Spec line 192 says "JobStore likely already generic." It is not:

- `services/analysis/src/sow_analysis/storage/db.py:67` has `CHECK (type IN ('analyze', 'lrc'))`.
- `_row_to_job` at `db.py:178-179` defaults the `else` branch to `LrcJobRequest` — silently misparses unknown types.

**Resolution (user choice): wipe `jobs.db` on first run** of the new image. Acceptable because the table only holds in-flight + recent terminal jobs (≤7-day retention). Easier than an in-place table-rebuild migration. Combined with explicit per-type branching in `_row_to_job`.

### 3. Concurrency model: spec misnames the LRC primitive

Spec implies LRC uses a "serial lock". Actual primitives in `workers/queue.py:89-92`:

```python
self._analysis_lock = asyncio.Lock()                        # serial, 1 ANALYZE
self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc) # default 2
```

If the LRC worker calls `_run_stem_separation_inline` while still holding its `_lrc_semaphore` slot, two concurrent LRC jobs both needing fresh stems collapse effective LRC concurrency to 1.

**Resolution (user choice): submit a child STEM_SEPARATION job and poll its status without holding the LRC slot.** The LRC worker releases its semaphore slot, awaits child job completion via store/queue polling, then re-acquires the slot to continue transcription. Reuses standard job machinery, no inline helper.

### 4. Qwen3 forced alignment doesn't see the vocals stem

`workers/lrc.py:551` hardcodes the alignment URL to `s3://{bucket}/{hash_prefix}/audio.mp3` — the full mix, not the stem. So today's "clean vocals" only benefit Whisper.

**Resolution (user choice): also pass the clean-vocals R2 URL to Qwen3.** Modify `_qwen3_refine` to send `s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac` when present (fall back to `audio.mp3`). May require a small change in `services/qwen3` to accept whatever format the URL points at; verify the qwen3 server fetches by URL with no extension assumption.

### 5. Admin CLI duplicates the algorithm

`src/stream_of_worship/admin/commands/audio.py:1358-1620` (`vocal_clean()`) already runs the same BS-Roformer + UVR-De-Echo pipeline locally and uploads `vocals_clean.wav` to R2 via the admin's `R2Client.upload_stem` (which hardcodes `.wav`).

**Resolution (user choice): deprecate `sow_admin audio vocal` outright** as part of this spec. The LRC worker's auto-trigger plus the new `/jobs/stem-separation` endpoint cover both the auto and manual flows. A future `regenerate-clean-stems` admin subcommand can be filed as a follow-up bd if/when needed.

### 6. Model caching pattern (mirror qwen3)

The analysis container today downloads-on-first-use into the `analysis-cache` named volume (only faster-whisper is wired correctly; Demucs falls into `~/.cache/torch/hub` and is not persisted). qwen3 uses host-bind-mount `:ro` + startup pre-warm.

**Resolution (user choice): mirror qwen3 exactly.** Pre-download `audio-separator` models on the host, bind-mount `:ro` into the analysis container, load the `Separator` once at FastAPI `lifespan` startup, keep resident. Specifics:

- New env vars: `SOW_AUDIO_SEPARATOR_MODEL_ROOT` (host path), pointed at the host's `audio-separator` model cache.
- New `:ro` bind mount in `services/analysis/docker-compose.yml`: `${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro`.
- New container env: `SOW_AUDIO_SEPARATOR_MODEL_DIR=/models/audio-separator`.
- New wrapper class (mirroring `Qwen3AlignerWrapper`) constructed in `main.py` lifespan with both `BS-Roformer` and `UVR-De-Echo` `Separator` instances pre-loaded; injected into `JobQueue`.
- README addition: host-side `audio-separator` download instructions (e.g. `python -c "from audio_separator.separator import Separator; ..."` as a one-time host setup step), mirroring `services/qwen3/README.md:154-167`.

### 7. Other smaller items folded into the implementation

- `instrumental_clean.flac` is brand-new naming; user confirmed **upload both** (vocals + instrumental). No existing consumer, but Stage 1 instrumental is a free output of BS-Roformer.
- Hash-prefix length: cache uses `[:32]`, R2 uses `[:12]`. Spec proposal `/cache/stems_clean/{hash[:32]}/` matches the cache convention. Keep.
- Cache filename inside `/cache/stems_clean/` should match R2 naming (`vocals_clean.flac`, `instrumental_clean.flac`) — no local-vs-R2 divergence.
- Update `_row_to_job` to explicit per-type branching (no `else: # LRC`).
- Update `models.JobResult` with `vocals_clean_url`, `instrumental_clean_url`. Update `routes/jobs.py:job_to_response` to copy them.
- Update `Job.request` Union to include `StemSeparationJobRequest`.
- Update `admin/services/analysis.py` with `submit_stem_separation()` client method (no CLI command needed; only used for parity with `submit_lrc`/`submit_analysis`).
- Update `admin/services/r2.py:95-100` is moot since the admin command is being deprecated.
- POC ↔ worker code sharing: keep `poc/gen_clean_vocal_stem.py` untouched; the worker copies the algorithm. Accept divergence; the POC is a research artifact.

---

## Final implementation plan

### Files to modify / create

| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | Add `JobType.STEM_SEPARATION`; add `StemSeparationOptions`, `StemSeparationJobRequest`; extend `JobResult` with `vocals_clean_url`, `instrumental_clean_url`; extend `Job.request` Union |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` (new) | Two-stage worker; ports algorithm from `poc/gen_clean_vocal_stem.py:21-199`; uses pre-loaded `Separator` instances from a wrapper |
| `services/analysis/src/sow_analysis/workers/separator_wrapper.py` (new) | `AudioSeparatorWrapper` (mirroring `services/qwen3/.../aligner.py`) — holds two pre-loaded `Separator` instances; thread-pool executor for blocking calls |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `_stem_separation_lock`; add `_process_stem_separation_job`; add third dispatch branch in `_process_job_with_semaphore` (line 235); modify `_process_lrc_job` stem-lookup block (lines 559-571) to: (a) check for `vocals_clean.flac`, (b) when missing, submit a child STEM_SEPARATION job, release `_lrc_semaphore`, poll until complete, re-acquire; pass clean-vocals URL to `_qwen3_refine` |
| `services/analysis/src/sow_analysis/workers/lrc.py` | Modify `_qwen3_refine` (~line 548-551) to accept and forward a clean-vocals URL when available; fall back to `audio.mp3` |
| `services/analysis/src/sow_analysis/storage/r2.py` | Add `upload_clean_stems(hash_prefix, vocals_clean, instrumental_clean) -> tuple[str, str]` writing keys `{hash_prefix}/stems/vocals_clean.flac` and `{hash_prefix}/stems/instrumental_clean.flac` |
| `services/analysis/src/sow_analysis/storage/db.py` | Wipe `jobs.db` on startup if schema CHECK constraint mismatch detected (or unconditionally on first run with the new image); fix `_row_to_job` (lines 176-179) to explicit per-type branching including `STEM_SEPARATION` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/stem-separation`; update `job_to_response` to copy new URL fields |
| `services/analysis/src/sow_analysis/main.py` | Extend FastAPI `lifespan` (lines 26-66) to construct `AudioSeparatorWrapper` with both models pre-loaded; inject into `JobQueue` |
| `services/analysis/src/sow_analysis/config.py` | Add `SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: int = 1`; add `SOW_AUDIO_SEPARATOR_MODEL_DIR: Path = Path("/models/audio-separator")` |
| `services/analysis/Dockerfile` | Add `RUN mkdir -p /models/audio-separator` |
| `services/analysis/docker-compose.yml` | Add bind mount `${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro` to both `analysis` and `analysis-dev` services; add env passthrough for `SOW_AUDIO_SEPARATOR_MODEL_DIR` |
| `services/analysis/.env.example` | Add `SOW_AUDIO_SEPARATOR_MODEL_ROOT=...` with comment pointing at host download instructions |
| `services/analysis/README.md` | Add host-side model download section, mirroring `services/qwen3/README.md:154-167` |
| `services/analysis/pyproject.toml` | Add `audio-separator>=0.30.0`, `onnxruntime>=1.17.0` to `service` extra |
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_stem_separation(audio_url, content_hash, force=False, dereverb_model=None)`; extend `AnalysisResult` and `_parse_job_response` for new URL fields |
| `src/stream_of_worship/admin/commands/audio.py` | **Remove** `vocal_clean()` (lines 1358-1620) and the `vocal` Typer command; replace with a deprecation message pointing at the LRC auto-trigger or future `regenerate-clean-stems` |
| `src/stream_of_worship/admin/services/r2.py` | Remove `upload_stem()` (lines 92-107) since no caller after admin deprecation; verify no other consumers first |
| `tests/services/analysis/test_stem_separation_worker.py` (new) | Mock `Separator.separate()` returning sentinel paths; verify two-stage orchestration + output renaming + R2 upload |
| `tests/services/analysis/test_routes_jobs.py` | Extend with stem-separation submission test |
| `tests/services/analysis/test_queue_lrc_auto_trigger.py` (new) | Verify LRC worker submits child STEM_SEPARATION job, releases semaphore, polls, re-acquires |

### Concurrency model (final)

```python
# JobQueue.__init__
self._analysis_lock = asyncio.Lock()                                 # 1 ANALYZE
self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc)          # default 2
self._stem_separation_lock = asyncio.Lock()                          # 1 STEM_SEPARATION
```

LRC auto-trigger flow:
1. LRC worker holds `_lrc_semaphore` slot, downloads audio.
2. Stem lookup: check R2 for `vocals_clean.flac`.
3. If none found and `use_vocals_stem=True`:
   - Submit a child `STEM_SEPARATION` job via `JobQueue.submit()`.
   - **Release `_lrc_semaphore` slot.**
   - Poll the child job's status via `JobStore.get_job(child_id)` until terminal state (with timeout / cancellation propagation).
   - **Re-acquire `_lrc_semaphore` slot.**
4. Download `vocals_clean.flac`.
5. Continue with Whisper (path) + Qwen3 (URL of clean vocals).

### Data-flow contract

- R2 layout (canonical): `s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac`, `s3://{bucket}/{hash_prefix}/stems/instrumental_clean.flac` (both written by the new worker).
- R2 fallbacks (read-only legacy): `vocals_clean.wav` (admin-produced before deprecation), `vocals.wav` (Demucs).
- Local cache: `/cache/stems_clean/{content_hash[:32]}/vocals_clean.flac`, `instrumental_clean.flac` (FLAC-only, matching R2 names).
- Models: pre-downloaded on host, bind-mounted `:ro` at `/models/audio-separator/`. Loaded once at lifespan startup.

### Verification

1. **Unit tests (no Docker):**
   - `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/services/analysis/test_stem_separation_worker.py -v`
   - Mock `audio_separator.Separator.separate()`; verify two-stage orchestration, output finding, FLAC rename, R2 upload calls.

2. **Integration test (Docker):**
   - Pre-download host models per new README section.
   - Rebuild: `cd services/analysis && docker compose build`.
   - `docker compose up -d`. Confirm lifespan logs "audio-separator models loaded".
   - `curl -X POST http://localhost:8000/api/v1/jobs/stem-separation -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" -d '{"audio_url":"s3://...","content_hash":"<hash>"}'`.
   - Poll `/api/v1/jobs/{id}` through stages: `downloading → stage1_bs_roformer → stage2_dereverb → uploading → complete`.
   - Confirm both `vocals_clean.flac` and `instrumental_clean.flac` exist in R2.

3. **End-to-end LRC integration test:**
   - Pick a song without `vocals_clean.flac` in R2.
   - `sow_admin audio lrc <song_id>`.
   - Confirm: LRC job stages through `submitting_stem_separation_child → awaiting_stem_separation:<child_id> → using_vocals_clean_stem → transcribing`. Confirm child STEM_SEPARATION job appears in `bd`/job list. Confirm Qwen3 was called with the clean-vocals URL (log line in `_qwen3_refine`).
   - Compare LRC quality vs prior Demucs run via `poc/score_lrc_quality.py`.

4. **Idempotency:** Re-submit same stem-separation job — returns `complete` quickly via R2 short-circuit.

5. **Concurrency:**
   - Two stem-separation jobs in quick succession: only one runs at a time.
   - Two LRC jobs both needing fresh stems: both child STEM_SEPARATION jobs queue serially; LRC semaphore slots are released during the wait, so other LRC jobs (with cached stems) can proceed.
   - ANALYZE + STEM_SEPARATION concurrent: both run (different locks). Watch memory — fall back to sharing `_analysis_lock` if OOM.

6. **DB migration:** Confirm `jobs.db` is wiped/recreated cleanly on first startup with the new image; no orphaned rows.

### Out of scope (file follow-up bd issues)

- Rename `services/qwen3` → `services/forced_aligner` (and env vars, client class names, doc references).
- Pre-bake host model directory provisioning automation (currently manual one-time setup).
- `sow_admin audio regenerate-clean-stems <song_id>` CLI subcommand (manual re-run path post-deprecation of `vocal`).
- Backfill script: regenerate `vocals_clean.flac` for the existing 21-song catalog so future LRC fixes start clean.
- Remove `admin/services/r2.py:upload_stem()` if no remaining callers (verify before removal).
