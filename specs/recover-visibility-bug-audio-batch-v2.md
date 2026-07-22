# Recover `visibility_status` Reverted by `audio batch` Bug — v2 (Post-Analyze-Bump Recovery)

## Status

**Planning only — do not implement yet.** This is a v2 follow-up to
`specs/recover-visibility-bug-audio-batch.md` (v1), fixing a fatal assumption
invalidated by a recent all-recordings `FAST_ANALYZE`/`ANALYZE` run.

## Why v2 is needed (assumption re-validation result)

### The v1 assumption

v1's signal (in `recover_visibility.py`, `_compute_verdict`) treats
`recordings.updated_at` as `T_bug` — the bug-fire time — and computes
`delta_h = db_updated_at − r2_last_modified`. The verdict matrix keys off
`delta_h` alone (`OK_FRESH_LRC` / `SUSPECTED_BUG_REVERT` / `INCONCLUSIVE`),
with `--with-analysis` only adding a cross-check column.

### Why it is wrong after the analyze run

`recordings.updated_at` is bumped by a generic `BEFORE UPDATE` trigger
(`db/schema.py:188-194`) on **every** write path, not just the bug path.
Relevant writers that bump `updated_at = NOW()` without touching
`visibility_status` (all in `db/client.py`):

- `update_recording_analysis` (SQL at `1005-1060`) — writes `analysis_status`,
  `key_detected_at`, etc. Unconditional `updated_at = NOW()`. The safe LRC path
  passes `visibility_status=None` → `COALESCE` preserves `published`, but
  `updated_at` still jumps to `T_analyze`.
- `update_recording_status` (`917-918`)
- `update_recording_lrc` (`1116-1132`)
- `update_recording_download` (`1165-1167`)
- `update_recording_duration` (`1211-1213`)
- `update_recording_r2_url` (`1234-1236`)
- `update_recording_youtube_url` (`1188-1190`)
- `update_recording_visibility` (`1269-1271`) — the only one that actually
  changes visibility; intentionally.
- `set_hold` / `set_review` helpers (`1311-1312`, `1381-1382`).

The recent all-recordings `FAST_ANALYZE`/`ANALYZE` run fired
`update_recording_analysis` on essentially every recording. Concrete
consequences:

1. `db_updated_at` is now `T_analyze` for essentially every candidate, **not**
   `T_bug`. The original `T_bug` value is overwritten and **unrecoverable from
   the live DB**.
2. v1's clustering mitigation ("bug run produces a tight batch") collapses —
   today *everything* clusters around `T_analyze`, hiding the genuine
   bug cluster.
3. `delta_h = T_analyze − T_manual_edit` exceeds the `1h..1440h` window purely
   because a published LRC was edited before the analyze window — so any
   legitimately-`review` recording whose LRC predates the analyze run gets a
   false `SUSPECTED_BUG_REVERT` flag. False-positive rate jumps.
4. `--until <pre-analyze>` cannot help: no candidate qualifies anymore (their
   `updated_at` is now post-analyze). R2 versioning cannot help either: the
   bug `_confirm_r2_lrc` (`audio.py:6471-6497`) never re-uploads the LRC, so
   the R2 object didn't change and `r2_last_modified` is unchanged.

### What survives the bump

- **`r2_last_modified`** still equals the manual-edit time whenever a human
  edited the LRC via the admin editor (`editor/upload.py`). The analyze run
  does not touch the LRC object.
- **`recordings.analysis_job_id`** exists (`schema.py` column `analysis_job_id`)
  and is resolvable via `AnalysisClient.get_job()` → `JobInfo.updated_at`
  (job completion ≈ `T_analyze`).
- **`recordings.key_detected_at`** (timestamptz) is written by
  `update_recording_analysis` and ≈ `T_analyze` when the key-detection branch
  runs.
- **`recordings.lrc_job_id`** + `AnalysisClient.get_job()` → LRC job
  completion (`lrc_job.updated_at`), independent of `recordings.updated_at`.

These give us loss-less signals that v1 ignores or treats as secondary.

## Goal

Produce a corrected dry-run report that:

1. Detects the analyze-bump (demote `delta_h` to informational only).
2. Switches the primary bug signal to the R2-vs-LRC-job ordering, which is
   `updated_at`-independent.
3. Preserves v1's read-only / no-code-fix / dry-run-only contract.

## New signal model (verdict inputs)

For each candidate (`visibility_status='review' AND lrc_status='completed' AND
r2_lrc_url IS NOT NULL AND deleted_at IS NULL`):

1. `r2_lm` = R2 `head_object` LastModified for `{hash_prefix}/lyrics.lrc`
   (`R2Client.get_lrc_identity`).
2. `lrc_job_done` = `AnalysisClient.get_job(lrc_job_id).updated_at` when
   `status='completed'` (tolerate 404 → `lrc_job_done = None`).
3. `analyze_job_done` = `AnalysisClient.get_job(analysis_job_id).updated_at`
   when `status='completed'` (tolerate 404 → `None`). Requires the new
   `analysis_job_id` column in the candidate SELECT.
