# audio batch: `--all-steps` intelligent backfill

## Goal

`sow-admin audio batch --all-steps --album X` is the ONE command that fills
everything missing and re-generates anything whose input data changed — no
thinking about which recordings lack structured lyrics, LRC, component data,
or recording-level theme/posture.

## Use cases (single invocation across a mixed album)

1. **New song full pipeline**: catalog song without recording → recording
   downloaded (structured lyrics fetched during download) → LRC generated →
   recording analyzed (fast tier) → embeddings generated → components
   identified (structured-lyrics-backed) + theme/posture classified →
   recording-level theme/posture aggregated.

2. **Re-identify on changed input**: existing song missing structured lyrics
   with inaccurate component metadata → structured lyrics backfilled, then
   components re-identified via structured lyrics (input data changed →
   re-submit even when existing components carry theme/posture).

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
- **Re-identify trigger**: this run only. `newly_backfilled` set tracks songs
  whose structured lyrics were backfilled THIS invocation. Songs whose
  structured lyrics were updated by a prior command need `--force
  --backfill-lyrics --components` to force re-identification.

## Fast-tier interaction with components eligibility

`has_full_analysis` is `analysis_status == "completed"` (full tier only).
With fast tier, songs get `analysis_status = "partial"`, so
`has_full_analysis` is False. The components eligibility check mirrors the
stdin-batch check (audio.py:3380):

```python
if not recording.has_full_analysis and not recording.has_lrc:
    # skip — no sections or LRC available
```

With LRC completed (from the LRC step in `--all-steps`), this passes even
without full analysis. The components step then uses structured_lyrics +
lrc_content for identification (sections=None is fine — the analysis service
falls back to lyrics-based segmentation). Without LRC AND without full
analysis, components are skipped with a "run audio lrc first" hint.

## Approach

All production edits in
`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` unless noted.
Run tests from `ops/admin-cli/` (never repo root — torch import breaks).

### Step 1 — Extract component-job input prep (behavior-preserving refactor)

Extract the gather + preflight + R2-cache-check portion of
`_submit_component_analysis_job` (audio.py:2867-2983) into:

```python
def _prepare_component_job_inputs(
    recording: Recording,
    song_id: str,
    config: AdminConfig,
    console: Console,
    force: bool,
    classify_theme: bool,
    classify_vocal_posture: bool,
    segmentation_mode: Optional[str] = None,
) -> Optional[dict]:
```

Returns `{"sections", "beats", "downbeats", "lrc_content",
"structured_lyrics", "cached_result"}` where `cached_result` is the
validated cached components.json dict (R2 hit +
`_cached_components_have_llm_fields(cached["components"], classify_theme,
classify_vocal_posture, all_components=False)` true) or None. Preserve
today's internals exactly: LRC fetch via `AdminConfig.load(None)` + `R2Client`
(audio.py:2893-2894), cache check via fresh
`R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)` +
`AnalysisClient(config.analysis_url, timeout=300)` (audio.py:2946-2956),
segmentation_mode preflight messages (audio.py:2904-2940), and the "cached
lacks LLM fields" message (audio.py:2980-2983). `None` return only on
segmentation_mode preflight failure (prints its red message).

`_submit_component_analysis_job` keeps its signature and calls prep; its
cached-fast-path (parse `_parse_component_results`, `upsert_song_components`,
`_persist_recording_theme`, return components — audio.py:2958-2983) stays in
that function, driven by `inputs["cached_result"]`.

No equivalent helper exists today — this is new.

### Step 2 — Components step in the unified loop

#### 2a. `_STEP_CHAIN` (audio.py:7173)

Append `"components"` at end:

```python
_STEP_CHAIN = ["download", "lrc", "analyze", "embedding", "components"]
```

Existing tests don't select components so unaffected.

#### 2b. Submit helper (submit-only, no wait — the unified loop polls)

```python
def _submit_components_for_song(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    config: AdminConfig,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
    newly_backfilled: bool = False,
) -> Tuple[Optional[str], str]:
```

`newly_backfilled` signals that structured lyrics were just backfilled this
run (input data changed → re-identify even when existing components carry
theme/posture).

