# Plan: Scale Key/BPM Analysis via Explicit `audio batch` Steps (v3)

## Summary

Revise the v2 plan around the existing `sow-admin audio batch` command instead of adding a
separate batch surface to `audio analyze`.

The new batch command is step-driven: it runs no work unless at least one step is explicitly
enabled. This makes overnight fast-analysis runs ergonomic while preventing accidental downloads,
LRC generation, embedding jobs, or full analysis.

Locked decisions:

- `audio batch` step flags are `--download`, `--lrc`, `--analyze`, `--embedding`, and `--all-steps`.
- `--all-steps` enables download, LRC, analysis, and embedding.
- Running `audio batch` with no step flag exits with an error and usage examples.
- Remove old `--skip-download`, `--skip-lrc`, and `--force-lrc`.
- `audio batch --analyze` defaults to fast analysis.
- `--force` applies only to selected steps:
  - `--force --download` re-downloads audio.
  - `--force --lrc` reruns LRC generation.
  - `--force --analyze` reruns analysis and overwrites prior analysis results.
  - `--force --embedding` recalculates and uploads embeddings.
- Batch failures continue per song, are recorded in output/state, and make the command exit
  nonzero.

## Current State

`sow-admin audio batch` currently performs an end-to-end download + LRC workflow:

1. Resolves song IDs through album/song/status filters or `--stdin`.
2. Downloads audio unless `--skip-download` is passed.
3. Submits LRC jobs unless `--skip-lrc` is passed.
4. Polls LRC jobs with service-first and R2 fallback reconciliation.
5. Submits missing embedding jobs as a side effect after the LRC phase.

This shape is not safe for an analysis-only run. Today, a command such as
`audio batch --skip-download --skip-lrc` would still reach the embedding phase and may submit
embedding jobs unexpectedly.

The current single-song `audio analyze` command only submits full allin1 analysis. It has no
batch input, no fast tier, and stores analysis results with `analysis_status='completed'`
unconditionally.

## Command Surface

Update `sow-admin audio batch` to use explicit step selection:

```bash
sow-admin audio batch [selection filters] [step flags] [step options]
```

Selection filters remain:

- `--album`
- `--song`
- `--stdin`
- `--limit`
- `--download-status`
- `--lrc-status`
- `--analysis-status`

Step flags:

- `--download`
- `--lrc`
- `--analyze`
- `--embedding`
- `--all-steps`

Shared options:

- `--force`: applies only to selected steps.
- `--dry-run`: prints selected songs, selected steps, force behavior, analysis tier, and counts.
- `--stale-after MINUTES`: reused for processing-job staleness decisions.
- `--format rich|json`: includes all selected step results.

Analysis options:

- `--analysis-tier fast|full`, default `fast`.

Rejected/deleted options:

- `--skip-download`
- `--skip-lrc`
- `--force-lrc`

Examples:

```bash
# Already downloaded songs with LRC generated: run fast key/BPM analysis only.
sow-admin audio list --analysis-status incomplete --format ids \
  | sow-admin audio batch --stdin --analyze --analysis-tier fast

# Fast key/BPM batch across incomplete recordings.
sow-admin audio batch --analysis-status incomplete --analyze --limit 500

# Force-refresh fast analysis even for previously analyzed recordings.
sow-admin audio batch --analysis-status completed --analyze --analysis-tier fast --force

# Regenerate LRC only.
sow-admin audio batch --lrc --force --limit 50

# Recalculate embeddings only.
sow-admin audio batch --embedding --force

# Full explicit pipeline.
sow-admin audio batch --all-steps
```

## Batch Step Semantics

Refactor `_process_batch()` so each phase is gated only by selected steps:

- Download runs only with `--download` or `--all-steps`.
- LRC runs only with `--lrc` or `--all-steps`.
- Analysis runs only with `--analyze` or `--all-steps`.
- Embedding runs only with `--embedding` or `--all-steps`.
- No phase may run as a side effect of another selected phase.

If no step flag is selected, exit with code `1` and print concise examples.

`--force` behavior:

