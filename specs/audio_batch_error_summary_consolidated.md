# audio batch: manual-intervention listing (consolidated plan)

Consolidates `specs/audio_batch_error_summary.md` and `specs/audio_batch_error_summary_glm53flash.md`.
All line anchors re-verified against `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` (HEAD, 2026-09-06).

## Context

`sow-admin audio batch` prints a Batch Summary panel plus five per-step "Failed X" detail sections. The reported failure: a run with `LRC Skipped (dl failed): 1` names no song — the user cannot tell which song needs attention without re-reading scrollback. Root cause (both source plans agree, verified):

- The summary is built by `_print_stats(results, db_client, console, format)` at line 9743; panel lines 9803–9891; five per-step "Failed X" sections at 9895–9954.
- `lrc_skipped_download` (line 9777) is just `download == "failed"` recounted — no song identity is kept.
- `_submit_lrc_for_song` (line 7343) returns `"skipped_no_recording"` / `"skipped_no_lyrics"` (lines 7383–7391) but writes nothing into `results`, so those skips are invisible in the summary.
- The components step DOES write skip statuses already: `results[song_id]["components"] = "skipped_no_recording"` + `components_error` (7792–7793), `"skipped_no_sections"` (7811) — but `_print_stats` never surfaces them.

End state: after a batch run, the summary lists every song that requires manual intervention — failed steps AND data-gap skips — with the per-step reason, including the reported case where a download failure silently skipped the LRC step.

## Design decisions (inherited from the two source plans)

| Decision | Chosen | Why |
|---|---|---|
| Consolidated section placement | REPLACE the five per-step sections (glm53flash) | Lossless: the attention loop reads exactly the same five `== "failed"` statuses and error keys (`error`, `lrc_error`, `analyze_error`, `embedding_error`, `components_error`) the per-step sections consume at 9896–9954; appending alongside would print every failed song twice |
| `selected_steps` param on `_print_stats` | Add it (glm53flash) | Required to detect "LRC skipped (download failed)" — the exact bug-report scenario, which the other plan misses |
| `lrc_error` for skip statuses | Do NOT write one (glm53flash) | Skip reasons are deterministic from the status code; a redundant field duplicates what the display layer maps anyway |
| `backfill_lyrics` failures | Include in listing (glm53flash) | `--all-steps` includes it; a failed backfill is a data gap |
| Panel count | Add `Needs attention: N` row (glm53flash) | Visible at a glance without scrolling to the detail section |
| Rendering | Multi-line per song (error_summary) | Indented sub-lines per step stay readable when a song has 3+ issues; single semicolon-joined lines wrap badly at 100 cols |
| Rich markup | `markup=False` on all printed lines (glm53flash) | Song titles and error strings may contain `[` that Rich would eat as markup; the existing per-step sections already do this (9904, 9915, …) |
| Status→reason mapping | Inline in a `_build_attention_list` helper (glm53flash shape) | The dict approach from error_summary maps `"failed": "failed"` and still needs separate inline logic for error messages and the download→LRC inference — the dict is half a solution; one explicit builder covers all cases |

## Approach

### 1. Record LRC skip reasons in `results` (`_submit_lrc_for_song`, lines 7382–7391)

In the two early-skip branches, add one line each so the summary can name them:

```python
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"  [yellow]→ {song_id} (skipped: lrc no recording)[/yellow]")
        results[song_id]["lrc"] = "skipped_no_recording"        # NEW
        return "skipped_no_recording"

    song = db_client.get_song(song_id)
    lyrics_text = _resolve_lyrics_text(song, recording) if song else None
    if not song or not lyrics_text:
        console.print(f"  [yellow]→ {song_id} (skipped: lrc no lyrics)[/yellow]")
        results[song_id]["lrc"] = "skipped_no_lyrics"           # NEW
        return "skipped_no_lyrics"
```

Safety (all consumers verified this session):

- `_advance_song` (8139) checks the **return value** `status` in its continue branch (8196–8204), not `results[song_id][step]` — writing to results does not change control flow. Its completed/failed guard at 8172 (`in ("completed", "failed")`) does not match the new `skipped_*` values, so no double-submit.
- Re-walk after an eager skip is idempotent: `lrc_attempted` is populated (7380) but never consulted, so a chain re-walk re-queries and re-returns the same skip.
- Every consumer of `results[sid]["lrc"]` tests only `== "completed"` / `== "failed"` / `lrc_source`: panel counters 9772–9777, exit-code checks 6828–6830 and 6676. New statuses are inert to all of them; exit-code semantics unchanged (skips never flip `failed_any`).
- Both callers of `_submit_lrc_for_song` — `_submit_step` (8084) and the eager handoff in `_download_worker` (8784) — pass the `_process_batch`-initialized `results` dict (`{sid: {} for sid in song_ids}`, line 9113), so `song_id` keys always pre-exist; direct assignment (matching the `skipped_r2` style at 7402) needs no `setdefault`.