Behavior, in order:

1. `recording = db_client.get_recording_by_song_id(song_id)`; None →
   `results[sid]["components"]="skipped_no_recording"`, return
   `(None, "skipped_no_recording")`.

2. Eligibility mirror of the components stdin-batch check (audio.py:3380):
   `not recording.has_full_analysis and not recording.has_lrc` → results
   `"skipped_no_sections"` + console yellow "no sections or LRC — run audio
   lrc first", return `(None, "skipped_no_sections")`.

3. DB-first fill-missing skip (unless `force` OR `newly_backfilled`):
   `comps = db_client.get_song_components(song_id)`; skip iff comps non-empty
   AND every essential-role candidate has the LLM fields — mirror
   `_cached_components_have_llm_fields` essential logic: candidates = rows
   with `role in {"entry","exit","loop_target","entry_exit"}` or
   (`component_type=="bridge"` and `occurrence_index==1`); skip iff candidates
   and all have `theme` and `vocal_posture` (batch always classifies both).
   `newly_backfilled` bypasses this skip: structured lyrics input changed
   since the components were last identified, so re-identify even when
   theme/posture are present. **Before skipping**: if
   `recording.theme is None or recording.vocal_posture is None`, call
   `_persist_recording_theme(recording, comps, db_client)` to backfill the
   recording-level aggregate from existing component data (no submit needed —
   covers use case 3 songs whose components were classified but whose
   recording row was never updated). On skip: results `"completed"`,
   `results[sid]["components_source"]="db_existing"`, manifest entry
   `("components", "components", None, "completed")`, return
   `(None, "skipped_completed")`.

4. `inputs = _prepare_component_job_inputs(recording, song_id, config,
   console, force or newly_backfilled, True, True)`; if
   `inputs["cached_result"]`: parse +
   `db_client.upsert_song_components(song_id, recording.content_hash,
   components)` + `_persist_recording_theme(recording, components,
   db_client)` (guard: only when components non-empty), results
   `"completed"`, `components_source="r2_cache"`, manifest completed, return
   `(None, "skipped_completed")`. Note: `force or newly_backfilled` makes
   the prep pass `force=True` to `_prepare_component_job_inputs`, which
   bypasses the R2 cache check (audio.py:2943 `if not force:`) — so
   newly-backfilled songs always re-submit rather than returning stale
   cached components.json.

5. Else submit: `analysis_client.submit_component_analysis(
   audio_url=recording.r2_audio_url, content_hash=recording.content_hash,
   song_id=song_id, sections=inputs["sections"], beats=inputs["beats"],
   downbeats=inputs["downbeats"], lrc_content=inputs["lrc_content"],
   structured_lyrics=inputs["structured_lyrics"],
   force=force or newly_backfilled, snap_to_downbeat=True,
   energy_aware_roles=True, use_stems=False, classify_theme=True,
   classify_vocal_posture=True, skip_beat_cache=False, all_components=False,
   segmentation_mode=None)` — the exact flag set of the user's manual
   `--compute-all-fields` command. `AnalysisServiceError` → results
   `"failed"`, `components_error=str(e)`, manifest failed
   (`error_class=type(e).__name__`), return `(None, "failed")`.

6. Manifest entry submitted, console green
   `→ {song_id} (submitted: {job_id})`, return `(job.job_id, "submitted")`.

No DB status field exists for components — job id lives in `active_jobs` +
manifest only (resume from manifest covers crashes).

#### 2c. Poll handler

```python
def _handle_components_completion(
    song_id: str, job_id: str, job: JobInfo,
    db_client: DatabaseClient, console: Console,
    results: dict, _add_manifest_entry: Any,
) -> Tuple[bool, Optional[str]]:
```

