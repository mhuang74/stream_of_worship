# audio batch: `--all-steps` intelligent backfill (v3 — hardened merge)

## Goal

`sow-admin audio batch --all-steps --album X` is the ONE command that fills
everything missing and re-generates anything whose input data changed — no
thinking about which recordings lack structured lyrics, LRC, component data,
or recording-level theme/posture.

This is v3: v2's base design (kept `--all-steps` includes `backfill_lyrics`,
kept `newly_backfilled` auto re-identify) hardened with glm53flash's
correctness fixes and resolving every defect found in the v2 review.

## Use cases (single invocation across a mixed album)

1. **New song full pipeline**: catalog song without recording → recording
   downloaded (structured lyrics fetched during download) → LRC generated →
   recording analyzed (fast tier) → embeddings generated → components
   identified (structured-lyrics-backed) + theme/posture classified →
   recording-level theme/posture aggregated.

2. **Re-identify on changed input**: existing song missing structured lyrics
   with inaccurate component metadata → structured lyrics backfilled, then
   components re-identified via structured lyrics (input data changed →
   re-submit even when existing components carry theme/posture, via the
   run-local `newly_backfilled` set — no `--force` needed for `--all-steps`).

3. **Backfill missing metadata**: existing song with structured lyrics and
   component-level theme/posture but missing recording-level theme/posture
   aggregate → recording-level theme/posture backfilled from existing
   component data without re-submitting. Existing song missing component-level
   theme/posture → components submitted.

## Confirmed design decisions

- **Analysis tier**: fast (default). Components step uses structured lyrics +
  LRC for segmentation; analysis service computes beats on-demand for
  downbeat snapping. No allin1 segmentation needed.
- **Classification scope**: essential roles only (entry/exit/loop_target/
  entry_exit + first bridge). Fewer LLM calls. Non-essential components won't
  get theme/posture.
- **Re-identify trigger (RESOLVED, question 1)**: keep v2's `newly_backfilled`
  set. `--all-steps` re-identifies ONLY songs whose structured lyrics were
  backfilled THIS invocation (surgical, cheapest LLM cost). Songs whose
  structured lyrics were updated by a prior command need `--force
  --backfill-lyrics --components` to force re-identification.
- **Force vs lyrics interplay (RESOLVED, question 2)**: `--force` is scoped to
  the non-backfill step (components) ONLY. The structured-lyrics skip-if-present
  (Step 4) is **unconditional** — `--force` does NOT override it. So
  `--force --backfill-lyrics --components` re-identifies components for all
  songs (force bypasses the components DB-first skip) but does NOT refetch
  structured lyrics that are already present and parseable. This corrects
  v2's internal inconsistency (its Assumptions claimed "backfill always
  overwrites structured lyrics (B7)" contradicting its own Step 4 skip logic).

## Verified code facts this plan relies on

All line anchors below were verified against the working tree.

- `_STEP_CHAIN` audio.py:7173 = `["download","lrc","analyze","embedding"]`;
  `_submit_step`:7429 (already carries `force` at :7435 — does NOT need a new
  `force` param, only `config` + `newly_backfilled`); `_advance_song`:7494
  (skip-status tuple :7547-7554, calls `_submit_step` at :7528 passing `force`
  at :7534); `_poll_one_cycle`:8193 (advance calls :8228, :8302, :8338; dispatch
  :8258-8297; 404 else-branch :8356-8377 with `update_recording_status` at
  :8360 passing `**{f"{step}_status": "failed"}` at :8362, tier label at
  :8372); `_process_batch`:8389 (executor.submit :8523, no-download advance
  :8546 — note: actual line is ~8545 in current tree, poll call, KeyboardInterrupt
  :8589-8605 with reconcile caller at :8597-8603 collapsing
  `{sid: jid for (sid, step), jid in active_jobs.items()}` at :8598);
  `_reconcile_on_interrupt`:8639 (signature :8639-8645, iterates
  `for song_id, job_id in active_jobs.items()` at :8658 — assumes flat
  `{sid: jid}` shape, treats every job as LRC); `_resume_from_manifest`:8717
  (reconcile caller :8880-8886, same collapse at :8881); `_apply_manifest_writeback`:8894
  (DB-first short-circuit :8912-8925, `job.status != "completed"` guard :8928,
  try/except wraps everything :8908-8983); `_print_stats`:8986; `_print_unified_progress`:8164;
  `_print_dry_run_v4`:6679; `batch()`:6207 (step_flags :6340, `--all-steps`
  :6349-6350, no-flags error :6351-6357, backfill conflict :6359-6368, force
  scoping :6370-6397 with the `len(selected_steps) != 1` check at :6381 and
  `non_backfill_steps` built at :6380).
- backfill pre-loop :6478-6528; `_backfill_lyrics_batch`:1271;
  `_backfill_lyrics_for_song`:916; `_fetch_structured_lyrics`:824 (keyword-only,
  returns `(structured_raw, structured_json_str, source_used)`);
  `_download_worker`:8074 (new-recording branch :8103-8115, eager LRC :8127-8146);
  `_download_and_create_recording`:6756 (Recording constructed at :6838-6849,
  `youtube_url` available by :6791); `_submit_component_analysis_job`:2812
  (signature :2812-2831, gather :2867-2902, **config shadowing at :2893**
  `config = AdminConfig.load(None)` overwrites the parameter — the R2 cache
  check at :2946-2956 then uses the RELOADED default config, not the caller's;
  segmentation preflight :2904-2940, R2 cache check :2942-2983);
  `_persist_recording_theme`:2794 (self-guarding via try/except :2804-2809,
  only writes when theme or posture non-None :2806, swallows DB errors — safe
  to call unconditionally); `_parse_component_results`:3084; stdin eligibility
  check :3380; `--compute-all-fields` flag override :3247-3251;
  `_resolve_lyrics_text`:727.
- `db/client.py`: `update_recording_status`:911 accepts ONLY analysis/lrc
  status+job kwargs (a `**{"components_status": ...}` call raises TypeError);
  `update_recording_structured_lyrics`:962; `update_recording_theme`:991;
  `upsert_song_components`:2071 (DELETE+INSERT, validates theme/posture against
  enum sets); `get_song_components`:2159.
- `services/analysis.py`: `submit_component_analysis`:620 payload contract;
  `get_cached_component_result`:744; `_cached_components_have_llm_fields`:141
  (essential roles `{"entry","exit","loop_target","entry_exit"}` or bridge
  `occurrence_index==1`); `JobInfo.result` is `AnalysisResult` with
  `components: Optional[List[Dict]]` (:75), populated by `_parse_job_response`.
- `_submit_analysis_for_song`:7201 guards `not recording or not
  recording.r2_audio_url` at :7220 → `(None, "skipped_no_recording")` with
  manifest failed entry. The components submit helper mirrors this.
