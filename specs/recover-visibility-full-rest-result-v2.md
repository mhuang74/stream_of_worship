# Recover `visibility_status` — Use Full REST Job Result — v2

## Status

**Planning only — do not implement.** This is a revision of
`specs/recover-visibility-full-rest-result-v1.md`. It keeps the v1
"no-refactor, REST-stays" approach for reducing `INCONCLUSIVE` verdicts but
introduces one structural change versus v1:

**v1 shipped three speculative verdict branches whose corroborating evidence
rested on bug-mechanism assumptions not yet validated against known-bug rows:**
`LRC_URL_DRIFT`, `BENIGN_STEM_BUMP`, `LRC_SOURCE_TRANSCRIPT`. v2 **demotes all
three to diagnostic-only signals** — they surface as new columns +
`debug_notes` entries but no longer alter the verdict or recommendation. v2
introduces an acceptance gate that re-promotes a branch to verdict-changing
status only after it has been empirically validated against a labeled set of
known-bug rows AND known-clean rows.

Decisions carried forward unchanged from v1 (per interview 2026-10 and review):
- `LRC_JOB_FAILED` and `LRC_JOB_STUCK` remain verdict-changing branches.
- Step 6 `--with-analysis`-off deprecation notice stays in scope.
- Directive SQLite (Option 1) remains shelved; only Option 2 ships.
- `failed` jobs carry `result_json = NULL` (confirmed service behavior).

## What changed versus v1 (delta summary)

| Area | v1 | v2 |
|---|---|---|
| `LRC_URL_DRIFT` | Verdict-changer (`set-visibility published (url drift)`) | Diagnostic column + `debug_notes` only; never changes verdict |
| `BENIGN_STEM_BUMP` | Verdict-changer (bumps `SUSPECTED_*` → benign `—`) | Diagnostic column + `debug_notes` only; never changes verdict |
| `LRC_SOURCE_TRANSCRIPT` | Verdict-changer (`SUSPECTED_BUG_REVERT (transcript source)`) | Diagnostic column + `debug_notes` only; never changes verdict |
| `KEY_DETECT_DRIFT` | Diagnostic-only | Unchanged: diagnostic-only |
| Branch mutual-exclusivity | Claim applied only to branches 1 vs 3 | Explicit per-branch matrix; documents all pairwise intersections |
| Acceptance criterion | "INCONCLUSIVE strictly lower; reclassified rows land in new buckets" | "INCONCLUSIVE strictly lower; reclassification only via `LRC_JOB_FAILED` / `LRC_JOB_STUCK`; diagnostic columns populated but must NOT change verdict on any row" |
| Diagnostic columns | Mentioned in Step 5 | Sized-down: `lrc_source`, `lrc_status`, `lrc_error`, `lrc_result_url`, `analyze_status`, `analyze_error` + derived flags `lrc_url_drift`, `stem_bump_attributable_to_stems`, `transcript_source_bias` (all bool/str diagnostic only) |
| Step 6 deprecation notice | In scope | Unchanged — in scope |

## Motivation (unchanged)

The current `_lookup_analysis_job` discards everything except `updated_at`:

```python
job = analysis_client.get_job(job_id)
return (job.updated_at, None)
```

Surfacing the discarded `JobInfo` / `AnalysisResult` fields reclassifies rows
that today fall through to the `INCONCLUSIVE` fallback without any new
dependency, transport change, or service-side work. The full REST-response
table from v1 (field-by-field "currently used?" mapping) is reproduced in
Appendix A for reference; it is unchanged.

## Scope

- **In scope:** Inside `recover_visibility.py` only, expand
  `_lookup_analysis_job` / `_batch_lookup_analysis` to return the full
  `JobInfo`; extend `CandidateSignals` and `_compute_verdict` to consume the
  previously-discarded fields.
- **In scope:** Add the small set of fields the service `JobResult` already
  returns (`line_count`, `vocals_dry_url`, `vocals_url`, `instrumental_url`)
  but the admin-cli `AnalysisResult` dataclass does not yet model — so the
  parser silently drops them.
- **In scope:** Two verdict-changing branches: `LRC_JOB_FAILED`,
  `LRC_JOB_STUCK`. (down from five in v1.)
- **In scope:** Six diagnostic-only columns / derived flags surfacing the
  discarded fields without altering verdict.