### 2. Rewrite `_print_stats` failure reporting (9743–9954)

a. Add parameter `selected_steps: Optional[List[str]] = None` and update the docstring. `None` means "selection unknown" → treat the LRC-skip inference as present, matching today's unconditional `lrc_skipped_download` counter (9777). Both real callers pass the real list (step 3).

b. Build the attention list BEFORE the panel `lines = [...]` block (insert after the LRC-timing block ending at 9800):

```python
    attention: list[tuple[str, list[tuple[str, str]]]] = []
    for song_id, r in results.items():
        reasons: list[tuple[str, str]] = []
        if r.get("download") == "failed":
            reasons.append(("download", f"failed — {r.get('error') or 'unknown error'}"))
            if (selected_steps is None or "lrc" in selected_steps) and "lrc" not in r:
                reasons.append(("lrc", "skipped (download failed)"))
        if r.get("lrc") == "failed":
            reasons.append(("lrc", f"failed — {r.get('lrc_error') or 'unknown error'}"))
        elif r.get("lrc") in ("skipped_no_lyrics", "skipped_no_recording"):
            label = "no lyrics in catalog" if r["lrc"] == "skipped_no_lyrics" else "no recording"
            reasons.append(("lrc", f"skipped ({label})"))
        if r.get("analyze") == "failed":
            reasons.append(("analyze", f"failed — {r.get('analyze_error') or 'unknown error'}"))
        if r.get("embedding") == "failed":
            reasons.append(("embedding", f"failed — {r.get('embedding_error') or 'unknown error'}"))
        if r.get("components") == "failed":
            reasons.append(("components", f"failed — {r.get('components_error') or 'unknown error'}"))
        elif r.get("components") in ("skipped_no_sections", "skipped_no_recording"):
            label = (
                "no sections or LRC — run 'audio lrc' first"
                if r["components"] == "skipped_no_sections"
                else "no recording or audio"
            )
            reasons.append(("components", f"skipped ({label})"))
        if r.get("backfill_lyrics") == "failed":
            reasons.append(("backfill_lyrics", "failed — structured-lyrics backfill"))
        if reasons:
            attention.append((song_id, reasons))
```

Key-name note (verified): the download step's error key is `error` (written at 9203 and by `_download_worker` update dicts at 8764/8811; consumed by the existing "Failed downloads" section at 9897) — NOT `download_error`, which does not exist anywhere. The other four steps use `{step}_error`, matching the existing sections at 9908, 9919, 9932, 9945.

c. Add a panel row directly under "Songs processed" (after line 9805):

```python
        f"│ {'Needs attention:':<30} {len(attention):>18} │",
```

d. REPLACE the five per-step sections (lines 9895–9954) with ONE consolidated section:

```python
    if attention:
        console.print("\n[bold red]Requires manual intervention:[/bold red]")
        for song_id, reasons in attention:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name} [{song_id}]", markup=False)
            for step, reason in reasons:
                console.print(f"      {step}: {reason}", markup=False)
```

Example output for the reported run:

```
Requires manual intervention:
  - 三一頌 [san_yi_song_1b286496]
      download: failed — no matching title in top 5 search results
      lrc: skipped (download failed)
```

The `"lrc" not in r` inference yields to real data: if eager LRC already recorded `skipped_no_lyrics` in results, the actual skip reason is listed instead of the inferred one.

### 3. Update both `_print_stats` callers to pass `selected_steps`

- Line 6673 (resume path): `_print_stats(results, db_client, console, format, selected_steps=manifest_data.get("selected_steps", []))` — `manifest_data` is loaded at 6640 in the same branch; the manifest carries `selected_steps` (read at 9574 inside `_resume_from_manifest`).
- Line 6825 (normal path): `_print_stats(results, db_client, console, format, selected_steps=selected_steps)` — defined at 6561.

### 4. Tests

a. Extend `TestSubmitLrcForSongHelper` in `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` (reuse its `stubs` fixture at line 61 and the call convention of `test_no_lyrics_skips` at line 392):

- `test_no_lyrics_records_skip_in_results`: `song` without lyrics → assert `results[song_id]["lrc"] == "skipped_no_lyrics"`.
- `test_no_recording_records_skip_in_results`: `db_client.get_recording_by_song_id.return_value = None` → assert `results[song_id]["lrc"] == "skipped_no_recording"`.

b. New file `ops/admin-cli/tests/admin/test_audio_print_stats.py` — `_print_stats` has zero existing tests (verified). Use `Console(record=True, width=120)` + `console.export_text()`; `db_client = MagicMock()` (truthy `.title` works with the `song.title if song else song_id` lookup). Cases:

1. `download == "failed"` + `selected_steps=["download", "lrc"]` → both `download: failed —` and `lrc: skipped (download failed)` under one song bullet; same input with `selected_steps=["download"]` → no `lrc:` clause.
2. `lrc == "skipped_no_lyrics"` → `lrc: skipped (no lyrics in catalog)`.
3. `lrc == "skipped_no_recording"` → `lrc: skipped (no recording)`.
4. `components == "skipped_no_sections"` → `components: skipped (no sections or LRC — run 'audio lrc' first)`.
5. Fully-completed song → NOT listed; panel shows `Needs attention: 0`; section header absent.
6. One song with two failures (download + embedding) → single bullet with both sub-lines; reasons carry the `error` / `embedding_error` detail (proves the replacement of the old per-step sections is lossless).
7. `backfill_lyrics == "failed"` → listed with the backfill reason.

## Critical files & anchors

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
  - `_submit_lrc_for_song` skip branches (7382–7391): add the two `results` writes
  - `_print_stats` (9743–9954): signature, attention builder before panel, panel row, replace 9895–9954
  - Callers (6673, 6825): pass `selected_steps`
- `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` — extend `TestSubmitLrcForSongHelper` (284)
- `ops/admin-cli/tests/admin/test_audio_print_stats.py` — new file

Large-file caution: this file is 10k+ lines — grep-anchor each region and re-read immediately before editing; diff against HEAD after edits (silent-corruption pitfall).

## Verification

New-behavior proof first (throwaway script, no DB/network):

```python
from unittest.mock import MagicMock
from rich.console import Console
from stream_of_worship.admin.commands.audio import _print_stats

results = {
    "s_ok": {"download": "skipped_r2", "lrc": "completed", "lrc_source": "r2_preexisting",
             "analyze": "completed", "embedding": "completed", "components": "completed"},
    "s_dl": {"download": "failed", "error": "no matching title in top 5 search results"},
}
console = Console(record=True, width=120, force_terminal=False)
_print_stats(results, MagicMock(), console, "rich", selected_steps=["download", "lrc"])
print(console.export_text())
```

Expected: panel shows `Needs attention: 1`; section lists only `s_dl` with both sub-lines; `s_ok` absent.

Then:

```bash
cd ops/admin-cli && NO_COLOR=1 uv run --python 3.11 --extra admin --extra test pytest \
    tests/admin/test_audio_print_stats.py tests/admin/test_audio_batch_eager_lrc.py \
    tests/admin/test_audio_batch_unified.py tests/admin/test_audio_batch_v4.py -v

# Full admin-cli suite must stay green
cd ops/admin-cli && uv run --python 3.11 --extra admin --extra test pytest -v
```

Manual smoke (requires DB + R2 creds; run against an album known to have a song with missing lyrics):

```bash
zsh -ic 'sow-admin audio batch --album <album> --lrc --analyze'
```

Confirm the summary lists the skipped song with `lrc: skipped (no lyrics in catalog)`.

Session close: commit the feature, run `graphify update .` as a separate chore commit, then `git pull --rebase && git push`.

## Assumptions & contingencies

- **Replacement is lossless** (verified): the five per-step sections (9895–9954) each filter `== "failed"` on one of the five `_STEP_CHAIN` steps (7494) and print the matching error key; the attention loop enumerates the same five statuses and the same five error keys, and adds skip statuses plus the downstream LRC-skip inference. If the user prefers keeping per-step sections too, only step 2d changes — append instead of replace.
- **Exit-code semantics unchanged**: skip statuses never flip `failed_any` (checks `== "failed"` at 6828–6830; resume checks any-value at 6676).
- **JSON output unchanged**: the `format == "json"` early return (9757–9761) dumps raw results; it now includes LRC skip statuses automatically as a side effect of step 1, with no code change.
- **`backfill_lyrics` skips not listed**: its three skip causes (no recording 6720, no YouTube URL 6724, already present 6737) all write the bare status `"skipped"` with no distinguishing field — listing them would be ambiguous. Only `"failed"` is listed. Backfill-only runs (`selected_steps == ["backfill_lyrics"]`) print their own Backfill Summary and return at 6755 before `_print_stats` is reached — unaffected.
- **Resume-path limitation (pre-existing, both source plans affected)**: `_resume_from_manifest` reconstructs results only for failed entries (`results[song_id][step] = "failed"` + `{step}_error`, 9546–9548) and triggers writeback for completed ones (9549–9559); skip statuses never land in `results` on resume, so the attention section undercounts on resumed runs. Acceptable per the rationale in `audio_batch_error_summary.md`: resume re-polls active jobs rather than re-deriving skips, and the original run's summary already surfaced them. Note: the function's docstring (9468–9476) claims completed entries are skipped while the code calls `_apply_manifest_writeback` — a docstring-vs-code discrepancy that is out of scope here. Making skips visible on resume is a separate enhancement.
- **`selected_steps=None` default** treats the LRC-skip inference as present, matching today's unconditional `lrc_skipped_download` counter (9777); both real callers pass the real list, so the default only matters for tests.