- Existing tests broken by the `config` param (no default) threaded through
  `_advance_song`/`_poll_one_cycle`/`_resume_from_manifest`:
  `tests/admin/test_audio_batch_unified.py` — 5 `_advance_song` callsites
  (:157, :186, :203, :221, :239), 2 `_poll_one_cycle` (:558, :612), 1
  `_resume_from_manifest` (:699); `tests/admin/test_audio_lrc_visibility.py:229`
  — `_reconcile_on_interrupt` with old `{song_id: job_id}` shape. v2 falsely
  claimed "All defaults are None/True so existing callers and tests are
  unaffected" — `config: AdminConfig` has no default and breaks all these.
  v3 explicitly updates them.

## Approach

All production edits in
`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` unless noted.
Run tests from `ops/admin-cli/` (never repo root — torch import breaks).

### Step 1 — Extract `_prepare_component_job_inputs` (behavior-preserving + shadowing fix)

Extract audio.py:2867-2983 (gather + segmentation preflight + R2-cache check)
from `_submit_component_analysis_job` into:

```python
def _prepare_component_job_inputs(
    recording: Recording,
    song_id: str,
    analysis_url: str,
    config: AdminConfig,
    console: Console,
    force: bool,
    classify_theme: bool,
    classify_vocal_posture: bool,
    segmentation_mode: Optional[str] = None,
) -> Optional[dict]:
```

Returns `{"sections", "beats", "downbeats", "lrc_content", "structured_lyrics",
"cached_result"}` where `cached_result` is the validated cached components.json
dict (R2 hit + `_cached_components_have_llm_fields(cached["components"],
classify_theme, classify_vocal_posture, all_components=False)` true) or None.
`None` return only on segmentation preflight failure (prints its red message).

**DEFECT FIX — config shadowing (glm53flash):** today audio.py:2893 does
`config = AdminConfig.load(None)` which SHADOWS the function's `config`
parameter, so the R2 cache check at :2946-2956 silently uses the reloaded
default config rather than the caller's. In the helper, assign to a separate
local `lrc_config = AdminConfig.load(None)` +
`R2Client(lrc_config.r2_bucket, lrc_config.r2_endpoint_url)` so the helper's
`config` parameter stays intact; the R2 cache check
(`R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)` +
`AnalysisClient(analysis_url, timeout=300)`, :2946-2956) then uses the
passed-in config/analysis_url. Identical in practice (all callers pass
`config.analysis_url`), strictly more correct. This is why the helper takes
BOTH `analysis_url` and `config` — v2's signature (no `analysis_url`) cannot
express the fix and would preserve the bug.

Keep the segmentation_mode preflight messages (:2904-2940) and the "cached
lacks LLM fields" message (:2980-2983) verbatim.

`_submit_component_analysis_job` keeps its signature; it calls prep and drives
its cached fast path (parse via `_parse_component_results`,
`upsert_song_components`, `_persist_recording_theme`, return components —
:2958-2979) from `inputs["cached_result"]`. No equivalent helper exists today.

### Step 2 — Components step in the unified loop

#### 2a. `_STEP_CHAIN` (audio.py:7173)

Append `"components"`:

```python
_STEP_CHAIN = ["download", "lrc", "analyze", "embedding", "components"]
```

Existing tests don't select components so unaffected.

#### 2b. New DB-fill predicate helper (no equivalent today)

```python
def _db_components_have_llm_fields(comps: list[SongComponent]) -> bool:
```

`candidates = rows with role in {"entry","exit","loop_target","entry_exit"} or
(component_type=="bridge" and occurrence_index==1)`; return
`bool(candidates) and all(c.theme and c.vocal_posture for c in candidates)`.
Mirrors `_cached_components_have_llm_fields` essential logic for SongComponent
rows. Extracted as a helper (glm53flash) so it is reused identically by the
submit skip (2c.3), the manifest writeback (8c), and tested in isolation —
v2 inlined the rule in two places, inviting drift.

#### 2c. Submit helper (submit-only, no wait — the unified loop polls)

```python
def _submit_components_for_song(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    config: AdminConfig,
    force: bool,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
    newly_backfilled: bool = False,
) -> Tuple[Optional[str], str]:
```

**DEFECT FIX — force threading (v2 bug):** v2's helper signature omitted
`force` entirely, yet its behavior text referenced `force` ("unless force",
`force or newly_backfilled`). v2's `_submit_step` wiring showed only
`_submit_components_for_song(..., newly_backfilled=newly_backfilled)` — `force`
never forwarded. A literal v2 implementation made `--force --components` and
`--force --backfill-lyrics --components` silently behave as fill-missing. v3
adds `force: bool` to the signature (glm53flash) and `_submit_step` forwards
the existing `force` it already carries (:7435) into the components branch.

`newly_backfilled` signals that structured lyrics were just backfilled this
run (input data changed → re-identify even when existing components carry
theme/posture).

Behavior, in order:

1. `recording = db_client.get_recording_by_song_id(song_id)`; None **OR**
   `not recording.r2_audio_url` → results `"skipped_no_recording"` + console
   yellow "no recording/audio" + manifest failed entry
   `("components", "components", None, "failed", error_message="No recording or
   audio URL")`, return `(None, "skipped_no_recording")`.
   **DEFECT FIX — r2_audio_url guard (glm53flash):** mirrors
   `_submit_analysis_for_song` audio.py:7220. v2 only checked `recording is
   None` and would proceed to submit with `audio_url=None`, failing late at the
   service or with an opaque error.

2. Eligibility mirror of the components stdin-batch check (audio.py:3380):
   `not recording.has_full_analysis and not recording.has_lrc` → results
   `"skipped_no_sections"` + console yellow "no sections or LRC — run audio
   lrc first", return `(None, "skipped_no_sections")`.