- `job.status == "completed"`: fetch recording (vanished → failed, pattern
  of `_handle_analysis_completion` audio.py:7835-7849). `raw =
  getattr(job.result, "components", None)` (guard against result-schema
  variants). Empty/None → results `"completed"`, `components_count=0`,
  console yellow "no components extracted", manifest completed. Non-empty:
  `_parse_component_results(raw, song_id, recording.content_hash)`; wrap
  `upsert_song_components` + `_persist_recording_theme` in try/except — on
  exception results `"failed"` + `components_error` + manifest failed +
  return `(True, None)` (prevents the poll loop's generic `except Exception`
  at audio.py:8380-8381 from retrying a permanently failing DB write
  forever). Success: results `"completed"` +
  `components_count=len(components)`, manifest completed, console
  `✓ {title} — components completed (N)`, return `(True, None)`.
- `"failed"`/`"cancelled"` → results `"failed"` + `components_error`,
  manifest failed, `(True, None)`.
- Else `(False, None)`.

#### 2d. Wire into dispatch + cascade

- `_submit_step` (audio.py:7429): add `elif step == "components":` returning
  `_submit_components_for_song(..., newly_backfilled=newly_backfilled)`;
  add `config: AdminConfig` and `newly_backfilled: Optional[set[str]] = None`
  parameters.
- `_advance_song` (audio.py:7494): add `config: AdminConfig` and
  `newly_backfilled: Optional[set[str]] = None` parameters, pass into
  `_submit_step`; add `"skipped_no_sections"` to the skip-status tuple
  (audio.py:7547-7553).
- `_poll_one_cycle` (audio.py:8193): add `config: AdminConfig` and
  `newly_backfilled: Optional[set[str]] = None` parameters; pass to
  `_advance_song` (audio.py:8228, 8302, 8338). Add dispatch branch:
  ```python
  elif step == "components":
      is_terminal, new_job_id = _handle_components_completion(
          song_id, job_id, job, db_client, console, results,
          _add_manifest_entry,
      )
  ```
  (audio.py:8285-8295 region).

#### 2e. Fix 404 branch for components

The generic 404 else-branch (audio.py:8356-8377) calls
`db_client.update_recording_status(hash_prefix=hash_prefix,
**{f"{step}_status": "failed"})`. For `step == "components"` this passes
`components_status` — a non-existent column. Guard it:

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
- `--all-steps` (audio.py:6350):
  ```python
  selected_steps = ["download", "backfill_lyrics", "lrc", "analyze",
                    "embedding", "components"]
  ```
  Order: `backfill_lyrics` runs before `_process_batch` (audio.py:6478),
  then download/lrc/analyze/embedding/components run in the unified loop.
- No-step-flags error (audio.py:6352-6356): mention `--components`.
- `--backfill-lyrics` conflict check (audio.py:6361): add `if all_steps:`
  guard before the conflict check so `--all-steps` is not rejected for
  including `backfill_lyrics` alongside download/analyze/embedding/components.
  The conflict check still fires for explicit `--backfill-lyrics --download`
  etc. — only `--all-steps` bypasses it. Conflicting set stays
  `{"download", "analyze", "embedding"}` — `--backfill-lyrics --components`
  and `--backfill-lyrics --lrc --components` are allowed (fill-missing pair).
  `--backfill-lyrics --force --components` is also allowed (force scoping
  fix below).
- Force scoping (audio.py:6371-6397): change `len(selected_steps) != 1` to
  `len(non_backfill_steps) != 1` at audio.py:6381. This allows
  `--force --backfill-lyrics --components` (non_backfill_steps=["components"],
  len=1, passes) while still blocking `--force --lrc --analyze`
  (non_backfill_steps=["lrc","analyze"], len=2, fails) and
  `--force --backfill-lyrics --lrc --components` (non_backfill_steps=["lrc",
  "components"], len=2, fails). Update error string at audio.py:6383-6384 to
  list `--components`. The `--force --download` block (audio.py:6387) still
  works: `non_backfill_steps=["download"]`, len=1, `"download" in
  non_backfill_steps` → blocked.
- `_print_dry_run_v4` (audio.py:6679): for each with-recording song add one
  dim line `Components: {len(db_client.get_song_components(song_id))} row(s)`
  so dry-run shows what's missing.
