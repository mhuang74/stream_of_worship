# Plan: Scale Key/BPM Analysis via Explicit `audio batch` Steps (v4)

> v4 supersedes v3. v3 is preserved unchanged at `specs/scale-key-bpm-analysis-batch-v3.md`
> for review history. v4 narrows the surface, removes silent footguns v3 left open, and
> adds an explicit resume mechanism.

## Delta from v3 (why this version exists)

v3 was sound on the fast-analysis service design but under-specified on operator
safety. v4 closes these gaps:

1. **`--force` is narrowly scoped.** v3 let `--all-steps --force` cascade re-download,
   re-LRC, re-analyze AND re-embed in a single shot. v4 requires exactly one step flag
   alongside `--force`.
2. **`--force --download` is removed from `audio batch`.** Re-download now requires the
   existing soft-delete + `maintenance purge-soft-deletes` two-step (see
   `commands/maintenance.py:234` and `db/client.py:1409`). This eliminates the hash-prefix
   drift problem v3 waved at but never resolved.
3. **Forced fast analysis does not clear full-tier fields.** v3 handed the implementer
   a choice between clearing and preserving `beats`/`sections`/etc. v4 fixes the
   decision: preserve them; only set `analysis_status='partial'`.
4. **Manifest file on disk + `--resume`.** v3 said "audit and resume manually" but
   defined no resume surface. v4 adds a manifest writer and a `--resume` flag.
5. **`incomplete` and `partial` are distinct filter values.** v3 implied `incomplete`
   would absorb `partial`; v4 keeps them as separate predicates.
6. **Default ordering is explicit.** v3 silently relied on DB return order for
   `--limit N`. v4 mandates oldest-pending-first by `recordings.created_at`.
7. **Fix the existing `_poll_all_jobs` NameError.** The current failed-status branch
   references `recording.hash_prefix` (`commands/audio.py:4974-4976`) but
   `recording` is only assigned in completed/404 branches. v4 makes the fix
   in-scope of this refactor.
8. **Fast analysis concurrency default is cgroup-aware.** v3's
   `max(1, os.cpu_count() // 2)` returns host CPU count inside Docker, not the
   container quota. v4 uses `len(os.sched_getaffinity(0))` (Linux) with a hard cap.

Locked decisions:

- `audio batch` step flags: `--download`, `--lrc`, `--analyze`, `--embedding`,
  `--all-steps`. No step flag exits 1 with usage.
- `--analyze` defaults to fast tier; `--analysis-tier full` opts in to full.
- `--force` is valid only with exactly one of `--download`, `--lrc`, `--analyze`,
  `--embedding`. With `--all-steps` or zero step flags, exit 1.
- `--force --download` policy: NOT supported. A recording with existing R2 audio
  cannot be force-re-downloaded from `audio batch`. Operator must use the explicit
  two-step soft-delete + purge.
- Manifest file `{batch_id}_manifest.json` is written to a configured directory each
  run; `audio batch --resume <manifest_path>` re-polls jobs in the manifest.
- Existing `_poll_all_jobs` NameError (audio.py:4974-4976) must be fixed as part of
  this refactor.
- Stale processing job at submit time: resubmit new; mark old job_id as abandoned in
  the manifest.
- `partial` and `incomplete` are distinct `--analysis-status` filter values.

## Current State

`sow-admin audio batch` (`commands/audio.py:4216-4338`) is monolithic:

1. Resolves song IDs via album/song/status filters or `--stdin`
   (`_resolve_song_ids`, audio.py:4341-4410).
2. Downloads audio unless `--skip-download` is passed (Phase 1, audio.py:4696-4747).
3. Submits LRC jobs unless `--skip-lrc` is passed (Phase 2, audio.py:4749-4844).
4. Polls LRC jobs with service-first + R2 fallback reconciliation
   (`_poll_all_jobs`, audio.py:4890-5116).
5. Submits missing embedding jobs as an unconditional side effect after LRC poll
   ("Phase 3.5", audio.py:4860-4884).

The shape is not safe for an analysis-only overnight run. `audio batch
--skip-download --skip-lrc` still reaches Phase 3.5 and submits embeddings.