3. DB-first fill-missing skip (unless `force` OR `newly_backfilled`):
   `comps = db_client.get_song_components(song_id)`; if
   `_db_components_have_llm_fields(comps)`: call
   `_persist_recording_theme(recording, comps, db_client)` — **always, not
   guarded on `recording.theme is None`** (glm53flash). Rationale:
   `_persist_recording_theme` is already self-guarding (try/except :2804-2809,
   no-op when aggregate is empty :2806), idempotent, and re-aggregating catches
   the case where component rows changed but `recordings.theme` is stale even
   if non-None. v2's `recording.theme is None or recording.vocal_posture is
   None` guard skipped re-aggregation when theme was present-but-stale. On
   skip: results `"completed"`, `results[sid]["components_source"]="db_existing"`,
   manifest entry `("components", "components", None, "completed")`, return
   `(None, "skipped_completed")`.

4. `inputs = _prepare_component_job_inputs(recording, song_id,
   config.analysis_url, config, console, force or newly_backfilled, True,
   True)`; if `inputs["cached_result"]`: parse +
   `db_client.upsert_song_components(song_id, recording.content_hash,
   components)` + `_persist_recording_theme(recording, components, db_client)`
   (guard: only when components non-empty), results `"completed"`,
   `components_source="r2_cache"`, manifest completed, return
   `(None, "skipped_completed")`. Note: `force or newly_backfilled` makes prep
   pass `force=True`, which bypasses the R2 cache check (audio.py:2943
   `if not force:`) — so newly-backfilled and force songs always re-submit
   rather than returning stale cached components.json.

5. Else submit: `analysis_client.submit_component_analysis(
   audio_url=recording.r2_audio_url, content_hash=recording.content_hash,
   song_id=song_id, sections=inputs["sections"], beats=inputs["beats"],
   downbeats=inputs["downbeats"], lrc_content=inputs["lrc_content"],
   structured_lyrics=inputs["structured_lyrics"],
   force=force or newly_backfilled, snap_to_downbeat=True,
   energy_aware_roles=True, use_stems=False, classify_theme=True,
   classify_vocal_posture=True, skip_beat_cache=False, all_components=False,
   segmentation_mode=None)` — the exact flag set of the user's manual
   `--compute-all-fields` command (audio.py:3247-3251).

   **DEFECT FIX — broad exception scope (glm53flash):** catch broad `Exception`,
   not just `AnalysisServiceError` (v2). Rationale: this helper is called from
   `_advance_song` which sits OUTSIDE the poll loop's `try/except Exception`
   at audio.py:8380-8381 — an uncaught exception here (TypeError, KeyError,
   network error not wrapped by AnalysisClient) would kill the whole batch
   run, not just this song. On any `Exception`: results `"failed"` +
   `components_error=str(e)`, manifest failed (`error_class=type(e).__name__`),
   return `(None, "failed")`.

6. Manifest entry submitted `(song_id, recording.hash_prefix, "components",
   "components", job.job_id, "submitted", submitted_at=...)`, console green
   `→ {song_id} (submitted: {job_id})`, return `(job.job_id, "submitted")`.

No DB status field exists for components — job id lives in `active_jobs` +
manifest only. Do NOT call `db_client.update_recording_status` anywhere in the
components flow (its kwargs contract at db/client.py:911 has no
`components_status` — passing one raises TypeError).

#### 2d. Poll handler

```python
def _handle_components_completion(
    song_id: str, job_id: str, job: JobInfo,
    db_client: DatabaseClient, console: Console,
    results: dict, _add_manifest_entry: Any,
) -> Tuple[bool, Optional[str]]:
```

- `job.status == "completed"`: fetch recording (vanished → failed + manifest
  failed + `(True, None)`, pattern of `_handle_analysis_completion`). `raw =
  getattr(job.result, "components", None)` (guard against result-schema
  variants). Empty/None → results `"completed"`, `components_count=0`, console
  yellow "no components extracted", manifest completed. Non-empty:
  `components = _parse_component_results(raw, song_id, recording.content_hash)`;
  wrap `upsert_song_components` + `_persist_recording_theme` in try/except — on
  exception results `"failed"` + `components_error` + manifest failed +
  return `(True, None)` (prevents the poll loop's generic `except Exception`
  at audio.py:8380-8381 from retrying a permanently failing DB write forever).
  Success: results `"completed"` + `components_count=len(components)`, manifest
  completed, console `✓ {title} — components completed (N)`, return
  `(True, None)`.
- `"failed"`/`"cancelled"` → results `"failed"` + `components_error`, manifest
  failed, `(True, None)`.
- Else `(False, None)`.

#### 2e. Wire into dispatch + cascade

- `_submit_step` (audio.py:7429): add `config: AdminConfig` and
  `newly_backfilled: Optional[set[str]] = None` parameters (NOT `force` — it
  already carries `force` at :7435 and forwards it to every branch). Add:
  ```python
  elif step == "components":
      return _submit_components_for_song(
          song_id,
          db_client,
          analysis_client,
          config,
          force,  # already a param — forward it (v2 forgot this)
          console,
          results,
          _add_manifest_entry,
          newly_backfilled=(song_id in (newly_backfilled or set())),
      )
  ```
- `_advance_song` (audio.py:7494): add `config: AdminConfig` and
  `newly_backfilled: Optional[set[str]] = None` parameters; pass both into
  `_submit_step` (:7528). `_submit_step` already receives `force` from
  `_advance_song` at :7534 — unchanged. Add `"skipped_no_sections"` to the
  skip-status tuple (:7547-7554).
- `_poll_one_cycle` (audio.py:8193): add `config: AdminConfig` and
  `newly_backfilled: Optional[set[str]] = None` parameters; pass to all three
  `_advance_song` calls (:8228, :8302, :8338). Add dispatch branch in the
  :8258-8297 region:
  ```python
  elif step == "components":
      is_terminal, new_job_id = _handle_components_completion(
          song_id, job_id, job, db_client, console, results,
          _add_manifest_entry,
      )
  ```

#### 2f. Fix 404 branch for components (v2 "Changes from v1" item 1, confirmed)

The generic 404 else-branch (audio.py:8356-8377) calls
`db_client.update_recording_status(hash_prefix=hash_prefix,
**{f"{step}_status": "failed"})` at :8360-8363. For `step == "components"`
this passes `components_status` — db/client.py:911 has no such kwarg and
raises TypeError. Guard it:

```python
else:
    recording = db_client.get_recording_by_song_id(song_id)
    hash_prefix = recording.hash_prefix if recording else ""
    if step != "components":
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            **{f"{step}_status": "failed"},
        )
    results[song_id][step] = "failed"
    results[song_id][f"{step}_error"] = "Job lost (404)"
    # ... rest unchanged except tier label fix below