- Download: bypass existing `download_status='completed'` and R2 audio checks, re-download from
  YouTube, upload the new audio, and update the recording metadata. If the new content hash differs
  from the old hash, the implementation must handle hash-prefix changes deliberately instead of
  silently updating the wrong row.
- LRC: bypass existing R2 LRC checks and existing LRC job reuse; submit a new LRC job.
- Analysis: bypass existing `partial`/`completed` checks and submit a new analysis job for the
  selected tier.
- Embedding: bypass existing embedding content-hash match and submit a new embedding job.

Non-force behavior:

- Download skips recordings already downloaded and present on R2.
- LRC skips if an LRC already exists on R2 or a non-stale processing job can be reused.
- Fast analysis skips records already `partial` or `completed`.
- Full analysis skips records already `completed`, but includes `partial` records when selected by
  filters.
- Embedding skips records with current matching embedding content hash.

## Fast Analysis Service Changes

Add a fast analysis tier to the analysis service.

Public API:

- `POST /api/v1/jobs/fast-analyze`
- `JobType.FAST_ANALYZE = "fast_analyze"`
- `FastAnalyzeJobRequest(audio_url, content_hash, options)`
- `FastAnalyzeOptions(force=False, sample_rate=22050, hop_length=4096)`

Implementation requirements:

- Add `FAST_ANALYZE` to Pydantic models, request unions, API routes, queue dispatch, queue stats,
  and job-store deserialization.
- Add a SQLite job-store migration for the `jobs.type` CHECK constraint. Updating the enum alone
  is not enough.
- Implement fast analysis with librosa only:
  - duration
  - tempo BPM
  - musical key
  - musical mode
  - key confidence
  - RMS loudness
- Use a separate fast cache key such as `{hash_prefix}_fast.json`.
- Do not upload fast results to R2 and do not overwrite full-tier `analysis.json`.
- Bound fast analysis concurrency with `SOW_FAST_ANALYZE_MAX_CONCURRENT`, defaulting
  conservatively to avoid CPU oversubscription.

Runtime safeguards:

- Validate R2 configuration before attempting download.
- Ensure the downloaded temp audio file exists before analysis.
- Keep `analyze_audio_fast()` and executor usage consistent. If it is called through
  `run_in_executor`, the function should be synchronous; do not submit an unawaited coroutine to
  the executor.
- Write fast cache files atomically to avoid corrupted JSON under concurrent jobs.

## Admin CLI and DB Changes

Add admin client support:

- Add `AnalysisClient.submit_fast_analysis()`.
- Reuse `AnalysisClient.get_job()` for polling.
- Parse `fast_analyze` results as normal `AnalysisResult` data.

Add analysis phase helpers for `audio batch`:

- Submit fast jobs through `/jobs/fast-analyze`.
- Submit full jobs through the existing `/jobs/analyze`.
- Poll active analysis jobs separately from LRC jobs.
- Store completed fast results as `analysis_status='partial'`.
- Store completed full results as `analysis_status='completed'`.

Update DB helpers:

- Add an `analysis_status` parameter to `update_recording_analysis()`.
- Ensure fast writes can preserve or clear fields intentionally according to force behavior.
- For non-force fast analysis, do not downgrade completed rows.
- For `--force --analyze --analysis-tier fast`, overwrite DB analysis fields and set
  `analysis_status='partial'`; this is an intentional downgrade of DB analysis completeness.

Data-loss protections:

- Fast analysis never writes `analysis.json` to R2.
- Non-force fast analysis does not overwrite `completed` records.
- Full-only DB fields (`beats`, `downbeats`, `sections`, `embeddings_shape`, `r2_stems_url`) must
  not be accidentally cleared by helper defaults. Clearing them is allowed only when explicitly
  implementing the documented `--force --analyze --analysis-tier fast` overwrite behavior.

Status support:

- Add `partial` as a supported analysis status in CLI filters, display, and colorization.
- Update `Recording.has_analysis` to include `partial`, and add `has_full_analysis` for callers
  that need beats/sections/embeddings.
- Audit current `has_analysis` callers so partial analysis is not mistaken for full analysis.