The embedding phase is also fire-and-forget: `_submit_embedding_single`
(audio.py:2081-2113) submits jobs whose IDs are appended to a local list that is
discarded at function return. Embedding submissions never appear in `--format json`
output and are not reconciled on Ctrl+C.

`_print_stats` (audio.py:5233-5358) only serializes `download`/`lrc` subkeys into
the JSON manifest; embedding and analysis are absent from the runtime model.

`_poll_all_jobs` has a latent NameError: the `failed` branch at 4974-4976 references
`recording.hash_prefix`, but `recording` is only fetched in the `completed` and 404
branches. A direct `failed` status from the service will crash the CLI.

The standalone `audio analyze` command (audio.py:1474-1636) submits full analysis
only. It has no batch input and hardcodes `analysis_status='completed'` via
`update_recording_analysis` (`db/client.py:894-962`), which forces the column
unconditionally (db/client.py:941).

`analysis_status` is `TEXT` in Postgres with NO CHECK constraint
(`db/schema.py:58`), so adding `partial` requires no Postgres migration. The
SQLite analysis-service job store DOES have a CHECK on `jobs.type`; that migration
is in scope.

## Command Surface

```bash
sow-admin audio batch [selection filters] [step flags] [step options]
```

Selection filters (unchanged from current command):

- `--album`, `--song`, `--stdin`, `--limit`
- `--download-status`, `--lrc-status`, `--analysis-status`
  - `--analysis-status` accepts: `pending`, `processing`, `partial`, `completed`,
    `failed`, `incomplete` (alias for `pending`/`processing`/`failed`). `partial`
    is a NEW distinct value and is NOT part of `incomplete`.

Step flags:

- `--download`, `--lrc`, `--analyze`, `--embedding`, `--all-steps`

Shared options:

- `--force`: requires exactly one step flag (see "Force scoping" below).
- `--dry-run`: prints selected songs, selected steps, force behavior, analysis
  tier, ordering, batch ID, and counts.
- `--stale-after MINUTES`: reused for processing-job staleness decisions.
  Default 120 (matches current).
- `--format rich|json`: serializes the full in-memory results dict, including
  all four step subkeys per song.
- `--limit N`: select at most N songs. Default ordering is
  `recordings.created_at ASC, recordings.hash_prefix ASC` (oldest pending first).
- `--resume <manifest_path>`: skip submission; only re-poll jobs already
  recorded in the manifest (see "Resume").

Analysis options:

- `--analysis-tier fast|full`, default `fast`. Only honored when `--analyze`
  or `--all-steps` is selected; ignored with a warning otherwise.

Rejected/deleted options:

- `--skip-download`, `--skip-lrc`, `--force-lrc` (replaced by positive step flags
  + `--force`).

### Force scoping

- `--force` with `--all-steps` exits 1 with an error explaining why
  cascading overrides are unsafe.
- `--force` with zero step flags exits 1 (same path as no step flags).
- `--force` with exactly one of `--download`/`--lrc`/`--analyze`/`--embedding`
  is the only accepted shape.
- `--force --download` is REJECTED. Re-download on an existing recording is a
  data-loss hazard (silently changes `content_hash`/`hash_prefix`, orphaning
  downstream R2 artifacts and service caches). The supported workflow is:

  ```bash
  sow-admin audio delete --recording --hash-prefix <old>
  sow-admin maintenance purge-soft-deletes --entity recordings \
    --hash-prefix <old> --confirm
  sow-admin audio batch --song <song_id> --download     # creates fresh recording
  ```

  `purge-soft-deletes` already hard-deletes the DB row and removes the R2 prefix
  via `r2_client.delete_prefix` (maintenance.py:283, services/r2.py:182).
  Attempting `--force --download` exits 1 with a hint pointing at the two-step.

### Examples

```bash
# Overnight fast analysis of incomplete recordings, oldest first.
sow-admin audio batch --analysis-status incomplete --analyze \
  --analysis-tier fast --limit 500

# Upgrade previously-fast (partial) recordings to full now that the queue is clear.
sow-admin audio batch --analysis-status partial --analyze --analysis-tier full

# Re-run LRC for a specific album's failed recordings.
sow-admin audio batch --album <album_id> --lrc-status failed --lrc --force

# Re-embed after lyrics changed.
sow-admin audio batch --song <song_id> --embedding --force

# Full pipeline (no force — safe idempotent backfill).
sow-admin audio batch --all-steps

# Resume a previously-interrupted batch.
sow-admin audio batch --resume ~/.local/share/sow-admin/batch/2026-06-30T0215_manifest.json
```

