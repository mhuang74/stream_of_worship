# audio batch: components step + fill-missing (use-case-verified revision)

## Context

`sow-admin audio batch` must run the full metadata chain natively with fill-missing semantics, replacing the two manual piped commands. Three use cases drive the design:

1. **New catalog song, no recording** (`--all-steps`): download → structured lyrics filled → LRC → analysis → embedding → components + theme/posture.
2. **Existing song missing structured lyrics** (`--backfill-lyrics --components --force`): lyrics backfilled, components re-identified from structured lyrics, component metadata regenerated.
3. **Existing song with structured lyrics but missing theme/posture or audio metadata** (`--components`, optionally `--analyze`): component-level LLM fields re-run; recording-level theme/posture re-aggregated; recording-level BPM/key/energy via `--analyze`.

All production edits in `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`. Run tests from `ops/admin-cli/` (never repo root — torch import breaks).

Verified code facts this plan relies on:
- `_STEP_CHAIN` audio.py:7173 = `["download","lrc","analyze","embedding"]`; `_submit_step`:7429; `_advance_song`:7494 (skip-status tuple 7547-7554); `_poll_one_cycle`:8193 (dispatch 8258-8297, 404 else-branch 8356-8377); `_process_batch`:8389 (executor.submit 8523, no-download advance 8545, poll call 8565, KeyboardInterrupt 8589-8605); `_reconcile_on_interrupt`:8639; `_resume_from_manifest`:8717 (reconcile call 8880); `_apply_manifest_writeback`:8894; `_print_stats`:8986; `_print_unified_progress`:8164; `_print_dry_run_v4`:6679; `batch()`:6207 (step_flags 6340, `--all-steps` 6349, no-flags error 6351, backfill conflict 6359, force scoping 6370-6397); backfill pre-loop 6478-6528; `_backfill_lyrics_batch`:1271; `_backfill_lyrics_for_song`:916; `_fetch_structured_lyrics`:824 (keyword-only, returns `(structured_raw, structured_json_str, source_used)`); `_download_worker`:8074 (new-recording branch 8103-8115, eager LRC 8127-8146); `_submit_component_analysis_job`:2812 (gather 2867-2902, segmentation preflight 2904-2940, R2 cache check 2942-2983); `_persist_recording_theme`:2794; `_parse_component_results`:3084; stdin eligibility check 3380; `--compute-all-fields` flag override 3247-3251; `_resolve_lyrics_text`:727.
- `db/client.py`: `update_recording_status`:911 accepts ONLY analysis/lrc status+job kwargs (no `components_status` — a `**{"components_status": ...}` call raises TypeError); `update_recording_structured_lyrics`:962; `update_recording_theme`:991; `upsert_song_components`:2071 (DELETE+INSERT, validates theme/posture against enum sets); `get_song_components`:2159.
- `services/analysis.py`: `submit_component_analysis`:620 payload contract; `get_cached_component_result`:744; `_cached_components_have_llm_fields`:141 (essential roles `{"entry","exit","loop_target","entry_exit"}` or bridge occurrence_index==1); `JobInfo.result` is `AnalysisResult` with `components: Optional[List[Dict]]` (line 75), populated by `_parse_job_response` (1066-1070).
- Analysis-service worker computes per-component BPM/key/energy for ALL components regardless of identification source (`ops/analysis-service/src/sow_analysis/workers/components.py:7-9`), so "essential candidates have theme+posture" is a sound proxy for "this run computed audio metadata too".
- Existing tests that signature changes break: `tests/admin/test_audio_batch_unified.py` — 5 `_advance_song` callsites (157, 186, 203, 221, 239), 2 `_poll_one_cycle` (558, 612), 1 `_resume_from_manifest` (699); `tests/admin/test_audio_lrc_visibility.py:229` — `_reconcile_on_interrupt` with old `{song_id: job_id}` shape.

## Approach