- Docstring examples (audio.py:6269-6273): replace with:
  ```
  sow-admin audio batch --album 深愛耶穌 --all-steps
  sow-admin audio batch --album 深愛耶穌 --backfill-lyrics --lrc --components
  sow-admin audio batch --album 深愛耶穌 --backfill-lyrics --force --components
  ```
  Note `--all-steps` is the one command that fills everything missing and
  re-identifies components whose structured-lyrics input changed; note
  `--all-steps` includes backfill-lyrics + components and fetches structured
  lyrics during download for new songs; note components auto-skips songs
  already classified with recording-level theme/posture (use `--force
  --components` to force reclassify everything, or `--backfill-lyrics --force
  --components` to re-identify after backfilling structured lyrics for all
  songs).

### Step 4 — Backfill-lyrics skip-if-present

In both batch loops that drive `_backfill_lyrics_for_song`, skip when the
recording already has parseable structured lyrics with sections:

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
  `~ Skipped (already has structured lyrics): {sid}` and counting it in
  neither success nor failed (track `skipped` count, add to summary line).
  The single-song explicit `audio download <id> --backfill-lyrics` path
  stays always-refetch.

### Step 5 — Structured lyrics in batch download path (use case 1)

The batch download path (`_download_and_create_recording`, audio.py:6756-6859)
creates a `Recording` without `structured_lyrics` or `structured_lyrics_raw`,
unlike the single-song `import_youtube_audio_for_song` (audio.py:1146-1204)
which calls `_fetch_structured_lyrics`. Fix: mirror the single-song path so
`--all-steps` on a new song yields structured-lyrics-backed component
identification.

In `_download_and_create_recording` (audio.py:6756):
- Add `use_llm: bool = True` parameter.
- After `download_with_info` returns `youtube_url` (audio.py:6791), before
  constructing `Recording` (audio.py:6838):
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
          console.print(
              f"  [yellow]Structured lyrics fetch failed (non-fatal): {e}[/yellow]"
          )
  ```
  `except Exception` catches `typer.Exit(1)` (subclass of `Exception` via
  `click.exceptions.Exit`) which `_fetch_structured_lyrics` raises on LLM
  parse failure with `use_llm=True` — critical in the thread context where
  `typer.Exit` would otherwise propagate to `_download_worker`'s
  `except Exception` and mark the download as failed.
- Add to `Recording` constructor (audio.py:6838-6849):
  `structured_lyrics_raw=structured_raw, structured_lyrics=structured_json_str,`
  — `insert_recording` persists these columns (proven by
  `import_youtube_audio_for_song` audio.py:1193-1206 using the same pattern).

`_download_if_needed` (existing-recording re-download path) is NOT modified
— structured lyrics for existing recordings are handled by
`--backfill-lyrics`.

### Step 6 — Pipeline threading (config, use_llm, newly_backfilled)

Consolidated parameter threading through the batch pipeline. All defaults
are None/True so existing callers and tests are unaffected.

#### 6a. Thread `config` + `newly_backfilled` through loop functions

| Function | New params | Passes to |
|---|---|---|
| `_process_batch` (8389) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_advance_song` calls (8546, 8302 via `_poll_one_cycle`, 8338), `_poll_one_cycle`, `_reconcile_on_interrupt` |
| `_poll_one_cycle` (8193) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_advance_song` (8228, 8302, 8338) |
| `_advance_song` (7494) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_submit_step` |
| `_submit_step` (7429) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_submit_components_for_song` (passes `newly_backfilled=(song_id in (newly_backfilled or set()))`) |
| `_resume_from_manifest` (8717) | `config: AdminConfig`, `newly_backfilled: Optional[set[str]] = None` | `_poll_one_cycle` |

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
"newly backfilled" (their structured lyrics are already present), so
components fill-missing applies normally. Caller in `batch()` resume path
(audio.py:6437) passes `config` (loaded at audio.py:6407).

### Step 7 — Interrupt reconciliation fix

`_reconcile_on_interrupt` (audio.py:8639) currently receives `{sid: jid}`
collapsed from `(sid, step)` keys — two active steps for one song collide,
and every job is treated as LRC. Change signature to take
`active_jobs: Dict[Tuple[str, str], str]` and iterate `(song_id, step),
job_id`:

- `lrc`: unchanged R2 check (audio.py:8665-8688).
- `components`: try `AnalysisClient`-free cache check —
  `_prepare_component_job_inputs(recording, song_id, config, console,
  force=False, True, True)`; if `cached_result` present, parse + upsert +
  persist theme, results `"completed"`; else results `"failed"` +
  `components_error="Batch interrupted"` + console red note (no DB status
  field to update).
- `analyze`/`embedding`: leave DB untouched; console dim note "job continues
  server-side; `audio status --reconcile` catches late completions" (existing
  tip at audio.py:8691-8694 already says this).
- Callers (audio.py:8597-8602, 8880-8886): pass `active_jobs` directly
  (remove the `{sid: jid for (sid, step), jid in ...}` collapse);
  `_process_batch` needs `config` anyway (Step 6a) — pass it to
  `_reconcile_on_interrupt` too (new param).

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

Add `elif step == "components":` — DB-first skip if essential candidates
already have theme+posture (same rule as Step 2b.3) AND `recording.theme`
and `recording.vocal_posture` are both non-None; else if
`job.status == "completed"` and `getattr(job.result, "components", None)`
non-empty: parse + upsert + `_persist_recording_theme` (guard `recording`
non-None; wrapped by the function's existing try/except).

### Step 9 — Tests

`ops/admin-cli/tests/admin/test_audio_batch_v4.py` (CliRunner against real
app; config to `postgresql://invalid/invalid`, `WIDE_ENV`):

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
  (list_recordings_with_songs → `[]`, list_songs → `[song]`,
  get_recording_by_song_id → None so unrecorded path), patch `audio.R2Client`,
  `audio.AnalysisClient`, and `audio._process_batch` with a capture Mock
  returning `{}`; invoke `audio batch --album X --all-steps --config …`;
  assert captured `selected_steps` ==
  `["download","backfill_lyrics","lrc","analyze","embedding","components"]`.