```

Also fix the tier label (audio.py:8372):
`analysis_tier if step == "analyze" else "embedding"` →
`analysis_tier if step == "analyze" else step` so the manifest tier for
components reads `"components"`.

### Step 3 — CLI surface on `batch` (audio.py:6207)

- New option after `embedding` (audio.py:6227):
  ```python
  components: bool = typer.Option(
      False, "--components",
      help="Run the component analysis step (snap-to-downbeat, energy roles, "
           "LLM theme + vocal posture classification)",
  ),
  ```
- `step_flags` dict (audio.py:6340): add `"components": components`.
- `--all-steps` (audio.py:6349-6350):
  ```python
  selected_steps = ["download", "backfill_lyrics", "lrc", "analyze",
                    "embedding", "components"]
  ```
  Order: `backfill_lyrics` runs before `_process_batch` (audio.py:6478),
  then download/lrc/analyze/embedding/components run in the unified loop.
- No-step-flags error (audio.py:6351-6357): mention `--components`.
- `--backfill-lyrics` conflict check (audio.py:6359-6368): add `if all_steps:`
  guard before the conflict check so `--all-steps` is not rejected for
  including `backfill_lyrics` alongside download/analyze/embedding/components.
  The conflict check still fires for explicit `--backfill-lyrics --download`
  etc. — only `--all-steps` bypasses it. Conflicting set stays
  `{"download", "analyze", "embedding"}` — `--backfill-lyrics --components`
  and `--backfill-lyrics --lrc --components` are allowed (fill-missing pair).
  Update the message tail to "Only --lrc and --components are allowed alongside
  --backfill-lyrics."
- Force scoping (audio.py:6370-6397): change `len(selected_steps) != 1` at
  :6381 to `len(non_backfill_steps) != 1`. This allows
  `--force --backfill-lyrics --components` (non_backfill_steps=["components"],
  len=1, passes) while still blocking `--force --lrc --analyze`
  (non_backfill_steps=["lrc","analyze"], len=2, fails) and
  `--force --backfill-lyrics --lrc --components` (non_backfill_steps=["lrc",
  "components"], len=2, fails). Update error string at :6383-6384 to list
  `--components`. The `--force --download` block (:6387) still works:
  `non_backfill_steps=["download"]`, len=1, `"download" in non_backfill_steps`
  → blocked.
- `_print_dry_run_v4` (audio.py:6679): for each with-recording song add dim
  lines, gated by selected steps (glm53flash — more granular than v2's
  unconditional Components line):
  - `Structured lyrics: yes/no` when `"backfill_lyrics" in selected_steps`
    (parse like Step 4: `json.loads(recording.structured_lyrics)` with
    `sections` non-empty = yes).
  - `Components: {len(db_client.get_song_components(song_id))} row(s)` when
    `"components" in selected_steps`.
- Docstring examples (audio.py:6263-6274): replace with:
  ```
  sow-admin audio batch --album 深愛耶穌 --all-steps
  sow-admin audio batch --album 深愛耶穌 --backfill-lyrics --lrc --components
  sow-admin audio batch --album 深愛耶穌 --backfill-lyrics --force --components
  ```
  Note `--all-steps` is the one command that fills everything missing and
  re-identifies components whose structured-lyrics input changed (this run
  only); note `--all-steps` includes backfill-lyrics + components and fetches
  structured lyrics during download for new songs; note components auto-skips
  songs already classified with recording-level theme/posture (use `--force
  --components` to force reclassify everything, or `--backfill-lyrics --force
  --components` to re-identify after backfilling structured lyrics for all
  songs — note `--force` does not refetch already-present structured lyrics).

### Step 4 — Backfill-lyrics skip-if-present (UNCONDITIONAL — force does not override)

**RESOLVED (question 2):** the structured-lyrics skip-if-present is
unconditional. `--force` is scoped to the components step only and does NOT
override this skip. This corrects v2's internal inconsistency (its Assumptions
claimed "backfill always overwrites structured lyrics (B7)" contradicting this
Step 4 skip logic).

In both batch loops that drive `_backfill_lyrics_for_song`, skip when the
recording already has parseable structured lyrics with sections, **regardless
of `force`**:

- `batch()` backfill loop (audio.py:6483-6507), after the no-recording /
  no-youtube-url checks:
  ```python
  if recording.structured_lyrics:
      try:
          existing = json.loads(recording.structured_lyrics)
      except (json.JSONDecodeError, TypeError):
          existing = None
      if existing and existing.get("sections"):
          console.print(
              f"  [yellow]→ {sid} (skipped: structured lyrics already present)[/yellow]"
          )
          backfill_results[sid]["backfill_lyrics"] = "skipped"
          continue
  ```
- `_backfill_lyrics_batch` (audio.py:1304-1324): same check inside the
  per-song loop before calling `_backfill_lyrics_for_song`, printing
  `~ Skipped (already has structured lyrics): {sid}` and tracking a `skipped`
  counter (neither success nor failed); summary line becomes
  `f"[bold]Summary:[/bold] {success} backfilled, {skipped} skipped, {failed}
  failed"`. The single-song explicit `audio download <id> --backfill-lyrics`
  path stays always-refetch.

### Step 5 — Structured lyrics in batch download path (use case 1)

The batch download path (`_download_and_create_recording`, audio.py:6756-6859)
creates a `Recording` without `structured_lyrics` or `structured_lyrics_raw`,
unlike the single-song `import_youtube_audio_for_song` (audio.py:1146-1204)
which calls `_fetch_structured_lyrics`. Fix: mirror the single-song path so
`--all-steps` on a new song yields structured-lyrics-backed component
identification.

In `_download_and_create_recording` (audio.py:6756):
- Add `use_llm: bool = True` parameter.
- **Placement (HAZARD FIX):** fetch AFTER the duplicate-hash early return
  (audio.py:6815-6832) and AFTER the R2 upload (audio.py:6834-6836),
  immediately before the `Recording(...)` constructor (audio.py:6838) —
  NOT after `download_with_info` returns `youtube_url` at :6791. The
  duplicate-hash branch `return None, f"duplicate hash: …"` at :6832
  discards the download; a fetch placed after :6791 would burn a full
  LLM-lyrics extraction for every download that gets discarded as a
  duplicate (wrong-video hash collisions are a known failure mode — the
  yellow warning at :6825-6830 is precisely that path). Placing the fetch
  after R2 upload also means the upload (the genuinely expensive,
  non-refundable side effect) is the only pre-fetch cost paid before the
  lyrics call.
- Immediately before constructing `Recording` (audio.py:6838):
  ```python
  structured_raw: Optional[str] = None
  structured_json_str: Optional[str] = None
  if youtube_url:
      try:
          structured_raw, structured_json_str, _src = _fetch_structured_lyrics(
              youtube_url=youtube_url,
              song_title=song.title,
              band=song.composer,
              source="auto",
              use_llm=use_llm,
              console=console,
          )
      except Exception as e:
          # Non-fatal: download still succeeds; components will use
          # fallback segmentation. User can --backfill-lyrics later.
          # Catches typer.Exit(1) (subclass of Exception via
          # click.exceptions.Exit) which _fetch_structured_lyrics raises on
          # LLM parse failure with use_llm=True — critical in the thread
          # context where typer.Exit would otherwise propagate to
          # _download_worker's except Exception and mark the download failed.
          console.print(
              f"  [yellow]Structured lyrics fetch failed (non-fatal): {e}[/yellow]"
          )
  ```
- Add to `Recording` constructor (audio.py:6838-6849):
  `structured_lyrics_raw=structured_raw, structured_lyrics=structured_json_str,`
  — `insert_recording` persists these columns (proven by
  `import_youtube_audio_for_song` audio.py:1193-1206 using the same pattern).

`_download_if_needed` (existing-recording re-download path) is NOT modified
— structured lyrics for existing recordings are handled by
`--backfill-lyrics`.

