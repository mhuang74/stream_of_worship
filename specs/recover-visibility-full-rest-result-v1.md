# Recover `visibility_status` — Use Full REST Job Result — v1

## Status

**Planning only — do not implement.** This is a follow-up plan to
`specs/recover-visibility-bug-audio-batch-v2.md`. It specifies a no-refactor
path to reduce `INCONCLUSIVE` verdicts: stop discarding the fields the analysis
service already returns in `JobResponse` and use them to refine the verdict
matrix, without touching the network transport (REST stays) or the service.

## Motivation

The current report
(`ops/admin-cli/src/stream_of_worship/admin/commands/recover_visibility.py`)
calls the analysis service REST endpoint and then throws away almost
everything it returns. Concretely, `_lookup_analysis_job` does:

```python
job = analysis_client.get_job(job_id)
return (job.updated_at, None)
```

The rest of `JobInfo` — `status`, `stage`, `error_message`, `created_at`,
`progress`, and the entire `result` object (`AnalysisResult`) — is discarded.

Investigation found that this discarded data includes signals that are
either (a) not mirrored to PostgreSQL at all, or (b) mirrored but never
compared. Surfacing them reclassifies rows that today fall through to the
`INCONCLUSIVE` fallback without any new dependency, transport change, or
service-side work.

This is the lower-risk companion to
`specs/recover-visibility-direct-sqlite-v1.md` (Option 1): Option 2 keeps the
existing REST transport and `AnalysisClient` intact, only changes how the
report consumes its response. **Decision (interview 2026-10): Option 1 is
shelved; only Option 2 will ship.** This spec is therefore self-contained and
not a precursor to a transport change — the network cost of `--with-analysis`
remains accepted.

## Scope

- **In scope:** Inside `recover_visibility.py` only, expand
  `_lookup_analysis_job` / `_batch_lookup_analysis` to return the full
  `JobInfo`; extend `CandidateSignals` and `_compute_verdict` to consume the
  previously-discarded fields.
- **In scope:** Add the small set of fields the service `JobResult` already
  returns (`line_count`, `vocals_dry_url`, `vocals_url`, `instrumental_url`)
  but the admin-cli `AnalysisResult` dataclass does not yet model — so the
  parser silently drops them.
- **Out of scope:** Switching the transport to direct SQLite (Option 1).
- **Out of scope:** Service-side changes: `JobResponse` shape, endpoint
  surface, retention policy.
- **Out of scope:** Persisting job-completion timestamps / `lrc_source` onto
  PostgreSQL (the durable fix for purged-row INCONCLUSIVE; tracked
  separately).
- **Out of scope:** HTTP performance work (batch endpoint, keep-alive
  pooling). The existing 10-worker `_batch_lookup_analysis` remains. If the
  always-on behavior proposed here makes latency unacceptable, that is the
  trigger for Option 1.

## What the REST response already contains (and the report discards)

From `JobInfo` / `AnalysisResult`
(`ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`) and the
service-side `JobResult`
(`ops/analysis-service/src/sow_analysis/models.py`):

| Field | Source | Currently used? | Useful for verdict? |
|---|---|---|---|
| `JobInfo.updated_at` | REST | ✅ used | LRC/analyze job completion time |
| `JobInfo.status` | REST | ❌ discarded | `failed` / `cancelled` explains a missing `updated_at` and a missing result |
| `JobInfo.stage` | REST | ❌ discarded | surfaces "in progress" jobs (stale `processing` after a crash) |
| `JobInfo.error_message` | REST | ❌ discarded | distinguishes "purged" from "failed with reason" |
| `JobInfo.created_at` | REST | ❌ discarded | distinguishes re-submit from continuation; bounds `updated_at` |
| `AnalysisResult.key_detected_at` | REST `result` | ❌ discarded (only PG `r.key_detected_at` is used) | cross-check vs PG; drift = strong bug signal |
| `AnalysisResult.lrc_url` | REST `result` | ❌ discarded | cross-check vs PG `r.r2_lrc_url`; mismatch = drift signal |
| `AnalysisResult.lrc_source` | REST `result` | ❌ discarded | `youtube_transcript \| qwen3_asr \| whisper_asr \| forced_alignment` — **not in PG at all**; new classification dimension |
| `AnalysisResult.line_count` | service `result` | ❌ not even modeled in admin AnalysisResult | sanity vs LRC content / manual-edit detection |
| `AnalysisResult.stems_url` | REST `result` | ❌ discarded | presence explains a post-analyze `updated_at` bump (stem separation writes) |
| `AnalysisResult.vocals_dry_url` / `vocals_url` / `instrumental_url` | service `result` | ❌ not modeled in admin AnalysisResult | same — stem-separation bump attribution |
| `AnalysisResult.tempo_bpm` / `musical_key` / `key_confidence` | REST `result` | ❌ discarded | diagnostic display only |

