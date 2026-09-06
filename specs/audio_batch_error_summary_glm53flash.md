# audio-batch manual-intervention listing

## Context

`sow-admin audio batch` prints a Batch Summary panel plus per-step "Failed X" lists. A run with `Skipped (dl failed): 1` under LRC names no song — the user cannot tell which song needs attention without re-reading scrollback. Ask: enhance the summary to list every song that requires manual intervention, with the per-step reason (e.g. "download failed → LRC skipped").

Key facts discovered this session (all in `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`):

- The summary is built by `_print_stats(results, db_client, console, format)` at line 9743; panel lines at 9803–9891; five per-step "Failed X" sections at 9895–9954.
- `lrc_skipped_download` (line 9777) is just `download == "failed"` recounted — no song identity is kept.
- `_submit_lrc_for_song` (line 7343) returns `"skipped_no_recording"` / `"skipped_no_lyrics"` (lines 7382–7391) but writes nothing into `results`, so those skips are invisible in the summary.
- `_print_stats` has exactly two callers: line 6673 (resume path, `manifest_data` in scope) and line 6825 (normal path, `selected_steps` in scope). No tests reference `_print_stats`.

## Approach

### 1. Record LRC skip reasons in `results` (`_submit_lrc_for_song`, ~line 7382)

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

Safety: verified every consumer of `results[sid]["lrc"]` only tests `== "completed"` / `== "failed"` (`_advance_song` line 8172, `_print_unified_progress` line 8829, panel counters lines 9772–9777, exit-code check line 6828). New status values are inert to all of them; re-walks through the lrc step re-hit the same skip (idempotent).

### 2. Rewrite `_print_stats` failure reporting

a. Add parameter `selected_steps: Optional[List[str]] = None` to `_print_stats` and update the docstring.

b. Compute the attention list BEFORE the panel `lines = [...]` block (insert after the LRC-timing block ending at line 9800). Exact code:

```python
    # Manual-intervention listing: one line per song that ended the batch
    # with a failed step or an actionable skip.
    attention: list[tuple[str, list[str]]] = []
    for song_id, r in results.items():
        reasons: list[str] = []
        if r.get("download") == "failed":
            reasons.append(f"download failed: {r.get('error') or 'unknown error'}")
            if (selected_steps is None or "lrc" in selected_steps) and "lrc" not in r:
                reasons.append("LRC skipped (download failed)")
        if r.get("lrc") == "failed":
            reasons.append(f"LRC failed: {r.get('lrc_error') or 'unknown error'}")
        elif r.get("lrc") in ("skipped_no_lyrics", "skipped_no_recording"):
            label = "no lyrics" if r["lrc"] == "skipped_no_lyrics" else "no recording"
            reasons.append(f"LRC skipped ({label})")
        if r.get("analyze") == "failed":
            reasons.append(f"analysis failed: {r.get('analyze_error') or 'unknown error'}")
        if r.get("embedding") == "failed":
            reasons.append(f"embedding failed: {r.get('embedding_error') or 'unknown error'}")
        if r.get("components") == "failed":
            reasons.append(f"components failed: {r.get('components_error') or 'unknown error'}")
        elif r.get("components") in ("skipped_no_sections", "skipped_no_recording"):
            label = (
                "no sections or LRC (run audio lrc first)"
                if r["components"] == "skipped_no_sections"
                else "no recording or audio"
            )
            reasons.append(f"components skipped: {label}")
        if r.get("backfill_lyrics") == "failed":
            reasons.append("structured-lyrics backfill failed")
        if reasons:
            attention.append((song_id, reasons))
```

c. Add a panel row directly under "Songs processed" (line 9805) so the count is visible in the panel itself:

```python
        f"│ {'Needs attention:':<30} {len(attention):>18} │",
```

d. Replace the five per-step sections ("Failed downloads", "Failed LRC", "Failed analysis", "Failed embedding", "Failed components", lines 9895–9954) with ONE consolidated section — clean cutover, no duplicated listings:

```python
    if attention:
        console.print("\n[bold red]Requires manual intervention:[/bold red]")
        for song_id, reasons in attention:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name} [{song_id}]: {'; '.join(reasons)}", markup=False)
```

For the reported run this prints:

```
Requires manual intervention:
  - 三一頌 [san_yi_song_1b286496]: download failed: no matching title in top 5 search results; LRC skipped (download failed)
```

### 3. Update both `_print_stats` callers to pass `selected_steps`

- Line 6673 (resume path): `_print_stats(results, db_client, console, format, selected_steps=manifest_data.get("selected_steps", []))` — `manifest_data` is loaded at line 6640 in the same branch.
- Line 6825 (normal path): `_print_stats(results, db_client, console, format, selected_steps=selected_steps)`.

### 4. Tests

a. Extend `TestSubmitLrcForSongHelper` in `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` (reuse its `stubs` fixture and call convention, e.g. `test_no_lyrics_skips` at line 392):

- `test_no_lyrics_records_skip_in_results`: `song.lyrics_raw = None`; assert `results[song_id]["lrc"] == "skipped_no_lyrics"`.
- `test_no_recording_records_skip_in_results`: `db_client.get_recording_by_song_id.return_value = None`; assert `results[song_id]["lrc"] == "skipped_no_recording"`.

b. New file `ops/admin-cli/tests/admin/test_audio_print_stats.py` testing `_print_stats` rich output via `Console(record=True, width=120)` + `console.export_text()`; `db_client = MagicMock()` (truthy `.title` attribute works with the `song.title if song else song_id` lookup). Cases:

- download-failed song with `selected_steps=["download", "lrc"]` → line contains `download failed:` AND `LRC skipped (download failed)`; same input with `selected_steps=["download"]` → no `LRC skipped` clause.
- `lrc == "skipped_no_lyrics"` → `LRC skipped (no lyrics)`.
- `components == "skipped_no_sections"` → `components skipped: no sections or LRC (run audio lrc first)`.
- fully-completed song → NOT listed under the section.
- panel contains `Needs attention:` with the correct count; empty-attention input → section header absent.
- one song with two failures (e.g. download failed + embedding failed) → single line, reasons joined with `; `.

## Critical files & anchors

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — `_print_stats` (9743–9954), `_submit_lrc_for_song` skip branches (7382–7391), callers (6673, 6825). Large file: grep-anchor each region and re-read before editing; diff against HEAD after edits (silent-corruption pitfall).
- `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` — extend helper tests.
- `ops/admin-cli/tests/admin/test_audio_print_stats.py` — new test file.

## Verification

```
cd ops/admin-cli && NO_COLOR=1 uv run --python 3.11 --extra admin --extra test pytest tests/admin/test_audio_print_stats.py tests/admin/test_audio_batch_eager_lrc.py tests/admin/test_audio_batch_unified.py -v
```

New-behavior proof (throwaway script, no DB/network):

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

Expected: panel shows `Needs attention: 1`; section lists only `s_dl` with both reasons; `s_ok` absent. Then the full admin-cli suite (`... pytest -v`) must stay green.

Finish: commit feature, run `graphify update .` as a separate chore commit, then `git pull --rebase && git push` (repo-mandated session close).

## Assumptions & contingencies

- The consolidated section REPLACES the five per-step "Failed X" sections (same error detail is carried into the per-song reasons; avoids printing the same song twice). If the user prefers keeping the per-step sections too, only step 2d changes — append instead of replace.
- `backfill_lyrics == "failed"` is included in the listing (it stores no error detail today; reason string is generic). Exit-code semantics are NOT changed: skipped statuses never flip `failed_any`.
- `selected_steps=None` (unknown) treats LRC-skipped note as present, matching today's counter behavior; both real callers now pass the real list.