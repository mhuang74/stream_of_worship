# Recover `visibility_status` — Direct SQLite Access to Analysis Service — v1

## Status

**Planning only — do not implement.** This is a follow-up plan to
`specs/recover-visibility-bug-audio-batch-v2.md`. It evaluates and specifies a
refactor of the `recover_visibility.py` dry-run report to read Analysis Service
job state directly from its SQLite `jobs.db`, instead of going through the
`AnalysisClient` REST endpoint (`GET /api/v1/jobs/{job_id}`).

## Motivation

The current report (`ops/admin-cli/src/stream_of_worship/admin/commands/recover_visibility.py`)
resolves Analysis Service job timestamps via `_batch_lookup_analysis`, which
issues one HTTP `GET /api/v1/jobs/{job_id}` per job ID across up to 10 worker
threads. Investigation found two inefficiencies and one data gap:

1. **Thrown-away data.** `_lookup_analysis_job` keeps only `job.updated_at` and
   discards the rest of `JobResponse` (`created_at`, `stage`, `error_message`,
   and the full `result` object including `key_detected_at`, `lrc_url`,
   `lrc_source`, `line_count`, `stems_url`).
2. **N HTTP round-trips.** Hundreds of candidate recordings each contribute up
   to two job IDs (`lrc_job_id`, `analysis_job_id`); each becomes a separate
   authenticated HTTP request. `--with-analysis` is therefore an expensive,
   opt-in flag — and forgetting to pass it is a primary cause of `INCONCLUSIVE`
   verdicts (the `_compute_verdict` fallback fires when
   `manual_edit_after_autogen == "unknown"` because `lrc_job_done is None` or
   `with_analysis` is false).
3. **REST omits the request payload entirely.** `JobResponse`
   (`ops/analysis-service/src/sow_analysis/models.py`) does not surface
   `request_json`. Direct SQLite access exposes it, unlocking genuinely new
   signals (see "Signals unlocked" below).

Switching to a single bulk SQLite read removes the per-job network cost, makes
`--with-analysis` behavior effectively free (so it can be defaulted on), and
exposes fields the REST API never returns.

## Scope

- **In scope:** Replace `_batch_lookup_analysis` / `_lookup_analysis_job` /
  `AnalysisClient` usage *within `recover_visibility.py` only* with a direct
  read-only query against the analysis service SQLite `jobs.db`.
- **In scope:** Surface additional fields from `result_json` and `request_json`
  to the report so INCONCLUSIVE rows can be resolved when the row exists.
- **Out of scope:** Any change to the Analysis Service itself (schema,
  `JobResponse`, endpoint surface, retention policy).
- **Out of scope:** Persisting job-completion timestamps into PostgreSQL
  (see "Non-fixes" — this is the durable fix for purged-row INCONCLUSIVE and is
  tracked separately).
- **Out of scope:** Admin-CLI-wide deprecation of `AnalysisClient`. Other
  commands still submit jobs and need the REST client; only the read-only
  recovery report is migrated here.

## SQLite target

Schema and location (from `ops/analysis-service/src/sow_analysis/storage/db.py`
and `workers/queue.py`):

- **Path inside the analysis container:** `CACHE_DIR / "jobs.db"` where
  `CACHE_DIR` defaults to `/cache` (`config.py`).
- **Volume:** named volume `analysis-cache:/cache`
  (`ops/analysis-service/docker-compose.yml`).
- **Driver:** `aiosqlite` in service, but admin-cli only needs the stdlib
  `sqlite3` module for a read-only query — no new heavy dependency required.
- **Table shape:**
  ```
  jobs(id TEXT PK, type TEXT, status TEXT, progress REAL, stage TEXT,
       error_message TEXT, request_json TEXT NOT NULL, result_json TEXT,
       created_at TEXT, updated_at TEXT, content_hash TEXT)
  ```
  with indexes on `status`, `content_hash`, `created_at`.