### What REST does NOT give you (Option 1 territory, out of scope here)

- `request_json` payload (`force` flags, `youtube_url`, `lyrics_text`) — the
  service `JobResponse` deliberately omits the request. The "benign
  force-rerun" disambiguation that Option 1 enables via `request_json.force`
  is **not achievable from REST alone**. This is the one verdict branch
  Option 2 cannot provide; it stays an INCONCLUSIVE fallback until Option 1
  (or a service-side request-surface change) lands.

## Design

### Step 1 — Extend the admin-cli `AnalysisResult` dataclass

`ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`:

Add four fields the service already returns but the parser drops:

```python
line_count: Optional[int] = None
vocals_dry_url: Optional[str] = None
vocals_url: Optional[str] = None
instrumental_url: Optional[str] = None
```

and wire them in `_parse_job_response`'s non-embedding branch:

```python
result = AnalysisResult(
    ...existing...,
    line_count=result_data.get("line_count"),
    vocals_dry_url=result_data.get("vocals_dry_url"),
    vocals_url=result_data.get("vocals_url"),
    instrumental_url=result_data.get("instrumental_url"),
)
```

No happy-path behavior changes — these are additive `Optional` fields.

### Step 2 — Stop discarding the response in `recover_visibility.py`

Replace the `(updated_at, note)` tuple return type of `_lookup_analysis_job`
with the full `JobInfo` (or `Optional[JobInfo]`), and let `_batch_lookup_analysis`
return `dict[str, Optional[JobInfo]]`. Preserve the per-job error note as a
separate `dict[str, str]` so it is not lost (`"job purged — relying on
R2/DB timestamps only"` etc. remains the user-facing message, derived from
`AnalysisServiceError.status_code == 404`).

Concretely, the loop in `_run_report` already resolves `lrc_jid` and
`ana_jid`; it then reads `analysis_results[jid]` for the `updated_at` only.
Change the consumer to read the whole `JobInfo`:

```python
lrc_job: Optional[JobInfo] = analysis_results.get(lrc_jid)
ana_job: Optional[JobInfo] = analysis_results.get(ana_jid)
```

and populate the enriched `CandidateSignals` (Step 3).

### Step 3 — Extend `CandidateSignals`

Add the surfaced fields:

```python
@dataclass
class CandidateSignals:
    db_updated_at: Optional[datetime]
    r2_lm: Optional[datetime]
    lrc_job_done: Optional[datetime]
    analyze_job_done: Optional[datetime]
    key_detected_at: Optional[datetime]          # PG
    with_analysis: bool
    bump_tolerance_s: float

    # NEW — from LRC JobInfo.result (REST, formerly discarded)
    lrc_status: Optional[str]                    # 'completed' | 'failed' | 'cancelled' | 'processing' | 'queued' | 'waiting'
    lrc_stage: Optional[str]
    lrc_error: Optional[str]
    lrc_created_at: Optional[datetime]
    lrc_result_url: Optional[str]               # AnalysisResult.lrc_url
    lrc_source: Optional[str]                   # AnalysisResult.lrc_source
    lrc_line_count: Optional[int]

    # NEW — from Analysis JobInfo.result (REST, formerly discarded)
    analyze_status: Optional[str]
    analyze_stage: Optional[str]
    analyze_error: Optional[str]
    analyze_result_key_detected_at: Optional[datetime]  # cross-check vs PG
    analyze_stems_present: Optional[bool]       # stems_url non-null
```

### Step 4 — Refine `_compute_verdict` (additive; do not weaken v2's matrix)

All new branches are inserted **before** the final `INCONCLUSIVE` fallback so
they only reclassify rows that would otherwise be inconclusive. Existing
`OK_FRESH_LRC` / `SUSPECTED_BUG_REVERT` / `NO_SIGNAL_POST_ANALYZE` /
`SUSPECTED_POST_ANALYZE` outcomes are untouched.