### Step 6 — Pipeline threading (config, use_llm, newly_backfilled)

Consolidated parameter threading. `newly_backfilled` defaults to None; `config`
has NO default (positional AdminConfig) — this is what breaks existing tests
(see Step 9); `use_llm` defaults to True.

#### 6a. Thread `config` + `newly_backfilled` through loop functions

| Function | New params | Passes to |
|---|---|---|
| `_process_batch` (8389) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_advance_song` calls (8546, 8302 via `_poll_one_cycle`, 8338), `_poll_one_cycle`, `_reconcile_on_interrupt` |
| `_poll_one_cycle` (8193) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_advance_song` (8228, 8302, 8338) |
| `_advance_song` (7494) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_submit_step` (passes `newly_backfilled=newly_backfilled`) |
| `_submit_step` (7429) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_submit_components_for_song` (passes `newly_backfilled=(song_id in (newly_backfilled or set()))`) |
| `_resume_from_manifest` (8717) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_poll_one_cycle`, `_reconcile_on_interrupt` |

#### 6b. Thread `use_llm` through download path

| Function | New param | Passes to |
|---|---|---|
| `_download_worker` (8074) | `use_llm: bool = True` | `_download_and_create_recording` (8105-8107) |
| `_process_batch` (8389) | `use_llm: bool = True` | `executor.submit(_download_worker, ..., use_llm)` (8523-8538) |
| `batch()` (6553) | — | Pass `use_llm=use_llm` to `_process_batch` call |

#### 6c. Build `newly_backfilled` set in `batch()`

In `batch()` (audio.py:6530-6531), after the backfill loop, before
`_process_batch`:

```python
newly_backfilled: set[str] = {
    sid for sid, r in backfill_results.items()
    if r.get("backfill_lyrics") == "completed"
}
```

Pass `newly_backfilled=newly_backfilled` to `_process_batch`.

#### 6d. Resume path

`_resume_from_manifest` (8717): `newly_backfilled` defaults to None — on
resume, songs that were newly backfilled in the original run are no longer
"newly backfilled" (their structured lyrics are already present from the
original run), so components fill-missing applies normally. Caller in `batch()`
resume path (audio.py:6437) passes `config` (loaded at audio.py:6407) and
`newly_backfilled=None` (the default).

### Step 7 — Interrupt reconciliation fix

`_reconcile_on_interrupt` (audio.py:8639) currently receives `{sid: jid}`
collapsed from `(sid, step)` keys at :8598 and :8881 — two active steps for
one song collide, and every job is treated as LRC (it iterates flat
`for song_id, job_id in active_jobs.items()` at :8658 and does only the LRC R2
check). Change signature to take
`active_jobs: Dict[Tuple[str, str], str]` and `config: AdminConfig`, and
iterate `for (song_id, step), job_id in active_jobs.items()`:

- `lrc`: unchanged R2 check (audio.py:8665-8688).
- `components`: try `AnalysisClient`-free cache check —
  `recording = db_client.get_recording_by_song_id(song_id)` (None → skip);
  `inputs = _prepare_component_job_inputs(recording, song_id,
  config.analysis_url, config, console, force=False, True, True)`; if
  `inputs and inputs["cached_result"]`: parse +
  `db_client.upsert_song_components(song_id, recording.content_hash,
  components)` + `_persist_recording_theme(recording, components, db_client)`
  (guard non-empty components), results `"completed"`; else results `"failed"`
  + `components_error="Batch interrupted"` + console red note (no DB status
  field to update).
- `analyze`/`embedding`: leave DB untouched; console dim note "job continues
  server-side; `audio status --reconcile` catches late completions" (existing
  tip at audio.py:8691-8694 already says this).
- Callers (audio.py:8597-8603 and :8880-8886): pass `active_jobs` directly
  (remove the `{sid: jid for (sid, step), jid in ...}` collapse at :8598 and
  :8881) plus `config`. `_process_batch` has `config` anyway (Step 6a) — pass
  it to `_reconcile_on_interrupt` too (new param).

### Step 8 — Progress display + stats + resume writeback

#### 8a. `_print_unified_progress` (audio.py:8164)

Add components counts to the one-line progress:

```python
components_active = sum(1 for (_, s) in active_jobs if s == "components")
components_done = sum(1 for r in results.values() if r.get("components") == "completed")
```

Update the format string:
`pending(down/lrc/ana/emb/comp)=.../{components_active}` and
`✓(lrc/ana/emb/comp)=.../{components_done}`.

#### 8b. `_print_stats` (audio.py:8986)

Add a Components block after Embedding — completed / failed / skipped
(`skipped_no_sections` + `skipped_no_recording`) counts, shown when any is
nonzero; and a failed-components list (audio.py:9154-9165 pattern) printing
`components_error`.

#### 8c. `_apply_manifest_writeback` (audio.py:8894)

In the DB-first short-circuit section (:8912-8925) add `elif step ==
"components":`:
```python
elif step == "components":
    comps = db_client.get_song_components(song_id)
    if _db_components_have_llm_fields(comps):
        if recording:
            _persist_recording_theme(recording, comps, db_client)
        return
```
Then after the `job.status != "completed"` guard (:8928) add:
```python
elif step == "components":
    raw = getattr(job.result, "components", None) if job.result else None
    if raw and recording:
        components = _parse_component_results(raw, song_id, recording.content_hash)
        db_client.upsert_song_components(song_id, recording.content_hash, components)
        _persist_recording_theme(recording, components, db_client)