- Backfill skip: patch `audio._backfill_lyrics_for_song` (must NOT be
  called) + db mock whose `get_recording_by_song_id` returns a Recording
  with `structured_lyrics='{"sections":[{"type":"verse"}]}'` and
  `youtube_url` set; invoke `audio batch --album X --backfill-lyrics
  --config …`; assert output contains `skipped: structured lyrics already
  present` and patched function not called. Also a
  `_backfill_lyrics_batch` direct test (patch `sys.stdin` via
  `io.StringIO`, same mock) asserting the summary counts a skip.

`ops/admin-cli/tests/admin/test_audio_batch_unified.py` (unit, MagicMock
db/clients; follow `_make_recording`/`_make_song` helpers):

- `TestSubmitComponentsForSong`:
  - no recording → `(None, "skipped_no_recording")`;
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
    re-identify on changed input);
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
  ["lrc","components"], …)` with db recording `lrc_status="completed"` →
  `active_jobs` gains `(sid, "components")` (mock
  `analysis_client.submit_component_analysis`).
- `TestReconcileOnInterrupt`: active_jobs with `("s1","components")` and
  cached_result present → upsert called; with `("s1","analyze")` →
  `db.update_recording_lrc` NOT called (regression guard for the collapse
  bug).
- `TestDownloadStructuredLyrics`: patch
  `audio._fetch_structured_lyrics` to return
  `("raw", '{"sections":[]}', "youtube")`; call
  `_download_and_create_recording(..., use_llm=True)` with a mock
  downloader returning a temp audio file + `youtube_url="https://..."`;
  assert the returned Recording has `structured_lyrics='{"sections":[]}'`
  and `structured_lyrics_raw="raw"`. Also test non-fatal failure: patch to
  raise `typer.Exit(1)` → Recording still created with
  `structured_lyrics=None`.

Run:
```bash
cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 \
  --extra admin --extra test pytest \
  tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py -v
