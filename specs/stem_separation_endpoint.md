# Spec: Stem-Separation Service Endpoint for High-Quality Vocal Stems

## Context

LRC generation quality depends heavily on the cleanliness of the vocal stem fed to Whisper / forced alignment. The signal experiment in `specs/experiment_with_lrc_eval_approaches.md` and the manual workflow in `docs/manually-fix-lrc.md` both establish that **de-echoed, BS-Roformer-extracted vocals (`clean_vocals.flac`) materially outperform** the Demucs `vocals.wav` produced today by the analysis pipeline. The improvement is large enough that the manual fix workflow makes Step 0 ("Generate clean vocal stems") effectively mandatory.

Currently, this clean-vocals generation only exists as an offline POC (`poc/gen_clean_vocal_stem.py`) run by hand. To get the same quality during automated LRC creation for new songs, we need a backend service capability that:

- Runs the two-stage pipeline (BS-Roformer → UVR-De-Echo) asynchronously with job status reporting
- Uploads the resulting clean vocal + clean instrumental stems to R2
- Is **independent** of the heavyweight `/jobs/analyze` endpoint (allin1 song structure analysis) — most new songs will only need LRC, not full analysis
- Is invoked automatically by the `/jobs/lrc` worker when no clean vocal stem exists in R2 yet

The qwen3 service rename (it actually runs forced alignment, not transcription) is acknowledged but explicitly **deferred to a follow-up bd issue** to keep this change focused.

## Decisions (confirmed with user)

1. **Deployment:** Add the new worker to the **existing `services/analysis` container**. Reuses `JobQueue`, `JobStore`, `R2Client`, the SQLite recovery path, and the FastAPI app. Lets the LRC worker await stem-separation in-process without a second HTTP hop.
2. **LRC integration:** **Auto-trigger inside the LRC worker.** When a clean vocal stem is missing in R2, the LRC worker submits a stem-separation job and awaits its completion before transcribing. From the caller's perspective, `/jobs/lrc` remains a single job id.
3. **Output format & R2 layout:** **FLAC**, with both the clean vocals **and** the clean instrumental uploaded to R2:
   - `s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac`
   - `s3://{bucket}/{hash_prefix}/stems/instrumental_clean.flac`
4. **Qwen3 rename:** Out of scope — file a bd follow-up.

## Architecture

### New endpoint

`POST /api/v1/jobs/stem-separation` (Bearer-auth, alongside existing `/jobs/analyze` and `/jobs/lrc`).

Request body (`StemSeparationJobRequest`):

```python
{
  "audio_url": "s3://bucket/{hash_prefix}/audio/audio.mp3",
  "content_hash": "<sha256>",
  "options": {
    "dereverb_model": "UVR-De-Echo-Normal.pth",   # default; alternates documented
    "force": false                                 # bypass cache
  }
}
```

Returns the standard `JobResponse` (job_id, status, progress, stage, result). On completion, `result.vocals_clean_url` and `result.instrumental_clean_url` are populated.

### Concurrency model

- New `JobType.STEM_SEPARATION` added to `models.py`.
- New `_stem_separation_lock = asyncio.Lock()` in `JobQueue.__init__` — **serialized**, matching the analysis lock. Rationale: BS-Roformer loads ~2-3 GB and `specs/experiment_with_lrc_eval_approaches.md` lines 70-74 explicitly warns that running multiple instances in parallel causes mutual memory starvation (chunk times balloon from 38s to 4000-9000s).
- Stem separation has its **own lock** (not shared with analysis), so a stem-separation job can run while a previous analysis lock holder finishes — but two stem-separation jobs cannot overlap. If we discover memory pressure when allin1 + stem-separation overlap on the same host, we switch the new worker to share `_analysis_lock`. We start with a separate lock and watch.
- `_process_job_with_semaphore` gets a third branch dispatching `JobType.STEM_SEPARATION` to `_process_stem_separation_job`.

### Caching

Two layers:

1. **R2 idempotency check** (primary): before running, the worker checks `vocals_clean.flac` already exists in R2 for this `hash_prefix`. If yes and `options.force=False`, return cached URLs without re-running.
2. **Service-side cache** in `/cache/stems_clean/{hash[:32]}/` (vocals.flac, instrumental.flac) — enables fast restart if R2 upload failed mid-flight; mirrors the existing `/cache/stems/{hash}/` directory used by `separator.py`.

### LRC worker integration

In `workers/queue.py:_process_lrc_job` (around line 559-571), replace the current vocals-stem lookup with:

```python
if request.options.use_vocals_stem and self.r2_client:
    clean_vocals_url = f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/vocals_clean.flac"
    if not await self.r2_client.check_exists(clean_vocals_url):
        # Auto-trigger stem separation inline — same process, awaits completion
        job.stage = "generating_clean_vocals"
        job.updated_at = datetime.now(timezone.utc)
        await self._run_stem_separation_inline(
            audio_url=request.audio_url,
            content_hash=request.content_hash,
            parent_job=job,
        )
    # Now download whichever cleaned/uncleaned stem is best available
    for stem_name, suffix in [
        ("vocals_clean", "flac"),
        ("vocals_clean", "wav"),  # legacy, may exist on older songs
        ("vocals", "wav"),         # Demucs fallback
    ]:
        stem_url = f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/{stem_name}.{suffix}"
        if await self.r2_client.check_exists(stem_url):
            stem_path = temp_path / f"{stem_name}.{suffix}"
            await self.r2_client.download_audio(stem_url, stem_path)
            transcription_path = stem_path
            job.stage = f"using_{stem_name}_stem"
            break
```