- **Out of scope:** Verdict-changing use of `LRC_URL_DRIFT`,
  `BENIGN_STEM_BUMP`, `LRC_SOURCE_TRANSCRIPT`. These are **deferred to v3**
  pending empirical validation (see Validation gate).
- **Out of scope:** Switching the transport to direct SQLite (Option 1).
- **Out of scope:** Service-side changes (`JobResponse` shape, endpoint
  surface, retention policy).
- **Out of scope:** Persisting `lrc_job_completed_at` / `lrc_source` /
  `analysis_job_completed_at` onto PostgreSQL (the durable fix for
  purged-row INCONCLUSIVE; tracked separately).
- **Out of scope:** HTTP performance work (batch endpoint, keep-alive pooling).
  The existing 10-worker `_batch_lookup_analysis` remains.

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
with the full `JobInfo` (or `Optional[JobInfo]`), and let
`_batch_lookup_analysis` return `dict[str, Optional[JobInfo]]`. Preserve the
per-job error note as a separate `dict[str, str]` so it is not lost
(`"job purged — relying on R2/DB timestamps only"` etc. remains the
user-facing message, derived from `AnalysisServiceError.status_code == 404`).

Concretely, the loop in `_run_report` already resolves `lrc_jid` and
`ana_jid`; it then reads `analysis_results[jid]` for the `updated_at` only.
Change the consumer to read the whole `JobInfo`:

```python
lrc_job: Optional[JobInfo] = analysis_results.get(lrc_jid)
ana_job: Optional[JobInfo] = analysis_results.get(ana_jid)
```

and populate the enriched `CandidateSignals` (Step 3).

### Step 3 — Extend `CandidateSignals`

Add the surfaced fields. v2 keeps the same surface as v1 Step 3 (the four
new `AnalysisResult` fields didn't make it into CandidateSignals in v1 either;
they are surfaced only via `JobInfo.result` access in Step 4 diagnostic
computation). The dataclass:

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

    # from LRC JobInfo (REST, formerly discarded)
    lrc_status: Optional[str]
    lrc_stage: Optional[str]
    lrc_error: Optional[str]
    lrc_created_at: Optional[datetime]
    lrc_result_url: Optional[str]               # AnalysisResult.lrc_url
    lrc_source: Optional[str]
    lrc_line_count: Optional[int]
    lrc_result_key_detected_at: Optional[datetime]   # not present on lrc jobs in practice; None

    # from Analysis JobInfo (REST, formerly discarded)
    analyze_status: Optional[str]
    analyze_stage: Optional[str]
    analyze_error: Optional[str]
    analyze_result_key_detected_at: Optional[datetime]  # cross-check vs PG
    analyze_stems_present: Optional[bool]       # stems_url non-null

    # PG-side reference values for diagnostic drift comparison
    pg_r2_lrc_url: Optional[str]
