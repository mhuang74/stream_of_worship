# Implementation Plan v1: Admin CLI `--segmentation-mode` Flag

## Goal

Expose a mutually-exclusive segmentation-mode selector on `sow-admin audio analyze components` so operators can force a single identification source (LLM / lyrics-repetition / allin1) for A/B testing and debugging, without the bigger `use_llm_segmentation` one-way-OR env gate complexity. Default (no flag) preserves the existing best-available priority: allin1 sections first → LLM if enabled → lyrics-repetition.

## Design decisions (fixed)

1. **Modes are mutually exclusive sources, skip everything else.** Each mode runs ONLY that source and returns `[]` (empty) when unavailable; NO fallback chains. Default (no flag) = current best-available priority (allin1 sections first → LLM if enabled → lyrics-repetition).

2. **Scope limited to the `components` command only (single song + stdin batch).** Add the flag to the typer command; thread a new param through the `submit_component_analysis` service function. Do NOT update other callers (songset constructor backfill etc.). The service function gains the param for the CLI's benefit.

## Mode behavior matrix

| Mode | What runs | What is skipped | Unavailable behavior | `source` in result |
|------|-----------|-----------------|----------------------|--------------------|
| `llm` | `segment_song` only | allin1 cached sections path AND lyrics-repetition fallback | `SOW_LLM_API_KEY` unset → returns `[]` (no fallback); LLM raises / returns empty / JSON invalid → returns `[]` | `llm_segmentation` on success; `none` on empty |
| `repetition` | `identify_from_lyrics_repetition` only | allin1 cached sections path (even if cached sections present) | no LRC → returns `[]` | `lyrics_repetition` or `none` |
| `allin1` | `identify_from_allin1_sections` only IF `sections` (cached allin1) provided | entire LRC block (LLM + repetition) | no sections → returns `[]` | `allin1_sections` or `none` |
| `(none)` = no flag | current behavior: allin1 first (if sections), then LRC block with LLM-gate-OR + repetition fallback | nothing (current) | current fallback chain unchanged | unchanged |

## Critical files

**Analysis-service backend**
- `ops/analysis-service/src/sow_analysis/models.py` — add `segmentation_mode` to `ComponentAnalysisOptions`.
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — thread `segmentation_mode` into `extract_components`.
- `ops/analysis-service/src/sow_analysis/workers/components.py` — enforce mutual exclusivity in the identification block.

**Admin CLI client**
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — add `segmentation_mode` param to `submit_component_analysis`.
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — add `--segmentation-mode` typer flag; thread through `_submit_component_analysis_job` and both call paths.

## Implementation changes

### Change 1 — Backend: new `segmentation_mode` option on `ComponentAnalysisOptions`

File: `ops/analysis-service/src/sow_analysis/models.py` (near line 103). Add to the imports at top of file:

```python
from typing import Literal, Optional
```

Add the field immediately after `use_llm_segmentation` (line 103):

```python
    segmentation_mode: Optional[Literal["llm", "repetition", "allin1"]] = None
```

The field is a per-job option; when set (a non-`None` value) it is mutually exclusive and overrides both `use_llm_segmentation` and the env gate in the worker.

### Change 2 — Backend: thread `segmentation_mode` through queue.py

File: `ops/analysis-service/src/sow_analysis/workers/queue.py:1012`. Add one line mirroring the existing `use_llm_segmentation=request.options.use_llm_segmentation`:

```python
segmentation_mode=request.options.segmentation_mode,
```

This must appear alongside the existing `use_llm_segmentation=...` argument in the `extract_components(...)` call.

### Change 3 — Backend: enforce mutual exclusivity in `extract_components`

File: `ops/analysis-service/src/sow_analysis/workers/components.py`.

Extend the signature (line ~1357, after `use_llm_segmentation`):

```python
    segmentation_mode: Optional[str] = None,
```

Rewrite the identification block (current lines ~1424-1499) in full. This block keeps all existing behavior for the `None` (no-flag) case exactly as today:

```python
    components: list[ComponentInstance] = []
    source = "none"

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
            if not beats and not downbeats:
                cached_grid = cache_manager.get_beat_grid(content_hash)
                if cached_grid is not None:
                    downbeats = cached_grid.get("downbeats")

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

    if segmentation_mode == "repetition" and lrc_content:
        components = identify_from_lyrics_repetition(
            lrc_content,
            beats=beats,
            downbeats=downbeats,
            song_total_duration=gf.duration if gf is not None else None,
            snap_to_downbeat=snap_to_downbeat,
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

- `llm` mode: no allin1 read (guarded at the top), no repetition fallback (repetition runs only when `segmentation_mode is None` inside the LRC block, and the separate `repetition` block is mode-gated), and on LLM failure/empty/unset-key it returns `[]` via the `return ([], "none")` tail.
- `repetition` mode: the allin1 path is skipped entirely (top guard), the LRC block is skipped (guarded by `segmentation_mode in (None, "llm")`), and the separate repetition block runs unconditionally given `lrc_content`.
- `allin1` mode: the LRC block and repetition block are both skipped; only the allin1 path runs, and only if `sections` is present.
- `None` mode: behavior is bit-identical to the current code.
- A `logger.warning(...)` is emitted for every "mode requested but source unavailable → returning []" case.

### Change 4 — Admin CLI service function: add `segmentation_mode` param to `submit_component_analysis`

File: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:611`. Add the param after `all_components` (line 627):

```python
        all_components: bool = False,
        segmentation_mode: Optional[str] = None,
```

Document it in the docstring (prose, not code comments):

> `segmentation_mode`: optional mutually-exclusive identification source for the worker (`"llm"`, `"repetition"`, `"allin1"`); when set it overrides the worker's best-available priority and any `use_llm_segmentation` option. `None` (default) preserves current priority.

Update the `payload["options"]` dict (current lines 681-690):

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

### Change 5 — Admin CLI helper: thread `segmentation_mode` through `_submit_component_analysis_job`

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:1944`. Add the param after `all_components` (line 1961):

```python
    segmentation_mode: Optional[str] = None,
```

Document it in the docstring (prose):

> `segmentation_mode`: mutually-exclusive identification source; `"llm"` also forces `use_llm_segmentation=True` so the worker LLM gate is satisfied.

Pass it into `client.submit_component_analysis(...)` (line 2075). Add to the call, after `all_components=all_components`:

```python
            segmentation_mode=segmentation_mode,
            use_llm_segmentation=(segmentation_mode == "llm"),
```

When `segmentation_mode == "llm"`, `use_llm_segmentation=True` is passed because the backend LLM gate still reads that option in addition to the new mode param. For `repetition`/`allin1`/`None`, `use_llm_segmentation=False` is passed so the mode gate (or the env gate for None) decides. This one-line expression replaces the need for an explicit conditional.

### Change 6 — Admin CLI command: add `--segmentation-mode` typer flag

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2197`. Add a new module-level constant near `COMPONENTS_FORMAT_VALUES` (line 75):

```python
SEGMENTATION_MODE_VALUES = {"llm", "repetition", "allin1"}
```

Add the typer Option after `all_components` (line 2242):

```python
    segmentation_mode: Optional[str] = typer.Option(
        None,
        "--segmentation-mode",
        help=(
            "Force a mutually-exclusive component identification source: "
            "llm | repetition | allin1. Default (omitted) uses current "
            "best-available priority."
        ),
    ),
```

Validate it next to the existing `--format` validation (line 2289):

```python
    _validate_choice(
        segmentation_mode, SEGMENTATION_MODE_VALUES, "--segmentation-mode"
    )
```

`_validate_choice` (audio.py:78) already skips validation when the value is `None`, so the no-flag path is unaffected.

Wire the value into all three `_submit_component_analysis_job(...)` call sites by adding `segmentation_mode=segmentation_mode,` to each. The stdin-batch `no_wait` call (line 2335):

```python
                    _submit_component_analysis_job(
                        recording, sid, config.analysis_url, db_client, out_console,
                        config=config, force=force, wait=False,
                        snap_to_downbeat=snap_to_downbeat,
                        energy_aware_roles=energy_roles,
                        use_stems=use_stems,
                        classify_theme=classify_theme,
                        classify_vocal_posture=classify_posture,
                        skip_beat_cache=skip_beat_cache,
                        all_components=all_components,
                        segmentation_mode=segmentation_mode,
                    )
```

The stdin-batch waiting call (line 2350) gets the same addition after `all_components=all_components`:

```python
                        segmentation_mode=segmentation_mode,
```

The single-song call (line 2411) gets the same addition after `all_components=all_components`:

```python
        segmentation_mode=segmentation_mode,
```

Force-llm mode is meaningful in batch A/B eval, so mode is threaded into the stdin batch calls too (not just single-song).

### Change 7 — Tests

**`ops/analysis-service/tests/test_components.py` (or new `test_segmentation_mode.py`):**