`_run_stem_separation_inline` is a private helper that calls into the same code path as the public endpoint, but does **not** create a separate `Job` record (or creates a child job linked to the parent). The LRC job's `progress` and `stage` reflect the in-flight separation stage (e.g., `stage="generating_clean_vocals.stage1_bs_roformer"`).

The legacy `vocals.wav` lookup is preserved as the last-resort fallback.

### Worker module: `workers/stem_separation.py` (new)

Mirror the structure of `workers/separator.py` (Demucs) and `workers/lrc.py`. The core function:

```python
async def separate_clean_vocals(
    input_audio: Path,
    output_dir: Path,
    dereverb_model: str = "UVR-De-Echo-Normal.pth",
    progress_cb: Callable[[float, str], None] | None = None,
) -> tuple[Path, Path]:
    """Run two-stage pipeline. Returns (vocals_clean.flac, instrumental_clean.flac).

    Reuses the algorithm from poc/gen_clean_vocal_stem.py:
      Stage 1: BS-Roformer (model_bs_roformer_ep_317_sdr_12.9755.ckpt) → vocals + instrumental
      Stage 2: UVR-De-Echo applied to Stage 1 vocals → "No Echo" (clean) vocals
    Stage 1 instrumental is renamed to instrumental_clean.flac;
    Stage 2 dry vocals is renamed to vocals_clean.flac.
    """
```

Run inside an executor (`loop.run_in_executor`) since `audio_separator` is synchronous + CPU/GPU-blocking. Models cached in container at `/cache/stem_models/` (download on first use; persisted across restarts via the `analysis-cache` volume).

### R2Client additions (`services/analysis/src/sow_analysis/storage/r2.py`)

Add a method:

```python
async def upload_clean_stems(
    self, hash_prefix: str, vocals_clean: Path, instrumental_clean: Path
) -> tuple[str, str]:
    """Upload vocals_clean.flac + instrumental_clean.flac. Returns (vocals_url, instrumental_url)."""
```

Keys: `{hash_prefix}/stems/vocals_clean.flac`, `{hash_prefix}/stems/instrumental_clean.flac`.

### Models (`services/analysis/src/sow_analysis/models.py`)

- Add `JobType.STEM_SEPARATION = "stem_separation"`.
- Add `StemSeparationOptions(BaseModel)` with `dereverb_model: str = "UVR-De-Echo-Normal.pth"`, `force: bool = False`.
- Add `StemSeparationJobRequest(BaseModel)` with `audio_url`, `content_hash`, `options`.
- Add to `JobResult`: `vocals_clean_url: Optional[str] = None`, `instrumental_clean_url: Optional[str] = None`.
- Update `Job.request` Union to include `StemSeparationJobRequest`.

### Routes (`services/analysis/src/sow_analysis/routes/jobs.py`)

Add:

```python
@router.post("/jobs/stem-separation", response_model=JobResponse)
async def submit_stem_separation_job(
    request: StemSeparationJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")
    job = await job_queue.submit(JobType.STEM_SEPARATION, request)
    return job_to_response(job)
```

Update `job_to_response` to copy `vocals_clean_url` and `instrumental_clean_url`.

### Admin client (`src/stream_of_worship/admin/services/analysis.py`)

Add `submit_stem_separation(audio_url, content_hash, force=False, dereverb_model=None)` mirroring `submit_lrc`. The CLI does not need to call it for the default LRC-creation path (the LRC worker auto-triggers), but exposing it allows manual re-runs and the `sow_admin audio` group can grow a `regenerate-clean-stems <song_id>` subcommand later (out of scope for this spec).

### Docker / dependencies

Add to `services/analysis/pyproject.toml` under the `service` extra:

```
"audio-separator>=0.30.0",
"onnxruntime>=1.17.0",
```

These match the `stem_separation` extra already declared in the **root** `pyproject.toml` (referenced by `docs/manually-fix-lrc.md`), so we are reusing a known-working version pin set.

`Dockerfile` changes (`services/analysis/Dockerfile`):

- No new system packages required (`audio-separator` runs on the existing ffmpeg + libsndfile1 base; CPU-only ONNX runtime works on the AMD64 wheel).
- Models (~430 MB combined: BS-Roformer 370 MB + UVR-De-Echo 60 MB) are downloaded on **first use** by `audio-separator` into `/cache/stem_models/`. Persisted via the existing `analysis-cache` named volume — no Dockerfile changes for model pre-bake.
- Optionally pre-warm during image build by adding a `RUN python -c "from audio_separator.separator import Separator; ..."` step that pre-downloads both models. **Not included in this spec** to keep the image build time bounded; first-job latency penalty is one-time.