```
then the full admin-cli suite.

## Critical files & anchors

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — all
  production edits; anchors:
  - `_STEP_CHAIN`:7173
  - `_submit_component_analysis_job`:2812 (refactor source)
  - `batch()`:6207 (flags/validation)
  - backfill loop:6478
  - `_backfill_lyrics_batch`:1271
  - `_download_and_create_recording`:6756 (structured lyrics fix)
  - `_download_worker`:8074
  - `_submit_step`:7429
  - `_advance_song`:7494
  - `_poll_one_cycle`:8193
  - `_process_batch`:8389
  - `_reconcile_on_interrupt`:8639
  - `_apply_manifest_writeback`:8894
  - `_print_stats`:8986
  - `_print_unified_progress`:8164
  - `_print_dry_run_v4`:6679
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` —
  read-only reference: `submit_component_analysis`:620 (payload contract),
  `get_cached_component_result`:744, `_cached_components_have_llm_fields`:141.
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — read-only
  reference: `get_song_components`:2159, `upsert_song_components`:2071,
  `update_recording_theme`:991.
- `ops/admin-cli/tests/admin/test_audio_batch_v4.py` — CLI validation
  tests; helpers `_make_recording`:336, `_make_song`:356.
- `ops/admin-cli/tests/admin/test_audio_batch_unified.py` — unified-loop
  unit tests; `_make_recording` helper + `TestHandleAnalysisCompletion`
  patterns to mirror.

## Verification

1. Unit/CLI:
   ```bash
   cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 \
     --extra admin --extra test pytest \
     tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py -v
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
   statuses and a `Components: N row(s)` line; zero YouTube/R2/LLM calls.
5. Use-case-2 CLI validation:
   ```bash
   zsh -ic 'sow_admin audio batch --album 深愛耶穌 --backfill-lyrics --force --components --dry-run'
   ```
   — expect no error (force scoping accepts the combination); dry-run
   proceeds.
6. User-gated live check (costs YouTube + LLM tokens; run only with user's
   go-ahead):
   ```bash
   zsh -ic 'sow_admin audio batch --album 深愛耶穌 --all-steps'
   ```
   — exercises the full mixed-album flow: songs missing structured lyrics
   get backfilled (then components re-identified), songs with structured
   lyrics + missing component theme get components submitted, songs with
   components but missing recording-level theme get aggregate backfilled,
   new songs get downloaded (with structured lyrics) → LRC → components.
   Verify with `sow_admin audio show <song_id>` that
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
  songs with structured lyrics already present); explicit single-song call
  still force-refetches.
- Components job ids are tracked in the manifest + memory only (no
  `recordings.components_status` column added); a crashed run resumes via
  `--resume <manifest>`. On resume, `newly_backfilled` is None (structured
  lyrics already present from the original run), so components fill-missing
  applies normally.
- `--force --backfill-lyrics --components` is the explicit "re-identify
  everything" command (alternative to `--all-steps` when you want to force
  re-identification of ALL songs regardless of backfill status): backfill
  always overwrites structured lyrics (B7), force bypasses the components
  DB-first skip (Step 2b.3 "unless force") and R2 cache check (Step 1
  preserves `if not force:`), so components are re-submitted for all songs.
  `--force` applies to the non-backfill step (components) only; backfill-
  lyrics is always-overwrite and does not need force.
- Structured lyrics fetch in the batch download path is non-fatal: a
  failure (YouTube metadata unreachable, LLM parse error, zanmei down) sets
  `structured_lyrics=None` on the Recording and the download still
  succeeds; components will use fallback segmentation. The user can
  `--backfill-lyrics` later.

## Changes from v1

1. **404 branch fix for components** (Step 2e): the generic 404 else-branch
   calls `update_recording_status(**{f"{step}_status": "failed"})` — for
   `step=="components"` this passes a non-existent `components_status`
   column. Guard with `if step != "components":` before the DB call. v1
   missed this.
2. **`_print_unified_progress` update** (Step 8a): add components counts to
   the one-line progress display. v1 omitted this.
3. **Consolidated threading** (Step 6): merged v1's Steps 3, 5b, 5c into a
   single section with a table for clarity.
4. **Clarified fast-tier interaction**: explicit note that
   `has_full_analysis` is False for fast-tier songs, and components
   eligibility depends on `has_lrc` (from the LRC step) rather than
   `has_full_analysis`.
5. **Restructured to lead with user intent**: use cases and confirmed
   design decisions up front, implementation details after.