- **Purge behavior:** `JobStore.purge_old_jobs` (default `max_age_days=7`)
  issues a hard `DELETE FROM jobs WHERE status IN ('completed','failed','cancelled') AND created_at < ?`.
  Purged rows are **absent from the file**, not just hidden behind REST. A
  REST 404 is therefore equivalent to an SQLite `miss`. Direct SQLite does
  not recover purged rows — see "Non-fixes".

## Signals unlocked

These become available to the verdict matrix without any service-side change:

### From `result_json` (already in REST `JobResponse.result`, but currently discarded)

- `key_detected_at` — independent cross-check vs PG `recordings.key_detected_at`.
- `lrc_url` — cross-check vs PG `recordings.r2_lrc_url`; a mismatch is a
  strong, cheap bug signal (URL regenerated/staled without PG update).
- `lrc_source` — `youtube_transcript | qwen3_asr | whisper_asr | forced_alignment`.
  **Not stored in PG at all.** Useful to discriminate LRC regeneration paths
  (manual edits are not tagged `lrc_source`).
- `line_count` — sanity vs LRC content.
- `stems_url` / `vocals_dry_url` / `vocals_url` / `instrumental_url` —
  presence explains a post-analyze `updated_at` bump unrelated to visibility.

### From `request_json` (NOT exposed by REST at all)

- `youtube_url` — distinguishes transcript-based generation from ASR.
- `force` / `force_whisper` / `force_qwen3_asr` / `force_qwen3_asr` flags — a
  `force=True` re-run is a benign cause of a fresh `updated_at` bump and
  should not be flagged as a suspected bug revert.
- `lyrics_text` (snippet / hash) — for diagnostic display only; not a verdict input.
- `use_vocals_stem`, `language`, `song_title`, `whisper_model` — context only.

### From the row directly (already in REST, currently discarded)

- `created_at` — distinguishes a re-submit from a continuation.
- `stage`, `error_message` — surfaces failed LRC jobs that never wrote a result,
  turning a silently-missing `lrc_job_done` into an explained `failed` state.
- `status` — quickly classify `failed` / `cancelled` jobs without relying on a
  missing timestamp.

## Design

### New module

Add `ops/admin-cli/src/stream_of_worship/admin/services/analysis_sqlite.py`
(name chosen to avoid colliding with `services/analysis.py`, the REST client).

Responsibilities:

1. **Locate the SQLite file.** Read path from `AdminConfig` under a new field
   `analysis_sqlite_path` (see Config changes). Resolve symlinks; reject a
   missing file with a clear error pointing at the docker volume mount.
2. **Open read-only.** Always use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
   and `PRAGMA query_only=1` to make writes physically impossible from this
   client. No WAL write lock can be acquired; the analysis service's WAL
   writer is unaffected.
3. **Bulk lookup.** Single parameterized query:
   ```sql
   SELECT id, type, status, created_at, updated_at, stage, error_message,
          result_json, request_json, content_hash
   FROM jobs
   WHERE id IN (?, ?, ?, ...)
   ```
   built with the right number of placeholders. Returns `dict[job_id, JobRow]`.
4. **Parse helpers** (pure functions, no Pydantic required to keep
   admin-cli lightweight):
   - `_parse_ts(iso_str) -> datetime | None` (mirror of `_parse_iso_datetime`
     in `recover_visibility.py` — de-duplicate by importing).
   - `_parse_result_json(blob) -> dict | None` — defensive `json.loads` with
     try/except, returning `{}` on malformed/missing so callers can treat
     missing keys uniformly.
   - `_parse_request_json(blob) -> dict | None` — same.
5. **Dataclass result.** `AnalysisJobRow` exposing the surfaced fields:
   `job_id, type, status, created_at, updated_at, stage, error_message,
   result_json (parsed), request_json (parsed)`. No Pydantic models to avoid
   importing `sow_analysis` into admin-cli.

### Config changes (`AdminConfig`)

Add under a new `[analysis_sqlite]` (or extend existing `service`) section:

- `analysis_sqlite_path: str = ""` — host-side path to the `jobs.db` file
  (e.g. `/var/lib/sow/analysis-cache/jobs.db` when the named volume is bind-
  mounted, or wherever the operator placed a copy).