Order matters: step 1 is a pure refactor (verify no regressions before continuing); steps 2-8 are independent of each other except where noted; step 9 last.

### 1. Extract `_prepare_component_job_inputs` (behavior-preserving)

Extract audio.py:2867-2983 (gather + segmentation preflight + R2-cache check) from `_submit_component_analysis_job` into:

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

Returns `{"sections", "beats", "downbeats", "lrc_content", "structured_lyrics", "cached_result"}` where `cached_result` is the validated cached components.json dict (R2 hit + `_cached_components_have_llm_fields(cached["components"], classify_theme, classify_vocal_posture, all_components=False)`) or None. `None` return only on segmentation preflight failure (prints its red message).

Preserve internals exactly, with one intentional micro-fix: the LRC fetch today does `config = AdminConfig.load(None)` (audio.py:2893) which SHADOWS the function's config parameter — in the helper, assign to a separate local `lrc_config = AdminConfig.load(None)` + `R2Client(lrc_config.r2_bucket, lrc_config.r2_endpoint_url)` so the helper's `config` parameter stays intact; the R2 cache check (fresh `R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)` + `AnalysisClient(analysis_url, timeout=300)`, audio.py:2946-2956) then uses the passed-in config/analysis_url (today it accidentally uses the reloaded default — the passed-in one is strictly more correct and identical in practice since all callers pass `config.analysis_url`). Keep the segmentation_mode preflight messages (2904-2940) and the "cached lacks LLM fields" message (2980-2983) verbatim.

`_submit_component_analysis_job` keeps its signature; it calls prep and drives its cached fast path (parse via `_parse_component_results`, `upsert_song_components`, `_persist_recording_theme`, return components — audio.py:2958-2979) from `inputs["cached_result"]`. No equivalent helper exists today — this is new.

### 2. Components step in the unified loop

- `_STEP_CHAIN` (audio.py:7173) → `["download", "lrc", "analyze", "embedding", "components"]`. Existing tests don't select components, so unaffected.
- New DB-fill predicate (no equivalent exists):

```python
def _db_components_have_llm_fields(comps: list[SongComponent]) -> bool:
```

candidates = rows with `role in {"entry","exit","loop_target","entry_exit"}` or (`component_type=="bridge"` and `occurrence_index==1`); return `bool(candidates) and all(c.theme and c.vocal_posture for c in candidates)`. Mirrors `_cached_components_have_llm_fields` essential logic for SongComponent rows.

- New submit helper (submit-only; the unified loop polls):

```python
def _submit_components_for_song(
    song_id: str, db_client: DatabaseClient, analysis_client: AnalysisClient,
    config: AdminConfig, force: bool, console: Console,
    results: dict, _add_manifest_entry: Any,
) -> Tuple[Optional[str], str]:
```