1. **`LRC_JOB_FAILED`** — `lrc_status == "failed"` (or `lrc_status == "cancelled"`).
   Recommendation: `eyes-on (lrc {status})`. Removes the silent-missing case
   where `lrc_job_done is None` because the job errored rather than completed.
   `error_message` is surfaced in a new diagnostic column.

2. **`LRC_JOB_STUCK`** — `lrc_status in ("processing", "queued", "waiting")`
   AND `lrc_created_at` is older than a threshold (default 12h). Recommendation:
   `restart analysis worker`. Distinguishes a genuinely-missing completion from
   a worker that died mid-job. (Threshold set per interview 2026-10: longest
   legitimate `lrc` job fits well under 12h.)

3. **`LRC_URL_DRIFT`** — `lrc_result_url` is set, non-null, and **differs** from
   PG `r.r2_lrc_url`. Recommendation: `set-visibility published (url drift)`.
   This is the strongest cheap signal: the service recorded a different LRC URL
   than PG holds, which is explained by the bug path writing PG stale while the
   LRC was regenerated. Branch fires before the generic `SUSPECTED_BUG_REVERT`
   paths only when drift is present; non-drift falls through normally.

4. **`KEY_DETECT_DRIFT`** — `analyze_result_key_detected_at` is set and differs
   from PG `recordings.key_detected_at`. This is **informational, not a verdict
   change**: set a new `key_detected_at_drift` column to `true` and append the
   drift to debug_notes. Does not alter recommendation (both timestamps are
   service-derived; drift is a marker, not proof of a visibility bug).

5. **`BENIGN_STEM_BUMP`** — `analyze_bump` holds AND `analyze_stems_present`
   is True AND `lrc_result_url` equals PG `r.r2_lrc_url` (no LRC drift).
   The `updated_at` bump is explained by stem separation writing back, not by
   a visibility toggle. Recommendation: `—`. Removes false `SUSPECTED_*`
   flags from stem-separation runs.

6. **`LRC_SOURCE_TRANSCRIPT`** — `manual_edit_after_autogen == "yes"` is
   ambiguous AND `lrc_source == "youtube_transcript"`. Bias toward `published`
   (the transcript source is the "official" path; a manual edit on top is
   rare). Replaces the `INCONCLUSIVE` fallback with
   `SUSPECTED_BUG_REVERT (transcript source)` in this subset only.

7. Default fallback remains `INCONCLUSIVE` for rows where:
   - the job is purged (REST 404 — Option 2 cannot recover these; see
     Non-fixes), or
   - `request_json.force` would be needed (Option 1 territory; shelved).

   **Confirmed service behavior (interview 2026-10):** `failed` jobs carry
   `result_json = NULL`. Therefore `LRC_JOB_FAILED` (branch 1) keys off
   `status` alone and will never co-fire with `LRC_URL_DRIFT` (branch 3); the
   branch ordering in steps 1–6 is preserved but the two are mutually
   exclusive in practice.

### Step 5 — Output surface changes (additive only)

- **TUI table:** append columns `lrc_source`, `lrc_status`, `lrc_result_url`,
  `key_detected_at_drift`. Keep existing columns; downstream width-calculation
  logic absorbs the additions since the table already wraps.
- **CSV:** append the same fields to `fieldnames`. Existing consumers keyed on
  column name keep working (DictReader extras ignored, missing fields → empty).
- **Panel counters:** add `LRC_JOB_FAILED`, `LRC_JOB_STUCK`, `LRC_URL_DRIFT`,
  `BENIGN_STEM_BUMP`, `LRC_SOURCE_TRANSCRIPT` to the summary count line.
- **Error note column:** surface `lrc_error` / `analyze_error` when the
  respective status is `failed`, replacing the opaque "analysis error: {e}"
  string.

### Step 6 — `--with-analysis` semantics

