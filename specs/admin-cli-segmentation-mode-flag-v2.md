# Implementation Plan v2: Admin CLI `--segmentation-mode` Flag

## Goal

Expose a mutually-exclusive segmentation-mode selector on `sow-admin audio analyze components` so operators can force a single identification source (LLM / lyrics-repetition / allin1) for A/B testing and debugging, without the bigger `use_llm_segmentation` one-way-OR env gate complexity. Default (no flag) preserves the existing best-available priority: allin1 sections first → LLM if enabled → lyrics-repetition.

## Relationship to v1

This v2 supersedes `admin-cli-segmentation-mode-flag-v1.md`. v1 is left unchanged for history; do NOT implement v1. v2 adds operational safety guards that v1 omitted:

1. **`--segmentation-mode` requires `--force`.** Without `--force` the CLI/worker both short-circuit on cached `components.json`, silently ignoring the mode. v2 makes this an explicit, validated precondition. (v1's examples always paired `--force` but did not enforce it.)
2. **CLI-side preflight for known-bad combos.** `--segmentation-mode llm` exits with a clear error when the recording has no LRC; `--segmentation-mode allin1` exits with a remediation hint when the recording has no cached allin1 sections. v1 let these silently return `[]`.
3. **Server-side echo-back of `segmentation_mode` in `JobInfo`.** Because `ComponentAnalysisOptions` uses pydantic's default `extra="ignore"`, a new admin-cli sending `segmentation_mode` to an OLD (not-yet-deployed) analysis-service backend would silently drop the field. v2 has the server echo the resolved mode back so the CLI can warn when the echo differs from the request.
4. **Beat-grid cache fallback applies to all modes, not just `None`.** v1's rewrite gated the defense-in-depth beat-grid cache read inside `segmentation_mode is None`, which made `repetition` mode non-apples-to-apples with default mode's repetition fallback. v2 moves the read outside the None-gate.
5. **Prominent batch-mode banner** declaring the no-fallback contract when `--stdin --segmentation-mode` are combined, so 100 silent `[]` results aren't misread as "no components exist."
6. **Drops the redundant `use_llm_segmentation=(segmentation_mode == "llm")` CLI expression** from v1's Change 5. That line referenced a kwarg that v1's Change 4 never added to `submit_component_analysis`'s signature/payload (would have raised `TypeError`). The new backend logic short-circuits on `segmentation_mode == "llm"` and does not need the legacy option.

## Design decisions (fixed)

1. **Modes are mutually exclusive sources, skip everything else.** Each mode runs ONLY that source and returns `[]` (empty) when unavailable; NO fallback chains. Default (no flag) = current best-available priority (allin1 sections first → LLM if enabled → lyrics-repetition).

2. **Scope limited to the `components` command only (single song + stdin batch).** Add the flag to the typer command; thread a new param through the `submit_component_analysis` service function. Do NOT update other callers (songset constructor backfill etc.). The service function gains the param for the CLI's benefit.

3. **`--segmentation-mode` requires `--force`.** Typer validation rejects the combination `(mode is set) and (force is False)` with a clear error. Rationale: without `--force` both the CLI (`get_cached_component_result`) and the worker (`cache_manager.get_component_result` / `r2_client.download_component_result`) return cached components without consulting the mode, silently ignoring the flag. Operators who pair `--segmentation-mode` with `--force` (as v1's examples did) see no behavior change.

4. **CLI-side preflight on the recording row.** Before submitting:
   - `--segmentation-mode llm` AND `not recording.has_lrc` → exit with red error: `"Cannot use --segmentation-mode llm: recording <song-id> has no LRC (lrc_status != 'completed'). Run \`sow-admin audio analyze lrc <song-id>\` first."`
   - `--segmentation-mode allin1` AND `not (recording.has_full_analysis and recording.sections)` → exit with red error: `"Cannot use --segmentation-mode allin1: recording <song-id> has no cached allin1 sections. Run \`sow-admin audio analyze --analysis-tier full <song-id>\` first."`
   - `--segmentation-mode repetition` AND `not recording.has_lrc` → exit with red error: `"Cannot use --segmentation-mode repetition: recording <song-id> has no LRC."`
   These preflight checks run in `_submit_component_analysis_job` AFTER the sections/beats/LRC gather block (so the values are already in hand) but BEFORE cache check / job submission.

5. **Server-side echo-back of the resolved mode.** The analysis-service worker writes the resolved `segmentation_mode` value into `JobInfo` (`segmentation_mode_resolved` field). The CLI compares the echoed value against the requested mode after job completion; if they differ (e.g., the backend is OLD and dropped the unknown field → echoes `None`), the CLI prints a red warning that the requested mode was not honored and aborts result persistence.

6. **Beat-grid cache fallback applies to all modes.** The defense-in-depth `cache_manager.get_beat_grid(content_hash)` read (currently nested inside the `segmentation_mode is None` branch) is promoted to run unconditionally when `beats` AND `downbeats` are both `None`, regardless of mode. This makes `repetition` mode apples-to-apples with the default mode's repetition fallback. `allin1` mode technically does not consume beats/downbeats, so reading the cache is a no-op cost (acceptable; cache reads are cheap).

7. **Batch-mode banner.** When `--stdin` AND `--segmentation-mode` are both set, the CLI prints a one-time yellow banner to stderr BEFORE reading stdin:

   ```
   ╔══════════════════════════════════════════════════════════════════╗
   ║ --segmentation-mode=llm ACTIVE — NO-FALLBACK CONTRACT              ║
   ║ Each song runs ONLY the 'llm' source. Empty [] results are        ║
   ║ EXPECTED when the source is unavailable (no LRC, unset API key,   ║
   ║ LLM error, invalid JSON). There is NO fallback to allin1 or       ║
   ║ lyrics-repetition. Do NOT interpret [] as "song has no components."║
   ╚══════════════════════════════════════════════════════════════════╝
   ```

   (Mode name substituted into the second line.)

## Mode behavior matrix

| Mode | What runs | What is skipped | Unavailable behavior | `source` in result |
|------|-----------|-----------------|----------------------|--------------------|
| `llm` | `segment_song` only | allin1 cached sections path AND lyrics-repetition fallback | `SOW_LLM_API_KEY` unset → returns `[]` (no fallback); LLM raises / returns empty / JSON invalid → returns `[]` | `llm_segmentation` on success; `none` on empty |
| `repetition` | `identify_from_lyrics_repetition` only (with beat-grid cache fallback) | allin1 cached sections path (even if cached sections present) AND LLM block | no LRC → CLI preflight rejects (does not reach worker) | `lyrics_repetition` or `none` |
| `allin1` | `identify_from_allin1_sections` only IF `sections` (cached allin1) provided | entire LRC block (LLM + repetition) | no sections → CLI preflight rejects (does not reach worker) | `allin1_sections` or `none` |
| `(none)` = no flag | current behavior: allin1 first (if sections), then LRC block with LLM-gate-OR + repetition fallback | nothing (current) | current fallback chain unchanged | unchanged |

## Critical files

**Analysis-service backend**
- `ops/analysis-service/src/sow_analysis/models.py` — add `segmentation_mode` to `ComponentAnalysisOptions`; add `segmentation_mode_resolved` echo field to `JobInfo` (or whichever response model the CLI polls for component-analysis job status).
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — thread `segmentation_mode` into `extract_components`; set `segmentation_mode_resolved` on the returned `JobInfo` (or result envelope).
- `ops/analysis-service/src/sow_analysis/workers/components.py` — enforce mutual exclusivity in the identification block; promote beat-grid cache fallback outside the `None`-gate.

**Admin CLI client**
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — add `segmentation_mode` param to `submit_component_analysis`; add an accessor on the polled `JobInfo` (or response dict) for `segmentation_mode_resolved`.
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — add `--segmentation-mode` typer flag; enforce `--force` co-requirement; run preflight on `recording.has_lrc`/`has_full_analysis`; print batch banner; thread through `_submit_component_analysis_job` and both call paths; verify echo vs. requested.

## Implementation changes

### Change 1 — Backend: new `segmentation_mode` option + `segmentation_mode_resolved` echo field

File: `ops/analysis-service/src/sow_analysis/models.py`. Verify the import line at the top of file already includes `Literal` and `Optional`; if not, extend the existing `from typing import ...` import.

Add the field immediately after `use_llm_segmentation`:

```python
    segmentation_mode: Optional[Literal["llm", "repetition", "allin1"]] = None
```

Add a corresponding echo field on the `JobInfo` model (or the component-analysis result envelope the CLI polls — verify the exact class name in `models.py`):

```python
    segmentation_mode_resolved: Optional[Literal["llm", "repetition", "allin1"]] = None
```

The `segmentation_mode` field is a per-job option; when set (a non-`None` value) it is mutually exclusive and overrides both `use_llm_segmentation` and the env gate in the worker. The `segmentation_mode_resolved` field is set by the worker to the value it actually applied (echoing the request for sanity-check; in the `None` default path it echoes back `None`).

### Change 2 — Backend: thread `segmentation_mode` through queue.py and echo back resolved value

File: `ops/analysis-service/src/sow_analysis/workers/queue.py` near the existing `extract_components(...)` call. Add one line mirroring the existing `use_llm_segmentation=request.options.use_llm_segmentation`:

```python
                        segmentation_mode=request.options.segmentation_mode,
```

This must appear alongside the existing `use_llm_segmentation=...` argument in the `extract_components(...)` call.

After `extract_components` returns (or at the point the `JobInfo` / result envelope is constructed), set the echo field from the request:

```python
                    job_info.segmentation_mode_resolved = request.options.segmentation_mode
```

If the result is constructed via a dict literal rather than mutated on an object, add the key to that dict:

```python
                    "segmentation_mode_resolved": request.options.segmentation_mode,
```

Pick whichever form matches the existing `JobInfo` construction pattern.

### Change 3 — Backend: enforce mutual exclusivity in `extract_components`

File: `ops/analysis-service/src/sow_analysis/workers/components.py`.

Extend the signature (after `use_llm_segmentation`):

```python
    segmentation_mode: Optional[str] = None,
```

Replace the identification block (the section currently spanning from `# 3. Identification.` through the `if not components: return ([], "none")` early-return) in full. This block keeps all existing behavior for the `None` (no-flag) case exactly as today, with TWO differences from v1:

(a) The beat-grid cache defense-in-depth read is hoisted out of the `None`-gate so it runs for every mode when `beats` AND `downbeats` are both `None`.
(b) A `logger.warning(...)` is emitted for every "mode requested but source unavailable → returning []" case (including the LRC-absent case for `llm` mode, which v1 swallowed silently).

```python
    # 3. Identification.
    components: list[ComponentInstance] = []
    source = "none"

    # Beat-grid cache defense-in-depth: applies to ALL modes, not just None,
    # so that `repetition` is apples-to-apples with the default-mode repetition
    # fallback. `allin1` does not consume beats/downbeats so the read is a
    # benign no-op cost there.
    if not beats and not downbeats:
        cached_grid = cache_manager.get_beat_grid(content_hash)
        if cached_grid is not None:
            downbeats = cached_grid.get("downbeats")

    if segmentation_mode in (None, "allin1") and sections:
        components = identify_from_allin1_sections(
            sections, snap_to_downbeat=snap_to_downbeat, downbeats=downbeats
        )
        if components:
            source = "allin1_sections"
        elif segmentation_mode == "allin1":
            logger.warning(
                "segmentation_mode='allin1' requested but no sections available; "
                "returning empty"
            )

    if not components and lrc_content and segmentation_mode in (None, "llm"):
        _use_llm = (segmentation_mode == "llm") or (
            segmentation_mode is None
            and (use_llm_segmentation or settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION)
        )
        if _use_llm and settings.SOW_LLM_API_KEY:
            try:
                from .section_segmenter import segment_song

                seg_start = time.time()
                components = await segment_song(
                    lrc_content,
                    song_title=None,
                    duration=gf.duration if gf is not None else None,
                    beats=beats,
                    downbeats=downbeats,
                    snap_to_downbeat=snap_to_downbeat,
                )
                logger.info(
                    f"LLM segmentation completed in {time.time() - seg_start:.2f}s "
                    f"({len(components)} components)"
                )
                if components:
                    source = "llm_segmentation"
            except Exception as e:
                logger.warning("LLM segmentation failed: %s", e)
                components = []
        elif segmentation_mode == "llm":
            logger.warning(
                "segmentation_mode='llm' requested but SOW_LLM_API_KEY is unset; "
                "returning empty"
            )

        if not components and segmentation_mode is None:
            song_total_duration = gf.duration if gf is not None else None

            identify_start = time.time()
            components = identify_from_lyrics_repetition(
                lrc_content,
                beats=beats,
                downbeats=downbeats,
                song_total_duration=song_total_duration,
                snap_to_downbeat=snap_to_downbeat,
            )
            logger.info(
                f"Component identification completed in "
                f"{time.time() - identify_start:.2f}s ({len(components)} components)"
            )
            if components:
                source = "lyrics_repetition"
                if not downbeats:
                    for c in components:
                        c.confidence = 0.5

    if segmentation_mode == "llm" and not components and not lrc_content:
        logger.warning(
            "segmentation_mode='llm' requested but lrc_content is None; "
            "returning empty (CLI preflight should normally prevent this path)"
        )

    if segmentation_mode == "repetition" and lrc_content:
        song_total_duration = gf.duration if gf is not None else None
        identify_start = time.time()
        components = identify_from_lyrics_repetition(
            lrc_content,
            beats=beats,
            downbeats=downbeats,
            song_total_duration=song_total_duration,
            snap_to_downbeat=snap_to_downbeat,
        )
        logger.info(
            f"Component identification (repetition mode) completed in "
            f"{time.time() - identify_start:.2f}s ({len(components)} components)"
        )
        if components:
            source = "lyrics_repetition"
            if not downbeats:
                for c in components:
                    c.confidence = 0.5
        else:
            logger.warning(
                "segmentation_mode='repetition' requested but no lyrics-repetition "
                "components found; returning empty"
            )

    if not components:
        return ([], "none")
```

Key behavioral guarantees this block enforces:

- `llm` mode: no allin1 read (guarded at the top), no repetition fallback (the LRC-block repetition path is gated by `segmentation_mode is None`, and the separate `repetition` block is mode-gated to `repetition`), and on LLM failure/empty/unset-key OR missing LRC it returns `[]` via the `return ([], "none")` tail with a logged warning.
- `repetition` mode: the allin1 path is skipped entirely (top guard), the LRC block is skipped (guarded by `segmentation_mode in (None, "llm")`), and the separate repetition block runs given `lrc_content`. Beats/downbeats come from the caller OR the now-unconditional beat-grid cache fallback.
- `allin1` mode: the LRC block and repetition block are both skipped; only the allin1 path runs, and only if `sections` is present.
- `None` mode: behavior is bit-identical to the current code (the beat-grid read used to be nested inside this branch; it now runs unconditionally but the downstream code paths are unchanged since the default-mode LRC block already did this read).
- A `logger.warning(...)` is emitted for every "mode requested but source unavailable → returning []" case, including the LRC-absent case for `llm` mode.

### Change 4 — Admin CLI service function: add `segmentation_mode` param to `submit_component_analysis`

File: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`. Add the param after `all_components`:

```python
        all_components: bool = False,
        segmentation_mode: Optional[str] = None,
```

Document it in the docstring (prose, not code comments):

> `segmentation_mode`: optional mutually-exclusive identification source for the worker (`"llm"`, `"repetition"`, `"allin1"`); when set it overrides the worker's best-available priority and any `use_llm_segmentation` option. `None` (default) preserves current priority.

Update the `payload["options"]` dict (add ONE key — `use_llm_segmentation` is intentionally NOT added; the worker's new short-circuit logic handles `llm` mode without it):

```python
            "options": {
                "force": force,
                "snap_to_downbeat": snap_to_downbeat,
                "energy_aware_roles": energy_aware_roles,
                "use_stems": use_stems,
                "classify_theme": classify_theme,
                "classify_vocal_posture": classify_vocal_posture,
                "skip_beat_cache": skip_beat_cache,
                "all_components": all_components,
                "segmentation_mode": segmentation_mode,
            },
```

Add an accessor on the polled `JobInfo` (response dict OR typed model — match existing pattern) for `segmentation_mode_resolved`. If the existing code returns a dict, prefer a `.get("segmentation_mode_resolved")` lookup; if it returns a typed model, extend the model with the field.

### Change 5 — Admin CLI helper: thread `segmentation_mode` through `_submit_component_analysis_job` + preflight + echo verification

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`.

Add the param after `all_components`:

```python
    segmentation_mode: Optional[str] = None,
```

Document it in the docstring (prose):

> `segmentation_mode`: mutually-exclusive identification source; one of `"llm"`, `"repetition"`, `"allin1"`. The caller MUST also pass `force=True` (validated in the typer command). When set, the worker runs ONLY that source and returns `[]` if unavailable — no fallback chain.

Pass it into `client.submit_component_analysis(...)` (add ONE line after `all_components=all_components` — do NOT pass `use_llm_segmentation`; the worker's new short-circuit handles `llm` mode):

```python
            segmentation_mode=segmentation_mode,
```

**Preflight checks** (insert AFTER the LRC-content fetch block, BEFORE the cache-check / job-submission):

```python
    if segmentation_mode == "llm" and not lrc_content:
        console.print(
            f"[red]Cannot use --segmentation-mode llm: recording {song_id} has no LRC "
            f"(lrc_status != 'completed'). Run "
            f"`sow-admin audio analyze lrc {song_id}` first.[/red]"
        )
        return None
    if segmentation_mode == "repetition" and not lrc_content:
        console.print(
            f"[red]Cannot use --segmentation-mode repetition: recording {song_id} "
            f"has no LRC.[/red]"
        )
        return None
    if segmentation_mode == "allin1" and not sections:
        console.print(
            f"[red]Cannot use --segmentation-mode allin1: recording {song_id} has no "
            f"cached allin1 sections. Run "
            f"`sow-admin audio analyze --analysis-tier full {song_id}` first.[/red]"
        )
        return None
```

**Echo verification** (insert in the wait path, AFTER the job result is fetched, BEFORE persistence):

```python
        resolved = job.result.get("segmentation_mode_resolved") if job.result else None
        if segmentation_mode is not None and resolved != segmentation_mode:
            console.print(
                f"[red]WARNING: requested segmentation_mode={segmentation_mode!r} "
                f"but backend echoed {resolved!r}. The analysis-service backend may "
                f"be outdated (does not support segmentation_mode). Result will "
                f"NOT be persisted.[/red]"
            )
            return None
```

(Adapt `job.result` access to match the actual polling API the helper uses. If `JobInfo` has a `.segmentation_mode_resolved` attribute, prefer that.)

### Change 6 — Admin CLI command: add `--segmentation-mode` typer flag, force co-requirement, batch banner

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`.

Add a new module-level constant near `COMPONENTS_FORMAT_VALUES`:

```python
SEGMENTATION_MODE_VALUES = {"llm", "repetition", "allin1"}
```

Add the typer Option after `all_components`:

```python
    segmentation_mode: Optional[str] = typer.Option(
        None,
        "--segmentation-mode",
        help=(
            "Force a mutually-exclusive component identification source: "
            "llm | repetition | allin1. Default (omitted) uses current "
            "best-available priority. REQUIRES --force (validated). "
            "llm/repetition require LRC; allin1 requires cached sections "
            "from a prior `audio analyze --analysis-tier full` run."
        ),
    ),
```

Validate it next to the existing `--format` validation:

```python
    _validate_choice(
        segmentation_mode, SEGMENTATION_MODE_VALUES, "--segmentation-mode"
    )
```

`_validate_choice` already skips validation when the value is `None`, so the no-flag path is unaffected.

**Force co-requirement** (immediately after `_validate_choice` for segmentation_mode):

```python
    if segmentation_mode is not None and not force:
        console.print(
            "[red]--segmentation-mode requires --force. Without --force, "
            "cached components.json is returned without consulting the mode "
            "(silent no-op). Re-run with --force.[/red]"
        )
        raise typer.Exit(code=2)
```

**Batch banner** (when `stdin` and `segmentation_mode` are both set — place near the existing stdin-branch entry, before reading stdin):

```python
    if stdin and segmentation_mode is not None:
        out_console.print(
            f"[yellow]╔══════════════════════════════════════════════════════════════════╗\n"
            f"║ --segmentation-mode={segmentation_mode} ACTIVE — NO-FALLBACK CONTRACT{' ' * (28 - len(segmentation_mode))}║\n"
            f"║ Each song runs ONLY the '{segmentation_mode}' source. Empty [] results are{' ' * (3 if len(segmentation_mode) == 3 else 2)}║\n"
            f"║ EXPECTED when the source is unavailable (no LRC, unset API key,{' ' * 1}║\n"
            f"║ LLM error, invalid JSON). There is NO fallback to allin1 or{' ' * 3}║\n"
            f"║ lyrics-repetition. Do NOT interpret [] as \"song has no components.\"║\n"
            f"╚══════════════════════════════════════════════════════════════════╝[/yellow]"
        )
```

(If the box-drawing alignment proves fragile, fall back to a simpler banner without the box; the contract text matters more than alignment.)

Wire `segmentation_mode=segmentation_mode,` into all three `_submit_component_analysis_job(...)` call sites (single-song call, stdin-batch `no_wait` call, stdin-batch waiting call).

### Change 7 — Tests

**`ops/analysis-service/tests/test_components.py` (or new `test_segmentation_mode.py`):**

- Mode `llm` with monkeypatched `segment_song` returning `[]` → final `source == "none"` AND repetition NOT invoked (assert via a spy on `identify_from_lyrics_repetition`).
- Mode `llm` with `lrc_content=None` → `[]` returned with the new "lrc_content is None" warning, and `segment_song` NOT called.
- Mode `repetition` with `beats=None` AND `downbeats=None` AND a populated beat-grid cache → cache IS read and `downbeats` populated (assert `identify_from_lyrics_repetition` was called with non-None downbeats). This guards the beat-grid hoist.
- Mode `repetition` with cached `sections` present → allin1 NOT called (spy on `identify_from_allin1_sections`) → `source == "lyrics_repetition"`.
- Mode `allin1` with no `sections` → `[]` returned, LRC path untouched (spy on both `segment_song` and `identify_from_lyrics_repetition`).
- Mode `None` → result identical to current behavior (regression guard); beat-grid cache fallback still runs.
- Worker sets `segmentation_mode_resolved` echo field on `JobInfo` for all four cases.

**`ops/admin-cli/tests/admin/test_analysis_client.py`:** extend `test_options_passed_correctly` to assert `payload["options"]["segmentation_mode"]` is set when passed, and absent/`None` when not.

**`ops/admin-cli/tests/admin/test_audio_commands.py`:**

- `--segmentation-mode llm` (without `--force`) → exits non-zero with the "requires --force" error; no job submitted.
- `--segmentation-mode llm --force` (with LRC) → payload carries `segmentation_mode="llm"` and NO `use_llm_segmentation` key (regression guard against v1's bug).
- `--segmentation-mode llm` on a recording with `has_lrc=False` → exits with the preflight error before submitting.
- `--segmentation-mode allin1` on a recording with `has_full_analysis=False` → exits with the preflight remediation hint.
- `--segmentation-mode repetition --force` on a recording without LRC → exits with the preflight error.
- `--stdin --segmentation-mode llm --force` → banner is printed once before reading stdin.
- Echo-mismatch path: stub backend to return `segmentation_mode_resolved=None` while request was `"llm"` → CLI prints red warning and does NOT persist.

## Precedence and backward compatibility

- `segmentation_mode` (when set) takes precedence over the `use_llm_segmentation` option AND over the `SOW_COMPONENTS_USE_LLM_SEGMENTATION` env var. The env flag is now consulted ONLY in the `None` (no-flag) default path — exactly as today.
- Mode `llm` ignores the env flag's "fall back to repetition on failure" semantics; it is strictly no-fallback.
- The existing `use_llm_segmentation` field on `ComponentAnalysisOptions` stays for programmatic callers; it is not removed. The admin CLI no longer sends it (see Change 4).
- Other admin-cli callers (songset constructor backfill) are NOT updated; they continue to omit `segmentation_mode` (defaults to None = current priority).
- **Schema version: NO bump required.** The new option is per-job and does not change the persisted `components.json` shape — the existing `source` field already carries `llm_segmentation` / `lyrics_repetition` / `allin1_sections` / `none`, and the `section_label` / `lyrics_excerpt` / `llm_rationale` fields already exist from v6. The `segmentation_mode_resolved` echo field lives on `JobInfo`, not on the persisted `components.json`.

## Backend version-skew mitigation

`ComponentAnalysisOptions` uses pydantic v2's default `extra="ignore"` (NOT verified as `extra="forbid"`; check `models.py` to confirm). This means a new admin-cli sending `segmentation_mode` to an OLD (pre-deployment) analysis-service backend would silently drop the field, and the operator would get default behavior while believing the mode is active.

v2 mitigates this with the `segmentation_mode_resolved` echo field on `JobInfo` (Change 1, 2, 5). When the CLI requests a mode and the backend echoes a different value (the OLD backend echoes `None` because it never received the field), the CLI prints a red warning and refuses to persist the result. This catches the skew at result time, not at submission time, but is sufficient because the no-fallback contract guarantees the operator will inspect the result before trusting it.

If earlier detection is desired in a future v3, add a `/version` (or `/health`) endpoint check on first mode use; out of scope for v2.

## CLI usage examples

Force LLM only (no allin1 cache, no repetition fallback):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components <song-id> --segmentation-mode llm --force
```

Force lyrics-repetition only (skip cached allin1 sections):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components <song-id> --segmentation-mode repetition --force
```

Force allin1 only (must have cached sections; errors/empties otherwise):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components <song-id> --segmentation-mode allin1 --force
```

Default (current behavior):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components <song-id> --force
```

Batch via stdin (banner printed once):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components --stdin --segmentation-mode llm --force < song_ids.txt
```

Wrong usage (rejected):

```bash
uv run --project ops/admin-cli sow-admin audio analyze components <song-id> --segmentation-mode llm
# Exit 2: "--segmentation-mode requires --force..."
```

## Rollout order

1. Backend changes 1-3 (`models.py`, `queue.py`, `components.py`) + backend tests. Deploy analysis-service.
2. Admin-cli service function + helper changes 4-5 (`services/analysis.py`, `commands/audio.py` helper + preflight + echo verification).
3. Typer flag change 6 (command signature, validation, force co-requirement, batch banner, three call sites).
4. Admin-cli tests change 7.
5. Manual verification per checklist below.

The backend MUST be deployed before the admin-cli, because the `segmentation_mode_resolved` echo field is introduced in Change 1. If the CLI is deployed first, every mode request will fail the echo verification and refuse to persist results (the CLI will loudly warn, which is the desired fail-safe — but it is operationally noisy).

## Manual verification checklist

- With `SOW_LLM_API_KEY` unset and `--segmentation-mode llm --force` on a song with valid LRC, the result is empty `[]` with `source=none` and a warning log mentioning the missing key; the lyrics-repetition path is NOT invoked.
- With `--segmentation-mode llm` (no `--force`) → exits 2 with the "requires --force" error; no job submitted.
- With `--segmentation-mode llm --force` on a recording with `has_lrc=False` → exits with the preflight error mentioning `sow-admin audio analyze lrc`.
- With `--segmentation-mode allin1 --force` on a recording with `has_full_analysis=False` → exits with the preflight remediation hint mentioning `--analysis-tier full`.
- With `--segmentation-mode repetition --force` on a song with no LRC → exits with the preflight error.
- With `--segmentation-mode repetition --force` on a song that HAS cached allin1 sections, the result uses `source=lyrics_repetition` and the allin1 path is skipped.
- With `--segmentation-mode repetition --force` on a song where DB has no beats/downbeats but beat-grid cache is populated → cache IS read and `downbeats` populated (assert via log: "Component identification (repetition mode) completed").
- With `--segmentation-mode allin1 --force` on a song WITH cached sections, the result uses `source=allin1_sections`.
- With no flag on a song with cached sections + LRC + `SOW_LLM_API_KEY` set and `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true`, current priority is preserved (allin1 → LLM → repetition fallback).
- `--segmentation-mode nonsense --force` is rejected via `_validate_choice` with a clear error.
- Batch `--stdin --segmentation-mode llm --force` prints the banner once before reading stdin, applies mode per song, and reports per-song empty results without corrupting others.
- Backend-skew simulation: stop an OLD backend (no `segmentation_mode_resolved`), run `--segmentation-mode llm --force` → CLI prints red "backend echoed None" warning, refuses to persist, exits non-zero.
- `--compute-all-fields --segmentation-mode llm --force` together work (orthogonal).

## Open questions

- Should `--segmentation-mode allin1` ALSO auto-run `audio analyze --analysis-tier full` if sections are missing, instead of erroring? Recommendation: NO — keep the preflight error; auto-running full analysis is a surprising side-effect.
- Should the `--compute-all-fields` shortcut (which currently sets snap/energy/theme/posture flags) ALSO default `--segmentation-mode` to anything? Recommendation: NO — keep them orthogonal.
- Should the echo verification block on `--segmentation-mode repetition`/`allin1` (where the resolved value should match the request) AND on `None` (where the echo should be `None`)? Recommendation: YES — assert echo matches request unconditionally so any silent field-drop is caught.

## Out of scope

- Songset-constructor backfill plumbing to pass `segmentation_mode` (its callers keep default None behavior).
- Webapp / Android UI exposure of a segmentation-mode selector.
- Removing the existing `use_llm_segmentation` option field.
- Any change to the persisted `components.json` schema or the `source` field contract.
- A `/version` endpoint check for earlier-than-result-time detection of backend skew (deferred to v3 if needed).

## End of file