### Configuration (`services/analysis/src/sow_analysis/config.py`)

- Add `SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: int = 1` — parallels the existing analysis setting; documents the serial-only constraint.
- No new R2 / API-key envs needed.

## Files to modify / create

| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | Add `JobType.STEM_SEPARATION`, `StemSeparationOptions`, `StemSeparationJobRequest`; extend `JobResult` and `Job.request` Union |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` (new) | Two-stage worker; ports algorithm from `poc/gen_clean_vocal_stem.py` |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `_stem_separation_lock`, `_process_stem_separation_job`, `_run_stem_separation_inline`; modify `_process_job_with_semaphore` dispatch (line 235); modify `_process_lrc_job` stem-lookup block (line 559-571) |
| `services/analysis/src/sow_analysis/storage/r2.py` | Add `upload_clean_stems()` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/stem-separation`; update `job_to_response` |
| `services/analysis/src/sow_analysis/storage/db.py` | Verify `JobStore` round-trips the new `JobType.STEM_SEPARATION` enum value (likely already generic) |
| `services/analysis/pyproject.toml` | Add `audio-separator>=0.30.0`, `onnxruntime>=1.17.0` to `service` extra |
| `services/analysis/src/sow_analysis/config.py` | Add `SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS` |
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_stem_separation()` client method (no CLI command yet) |
| `tests/services/analysis/test_stem_separation_worker.py` (new) | Unit-test the worker with a small fixture audio; mock `Separator` to verify two-stage orchestration + output renaming |
| `tests/services/analysis/test_routes_jobs.py` | Extend with stem-separation submission test |

## Reused existing utilities

- `poc/gen_clean_vocal_stem.py` (`extract_vocals_two_stage`) — the ported algorithm; keep the POC script in place but factor the core into the new worker module so both call the same code (or have the POC import the worker module).
- `JobQueue`, `JobStore`, `CacheManager`, `R2Client` — unchanged structurally.
- `_process_lrc_job` stem-lookup loop already prefers `vocals_clean` over `vocals`; we extend the format priority to FLAC first, then WAV (legacy), then `vocals.wav` (Demucs).
- `services/analysis/src/sow_analysis/services/qwen3_client.py` — unchanged (qwen3 rename deferred).

## Verification

1. **Unit tests (fast, no Docker):**
   - `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/services/analysis/test_stem_separation_worker.py -v`
   - Mocks `audio_separator.Separator.separate()` returning sentinel paths; verifies the worker calls Stage 1 then Stage 2, finds the "No Echo" output, and renames to `vocals_clean.flac`.

2. **Integration test (Docker required):**
   - Rebuild the analysis image with new deps: `cd services/analysis && docker compose build`.
   - Start service: `docker compose up -d`.
   - Submit a job with a small known song:
     ```bash
     curl -X POST http://localhost:8000/api/v1/jobs/stem-separation \
       -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
       -H "Content-Type: application/json" \
       -d '{"audio_url":"s3://...","content_hash":"<hash>"}'
     ```
   - Poll `/api/v1/jobs/{id}` to confirm staging progresses through `downloading → stage1_bs_roformer → stage2_dereverb → uploading → complete`.
   - Confirm both `vocals_clean.flac` and `instrumental_clean.flac` exist in R2 under `{hash_prefix}/stems/`.

3. **End-to-end LRC integration test:**
   - Pick a song that does **not** yet have `vocals_clean.flac` in R2.
   - Submit `/jobs/lrc` for it via `sow_admin audio lrc <song_id>`.
   - Confirm: (a) stage progresses through `generating_clean_vocals` before `transcribing`, (b) `vocals_clean.flac` appears in R2, (c) the LRC output uses the clean vocals (verifiable by checking job logs `[job_id] Using vocals_clean stem for transcription`).
   - Compare LRC quality against the previous run with Demucs-only `vocals.wav` using `poc/score_lrc_quality.py` for the same song.

4. **Idempotency check:** Re-submit the same stem-separation job. Confirm it returns `complete` quickly without re-running the models (R2 existence short-circuit), and that progress jumps from 0 → 1 with stage `cached`.

5. **Concurrency check:** Submit two stem-separation jobs in quick succession. Confirm only one runs at a time (second waits, stage stays `queued` until the first completes). Confirm submitting an analysis job and a stem-separation job concurrently lets both run (different locks), then watch memory metrics — if OOM observed, switch the new worker to share `_analysis_lock` instead of its own.

## Out of scope (follow-up bd issues to file)

- **Rename `services/qwen3` → `services/forced_aligner`** (or similar). Updates: directory rename, `docker-compose.yml`, `services/analysis/.../qwen3_client.py` → `forced_aligner_client.py`, env vars `SOW_QWEN3_*` → `SOW_FORCED_ALIGNER_*`, all docs/poc references.
- **Pre-bake stem-separation models into the Docker image** if first-run latency proves too high.
- **`sow_admin audio regenerate-clean-stems <song_id>`** CLI subcommand (manual re-run path).
- **Migration/backfill script** to generate `vocals_clean.flac` for the existing 21-song catalog so future LRC fixes start from cleaned stems.