Behavior, in order:
1. `recording = db_client.get_recording_by_song_id(song_id)`; None → `results[sid]["components"]="skipped_no_recording"`, return `(None, "skipped_no_recording")`. Also `not recording.r2_audio_url` → same status (mirrors `_submit_analysis_for_song` audio.py:7220).
2. Eligibility mirror of the stdin check (audio.py:3380): `not recording.has_full_analysis and not recording.has_lrc` → `"skipped_no_sections"` + console yellow "no sections or LRC — run audio lrc first", return `(None, "skipped_no_sections")`.
3. DB-first fill-missing skip (unless `force`): `comps = db_client.get_song_components(song_id)`; if `_db_components_have_llm_fields(comps)`: call `_persist_recording_theme(recording, comps, db_client)` (re-aggregate recording-level theme/posture — covers songs whose components exist but `recordings.theme` is NULL; idempotent, self-guarding on empty aggregate), results `"completed"`, `results[sid]["components_source"]="db_existing"`, manifest entry `("components", "components", None, "completed")`, return `(None, "skipped_completed")`.
4. `inputs = _prepare_component_job_inputs(recording, song_id, config.analysis_url, config, console, force, True, True)`; if `inputs["cached_result"]`: parse + `db_client.upsert_song_components(song_id, recording.content_hash, components)` + `_persist_recording_theme(...)` guarded by non-empty components, results `"completed"`, `components_source="r2_cache"`, manifest completed, return `(None, "skipped_completed")`.
5. Else submit with the exact `--compute-all-fields` flag set (audio.py:3247-3251): `analysis_client.submit_component_analysis(audio_url=recording.r2_audio_url, content_hash=recording.content_hash, song_id=song_id, sections=inputs["sections"], beats=inputs["beats"], downbeats=inputs["downbeats"], lrc_content=inputs["lrc_content"], structured_lyrics=inputs["structured_lyrics"], force=force, snap_to_downbeat=True, energy_aware_roles=True, use_stems=False, classify_theme=True, classify_vocal_posture=True, skip_beat_cache=False, all_components=False, segmentation_mode=None)`. On `Exception` (broad — a crash here would kill `_advance_song` outside the poll try/except): results `"failed"`, `components_error=str(e)`, manifest failed (`error_class=type(e).__name__`), return `(None, "failed")`.
6. Manifest entry `(song_id, recording.hash_prefix, "components", "components", job.job_id, "submitted", submitted_at=...)`, console `  [green]→ {song_id} (submitted: {job.job_id})[/green]`, return `(job.job_id, "submitted")`.

No DB status field for components — job id lives in `active_jobs` + manifest only; do NOT call `db_client.update_recording_status` anywhere in the components flow.

- New poll handler:

```python
def _handle_components_completion(
    song_id: str, job_id: str, job: JobInfo, db_client: DatabaseClient,
    console: Console, results: dict, _add_manifest_entry: Any,
) -> Tuple[bool, Optional[str]]:
```