```
(`recording` may be None from the fetch at :8913 — guard; the function's
existing try/except at :8908-8983 wraps everything.)

Note on the DB-first branch: v2 additionally required `recording.theme` and
`recording.vocal_posture` both non-None before short-circuiting; glm53flash
short-circuits on `_db_components_have_llm_fields` alone and always
re-aggregates via `_persist_recording_theme`. v3 follows glm53flash — the
re-aggregation is idempotent and catches stale `recordings.theme` when
component rows changed, and avoids falling through to the completed-job branch
(which requires a surviving job result and would no-op for an
interrupted/lost job).

### Step 9 — Tests

#### 9a. Update existing callsites broken by signature changes (DEFECT FIX — v2 omitted this entirely)

v2 falsely claimed "All defaults are None/True so existing callers and tests
are unaffected" — `config: AdminConfig` has no default and breaks every
callsite below. v3 explicitly updates them:

- `tests/admin/test_audio_batch_eager_lrc.py`: two kinds of breakage,
  both from the same root cause (Step 6b adds `use_llm: bool` to
  `_download_worker`, which forwards it to `_download_and_create_recording`;
  `_process_batch` gains `config: AdminConfig`):
  1. The 5 `_process_batch` callsites (:145, :181, :209, :235, :261) lack
     the new `config` param — add `config=MagicMock()` to each.
  2. The 3 fakes of `_download_and_create_recording` are 5-arg and
     TypeError on the new 6th `use_llm` kwarg:
     - `def _download_and_create_recording(song_id, song, db, r2, console):`
       at :129 and :167 → add `use_llm: bool = True` (or `**kwargs`) to the
       signature;
     - `lambda sid, song, db, r2, c: (None, "boom")` at :233 →
       `lambda sid, song, db, r2, c, use_llm=True: (None, "boom")`
       (or `lambda *a, **k: (None, "boom")`).
     **Why this is a silent failure, not a loud one:** `_download_worker`
     wraps its body in a bare `except Exception` at audio.py:8155 that
     returns `{"status": "failed", "updates": {"download": "failed",
     "error": str(e)}}` (:8156-8161). A 5-arg fake called with 6 args raises
     `TypeError`, the `except` catches it, and every download in the test
     silently reports failed — tests that assert download success break
     with an opaque "download: failed" rather than a TypeError pointing at
     the fake. Updating the fakes is the only reliable fix; relying on the
     test failure message to find them wastes a debugging round.
- `tests/admin/test_audio_batch_unified.py`: add `config=MagicMock()` to the 5
  `_advance_song` callsites (:157, :186, :203, :221, :239), 2 `_poll_one_cycle`
  callsites (:558, :612), and `_resume_from_manifest` (:699).
- `tests/admin/test_audio_lrc_visibility.py:229`: update
  `_reconcile_on_interrupt(active_jobs={("song_1", "lrc"): "lrc-job-1"}, …,
  config=MagicMock())` (tuple-key shape).

#### 9b. New tests

`ops/admin-cli/tests/admin/test_audio_batch_v4.py` (CliRunner against real
app; config to `postgresql://invalid/invalid`, `WIDE_ENV`; helpers
`_make_recording`:336, `_make_song`:356):

- `--components` alone passes step-flag validation (assert
  `"No step flags selected" not in result.output`).
- `--force --components` passes force scoping (assert
  `"exactly one step flag" not in result.output`).
- `--backfill-lyrics --components` passes mutual exclusivity (assert
  `"cannot be combined" not in result.output`).
- `--force --backfill-lyrics --components` passes force scoping (assert
  `"exactly one step flag" not in result.output`) — use-case-2 CLI
  validation.
- `--all-steps` includes components + backfill_lyrics: patch
  `stream_of_worship.admin.commands.audio.get_db_client` → MagicMock db
  (`list_recordings_with_songs` → `[]`, `list_songs` → `[song]`,
  `get_recording_by_song_id` → None so unrecorded path), patch `audio.R2Client`,
  `audio.AnalysisClient`, and `audio._process_batch` with a capture Mock
  returning `{}`; invoke `audio batch --album X --all-steps --config …`;
  assert captured `selected_steps` ==
  `["download","backfill_lyrics","lrc","analyze","embedding","components"]`.
- Backfill skip (unconditional — force does not override): patch
  `audio._backfill_lyrics_for_song` (must NOT be called) + db mock whose
  `get_recording_by_song_id` returns a Recording with
  `structured_lyrics='{"sections":[{"type":"verse"}]}'` and `youtube_url` set;
  invoke `audio batch --album X --backfill-lyrics --force --config …`; assert
  output contains `skipped: structured lyrics already present` and patched
  function not called — this guards the resolved force-lyrics decision
  (question 2). Also a `_backfill_lyrics_batch` direct test (patch
  `sys.stdin` via `io.StringIO`, same mock) asserting the summary counts a
  skip.

`ops/admin-cli/tests/admin/test_audio_batch_unified.py` (unit, MagicMock
db/clients; follow `_make_recording`/`_make_song` helpers):

- `TestDbComponentsHaveLlmFields`: essential candidates present + theme/posture
  set → True; missing one field → False; no candidates (only non-essential
  rows) → False; bridge occurrence_index==1 counts as candidate.
- `TestSubmitComponentsForSong`:
  - no recording → `(None, "skipped_no_recording")`;
  - recording with `r2_audio_url=None` → `(None, "skipped_no_recording")`
    (guards the r2_audio_url defect fix);
  - recording with `lrc_status="pending"`, `analysis_status="pending"` →
    `(None, "skipped_no_sections")`;
  - DB comps present (SongComponent rows with role entry/exit, theme+
    vocal_posture set) → `(None, "skipped_completed")` and
    `analysis_client.submit_component_analysis` not called;
  - DB comps present but `recording.theme=None` →
    `_persist_recording_theme` called (use-case-3 aggregate backfill) and
    still returns `(None, "skipped_completed")`;
  - DB comps present with theme+posture AND `newly_backfilled=True` →
    `analysis_client.submit_component_analysis` IS called (use-case-2
    re-identify on changed input) — guards the `newly_backfilled` bypass;
  - DB comps present with theme+posture AND `force=True` (newly_backfilled
    default False) → `analysis_client.submit_component_analysis` IS called —
    guards the force-threading defect fix (v2's omitted `force` param would
    have made this call NOT happen);
  - happy path: `analysis_client.submit_component_analysis` returns
    `JobInfo(job_id="comp-1", status="processing",
    job_type="component_analysis")` → returns `("comp-1", "submitted")`,
    manifest entry recorded (assert via the `_add_manifest_entry`
    side-effect list), `db.update_recording_status` NOT called.
- `TestHandleComponentsCompletion`:
  - completed job whose result is a
    `SimpleNamespace(components=[{...component dict...}])` →
    `db.upsert_song_components` called once with parsed SongComponent list,
    `db.update_recording_theme` called, results
    `{"components":"completed","components_count":1}`, returns
    `(True, None)`;
  - failed job → results failed;
  - empty components → completed with count 0.
- `TestAdvanceSongComponents`: `_advance_song(sid, "lrc",
  ["lrc","components"], …, config=MagicMock())` with db recording
  `lrc_status="completed"` → `active_jobs` gains `(sid, "components")` (mock
  `analysis_client.submit_component_analysis`).
- `TestReconcileOnInterrupt`: active_jobs with `("s1","components")` and
  cached_result present → upsert called; with `("s1","analyze")` →
  `db.update_recording_lrc` NOT called (regression guard for the collapse
  bug); a components job AND an lrc job for the SAME song both reconciled
  without collision (guards the tuple-key fix).
- `TestDownloadStructuredLyrics`: patch
  `audio._fetch_structured_lyrics` to return
  `("raw", '{"sections":[]}', "youtube")`; call
  `_download_and_create_recording(..., use_llm=True)` with a mock
  downloader returning a temp audio file + `youtube_url="https://..."`;
  assert the returned Recording has `structured_lyrics='{"sections":[]}'`
  and `structured_lyrics_raw="raw"`. Also test non-fatal failure: patch to
  raise `typer.Exit(1)` → Recording still created with
  `structured_lyrics=None` (guards the in-thread `typer.Exit` catch).