4. `key_detected_at` = the DB timestamptz (free, no service call).
5. `db_updated_at` = current (possibly analyze-bumped) recordings.updated_at.
6. `delta_h` = `db_updated_at − r2_lm` in hours (**informational only**).

### Derived booleans

- **`analyze_bump`** =
  `|db_updated_at − analyze_job_done| <= BUMP_TOLERANCE_S`
  (default 60s) when `analyze_job_done` is known, **OR**
  `|db_updated_at − key_detected_at| <= BUMP_TOLERANCE_S` (fallback when
  analysis service job is purged and `key_detected_at IS NOT NULL`).
  `BUMP_TOLERANCE_S` is a CLI flag (default 60) because the DB trigger and the
  analysis service clock are not the same host; allow the admin to widen it.
- **`/manual_edit_after_autogen/`** =
  `lrc_job_done IS NOT NULL AND r2_lm IS NOT NULL AND lrc_job_done < r2_lm`
  (manual edit produced the current R2 file after the auto-generated one).
  Requires `--with-analysis` (off by default). Without it, the boolean is
  `UNKNOWN`.

## New verdict matrix

Replaces v1's `_compute_verdict(delta_h, …)`.

| Conditions | Verdict | Recommendation |
|---|---|---|
| `r2_lm` missing/unparseable, or `db_updated_at` invalid | `INCONCLUSIVE` | eyes-on |
| `abs(delta_h) <= 0.1` (LRC just generated, same-tick write) | `OK_FRESH_LRC` | — |
| `/manual_edit_after_autogen/` true AND `analyze_bump` true | `SUSPECTED_BUG_REVERT` | `set-visibility published` |
| `/manual_edit_after_autogen/` true AND `analyze_bump` false | `SUSPECTED_BUG_REVERT` | `set-visibility published` (still smoking gun; bump flag informational) |
| `analyze_bump` true AND `manual_edit_after_autogen` UNKNOWN/false (no `--with-analysis`) | `SUSPECTED_POST_ANALYZE` | needs `--with-analysis` to resolve |
| `analyze_bump` true AND `manual_edit_after_autogen` false (`--with-analysis` says r2_lm <= lrc_job_done) | `NO_SIGNAL_POST_ANALYZE` | likely not bug-reverted |
| otherwise | `INCONCLUSIVE` | eyes-on |

Notes:

- `delta_h` is shown but no longer selects a verdict on its own.
- The intentional-revert false-positive class (admin deliberately ran
  `set-visibility review`) is unchanged from v1 — the ordering test cannot
  distinguish it. Mitigations remain cluster/`--since`/`--until` eyeballing.

## Implementation plan (file: `recover_visibility.py`)

### A. Candidate SELECT — extend columns

In `_build_candidate_query`, add to the SELECT list:

```sql
r.analysis_job_id,
r.key_detected_at
```

(`analysis_job_id` and `key_detected_at` both exist on `recordings`.) No other
SQL change.

### B. Extend `_batch_lookup_analysis` to fetch ANALYZE jobs too

- Rename concept: the same pool now resolves **two** job classes per candidate:
  the LRC job (`lrc_job_id`) and the ANALYZE job (`analysis_job_id`).
- `_lookup_analysis_job` is unchanged internally — it's job-id agnostic.
- In `_run_report`, after the R2 batch resolves, collect **both**
  `lrc_job_id` and `analysis_job_id` from each candidate into the lookup list
  (dedupe, skip empties). Today the code only collects `lrc_job_id` from
  `SUSPECTED_BUG_REVERT` rows up front, which presupposes the v1 delta-h
  verdict. v2 must collect both *for all candidates*, because the
  ordering-based verdict needs `lrc_job_done` regardless of `analyze_bump`.
- Guard `--with-analysis` gating: when off, skip all analysis-job lookups and
  fall back to `key_detected_at`-only bump detection + `manual_edit_after_autogen = UNKNOWN`.

### C. Rewrite `_compute_verdict` signature

Old:
```python
def _compute_verdict(delta_hours, min_h, max_h) -> tuple[str, str]
```

New — take a small dict/dataclass of resolved signals:
```python
@dataclass
class CandidateSignals:
    db_updated_at: Optional[datetime]
    r2_lm: Optional[datetime]
    lrc_job_done: Optional[datetime]   # None if --with-analysis off OR job purged
    analyze_job_done: Optional[datetime]
    key_detected_at: Optional[datetime]
    with_analysis: bool
    bump_tolerance_s: float

def _compute_verdict(sig: CandidateSignals) -> tuple[str, str, dict[str, Any]]:
    # returns (verdict, recommendation, debug_notes)
```

Logic per the matrix above; `debug_notes` carries the booleans
(`analyze_bump`, `manual_edit_after_autogen`, `analyze_bump_source` =
`analysis_job|key_detected_at|none`) for the table/CSV.

- `OK_FRESH_LRC`: `abs(delta_h) <= 0.1` (kept identical to v1).
- `INCONCLUSIVE`: missing timestamps.
- `analyze_bump`: compute from `analyze_job_done` first, `key_detected_at`
  fallback.
- `manual_edit_after_autogen`: only computable when `with_analysis` and
  `lrc_job_done` and `r2_lm` present.