- `job.status == "completed"`: fetch recording (vanished → failed + manifest failed + `(True, None)`, pattern of `_handle_analysis_completion`). `raw = getattr(job.result, "components", None)`. Empty/None → results `"completed"`, `components_count=0`, console yellow "no components extracted", manifest completed, `(True, None)`. Non-empty: `components = _parse_component_results(raw, song_id, recording.content_hash)`; wrap `upsert_song_components` + `_persist_recording_theme` in try/except — on exception results `"failed"` + `components_error` + manifest failed + return `(True, None)` (prevents the poll loop's generic `except Exception` at audio.py:8380-8381 from retrying a permanently failing DB write forever). Success: results `"completed"` + `components_count=len(components)`, manifest completed, console `✓ {title} — components completed (N)`, `(True, None)`.
- `"failed"`/`"cancelled"` → results `"failed"` + `components_error`, manifest failed, `(True, None)`. Else `(False, None)`.

- `_submit_step` (audio.py:7429): add `config: AdminConfig` parameter and `elif step == "components":` branch calling `_submit_components_for_song`.
- `_advance_song` (audio.py:7494): add `config: AdminConfig` parameter, pass into `_submit_step`; add `"skipped_no_sections"` to the skip-status tuple (7547-7554).
- `_poll_one_cycle` (audio.py:8193): add `config: AdminConfig` parameter; pass to all `_advance_song` calls (8228, 8302, 8338); add dispatch branch `elif step == "components": is_terminal, new_job_id = _handle_components_completion(song_id, job_id, job, db_client, console, results, _add_manifest_entry)` in the 8258-8297 region. In the 404 generic else-branch (8356-8377): guard the DB write — `if step != "components": db_client.update_recording_status(...)` (no `components_status` kwarg exists; the unguarded `**{f"{step}_status": "failed"}` raises TypeError) — and fix the tier label (8372) to `"analysis_tier" if step == "analyze" else step`.
- `_print_unified_progress` (audio.py:8164): add components active/done counts; lines become `pending(down/lrc/ana/emb/comp)=…` and `✓(lrc/ana/emb/comp)=…`.

### 3. Structured lyrics for new downloads (UC1)

`_download_worker` (audio.py:8074): add `use_llm: bool` parameter (append after `eager_lrc`; sole caller is the `executor.submit` at 8523). In the newly-created-recording branch (after `_download_and_create_recording` succeeds, BEFORE the eager-LRC block at 8127 so `_submit_lrc_for_song`'s re-fetch at 7061 sees them):

```python
try:
    structured_raw, structured_json_str, _src = _fetch_structured_lyrics(
        youtube_url=recording.youtube_url, song_title=song.title,
        band=song.composer, source="auto", use_llm=use_llm, console=quiet_console,
    )
    thread_db.update_recording_structured_lyrics(
        hash_prefix=recording.hash_prefix,
        structured_lyrics_raw=structured_raw, structured_lyrics=structured_json_str,
    )
    updates["structured_lyrics"] = "completed"
except Exception as e:
    updates["structured_lyrics"] = "failed"
    updates["structured_lyrics_error"] = str(e)
```

Non-fatal: LRC falls back to `songs.lyrics_raw` via `_resolve_lyrics_text`. Only for NEW recordings — existing recordings keep `--backfill-lyrics` (parity with the `audio download` command, which always fetches lyrics for recordings it creates, audio.py:1150). Record status in `updates` (merged into `results` under `results_lock`); no new stats printing.

`_process_batch` (audio.py:8389): add `config: AdminConfig` and `use_llm: bool = True` parameters; pass `use_llm` into `executor.submit`; pass `config` into `_poll_one_cycle`, the no-download `_advance_song` call (8546), and `_reconcile_on_interrupt` (8597). `batch()` caller (6553) passes `config=config, use_llm=use_llm`.

`_poll_one_cycle` (8193): add `config: AdminConfig` parameter; pass to `_advance_song` (8228, 8302, 8338).

`_resume_from_manifest` (8717): add `config: AdminConfig` parameter; pass into `_poll_one_cycle` and its `_reconcile_on_interrupt` call (8880). Caller in `batch()` (6437) passes `config`.

### 4. Backfill-lyrics fill-missing (UC2 precondition)

In both batch loops that drive `_backfill_lyrics_for_song`, skip when the recording already has parseable structured lyrics with sections:

- `batch()` pre-loop (audio.py:6483-6507), after the no-recording / no-youtube-url checks:
  ```python
  if recording.structured_lyrics:
      try:
          existing = json.loads(recording.structured_lyrics)
      except (json.JSONDecodeError, TypeError):
          existing = None
      if existing and existing.get("sections"):
          console.print(f"  [yellow]→ {sid} (skipped: structured lyrics already present)[/yellow]")
          backfill_results[sid]["backfill_lyrics"] = "skipped"
          continue
  ```
- `_backfill_lyrics_batch` (audio.py:1304-1324): same check inside the per-song loop before calling `_backfill_lyrics_for_song`; print `~ Skipped (already has structured lyrics): {sid}`; count in a new `skipped` counter (neither success nor failed); summary line becomes `f"[bold]Summary:[/bold] {success} backfilled, {skipped} skipped, {failed} failed"`. The single-song explicit `audio download <id> --backfill-lyrics` path (audio.py:1621) stays always-refetch.

### 5. Force scoping for backfill + one pipeline step (UC2 one-command)

audio.py:6379-6386: change `if non_backfill_steps and len(selected_steps) != 1:` to `if non_backfill_steps and len(non_backfill_steps) != 1:` and update the error string to list `--components`:
`"--force requires exactly one pipeline step (--download, --lrc, --analyze, --embedding, or --components)."`.
Effects: `--force --backfill-lyrics` still allowed (B7); `--force --backfill-lyrics --components` now allowed (UC2 one-command); `--force --lrc --analyze` still rejected; `--force --download` rejection (6387-6397) unchanged. Force does not override the structured-lyrics skip (fill-missing is unconditional in step 4).

### 6. CLI surface on `batch` (audio.py:6207)

- New option after `embedding` (audio.py:6227):
  ```python
  components: bool = typer.Option(
      False, "--components",
      help="Run the component analysis step (snap-to-downbeat, energy roles, LLM theme + vocal posture classification)",
  ),
  ```
- `step_flags` (audio.py:6340): add `"components": components`.
- `--all-steps` (audio.py:6349-6350): `selected_steps = ["download", "lrc", "analyze", "embedding", "components"]`.
- No-step-flags error (6351-6357): mention `--components`.
- `--backfill-lyrics` conflict check (6359-6368): conflicting set stays `{"download", "analyze", "embedding"}`; update the message tail to "Only --lrc and --components are allowed alongside --backfill-lyrics."
- `_print_dry_run_v4` (audio.py:6679): for each with-recording song add dim lines — `Structured lyrics: yes/no` when `"backfill_lyrics" in selected_steps` (parse like step 4: sections non-empty = yes), and `Components: {len(db_client.get_song_components(song_id))} row(s)` when `"components" in selected_steps`.
- Docstring (6263-6274): update summary line to include components; add recipes:
  ```
  sow-admin audio batch --album X --all-steps                      # new songs end-to-end
  sow-admin audio batch --album X --backfill-lyrics --components --force   # re-identify from structured lyrics
  sow-admin audio batch --album X --components                     # fill missing component/LLM metadata
  ```
  Note: `--all-steps` includes components; components auto-skips classified songs (`--force --components` to reclassify, one pipeline step); new downloads fetch structured lyrics during the download step.

### 7. Interrupt reconciliation fix (required by multi-step active_jobs)

`_reconcile_on_interrupt` (audio.py:8639) currently receives `{sid: jid}` collapsed from `(sid, step)` keys — two active steps for one song collide, and every job is treated as LRC. Change signature to `(active_jobs: Dict[Tuple[str, str], str], results, db_client, r2_client, console, config)` and iterate `for (song_id, step), job_id in active_jobs.items()`:
- `lrc`: unchanged R2 check (8665-8688).
- `components`: cache-first reconcile — `recording` fetch; `inputs = _prepare_component_job_inputs(recording, song_id, config.analysis_url, config, console, force=False, True, True)`; if `inputs and inputs["cached_result"]`: parse + upsert + `_persist_recording_theme`, results `"completed"`; else results `"failed"` + `components_error="Batch interrupted"` + console red note (no DB status field to update).
- `analyze`/`embedding`: leave DB untouched; console dim note (the existing tip at 8691-8694 already covers late completions).
- Callers: audio.py:8597-8602 and 8880-8886 pass `active_jobs` directly (drop the dict comprehension) plus `config`.

### 8. Stats + resume writeback (UC3 recording-level theme)

- `_print_stats` (audio.py:8986): after the Embedding block (9100-9113) add a Components block — completed (`r.get("components") == "completed"`), failed, skipped (`in ("skipped_no_sections", "skipped_no_recording")`), shown when any nonzero; and a failed-components list after the failed-embeddings block (9154-9165 pattern) printing `components_error`.
- `_apply_manifest_writeback` (audio.py:8894): in the DB-first short-circuit section add `elif step == "components": comps = db_client.get_song_components(song_id); if _db_components_have_llm_fields(comps): if recording: _persist_recording_theme(recording, comps, db_client); return` — then after the `job.status != "completed"` guard add `elif step == "components": raw = getattr(job.result, "components", None) if job.result else None; if raw: components = _parse_component_results(raw, song_id, recording.content_hash); db_client.upsert_song_components(song_id, recording.content_hash, components); if recording: _persist_recording_theme(recording, components, db_client)` (recording may be None from the fetch above — guard; the function's existing try/except wraps everything).

### 9. Tests

Update existing callsites broken by signature changes:
- `tests/admin/test_audio_batch_unified.py`: add `config=MagicMock()` to the 5 `_advance_song` callsites (157, 186, 203, 221, 239), 2 `_poll_one_cycle` callsites (558, 612), and `_resume_from_manifest` (699).
- `tests/admin/test_audio_lrc_visibility.py:229`: `_reconcile_on_interrupt(active_jobs={("song_1", "lrc"): "lrc-job-1"}, …, config=MagicMock())`.

New tests:

`tests/admin/test_audio_batch_v4.py` (CliRunner against real app; config `postgresql://invalid/invalid`, `WIDE_ENV`; helpers `_make_recording`:336, `_make_song`:356):
- `--components` alone passes step-flag validation (assert `"No step flags selected" not in result.output`).
- `--force --components` passes force scoping (assert `"exactly one step flag" not in result.output`).
- `--force --backfill-lyrics --components` passes force scoping (same assertion) — UC2 regression guard.
- `--backfill-lyrics --components` passes mutual exclusivity (assert `"cannot be combined" not in result.output`).
- `--all-steps` includes components: patch `stream_of_worship.admin.commands.audio.get_db_client` → MagicMock db (`list_recordings_with_songs` → `[]`, `list_songs` → `[song]`, `get_recording_by_song_id` → None), patch `audio.R2Client`, `audio.AnalysisClient`, `audio._process_batch` with a capture Mock returning `{}`; invoke `audio batch --album X --all-steps --config …`; assert captured kwargs `selected_steps == ["download","lrc","analyze","embedding","components"]`.
- Backfill skip: patch `audio._backfill_lyrics_for_song` (must NOT be called) + db mock whose `get_recording_by_song_id` returns a Recording with `structured_lyrics='{"sections":[{"type":"verse"}]}'` and `youtube_url` set (`list_recordings_with_songs` → `[(recording, title, album, series)]`); invoke `audio batch --album X --backfill-lyrics --config …`; assert output contains `skipped: structured lyrics already present` and patched function not called. Also a `_backfill_lyrics_batch` direct test (patch `sys.stdin` via `io.StringIO`, same mock) asserting the summary counts a skip.

`tests/admin/test_audio_batch_unified.py` (unit, MagicMock db/clients; follow `_make_recording`/`_make_song` helpers at 42/54):
- `TestSubmitComponentsForSong`: no recording → `(None, "skipped_no_recording")`; recording with `lrc_status="pending"`, `analysis_status="pending"` → `(None, "skipped_no_sections")`; DB comps present (SongComponent rows with role entry/exit, theme+vocal_posture set) → `(None, "skipped_completed")`, `analysis_client.submit_component_analysis` not called, `db.update_recording_theme` called (skip-path theme persist); happy path: `submit_component_analysis` returns `JobInfo(job_id="comp-1", status="processing", job_type="component_analysis")` → `("comp-1", "submitted")`, manifest entry recorded (assert via `_add_manifest_entry` side-effect list), `db.update_recording_status` NOT called.
- `TestHandleComponentsCompletion`: completed job with `SimpleNamespace(components=[{...component dict...}])` result → `db.upsert_song_components` called once with parsed SongComponent list, `db.update_recording_theme` called, results `{"components":"completed","components_count":1}`, returns `(True, None)`; failed job → results failed; empty components → completed with count 0.
- `TestAdvanceSongComponents`: `_advance_song(sid, "lrc", ["lrc","components"], …, config=MagicMock())` with db recording `lrc_status="completed"` → `active_jobs` gains `(sid, "components")` (mock `analysis_client.submit_component_analysis`).
- `TestReconcileOnInterrupt`: active_jobs `{("s1","components"): "j1"}` with cached_result present → `db.upsert_song_components` called; with `{("s1","analyze"): "j2"}` → `db.update_recording_lrc` NOT called (regression guard for the collapse bug); a components job and an lrc job for the SAME song both reconciled without collision.

Run: `cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 --extra admin --extra test pytest tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py tests/admin/test_audio_lrc_visibility.py -v`, then the full admin-cli suite.

## Critical files & anchors

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — all production edits; anchors listed in Context.
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — read-only reference: `update_recording_status`:911 (fixed kwargs — components must NOT call it), `upsert_song_components`:2071, `get_song_components`:2159, `update_recording_theme`:991, `update_recording_structured_lyrics`:962.
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — read-only reference: `submit_component_analysis`:620, `get_cached_component_result`:744, `_cached_components_have_llm_fields`:141, `AnalysisResult.components`:75.
- `ops/admin-cli/tests/admin/test_audio_batch_unified.py` — existing callsites needing `config=MagicMock()`; patterns to mirror.
- `ops/admin-cli/tests/admin/test_audio_lrc_visibility.py` — `_reconcile_on_interrupt` callsite needing the tuple-key shape.

## Verification

1. Targeted tests: `cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 --extra admin --extra test pytest tests/admin/test_audio_batch_v4.py tests/admin/test_audio_batch_unified.py tests/admin/test_audio_lrc_visibility.py -v` — all new tests pass, no regressions.
2. Full suite: `cd ops/admin-cli && NO_COLOR=1 uv run --project . --python 3.11 --extra admin --extra test pytest -v`.
3. Help surface: `zsh -ic 'sow_admin audio batch --help'` shows `--components` and the three docstring recipes.
4. Read-only end-to-end (no side effects): `zsh -ic 'sow_admin audio batch --album 深愛耶穌 --backfill-lyrics --lrc --components --dry-run'` — expect per-song lines with LRC/Analysis statuses, `Structured lyrics:` and `Components: N row(s)` lines; zero YouTube/R2/LLM calls. Also `zsh -ic 'sow_admin audio batch --album 深愛耶穌 --all-steps --dry-run'`.
5. User-gated live checks (costs YouTube + LLM tokens; only with user's go-ahead):
   - UC3: pick one album song with LRC but no component theme → `zsh -ic 'sow_admin audio batch --song "<partial title>" --components'`; then `zsh -ic 'sow_admin audio show <song_id>'` — expect recordings.theme/vocal_posture populated and song_components rows carrying theme + bpm/key/energy.
   - UC2: `zsh -ic 'sow_admin audio batch --song "<title>" --backfill-lyrics --components --force'` — expect lyrics backfilled then components resubmitted with structured_lyrics in payload.
   - UC1: `zsh -ic 'sow_admin audio batch --song "<new song title>" --all-steps'` — expect download → structured lyrics → LRC → analyze → embedding → components cascade, recording row ends with structured_lyrics, theme, vocal_posture set.

## Assumptions & contingencies

- `--components` hardcodes the `--compute-all-fields` flag set (snap-to-downbeat, energy roles, classify theme + posture; no stems, essential-roles only). Per-song finer control remains `audio components`. If the backend rejects a flag combination, fall back to `classify_theme/classify_vocal_posture` only and note it.
- `--all-steps` semantics change (now includes components) is intended — the "fill everything" entry point. `--all-steps` does NOT backfill structured lyrics for pre-existing recordings (only new downloads get lyrics via the download worker); for existing recordings use `--backfill-lyrics`. If the user wants lyrics fill inside `--all-steps` too, add `"backfill_lyrics"` handling as a pre-loop extension — not in this change.
- UC2 requires `--force` because DB components already carry LLM fields and there is no persisted column distinguishing "identified via structured lyrics" from "identified without" — requiring explicit force keeps fill-missing cheap and re-runs explicit.
- `--backfill-lyrics` in batch/stdin-batch becomes fill-missing (skips songs with structured lyrics already present, regardless of `--force`); explicit single-song call still force-refetches.
- Components job ids tracked in manifest + memory only (no `recordings.components_status` column); a crashed run resumes via `--resume <manifest>` (manifest writeback handles late completions).
- DB-write failures inside `_handle_components_completion` mark the song failed and do not retry (permanent failure); transient network errors during polling keep the existing retry behavior.