Keep the flag. Because Option 2 still pays the per-job HTTP cost, the
enrichment is **only applied when `--with-analysis` is set**, preserving the
current cost model. Document a deprecation-notice print when the flag is
**off** and `INCONCLUSIVE` rows exist, pointing at the flag — lowering the
"forgot to pass `--with-analysis`" INCONCLUSIVE source without forcing the
cost on by default. (Making it always-on is Option 1's job.)

## Non-fixes (explicit)

- **Purged jobs remain INCONCLUSIVE.** A REST 404 is exactly as empty as a
  missing SQLite row. Surfacing more fields only helps when the job still
  exists on the service. The durable fix (persist
  `lrc_job_completed_at` / `lrc_source` / `analysis_job_completed_at` onto
  `recordings` at job-completion time, or extend `purge_old_jobs` retention)
  is out of scope.
- **No `request_json` disambiguation.** The benign force-rerun verdict
  (`request_force` shortcut in Option 1) cannot be implemented from REST — the
  service `JobResponse` does not surface the request payload. Those rows stay
  INCONCLUSIVE under Option 2.
- **No transport/performance win.** Same N round-trips, same 10-worker pool,
  same `SOW_ANALYSIS_API_KEY` requirement. Option 2 is purely about
  consumption of the response. If the always-on behavior proposed in Option 1
  is desired, ship it as a follow-up to this one.

## Acceptance criteria

**Baseline (interview 2026-10):** the last run produced **31 INCONCLUSIVE**
rows. Acceptance is measured against that count: post-implementation, the
same candidate set must produce strictly fewer `INCONCLUSIVE` verdicts, with
the reclassified rows landing in the new buckets (`LRC_JOB_FAILED`,
`LRC_JOB_STUCK`, `LRC_URL_DRIFT`, `BENIGN_STEM_BUMP`, `LRC_SOURCE_TRANSCRIPT`).
The before/after comparison is done by diffing the CSV output of a pre- and
post-implementation run against the same `--since`/`--until`/`--album` filter.
The remaining `INCONCLUSIVE` rows after this change must each correspond to a
REST 404 (purged job) — there should be no row that *could* have been resolved
by the new branches but was missed.

- `_lookup_analysis_job` returns the full `JobInfo` (or `None` + note on 404);
  no consumed field of `JobInfo.result` is left on the floor.
- Admin-cli `AnalysisResult` models `line_count`, `vocals_dry_url`,
  `vocals_url`, `instrumental_url`; `_parse_job_response` populates them.
- For candidate rows whose `lrc_job_id` / `analysis_job_id` still resolve on
  the service (within the ~7-day purge window), the `INCONCLUSIVE` counter is
  strictly lower than today; the reclassified rows land in
  `LRC_JOB_FAILED` / `LRC_JOB_STUCK` / `LRC_URL_DRIFT` / `BENIGN_STEM_BUMP` /
  `LRC_SOURCE_TRANSCRIPT`.
- Existing v2 verdicts (`OK_FRESH_LRC`, `SUSPECTED_BUG_REVERT`,
  `SUSPECTED_POST_ANALYZE`, `NO_SIGNAL_POST_ANALYZE`) are unchanged in both
  semantics and counts for rows that do not match a new branch.
- CSV column set is a superset of the v2 column set (no renames, no removals);
  a v2 consumer reading the CSV keeps working.
- No new dependency added to `ops/admin-cli` `pyproject.toml`; the change is
  confined to `recover_visibility.py` and one additive patch to
  `services/analysis.py`.

## Resolved questions (interview 2026-10)

- **Baseline metric.** Last run produced **31 INCONCLUSIVE** rows; acceptance
  compares against that count (see Acceptance criteria above).
- **`LRC_JOB_STUCK` threshold.** Locked at **12 hours**; longest legitimate
  `lrc` job fits well under that.
- **Failed-job `result` presence.** Confirmed: `failed` jobs carry
  `result_json = NULL`. `LRC_JOB_FAILED` keys off `status` alone; `LRC_URL_DRIFT`
  never co-fires with it.
- **Transport scope.** Option 1 (direct SQLite) is **shelved**; only Option 2
  ships. The network cost of `--with-analysis` is accepted as-is.

## Open questions

1. Should `LRC_URL_DRIFT` compare normalized URLs (strip query, trailing slash)
   or exact strings? Exact is safer for detecting the bug (which writes stale
   URLs verbatim); normalization risks false negatives. Plan assumes exact
   string compare; confirm before implementing by inspecting a sample of
   `r.r2_lrc_url` vs `result_json.lrc_url` pairs.
2. Should the `--with-analysis`-off deprecation notice (Step 6) be loud
   (panel) or quiet (stderr dim)? Plan assumes a single stderr dim line;
   confirm preference.