- Mode `llm` with monkeypatched `segment_song` returning `[]` → final `source == "none"` AND repetition NOT invoked (assert via a spy on `identify_from_lyrics_repetition`).
- Mode `repetition` with cached `sections` present → allin1 NOT called (spy on `identify_from_allin1_sections`) → `source == "lyrics_repetition"`.
- Mode `allin1` with no `sections` → `[]` returned, LRC path untouched (spy on both `segment_song` and `identify_from_lyrics_repetition`).
- Mode `None` → result identical to current behavior (regression guard).

**`ops/admin-cli/tests/admin/test_analysis_client.py`:** extend `test_options_passed_correctly` (line 105) to assert `payload["options"]["segmentation_mode"]` is set when passed, and absent/`None` when not.

**`ops/admin-cli/tests/admin/test_audio_commands.py`:**

- `--segmentation-mode llm` → payload carries `segmentation_mode="llm"` and `use_llm_segmentation=true`.
- `--segmentation-mode repetition` → payload carries `segmentation_mode="repetition"` and `use_llm_segmentation=false`.
- No flag → neither key present in the payload options (or both default None/False).

## Precedence and backward compatibility

- `segmentation_mode` (when set) takes precedence over the `use_llm_segmentation` option AND over the `SOW_COMPONENTS_USE_LLM_SEGMENTATION` env var. The env flag is now consulted ONLY in the `None` (no-flag) default path — exactly as today.
- Mode `llm` ignores the env flag's "fall back to repetition on failure" semantics; it is strictly no-fallback.
- The existing `use_llm_segmentation` field on `ComponentAnalysisOptions` stays for programmatic callers; it is not removed.
- Other admin-cli callers (songset constructor backfill) are NOT updated; they continue to omit `segmentation_mode` (defaults to None = current priority).
- **Schema version: NO bump required.** The new option is per-job and does not change the persisted `components.json` shape — the existing `source` field already carries `llm_segmentation` / `lyrics_repetition` / `allin1_sections` / `none`, and the `section_label` / `lyrics_excerpt` / `llm_rationale` fields already exist from v6.

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

Batch via stdin:

```bash
uv run --project ops/admin-cli sow-admin audio analyze components --stdin --segmentation-mode llm --force < song_ids.txt
```

## Rollout order

1. Backend changes 1-3 (`models.py`, `queue.py`, `components.py`) + backend tests.
2. Admin-cli service function + helper changes 4-5 (`services/analysis.py`, `commands/audio.py` helper).
3. Typer flag change 6 (command signature, validation, three call sites).
4. Admin-cli tests change 7.
5. Manual verification per checklist below.

## Manual verification checklist

- With `SOW_LLM_API_KEY` unset and `--segmentation-mode llm` on a song with valid LRC, the result is empty `[]` with `source=none` and a warning log mentioning the missing key; the lyrics-repetition path is NOT invoked.
- With `--segmentation-mode repetition` on a song that HAS cached allin1 sections, the result uses `source=lyrics_repetition` and the allin1 path is skipped.
- With `--segmentation-mode allin1` on a song WITHOUT cached sections, the result is empty `[]`; no LRC/LLM/repetition code runs.
- With `--segmentation-mode allin1` on a song WITH cached sections, the result uses `source=allin1_sections`.
- With no flag on a song with cached sections + LRC + `SOW_LLM_API_KEY` set and `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true`, current priority is preserved (allin1 → LLM → repetition fallback).
- `--segmentation-mode allin1` and `--segmentation-mode nonsense` both reject invalid values via `_validate_choice` guidance (the latter with a clear error); no flag accepted.
- Batch `--stdin --segmentation-mode llm` applies mode per song and reports per-song empty results without corrupting others.
- `--compute-all-fields --segmentation-mode llm` together work (orthogonal).

## Open questions

- Should `--segmentation-mode allin1` print a helpful message guiding the operator to run `audio analyze --analysis-tier full` first when sections are missing, rather than silently returning `[]`?
- Should the `--compute-all-fields` shortcut (which currently sets snap/energy/theme/posture flags) ALSO default `--segmentation-mode` to anything? Recommendation: NO — keep them orthogonal.
- Whether to log a banner when a mode is active so batch output makes the no-fallback contract obvious.

## Out of scope

- Songset-constructor backfill plumbing to pass `segmentation_mode` (its callers keep default None behavior).
- Webapp / Android UI exposure of a segmentation-mode selector.
- Removing the existing `use_llm_segmentation` option field.
- Any change to the persisted `components.json` schema or the `source` field contract.