## Failed Job Handling and Batch Management

Submission handling:

- Continue processing later songs when one song fails validation or submission.
- Record per-song error class and message in results.
- Exit with code `1` if any selected phase has a failure.

Processing handling:

- Reuse non-stale processing jobs after probing the analysis service.
- If a processing job is completed, store its result.
- If a processing job is failed, mark the recording failed for that step.
- If the service returns 404 for a job:
  - For LRC, keep the existing R2 reconciliation fallback.
  - For fast analysis, check DB/cache only if a reliable result source exists; otherwise mark failed
    or resubmit according to the retry policy.

Retry policy:

- Retry transient service/network polling failures once.
- Do not automatically retry deterministic failures such as missing recording, missing audio URL,
  auth failure, decode failure, or invalid request data.

State output:

- Add manifest/state output for submitted analysis jobs, either as a dedicated manifest file or as
  structured JSON output when `--format json` is used.
- Include song ID, hash prefix, selected step, tier, job ID, status, attempts, error, submitted_at,
  and completed_at.
- Update state after each submission and poll cycle so interrupted runs can be audited and resumed
  manually.

Interrupt behavior:

- On Ctrl+C, reconcile completed LRC jobs through R2 as today.
- For analysis jobs, probe final service state before marking failed.
- Do not mark active jobs failed just because the CLI was interrupted unless the service confirms
  failure or the job is stale/missing.

## Operational Concerns

Recommended fast key/BPM run:

```bash
sow-admin audio batch --analysis-status incomplete --analyze --analysis-tier fast --limit 500
```

For already downloaded songs with LRC generated:

```bash
sow-admin audio list --download-status completed --lrc-status completed --format ids \
  | sow-admin audio batch --stdin --analyze --analysis-tier fast
```

Concurrency:

- Fast analysis loads full audio into memory and performs CPU-heavy librosa work.
- Default `SOW_FAST_ANALYZE_MAX_CONCURRENT` should be conservative, for example
  `max(1, os.cpu_count() // 2)`.
- Operators can raise or lower it based on CPU and memory saturation.

Statuses:

- `partial` means fast key/BPM/loudness/duration only.
- `completed` means full analysis with beats/sections/embeddings where available.
- `--force --analyze --analysis-tier fast` intentionally downgrades DB analysis completeness from
  `completed` to `partial`.

## Test Plan

Admin CLI tests:

- `audio batch` with no step flags exits with code `1`.
- `--download`, `--lrc`, `--analyze`, and `--embedding` each run only their selected phase.
- `--all-steps` runs all four phases.
- No implicit embedding, analysis, LRC, or download work happens from another phase.
- `--skip-download`, `--skip-lrc`, and `--force-lrc` are rejected.
- `--force --download` re-downloads.
- `--force --lrc` submits a fresh LRC job.
- `--force --analyze --analysis-tier fast` overwrites analysis and sets `partial`.
- `--force --embedding` submits a fresh embedding job even when content hash matches.
- `--analyze` defaults to fast; `--analysis-tier full` uses the existing full endpoint.
- Batch exits nonzero when any selected step fails after continuing other songs.

Analysis service tests:

- `FastAnalyzeJobRequest` model validation.
- `POST /api/v1/jobs/fast-analyze` auth and validation.
- Job-store CHECK migration accepts `fast_analyze`.
- `_row_to_job()` reconstructs fast jobs from SQLite.
- Queue dispatch processes fast jobs with the fast semaphore.
- Fast cache hit, miss, force bypass, and corrupt-cache fallback.
- Download failure, missing R2 config, audio decode failure, and cache write failure.
- Restart recovery requeues `processing` fast jobs.

Integration tests:

- Batch with mixed skipped, submitted, completed, failed, stale-processing, and retryable jobs.
- Analysis-only batch against already downloaded and LRC-completed songs submits no LRC or embedding
  work.
- `--all-steps` preserves intended phase order and does not continue dependent phases for songs
  whose prerequisites failed.
- Manifest/state output does not duplicate completed jobs during manual resume.