## Batch Step Semantics

Refactor `_process_batch` (`commands/audio.py:4666-4887`) so each phase is gated
strictly by its step flag:

- Download only runs with `--download` or `--all-steps`.
- LRC only runs with `--lrc` or `--all-steps`.
- Analysis only runs with `--analyze` or `--all-steps`.
- Embedding only runs with `--embedding` or `--all-steps`.
- No phase may execute as a side effect of another selected phase. Phase 3.5 is
  deleted.

### Phase ordering (sequential)

1. Download: create/refresh R2 audio for selected songs.
2. LRC submit + poll (`_poll_all_jobs` with the NameError fixed).
3. Analysis submit + a NEW analysis poll loop (separate from LRC poll).
4. Embedding submit + a NEW embedding poll loop (separate from LRC poll).

Phases run sequentially, never concurrently. Rationale: analysis depends on
download having produced R2 audio; polling concurrency in a single loop would
couple LRC and analysis failure modes unnecessarily. Sequential also matches
the current single-phase shape so the refactor is mechanical.

### Non-force behavior

- Download skips recordings where `download_status='completed'` AND R2 audio head
  exists.
- LRC skips if `lrc_status='completed'` OR an LRC object exists on R2 OR a
  non-stale processing job can be reused.
- Fast analysis skips `analysis_status IN ('partial', 'completed')`.
- Full analysis skips `analysis_status='completed'`; explicitly includes
  `partial` rows because upgrading a partial to full is a normal intent.
- Embedding skips if `get_embedding_content_hash(song_id)` matches a freshly
  computed hash (services/analysis.py:367-370).

### Force behavior (single step only)

- `--force --lrc`: bypass R2 existence check and existing-job reuse; submit a
  fresh LRC job.
- `--force --analyze --analysis-tier fast`: overwrite fast-tier DB columns
  (`duration_seconds`, `tempo_bpm`, `musical_key`, `musical_mode`,
  `key_confidence`, `loudness_db`) and set `analysis_status='partial'`.
  Full-only columns (`beats`, `downbeats`, `sections`, `embeddings_shape`,
  `r2_stems_url`) are PRESERVED. This is a deliberate status downgrade; existing
  full data remains on disk so a later `--analyze --analysis-tier full` upgrade
  does not need to regenerate it.
- `--force --analyze --analysis-tier full`: overwrite all analysis columns
  including full-only ones, set `analysis_status='completed'`. (Same as today's
  `audio analyze --force`.)
- `--force --embedding`: bypass content-hash match; submit a fresh embedding job.

### Stale job at submit time

Definition of stale: `now - recording.updated_at > stale_after_minutes` for that
step's `*_job_id`/`*_status='processing'` row.