- Final verdict per table.

### D. Output columns

Add (CSV and Rich table):

- `analysis_job_id` (context)
- `key_detected_at` (timestamp; informational)
- `analyze_job_done` (timestamp from service; informational)
- `analyze_bump` (bool, `yes|no|unknown`)
- `bump_source` (`analysis_job|key_detected_at|none`)
- `manual_edit_after_autogen` (`yes|no|unknown`)

Keep `delta_h` but recolor it as informational (dim, not the verdict color).
Demote `verdict` coloring so `SUSPECTED_POST_ANALYZE` is yellow and
`SUSPECTED_BUG_REVERT` is red (as v1).

### E. New CLI flag

- `--bump-tolerance-seconds` (default 60). Replaces reliance on
  `--min-delta-hours` as the dominant tuning knob for the analyze window.
  Keep `--min-delta-hours` / `--max-delta-hours` flags for back-compat but
  they now only gate the `OK_FRESH_LRC` window and historical
  `delta_h`-display filter, not the verdict.

### F. Summary panel

Update the footer counts:
```
Total | SUSPECTED_BUG_REVERT | SUSPECTED_POST_ANALYZE | OK_FRESH_LRC | INCONCLUSIVE | NO_SIGNAL_POST_ANALYZE
```
And print a one-line guidance: "Re-run with `--with-analysis` to resolve
`SUSPECTED_POST_ANALYZE` rows."

## Out of scope (carried from v1)

- No code fix to `audio.py:5458` / `audio.py:2820`
  (`visibility_status="review"` → `None`).
- No bulk `UPDATE recordings SET visibility_status='published'`.
- No new Admin CLI subcommand.
- No schema changes (no `visibility_status_updated_at`, no audit table) in
  this spec — `official-lrc-last-writer-wins-v3.md` may already track related
  work; check before scheduling a backfill.

## Risks & open questions

1. **`key_detected_at` only written on key-detection branch.** Some FAST-tier
   jobs skip it. When `--with-analysis` is off and `key_detected_at IS NULL`,
   we cannot detect the analyze bump at all → verdict falls to
   `INCONCLUSIVE` for those rows. Acceptable; admin re-runs with
   `--with-analysis`.
2. **`analysis_service` job retention.** If both `lrc_job_id` and
   `analysis_job_id` are purged (404) and `key_detected_at IS NULL`, the row is
   fully `INCONCLUSIVE`. No recovery possible without a pre-analyze DB
   snapshot (see workaround W4 in the re-validation) — out of scope here.
3. **`abs(delta_h) <= 0.1` legitimately-fresh-LRC carve-out** — spot-checked
   `audio.py:5430-5536`: `_handle_lrc_completion` on the `completed` path
   calls `_confirm_r2_lrc` which **only `head_object`s; it does NOT upload**.
   The R2 object is written by the analysis service worker at `T_gen_upload`;
   the poll loop detects completion ~one poll interval later at `T_handle` and
   only then writes the DB. So `OK_FRESH_LRC` fires because
   `T_handle − T_gen_upload < poll_interval` (typically < 6 min), *not* because
   the DB path uploads+writes in the same tick. **Post-analyze-bump this
   carve-out only fires for LRCs generated AFTER the last analyze run** — for
   LRCs generated before the analyze run, `db_updated_at = T_analyze` makes
   `delta_h` large and `OK_FRESH_LRC` never fires, so fresh-`review` (never
   published) recordings get misclassified as `SUSPECTED_BUG_REVERT`. This is
   an additional reason `delta_h` cannot be the primary signal post-bump; rely
   on the W2 ordering test, and treat `OK_FRESH_LRC` only as a fast-path for
   very-recent LRCs (since the last analyze run).
4. **Clock skew** between DB host and analysis service host could make the
   60s bump tolerance too tight or too loose. Make the flag user-tunable and
   document the trade-off; default 60s is a guess, validate against a known
   recently-analyzed row.

## References

- v1 spec: `specs/recover-visibility-bug-audio-batch.md`
- v1 impl: `ops/admin-cli/src/stream_of_worship/admin/commands/recover_visibility.py`
- Bug site 1: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:5430-5536`
- Bug site 2: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2811-2822`
- `_confirm_r2_lrc` (head-only, no re-upload): `.../commands/audio.py:6471-6497`
- `update_recording_analysis` (the analyze-bump writer):
  `.../db/client.py:952-1075`
- `update_recording_lrc` (COALESCE visibility): `.../db/client.py:1085-1138`
- `update_recording_visibility`: `.../db/client.py:1242-1276`
- `recordings` schema (incl. `analysis_job_id`, `key_detected_at`):
  `.../db/schema.py:38-90`
- `trg_recordings_updated_at` (trigger): `.../db/schema.py:188-194`
- `AnalysisClient.get_job` / `JobInfo`: `.../services/analysis.py:560-, 98-121`
- `R2Client.get_lrc_identity` (`R2ObjectIdentity`):
  `.../services/r2.py:458-484`
- Admin LRC editor save (manual-edit R2 upload): `.../editor/upload.py:184-279`