```

### Step 4 — Refine `_compute_verdict`

v2 splits the work into two well-defined layers:

**Layer A — Verdict branches (may change the verdict).** Only two:
1. **`LRC_JOB_FAILED`** — `lrc_status == "failed"` (or
   `lrc_status == "cancelled"`). Recommendation:
   `eyes-on (lrc {status})`. Removes the silent-missing case where
   `lrc_job_done is None` because the job errored rather than completed.
   `error_message` is surfaced in a new diagnostic column.
2. **`LRC_JOB_STUCK`** — `lrc_status in ("processing", "queued", "waiting")`
   AND `lrc_created_at` is older than a threshold (default 12h).
   Recommendation: `restart analysis worker`. Distinguishes a
   genuinely-missing completion from a worker that died mid-job.

Both Layer-A branches are inserted **before** the final `INCONCLUSIVE`
fallback so they only reclassify rows that would otherwise be inconclusive.
Existing `OK_FRESH_LRC` / `SUSPECTED_BUG_REVERT` / `NO_SIGNAL_POST_ANALYZE` /
`SUSPECTED_POST_ANALYZE` outcomes are untouched; Layer-A fires only when
today's logic would have returned `INCONCLUSIVE` and the surfaced job-status
field explains the missing `lrc_job_done`.

**Layer B — Diagnostic-only signals (never alter verdict).** Three signals
collected for every row and surfaced in new columns / `debug_notes`, but they
do NOT influence `_compute_verdict`'s return:

- `lrc_url_drift` (bool): `lrc_result_url` is set, non-null, and differs from
  PG `pg_r2_lrc_url`. Surfaced in `debug_notes` as
  `"lrc_url_drift: svc=<X> pg=<Y>"`. **Does NOT change the verdict.** v1
  promoted this to a `set-visibility published (url drift)` recommendation; the
  bug-mechanism link (does the bug rewrite `r2_lrc_url`?) was not established
  in review and is treated as a hypothesis until validated.
- `stem_bump_attributable_to_stems` (bool): `analyze_bump` holds AND
  `analyze_stems_present` is True. Surfaced in `debug_notes` as
  `"stem_bump_attributable_to_stems: true (stems_url present)"`. **Does NOT**
  demote a `SUSPECTED_*` row to benign. `stems_url` non-null only proves stems
  existed at some point, not that they wrote at this `updated_at` bump —
  insufficient to time-attribute.
- `transcript_source_bias` (bool): `manual_edit_after_autogen == "yes"` is
  ambiguous AND `lrc_source == "youtube_transcript"`. Surfaced in
  `debug_notes` as `"transcript_source_bias: manual_edit=yes, source=youtube_transcript"`.
  **Does NOT** reclassify. v1's "manual edit on top of youtube_transcript is
  rare" was a behavioral guess not tied to the bug mechanism.

**Layer-B ordering and mutual exclusivity** (tightened from v1):

| Pair | Mutual exclusivity | Notes |
|---|---|---|
| `LRC_JOB_FAILED` (A.1) vs `lrc_url_drift` (B) | exclusive in practice | `failed` jobs carry `result_json = NULL`, so `lrc_result_url` is `None` and drift cannot fire |
| `LRC_JOB_STUCK` (A.2) vs `lrc_url_drift` (B) | **not** exclusive | A stuck `processing` job may have a partial result already written; drift can still be observed as a diagnostic signal even while Layer-A returns the stuck verdict |
| `lrc_url_drift` (B) vs `stem_bump_attributable_to_stems` (B) | independent | Both diagnostics compute independently; both can be true on the same row. v1's B.3 (`BENIGN_STEM_BUMP`) required `lrc_result_url == pg_r2_lrc_url` (no drift), but in v2 both signals are reported regardless |
| `lrc_url_drift` (B) vs `transcript_source_bias` (B) | independent | Both reported independently |
| `stem_bump_attributable_to_stems` (B) vs `transcript_source_bias` (B) | independent | Both reported independently |

When Layer-B signals co-fire, append each on its own `debug_notes` line so
downstream diff tooling can detect individual signals.

**`KEY_DETECT_DRIFT`** (unchanged from v1, listed here for completeness):
informational only. `analyze_result_key_detected_at` is set and differs from
PG `recordings.key_detected_at`. Sets a `key_detected_at_drift` diagnostic
column to `true` and appends to `debug_notes`. Does not alter recommendation.

### Step 5 — Output surface changes (additive only)

- **TUI table:** append columns `lrc_source`, `lrc_status`, `lrc_result_url`,
  `key_detected_at_drift`, `lrc_url_drift`,
  `stem_bump_attributable_to_stems`, `transcript_source_bias`. Keep existing
  columns.
- **CSV:** append the same fields to `fieldnames` in `_print_csv`. Existing
  consumers keyed on column name keep working (DictReader extras ignored).
  **Reminder:** `_print_csv` uses `extrasaction="ignore"`; new keys passed in
  row dicts but not added to `fieldnames` are silently dropped, so the
  `fieldnames` list MUST be updated in lock-step with new debug keys
  populated by `_compute_verdict`.
- **Panel counters:** add `LRC_JOB_FAILED`, `LRC_JOB_STUCK` to the summary
  count line. (Diagnostic columns are not summed to a verdict bucket.)
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

The notice is a single dim stderr line emitted once, after the panel:

```
[dim]INCONCLUSIVE rows present; re-run with --with-analysis to surface
service-side job status and reduce inconclusive count.[/dim]
```

## Validation gate (v3 re-promotion criteria)

A diagnostic signal from Layer B may be re-promoted to a verdict-changing
branch in v3 only after **both**:

1. **Labeled-set validation.** Run the v2 report against a labeled set
   containing:
   - known-bug rows (rows confirmed reverted by the audio-batch bug, by
     inspecting audit logs / manual verification), AND
   - known-clean rows (rows confirmed NOT reverted, by `visibility_status`
     remaining `published` through the bug window).
   The signal's precision and recall against this set must be reported.
2. **Mechanism link.** A documented causal chain showing why the bug produces
   the signal. For example, for `lrc_url_drift` to be re-promoted, the bug
   implementation must be shown to rewrite `r2_lrc_url` (or to leave a stale
   value that the service never refreshes), with a reference to the
   responsible code path. Behavioral guesses ("manual edits are rare") do not
   qualify as a mechanism link.

Bundles blocked from re-promotion by the gate:
- `lrc_url_drift` — needs mechanism link (does the bug touch `r2_lrc_url`?).
- `stem_bump_attributable_to_stems` — needs mechanism link + time-attribution
  (stems written within the bump window, not just present).
- `transcript_source_bias` — needs labeled-set precision/recall reporting +
  mechanism link (why `youtube_transcript` source correlates with non-revert).

## Non-fixes (explicit)

- **Purged jobs remain INCONCLUSIVE.** A REST 404 is exactly as empty as a
  missing SQLite row. Surfacing more fields only helps when the job still
  exists on the service.
- **No `request_json` disambiguation.** The benign force-rerun verdict
  shortcut in Option 1 cannot be implemented from REST — the service
  `JobResponse` does not surface the request payload. Those rows stay
  INCONCLUSIVE under Option 2.
- **No transport/performance win.** Same N round-trips, same 10-worker pool,
  same `SOW_ANALYSIS_API_KEY` requirement.

## Acceptance criteria

**Baseline (interview 2026-10):** the last run produced **31 INCONCLUSIVE**
rows.

- `_lookup_analysis_job` returns the full `JobInfo` (or `None` + note on 404);
  no consumed field of `JobInfo.result` is left on the floor.
- Admin-cli `AnalysisResult` models `line_count`, `vocals_dry_url`,
  `vocals_url`, `instrumental_url`; `_parse_job_response` populates them.
- **Verdict invariants:**
  - For candidate rows whose `lrc_job_id` / `analysis_job_id` still resolve on
    the service (within the ~7-day purge window), the `INCONCLUSIVE` counter
    is strictly lower than today; reclassified rows land **only** in
    `LRC_JOB_FAILED` or `LRC_JOB_STUCK`.
  - Layer-B signals (`lrc_url_drift`, `stem_bump_attributable_to_stems`,
    `transcript_source_bias`, `key_detected_at_drift`) are populated on rows
    where the underlying condition holds, but the verdict on those rows
    **must be unchanged from what it would have been if the Layer-B signals
    were `None`/`false`**. Verified by running the report twice (with and
    without Layer-B computation) and diffing verdicts.
- Existing v2 verdicts (`OK_FRESH_LRC`, `SUSPECTED_BUG_REVERT`,
  `SUSPECTED_POST_ANALYZE`, `NO_SIGNAL_POST_ANALYZE`) are unchanged in both
  semantics and counts for rows that do not match `LRC_JOB_FAILED` /
  `LRC_JOB_STUCK`.
- CSV column set is a superset of the v1 column set (no renames, no removals);
  a v1 consumer reading the CSV keeps working.
- No new dependency added to `ops/admin-cli` `pyproject.toml`; the change is
  confined to `recover_visibility.py` and one additive patch to
  `services/analysis.py`.
- The remaining `INCONCLUSIVE` rows after this change must each correspond to a
  REST 404 (purged job) — there should be no row that *could* have been
  resolved by `LRC_JOB_FAILED` / `LRC_JOB_STUCK` but was missed. Layer-B
  signals on `INCONCLUSIVE` rows are surfaced in `debug_notes` even though the
  verdict does not change (this is the seed data for the v3 validation gate).

## Resolved questions (interview 2026-10)

- **Baseline metric.** Last run produced **31 INCONCLUSIVE** rows; acceptance
  compares against that count.
- **`LRC_JOB_STUCK` threshold.** Locked at **12 hours**; longest legitimate
  `lrc` job fits well under that.
- **Failed-job `result` presence.** Confirmed: `failed` jobs carry
  `result_json = NULL`. `LRC_JOB_FAILED` keys off `status` alone; `lrc_url_drift`
  (Layer B) cannot fire on a failed job.
- **Transport scope.** Option 1 (direct SQLite) is **shelved**; only Option 2
  ships. The network cost of `--with-analysis` is accepted as-is.

## Review decisions (this iteration)

- **Diagnostic demotion** of `LRC_URL_DRIFT` / `BENIGN_STEM_BUMP` /
  `LRC_SOURCE_TRANSCRIPT`: chosen over keep-as-proposed and over drop-
  entirely. Reasons:
  - Keep-as-proposed would emit *recommendations* (`set-visibility published`,
    benign treatment, source-biased reclassification) on rows whose underlying
    hypothesis has not been validated against the actual bug path; risk of
    mass false reclassification of the 31 INCONCLUSIVE rows in the wrong
    direction.
  - Drop-entirely discards the seed data needed to validate the hypotheses at
    all; the diagnostic columns let v3's validation gate run on real runs
    rather than ad-hoc queries.
- **Keep both** the Step 6 deprecation notice and the `LRC_JOB_STUCK` branch:
  - The deprecation notice is a single dim stderr line; cost-output ratio is
    acceptable and it directly addresses the "forgot to pass `--with-analysis`"
    INCONCLUSIVE source. Kept.
  - `LRC_JOB_STUCK` is a legitimate verdict branch: a stuck job IS the
    explanation for a missing `lrc_job_done`, distinct from a purged job.
    Kept despite its introducing a 12h threshold; threshold remains locked
    per interview.

## Open questions

1. Should `lrc_url_drift` (Layer B) compare normalized URLs (strip query,
   trailing slash) or exact strings? Exact is safer for detecting the bug
   (which writes stale URLs verbatim); normalization risks false negatives.
   v2 plan assumes exact string compare for the diagnostic; v3 re-promotion
   revisit confirms preference before any verdict-changing use.
2. The `LRC_JOB_STUCK` 12h threshold is a single magic number. Should it be
   exposed as a CLI option (`--stuck-threshold-hours`, default 12) for tunability
   without a code change, or remain a module constant? v2 plan assumes a
   module constant; confirm preference before implementing.

## Appendix A — REST response field table (unchanged from v1)

From `JobInfo` / `AnalysisResult`
(`ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`) and the
service-side `JobResult` (`ops/analysis-service/src/sow_analysis/models.py`):

| Field | Source | Currently used? | Useful for verdict? |
|---|---|---|---|
| `JobInfo.updated_at` | REST | used | LRC/analyze job completion time |
| `JobInfo.status` | REST | discarded | `failed` / `cancelled` explains a missing `updated_at` and a missing result |
| `JobInfo.stage` | REST | discarded | surfaces "in progress" jobs (stale `processing` after a crash) |
| `JobInfo.error_message` | REST | discarded | distinguishes "purged" from "failed with reason" |
| `JobInfo.created_at` | REST | discarded | distinguishes re-submit from continuation; bounds `updated_at`; used by `LRC_JOB_STUCK` threshold |
| `AnalysisResult.key_detected_at` | REST `result` | discarded (only PG `r.key_detected_at` is used) | cross-check vs PG; drift = diagnostic signal (Layer B `key_detected_at_drift`) |
| `AnalysisResult.lrc_url` | REST `result` | discarded | cross-check vs PG `r.r2_lrc_url`; mismatch = Layer-B `lrc_url_drift` diagnostic (verdict-unchanging in v2) |
| `AnalysisResult.lrc_source` | REST `result` | discarded | `youtube_transcript \| qwen3_asr \| whisper_asr \| forced_alignment` — not in PG; Layer-B `transcript_source_bias` diagnostic |
| `AnalysisResult.line_count` | service `result` | not modeled in admin AnalysisResult | sanity vs LRC content / manual-edit detection (v2 models and surfaces; no verdict use) |
| `AnalysisResult.stems_url` | REST `result` | discarded | presence ⇒ `stem_bump_attributable_to_stems` Layer-B diagnostic |
| `AnalysisResult.vocals_dry_url` / `vocals_url` / `instrumental_url` | service `result` | not modeled in admin AnalysisResult | modeled and surfaced in v2; no verdict use |
| `AnalysisResult.tempo_bpm` / `musical_key` / `key_confidence` | REST `result` | discarded | diagnostic display only |

### What REST does NOT give you (Option 1 territory, out of scope here)

- `request_json` payload (`force` flags, `youtube_url`, `lyrics_text`) — the
  service `JobResponse` deliberately omits the request. The "benign
  force-rerun" disambiguation that Option 1 enables via
  `request_json.force` is **not achievable from REST alone**. Stays an
  INCONCLUSIVE fallback until Option 1 (or a service-side request-surface
  change) lands.