Run:
```bash
cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 \
  --extra admin --extra test pytest \
  tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py \
  tests/admin/test_audio_lrc_visibility.py -v
```
then the full admin-cli suite.

## Critical files & anchors

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — all
  production edits; anchors:
  - `_STEP_CHAIN`:7173
  - `_submit_component_analysis_job`:2812 (refactor source; config shadowing
    at :2893 — the defect Step 1 fixes)
  - `_persist_recording_theme`:2794 (self-guarding — safe to call
    unconditionally)
  - `batch()`:6207 (flags/validation)
  - backfill loop:6478
  - `_backfill_lyrics_batch`:1271
  - `_download_and_create_recording`:6756 (structured lyrics fix)
  - `_download_worker`:8074
  - `_submit_step`:7429 (already carries `force` at :7435 — do NOT add force,
    only config + newly_backfilled)
  - `_advance_song`:7494
  - `_poll_one_cycle`:8193
  - `_process_batch`:8389
  - `_reconcile_on_interrupt`:8639 (flat-iter bug at :8658; collapse callers
    at :8598 and :8881)
  - `_apply_manifest_writeback`:8894
  - `_print_stats`:8986
  - `_print_unified_progress`:8164
  - `_print_dry_run_v4`:6679
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` —
  read-only reference: `submit_component_analysis`:620 (payload contract),
  `get_cached_component_result`:744, `_cached_components_have_llm_fields`:141,
  `AnalysisResult.components`:75.
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — read-only
  reference: `update_recording_status`:911 (fixed kwargs — components must
  NOT call it; `**{"components_status": ...}` raises TypeError),
  `upsert_song_components`:2071, `get_song_components`:2159,
  `update_recording_theme`:991, `update_recording_structured_lyrics`:962.
- `ops/admin-cli/tests/admin/test_audio_batch_v4.py` — CLI validation
  tests; helpers `_make_recording`:336, `_make_song`:356.
- `ops/admin-cli/tests/admin/test_audio_batch_unified.py` — unified-loop
  unit tests; `_make_recording` helper + `TestHandleAnalysisCompletion`
  patterns to mirror; callsites needing `config=MagicMock()`.
- `ops/admin-cli/tests/admin/test_audio_lrc_visibility.py:229` —
  `_reconcile_on_interrupt` callsite needing the tuple-key shape.

## Verification

1. Targeted tests:
   ```bash
   cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 \
     --extra admin --extra test pytest \
     tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py \
     tests/admin/test_audio_lrc_visibility.py -v
   ```
   — all new tests pass, no existing regressions.
2. Full suite:
   ```bash
   cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 \
     --extra admin --extra test pytest -v
   ```
3. Help surface: `zsh -ic 'sow_admin audio batch --help'` shows
   `--components` and updated docstring examples.
4. Read-only end-to-end (no side effects):
   ```bash
   zsh -ic 'sow_admin audio batch --album 深愛耶穌 --all-steps --dry-run'
   ```
   — expect `Selected steps: download, backfill_lyrics, lrc, analyze,
   embedding, components`; expect per-song lines listing LRC/Analysis
   statuses, a `Structured lyrics: yes/no` line, and a `Components: N row(s)`
   line; zero YouTube/R2/LLM calls.
5. Use-case-2 CLI validation:
   ```bash
   zsh -ic 'sow_admin audio batch --album 深愛耶穌 --backfill-lyrics --force --components --dry-run'
   ```
   — expect no error (force scoping accepts the combination); dry-run
   proceeds. Also verify the force-does-not-refetch-lyrics guard: run with an
   album whose songs already have structured lyrics and confirm the dry-run
   shows them as already-present (the `--force` does not mark them for
   refetch).
6. User-gated live check (costs YouTube + LLM tokens; run only with user's
   go-ahead):
   ```bash
   zsh -ic 'sow_admin audio batch --album 深愛耶穌 --all-steps'
   ```
   — exercises the full mixed-album flow: songs missing structured lyrics
   get backfilled (then components re-identified via `newly_backfilled`),
   songs with structured lyrics + missing component theme get components
   submitted, songs with components but missing recording-level theme get
   aggregate backfilled, new songs get downloaded (with structured lyrics) →
   LRC → components. Verify with `sow_admin audio show <song_id>` that
   recordings.structured_lyrics, recordings.theme/vocal_posture, and
   song_components rows are populated for songs across all three use cases.

## Assumptions

- `--components` hardcodes the `--compute-all-fields` flag set
  (snap-to-downbeat, energy roles, classify theme + posture; no stems,
  essential-roles only). Per-song finer control remains
  `audio components`. If reality differs (e.g. backend rejects a flag
  combination), fall back to `classify_theme/classify_vocal_posture` only
  and note it.
- `--all-steps` is the "fill everything + re-identify on changed input"
  entry point. It includes `backfill_lyrics` (bypassing the conflict
  check), `download`, `lrc`, `analyze`, `embedding`, `components`. The
  backfill loop runs first (fills structured lyrics for existing recordings
  missing them, tracks which songs were newly backfilled), then the
  unified loop runs download → lrc → analyze → embedding → components.
  Newly-backfilled songs get their components re-identified (input data
  changed); all other songs get fill-missing (skip if already present).
- `--backfill-lyrics` in batch/stdin-batch becomes fill-missing (skips
  songs with structured lyrics already present, **regardless of `--force`**);
  explicit single-song call still force-refetches. `--force` is scoped to the
  non-backfill step (components) only and does NOT override the
  structured-lyrics skip (resolved question 2).
- Components job ids are tracked in the manifest + memory only (no
  `recordings.components_status` column added); a crashed run resumes via
  `--resume <manifest>`. On resume, `newly_backfilled` is None (structured
  lyrics already present from the original run), so components fill-missing
  applies normally.
- `--force --backfill-lyrics --components` is the explicit "re-identify
  everything" command (alternative to `--all-steps` when you want to force
  re-identification of ALL songs regardless of backfill status): backfill is
  fill-missing (skips songs with existing parseable structured lyrics — force
  does NOT override this), force bypasses the components DB-first skip
  (Step 2c.3 "unless force") and R2 cache check (Step 1 preserves
  `if not force:`), so components are re-submitted for all songs.
  `--force` applies to the non-backfill step (components) only.

## Defects in v2 fixed by v3

1. **Force threading missing (STRUCTURAL)**: v2's `_submit_components_for_song`
   signature had no `force` param, and `_submit_step` wiring forwarded only
   `newly_backfilled`. `_submit_step` already carries `force` (audio.py:7435)
   but v2 never forwarded it into the components branch. A literal v2
   implementation made `--force --components` and
   `--force --backfill-lyrics --components` silently behave as fill-missing
   (no re-submit). v3 adds `force: bool` to the helper (Step 2c) and
   `_submit_step` forwards the existing `force` (Step 2e). Test guard: the
   `force=True` submit case in `TestSubmitComponentsForSong`.
2. **Config shadowing preserved (CORRECTNESS)**: v2 said "preserve today's
   internals exactly," keeping the `config = AdminConfig.load(None)` shadow at
   audio.py:2893 so the R2 cache check used the reloaded default config. v3
   applies glm53flash's fix: separate `lrc_config` local + explicit
   `analysis_url` param on the helper so the caller's config drives the cache
   check (Step 1).
3. **False "tests unaffected" claim (TEST GAP)**: v2 claimed "All defaults are
   None/True so existing callers and tests are unaffected," but
   `config: AdminConfig` (no default) threaded through `_advance_song`/
   `_poll_one_cycle`/`_resume_from_manifest`/`_process_batch` breaks 13
   existing callsites (test_audio_batch_unified.py: 5 `_advance_song` +
   2 `_poll_one_cycle` + 1 `_resume_from_manifest`; test_audio_batch_eager_lrc.py:
   5 `_process_batch`; test_audio_lrc_visibility.py:229
   `_reconcile_on_interrupt`), plus 3 5-arg `_download_and_create_recording`
   fakes in test_audio_batch_eager_lrc.py that TypeError on the new `use_llm`
   kwarg (see item 10). v3 explicitly updates all of them (Step 9a).
4. **`r2_audio_url` guard absent (ROBUSTNESS)**: v2 only checked
   `recording is None` and would submit with `audio_url=None`, failing late.
   v3 mirrors `_submit_analysis_for_song` audio.py:7220 (Step 2c.1).
5. **Narrow exception scope on submit (ROBUSTNESS)**: v2 caught only
   `AnalysisServiceError`; any other exception (TypeError/KeyError/unwrapped
   network error) would escape `_advance_song` (outside the poll loop's
   try/except) and kill the batch. v3 catches broad `Exception` with
   rationale (Step 2c.5).
6. **Inlined DB-fill predicate (DRY)**: v2 inlined the essential-candidates
   rule in 2b.3 and repeated it in 8c, inviting drift. v3 extracts
   `_db_components_have_llm_fields` (Step 2b), reused by submit, writeback,
   and tested in isolation.
7. **Skip-path theme re-aggregation guard (CORRECTNESS)**: v2 only called
   `_persist_recording_theme` when `recording.theme`/`vocal_posture` was None,
   leaving stale-but-present aggregates unrefreshed when component rows
   changed. v3 always re-aggregates on the skip path —
   `_persist_recording_theme` is self-guarding (try/except, no-op on empty
   aggregate) and idempotent (Step 2c.3, Step 8c).
8. **Dry-run granularity (UX)**: v2 printed `Components: N row(s)`
   unconditionally; v3 gates both the `Structured lyrics: yes/no` and
   `Components: N row(s)` lines by selected steps (Step 3).
9. **Internal inconsistency on force/lyrics (RESOLVED)**: v2's Assumptions
   claimed "backfill always overwrites structured lyrics (B7)" while its
   Step 4 made the batch backfill skip-if-present — under v2,
   `--force --backfill-lyrics --components` would skip lyrics yet
   force-resubmit components, contradicting the assumption text. v3 pins the
   resolved semantics (question 2): force scoped to components only, lyrics
   always fill-missing, and corrects the Assumptions text (Step 4).
10. **Duplicate-hash burns LLM tokens (PERFORMANCE/SILENT WASTE)**: v2's
    Step 5 placed the structured-lyrics fetch right after `download_with_info`
    (audio.py:6791), BEFORE the duplicate-hash early return at :6815-6832 and
    the R2 upload at :6834-6836. The duplicate-hash branch discards the
    download (`return None, f"duplicate hash: …"` at :6832) — a known
    wrong-video failure mode (the yellow warning at :6825-6830 is precisely
    that path). A fetch at :6791 burns a full LLM-lyrics extraction for every
    download that gets discarded as a duplicate. v3 moves the fetch to after
    the R2 upload (:6836), immediately before `Recording(...)` (:6838), so
    only downloads that actually persist pay the lyrics cost (Step 5).
11. **Eager-LRC test fakes silent-fail on 6th arg (TEST GAP/SILENT FAILURE)**:
    v2's test section never accounted for test_audio_batch_eager_lrc.py's 3
    5-arg fakes of `_download_and_create_recording` (`def` at :129 and :167,
    `lambda` at :233). Step 6b's `_download_worker` forwards the new
    `use_llm` arg into `_download_and_create_recording`, so the 6-arg call
    raises `TypeError` on a 5-arg fake. The failure is silent:
    `_download_worker` wraps its body in a bare `except Exception`
    (audio.py:8155) that returns `{"status": "failed", …}` (:8156-8161), so
    every download in those tests reports failed with an opaque "download:
    failed" instead of a TypeError pointing at the fake. v3 updates all 3
    fakes (add `use_llm: bool = True` / `**kwargs`) and the 5 `_process_batch`
    callsites (Step 9a).

## Changes from v2

1. Step 1: added `analysis_url` param + config-shadowing fix (glm53flash).
2. Step 2b: extracted `_db_components_have_llm_fields` helper (glm53flash).
3. Step 2c: added `force: bool` to `_submit_components_for_song` signature
   (force-threading defect fix); added `r2_audio_url` guard (2c.1); broadened
   submit exception to `Exception` (2c.5); unconditional
   `_persist_recording_theme` on skip (2c.3).
4. Step 2e: clarified `_submit_step` forwards the EXISTING `force` (not a new
   param) into the components branch.
5. Step 3: gated dry-run lines by selected steps (glm53flash).
6. Step 4: pinned force-does-not-override-lyrics-skip (resolved question 2),
   corrected the v2 "B7 always overwrites" inconsistency.
7. Step 7: reconcile signature includes `config` (needed for
   `_prepare_component_job_inputs` with the new `analysis_url` param).
8. Step 8c: DB-first short-circuit on `_db_components_have_llm_fields` alone
   (not also `recording.theme` non-None), always re-aggregate
   (glm53flash).
9. Step 9a: explicit existing-callsite updates (the test gap v2 denied) —
   13 callsites across 3 files + 3 eager-LRC 5-arg fakes (defect fix 11).
10. Step 9b: added `TestDbComponentsHaveLlmFields`, the `r2_audio_url=None`
    case, the `force=True` submit case (guards defect fix 1), the
    `--force --backfill-lyrics` backfill-skip-still-fires test (guards
    resolved question 2), and the reconcile same-song-collision test.
11. Step 5: moved the structured-lyrics fetch past the duplicate-hash early
    return and R2 upload to immediately before `Recording(...)` (defect
    fix 10) — discarded duplicate downloads no longer burn LLM tokens.