- `analysis_sqlite_copy_path: str = ""` — optional fallback; if set and the
  primary is missing, the report will copy the file here (via `shutil.copy2`)
  before opening. This is for the common case where the admin-cli runs on a
  host that cannot mount the named volume but can `docker cp` the file out
  on a cron. If unset, primary path is opened directly.
  > Note: copying a WAL-mode SQLite file via `shutil.copy2` does not capture
  > the WAL. The analysis service checkpoints periodically; for read-only
  > point-in-time recovery this is acceptable. If a fully-consistent snapshot
  > is needed, prefer a bind-mount of the named volume rather than a copy.

JSON shape in `config.json`:

```json
{
  "service": { "analysis_url": "http://localhost:8000" },
  "analysis_sqlite": {
    "path": "/var/lib/sow/analysis-cache/jobs.db",
    "copy_path": ""
  }
}
```

### Report changes (`recover_visibility.py`)

1. **Remove the REST path for analysis lookups in this report.** Delete
   `_lookup_analysis_job`, `_batch_lookup_analysis`, `ANALYSIS_LOOKUP_WORKERS`,
   and the `AnalysisClient` import used here. (Leave `AnalysisClient` itself
   intact — other commands use it.)
2. **Replace the batch call.** Inside `_run_report`, after computing the
   `all_job_ids` set, open the SQLite reader and do a single bulk lookup:
   ```python
   with AnalysisSqliteReader(config) as reader:
       analysis_rows = reader.lookup_many(list(all_job_ids))
   ```
   Because the cost is now a single indexed `IN (...)` query (no network),
   make the lookup **unconditional** — i.e. always executed, deprecating the
   `--with-analysis` opt-in. Keep the boolean as a no-op shim for one release
   to avoid breaking operators' scripts, printing a deprecation notice.
3. **Enrich `CandidateSignals`.** Add fields:
   - `lrc_job_status: str | None`
   - `lrc_job_stage: str | None`
   - `lrc_job_error: str | None`
   - `lrc_source: str | None` (from LRC job `result_json.lrc_source`)
   - `result_lrc_url: str | None` (from LRC job `result_json.lrc_url`)
   - `result_key_detected_at: datetime | None` (cross-check field)
   - `request_youtube_url: str | None` (LRC job)
   - `request_force: bool | None` (LRC job; covers `force`/`force_whisper`/`force_qwen3_asr`)
   - `analyze_job_status: str | None`