Action: resubmit a new job via the analysis client, overwrite the recording's
`*_job_id` with the new ID, set `*_status='processing'`. The OLD job_id is written
to the manifest with `status='abandoned'` so an operator can still find it in the
service job store. The old job is NOT cancelled (the analysis service has no
batch cancel API that wouldn't also nuke other in-flight jobs).

### Interrupt behavior

On Ctrl+C:

1. Flush the manifest immediately so all submitted job_ids are durable.
2. For LRC: call the existing `_reconcile_on_interrupt` (audio.py:5146-5201),
   R2-first.
3. For analysis: probe final service state once; only mark `failed` if the service
   confirms failure. Active or non-responsive jobs are left in
   `analysis_status='processing'` and the manifest; the operator resumes with
   `--resume`.
4. For embedding: same as analysis. No R2 fallback (embeddings have no R2
   artifact).

## Manifest and Resume

### Manifest writer

A new helper `_write_manifest(batch_id, results, manifest_dir)` writes
`{batch_id}_manifest.json` after every submission and poll cycle (so interrupted
runs persist progress). `batch_id` is derived from start time:
`{YYYY-MM-DDTHHMM}_batch`.

`manifest_dir` defaults to `~/.local/share/sow-admin/batch/` (XDG-aware) and is
overridable via `SOW_BATCH_MANIFEST_DIR` env var.

### Manifest row schema

Each entry is keyed by `(song_id, step, tier)` — the dedup key for resume. A
re-submission of the same `(song_id, step, tier)` overwrites the prior entry;
completed entries are NEVER re-submitted on resume (defense against duplicate
work).

```json
{
  "batch_id": "2026-06-30T0215_batch",
  "started_at": "2026-06-30T02:15:00Z",
  "selected_steps": ["download", "lrc", "analyze"],
  "analysis_tier": "fast",
  "stale_after_minutes": 120,
  "songs": [
    {
      "song_id": "abc123",
      "hash_prefix": "feedface",
      "step": "analyze",
      "tier": "fast",
      "job_id": "job_a1b2c3d4e5f6",
      "status": "submitted|processing|completed|failed|abandoned",
      "attempts": 1,
      "previous_job_id": null,
      "error_class": null,
      "error_message": null,
      "submitted_at": "2026-06-30T02:15:04Z",
      "completed_at": null
    }
  ]
}
```

### `--resume <manifest_path>`

Behavior:

- Skip all selection/filter logic. Read the manifest's `songs` list. Ignore
  filters; they are informational only.
- Re-poll any entry with `status IN ('submitted', 'processing')`.
- Skip any entry with `status IN ('completed', 'failed', 'abandoned')` and
  print a count of skipped entries.
- On a completed entry, apply the result writeback to the DB exactly as a
  non-resume run would have done (idempotent via the (song_id, step, tier) key).
- On Ctrl+C during resume, re-flush the manifest.

`--resume` is mutually exclusive with all selection filters and with `--force`;
specifying any of them with `--resume` exits 1.

## Failed Job Handling

### Submission failures

- Continue processing later songs when one song fails validation or submission.
- Record per-song `error_class` (e.g., `AnalysisServiceError`, `ValueError`,
  `R2ConnectionError`) and `error_message` in both the in-memory results dict
  and the manifest row.
- Exit code 1 if any selected phase has at least one failed song. Aggregate
  failure flags across phases: `failed_any = any(results[s]["<step>"]=="failed"
  for s in song_ids for step in selected_steps)`.

### Processing failures

- Reuse non-stale processing jobs after probing the analysis service
  (`analysis_client.get_job(job_id)`).
- `completed`: store result via `update_recording_analysis` (with the new
  `analysis_status` parameter, see "DB Changes").
- `failed`: mark `*_status='failed'` for that step; persist `error_message` to
  the manifest row.
- 404 (job lost):
  - LRC: existing R2 reconciliation fallback (unchanged from current).
  - Analysis: NO R2 fallback (fast analysis never writes R2; full analysis
    writes `analysis.json` but that is not a reconciliation source). Mark
    `failed` and let the operator re-run with `--force --analyze`.
  - Embedding: NO R2 fallback. Same: failed + operator re-run.

### Retry policy

- Retry transient service/network polling failures AT MOST once
  (`AnalysisServiceError` with `status_code` in `{502, 503, 504}` or
  `httpx.ConnectError`/`httpx.TimeoutException`).
- Do NOT retry deterministic failures: 401 auth, missing recording, missing
  audio URL, decode failure, invalid request data, 404 job-not-found, 4xx.

### Newly-fixed bug

`_poll_all_jobs` failed branch (audio.py:4974-4976) references `recording` only
assigned in completed/404 branches. Fix: fetch `recording =
db_client.get_recording_by_<appropriate_key>` at the top of every status branch
(`completed`, `failed`, `processing`, 404, network error). The fix lives in the
same refactor commit as the new analysis/embedding poll loops.

## Fast Analysis Service Changes

### Public API

- `POST /api/v1/jobs/fast-analyze`
- `JobType.FAST_ANALYZE = "fast_analyze"`
- `FastAnalyzeJobRequest(audio_url, content_hash, options)`
- `FastAnalyzeOptions(force=False, sample_rate=22050, hop_length=4096)`

### Implementation requirements

- Add `FAST_ANALYZE` to: `JobType` enum (`models.py:21-29`), the
  `AnalyzeJobRequest`-style pydantic union, the API route registration in
  `routes/jobs.py` (mirror `submit_analysis_job` at routes/jobs.py:148-166),
  the queue dispatch switch in `workers/queue.py:373-405`, the queue stats
  surface, and `_row_to_job` deserialization in `storage/db.py:381-451`.
- Add a SQLite migration `_migrate_fast_analyze_type` at `storage/db.py`
  following the exact `_migrate_embedding_type` / `_migrate_forced_alignment_type`
  pattern. Add `'fast_analyze'` to the new CHECK constraint on `jobs.type`. Call
  from `initialize()` (storage/db.py:52-58).
- Implement fast analysis worker with librosa only:
  - `duration_seconds`, `tempo_bpm`, `musical_key`, `musical_mode`,
    `key_confidence`, `loudness_db` (RMS).
  - Match `AnalyzeOptions` field semantics where overlap exists; key/BPM
    algorithms should be documented inline (librosa `librosa.beat.tempo` and
    `librosa.feature.chroma_cqt` + key classifier).
- Fast cache key: `{hash_prefix}_fast.json` under the existing `CacheManager`
  (`storage/cache.py`). Cache shape mirrors `JobResult` (the fast subset).
- DO NOT upload fast results to R2. DO NOT overwrite full-tier
  `{hash_prefix}.json` cache.
- Bound concurrency with `SOW_FAST_ANALYZE_MAX_CONCURRENT` (see "Concurrency").

### Runtime safeguards

- Validate R2 client configuration before attempting fast-analysis audio
  fetch (the worker downloads from R2 via `audio_url`, NOT from YouTube).
- Ensure the downloaded temp audio file exists before analysis (guard for
  truncated downloads).
- `analyze_audio_fast()` is synchronous; if called via
  `run_in_executor`, do NOT submit an unawaited coroutine (executor silently
  drops futures).
- Atomic write of `{hash_prefix}_fast.json` via `tempfile.NamedTemporaryFile`
  in the same directory + `os.replace`. Reader side (`CacheManager`) must
  tolerate ENOENT gracefully (treat as cache miss) in case of mid-write read.
- R2 configuration check failures and decode failures persist the job as
  `FAILED` with a descriptive `error_message`. The admin CLI surfaces
  `error_class`/`error_message` in the manifest.

## Admin CLI and DB Changes

### AnalysisClient

Add `AnalysisClient.submit_fast_analysis(audio_url, content_hash, options)`
(admin/services/analysis.py). Reuse the existing `get_job()` for polling; parse
fast-analyze responses as normal `AnalysisResult` (no schema divergence from full
analyze; both populate the same `JobResult` fields). Document that full-only
fields (`beats`, `downbeats`, `sections`, `embeddings_shape`, `stems_url`) will
be `None`/absent on fast results.

### Analysis phase helpers

- Submit fast via `POST /api/v1/jobs/fast-analyze`.
- Submit full via the existing `POST /api/v1/jobs/analyze`.
- Poll active analysis jobs in a dedicated loop, not interleaved with LRC poll.
- Store completed fast results with `analysis_status='partial'`.
- Store completed full results with `analysis_status='completed'`.

### DB helpers

Add an `analysis_status` parameter to `update_recording_analysis`
(`db/client.py:894-962`). The current implementation force-sets
`analysis_status='completed'` (db/client.py:941); v4 requires:

- `analysis_status: Optional[str] = None` parameter, default `None`.
- If caller passes `None`: preserve the EXISTING behavior (set to `'completed'`)
  to keep migrated callers and `audio analyze` unaffected.
- If caller passes `'partial'`: set `analysis_status='partial'` and update only
  fast-tier columns (`duration_seconds`, `tempo_bpm`, `musical_key`,
  `musical_mode`, `key_confidence`, `loudness_db`).
- If caller passes `'completed'`: update all columns as today.
- Full-only column writes: when `analysis_status='partial'`, `beats`,
  `downbeats`, `sections`, `embeddings_shape`, `r2_stems_url` MUST be omitted
  from the UPDATE (NOT set to NULL). This is the data-loss protection.

### New `Recording` model API

- `Recording.has_analysis` (db/models.py:328-335): currently returns
  `analysis_status == "completed"`. v4 changes it to
  `analysis_status in ("partial", "completed")`. Audit every caller
  (`grep has_analysis`) — callers that specifically need full-tier data
  (beats/sections/embeddings) must use the new property.
- Add `Recording.has_full_analysis`: strict `analysis_status == "completed"`.
- Add `Recording.has_fast_analysis`:
  `analysis_status in ("partial", "completed")` (alias of `has_analysis`).
- Update CLI display, colorization, and status filters in
  `commands/audio.py` to recognize and color `partial` distinctly
  (e.g., yellow; `completed` green; `failed` red).

### Status surface

- `partial` is a new DB value. No Postgres migration needed
  (TEXT without CHECK, db/schema.py:58).
- `partial` is a new CLI filter value distinct from `incomplete`.
- The DB-extras (`db/client.py:696` `incomplete` predicate) does NOT include
  `partial`.

## Operational Concerns

### Recommended fast run

```bash
# Stage A: fast pass on songs lacking any analysis (oldest pending first).
sow-admin audio batch --analysis-status incomplete --analyze \
  --analysis-tier fast --limit 500
```

### Recommended full upgrade pass

```bash
# Stage B: upgrade all fast-analyzed recordings to full.
sow-admin audio batch --analysis-status partial --analyze --analysis-tier full
```

### Concurrency and resource caps

- Fast analysis loads full audio into memory and is CPU-heavy. Fast semaphore is
  a NEW asyncio.Semaphore distinct from `_local_model_semaphore`
  (`workers/queue.py:164-167`), `_dashscope_asr_semaphore`, and
  `_embedding_semaphore`. They do not coordinate; the operator is responsible for
  sizing `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS`,
  `SOW_DASHSCOPE_ASR_MAX_CONCURRENT`, `SOW_FAST_ANALYZE_MAX_CONCURRENT`,
  `SOW_EMBEDDING_MAX_CONCURRENT` together.
- `SOW_FAST_ANALYZE_MAX_CONCURRENT` default: cgroup-aware.
  ```python
  try:
      default = min(4, max(1, len(os.sched_getaffinity(0)) // 2))
  except (AttributeError, OSError):  # macOS / unsupported
      default = 1
  ```
  Hard cap 4 prevents memory blowup on big hosts. Override via env. Operator
  guidance in docs: raise cautiously when memory is plentiful AND songs are
  short (<5 min).
- Expose `SOW_FAST_ANALYZE_MAX_CONCURRENT` in `analysis-service/docker-compose.yml`
  `x-common-env` block alongside other SOW_ variables.

### Statuses

- `partial`: fast key/BPM/loudness/duration only. No beats, sections, embeddings.
- `completed`: full analysis with beats/sections/embeddings_shape where available.
- `--force --analyze --analysis-tier fast` downgrades `completed` → `partial`
  INTENTIONALLY but preserves full-only DB columns on disk.

### Auth

- `POST /api/v1/jobs/fast-analyze` requires `verify_api_key` (routes/jobs.py:39-62)
  identical to the existing `/jobs/analyze`. No admin key needed for submission;
  job-store migration is performed automatically by `storage/db.py` on startup.

### Observability

- Each fast analysis job carries the same lifecycle logging as the existing
  `analyze` job (`workers/queue.py:342-405` — submit, processing started/
  completed/failed, queue position).
- Manifest rows include `submitted_at`/`completed_at` so wall-clock per step
  and per song is derivable.
- No metrics/tracing changes required; if present today, fast analysis is tagged
  `job_type=fast_analyze` consistently.

### Out-of-scope risks to flag

- No batch wall-clock timeout. A 500-song batch can run for hours. Operator
  uses Ctrl+C + `--resume`. Per-song poll timeout remains 600s
  (`services/analysis.py:524-560`).
- No isolation from concurrent `audio analyze` single-song runs targeting the
  same recording. Operator must coordinate manually.
- Orphaned R2 audio objects under a soft-but-not-hard-purged recording are out
  of scope; addressed by `maintenance purge-soft-deletes` separately.

## Test Plan

### Admin CLI tests (`ops/admin-cli/tests`)

- `audio batch` with no step flags exits 1.
- `--download`, `--lrc`, `--analyze`, `--embedding` each run only their selected
  phase. Assert no cross-phase side effect (especially: no embedding submission
  when only `--analyze` is selected).
- `--all-steps` runs all four phases in documented order: download → LRC →
  analysis → embedding.
- `--all-steps --force` exits 1 with the cascade-rejection message.
- `--force` with zero step flags exits 1.
- `--force --download` exits 1 with the two-step purge hint.
- `--force --lrc` submits a fresh LRC job ignoring R2/existing-job reuse.
- `--force --analyze --analysis-tier fast` overwrites fast columns AND sets
  `partial` AND preserves `beats`/`sections`/etc. assert column values
  unchanged before/after.
- `--force --analyze --analysis-tier full` overwrites everything, sets
  `completed`.
- `--force --embedding` submits a fresh embedding job even when content hash
  matches.
- `--analyze` defaults to fast; `--analysis-tier full` uses the existing full
  endpoint.
- `--analysis-status partial` selects partial rows only; `--analysis-status
  incomplete` selects `pending`/`processing`/`failed` (NOT partial).
- `--limit N` selects oldest-by-`recordings.created_at` first. Assert ordering
  in the query.
- Batch exits nonzero when any selected step has a failure; counterexamples:
  zero failures exits 0.
- Manifest writer: produces a parseable `{batch_id}_manifest.json`; contains one
  row per (song_id, step, tier); row schema matches spec.
- `--resume`: re-polls entries with `status IN ('submitted', 'processing')`,
  writes back results to DB identically to a non-resume run, skips
  `completed`/`failed` entries.
- `--resume` mutual-exclusivity: exits 1 when combined with any selection flag
  or `--force`.
- `_poll_all_jobs` NameError fix: a job whose service status is `failed`
  without a prior `completed` poll no longer raises NameError.

### Analysis service tests (`ops/analysis-service/tests`)

- `FastAnalyzeJobRequest` model validation (audio_url required, content_hash
  required, options optional with defaults).
- `POST /api/v1/jobs/fast-analyze` auth (401 without key, 200 with),
  validation (400 on bad body).
- Job-store CHECK migration accepts `fast_analyze`: pre-stuff the DB with a
  fast_analyze row, call `initialize()`, assert no migration error and
  round-trip `_row_to_job` reproduces the same `FastAnalyzeJobRequest`.
- `_row_to_job` reconstructs fast jobs from SQLite.
- Queue dispatch processes fast jobs under the fast semaphore; assert
  semaphore cap respected even when `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS`
  is saturated by `analyze` jobs.
- Fast cache: hit returns cached result without recomputation; miss computes
  and writes; `force=True` bypasses cache; corrupt JSON falls back to
  recomputation (catch JSONDecodeError, delete, recompute).
- Worker failure paths: R2 misconfiguration → job FAILED with descriptive
  message; audio download failure → job FAILED; audio decode failure (bad
  bytes) → job FAILED; cache write failure → job FAILED without leaving
  partial JSON on disk.
- Restart recovery: `processing` fast jobs are requeued by
  `get_interrupted_jobs()` (queue.py:240-257).
- Concurrency default computation: assert default formula yields 1 on
  macOS/unsupported (uses fallback path) and cgroup-aware count on Linux.

### Integration tests

- Batch across mixed inputs:
  - skipped (already completed)
  - submitted (just queued)
  - completed (resolved during poll)
  - failed (service returns FAILED)
  - stale-processing (older than `--stale-after`)
  - retryable transient failure (502 then 200)
- Analysis-only batch against already-downloaded + LRC-completed songs
  submits ZERO LRC and ZERO embedding jobs.
- `--all-steps` preserves phase order; dependent phases (LRC after download,
  analysis after LRC) do not run prerequisites for songs whose prior phase
  failed.
- Manifest dedup: re-running `--resume` against a manifest with completed
  entries does NOT re-submit those jobs.
- Ctrl+C mid-poll: manifest flushed; `_reconcile_on_interrupt` runs;
  analysis jobs left in `processing` status survive resume.