4. **Extend `_compute_verdict` (new branches, additive — do not weaken v2's matrix):**
   - If `lrc_job_status == "failed"` and `r2_lm` present and `db_updated_at`
     is recent → `LRC_JOB_FAILED` (distinct from `INCONCLUSIVE`) with
     recommendation `eyes-on (lrc failed)`. Removes a silent-missing case.
   - If `result_lrc_url` is set and non-null and **differs** from
     PG `recordings.r2_lrc_url` → `LRC_URL_DRIFT`, recommendation
     `set-visibility published (url drift)`. Strongest cheap signal.
   - If `request_force` is True for the LRC job and `analyze_bump` holds →
     `BENIGN_FORCE_RERUN` (not bug-revert), recommendation `—`.
   - If `lrc_source` indicates `youtube_transcript` and `manual_edit_after_autogen == "yes"`
     is otherwise ambiguous, bias toward `published` (transcript path is the
     "official" source; a manual edit on top is rare).
   - Keep the existing `key_detected_at` bump check, but additionally
     cross-check SQLite `result_json.key_detected_at` vs PG
     `recordings.key_detected_at`; if they disagree, emit a new diagnostic
     column `key_detected_at_drift = True` (informational; does not change
     verdict but is surfaced in CSV/table).
5. **New output columns / panel counters:** `lrc_source`, `lrc_job_status`,
   `result_lrc_url`, `request_force`, `key_detected_at_drift`.
6. **Error handling when SQLite unavailable.** If the file is missing or
   unreadable, print a clear `[red]` message naming the configured path and
   the `analysis_sqlite.path` config key, then exit non-zero. Do **not**
   silently fall back to REST — that would reintroduce the "forgot to
   configure / silent degradation" class of INCONCLUSIVE.

### Invocation

No new CLI flag required (the lookup is now always-on). Behavior preserved:
the report remains strictly read-only against both PG and SQLite.

## Migration / rollout

1. Operator binds the `analysis-cache` named volume to a host path readable
   by the admin-cli environment (e.g. in `docker-compose.yml` add a
   bind-mount, or run admin-cli inside a container that shares the volume).
2. Operator adds `[analysis_sqlite].path` to `config.json`.
3. Run the report; verify `INCONCLUSIVE` counter drops for rows whose job
   records still exist in SQLite (i.e. within the `purge_old_jobs` window).
4. (Out of scope here, tracked separately) Extend analysis-service retention
   or persist completion timestamps to PG to recover the post-7-day window.

## Non-fixes (explicitly out of scope)

- **Purged jobs remain INCONCLUSIVE.** A row deleted by `purge_old_jobs` is
  gone from the SQLite file just as it 404s over REST. Direct SQLite, REST,
  or any read path returns the same empty result. The durable fix is to
  either (a) raise `max_age_days` / disable purge, or (b) persist
  `lrc_job_completed_at` / `analysis_job_completed_at` / `lrc_source` onto
  `recordings` at job-completion time. That is a separate, larger change and
  is **not** what this spec delivers.
- **Artifact of thrown-away REST data.** Could be fixed without switching to
  SQLite by simply unpacking more of the existing `JobResponse.result`. This
  spec opts for the SQLite path because the network-cost win (single bulk
  query vs N round-trips) is what enables making the lookup always-on — and
  it additionally unlocks `request_json`, which REST never returns.
- **Analysis-service-internal coupling.** Reading the SQLite file directly
  couples admin-cli to the analysis-service on-disk schema (not its REST
  contract). This is judged acceptable for a read-only recovery tool because
  (a) the `jobs` table schema has only grown additively and never dropped
  columns historically (see the `_migrate_*` methods in `db.py`), and
  (b) the reader is isolated in one module so a schema change has a single
  blast radius. If the schema ever does break, the failure mode is a visible
  runtime error in one module, not silent mis-classification.

## Acceptance criteria

- Running `recover_visibility.py` no longer issues any HTTP request to the
  analysis service; analysis data comes from a single SQLite `IN (...)` query.
- The `SUSPECTED_POST_ANALYZE` / `INCONCLUSIVE` counters drop strictly for
  candidate rows whose `lrc_job_id`/`analysis_job_id` resolve to extant rows
  in `jobs.db` (within the 7-day purge window).
- New `LRC_URL_DRIFT`, `LRC_JOB_FAILED`, `BENIGN_FORCE_RERUN` verdicts classify
  rows previously bucketed as `INCONCLUSIVE`.
- No file is written and no SQLite write transaction is ever opened (verified
  by code review: `mode=ro` URI, `PRAGMA query_only=1`, no `INSERT`/`UPDATE`/
  `DELETE` strings in `analysis_sqlite.py`).
- Existing v2 matrix and CSV/TUI outputs remain valid; new columns are
  appended only (additive change so downstream scripts do not break).

## Open questions

1. Does the production analysis service run with `purge_old_jobs` at the
   default 7 days, or has retention already been raised? Need to confirm the
   effective retention window before claiming tighter INCONCLUSIVE coverage.
   → action: grep the analysis-service deployment config / compose env for
   `max_age_days` overrides before implementing.
2. Is the `analysis-cache` volume already bind-mounted on any operator host?
   If not, the rollout step (1) requires a compose change and a service
   restart, which is an ops-side dependency to flag.
3. Should `--with-analysis` be removed outright or kept as a deprecated
   no-op? Choice here is the latter (one-release deprecation). Confirm the
   team prefers a shim over a hard break.
