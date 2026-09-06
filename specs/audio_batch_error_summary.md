# Plan: Enhance `audio batch` summary to list songs requiring manual intervention

## Context

The `audio batch` summary panel shows aggregate counts per step (Downloads, LRC, Analysis, Embedding, Components) but the per-step failure detail sections at the bottom only list songs whose step status is exactly `"failed"`. Songs that were **skipped** mid-pipeline — e.g. LRC skipped because the song has no lyrics (`skipped_no_lyrics`), or no recording (`skipped_no_recording`), or components skipped because no sections/LRC (`skipped_no_sections`) — are invisible in the summary. The user observed this: 108 songs processed, LRC completed = 107, LRC skipped (dl failed) = 1, but LRC failed = 0 — meaning one song was skipped for a non-download-failure reason and the user has no way to identify which song needs manual intervention (e.g. missing lyrics in the catalog).

The root cause: `_submit_lrc_for_song` returns skip statuses (`skipped_no_recording`, `skipped_no_lyrics`) but does **not** write them into `results[song_id]["lrc"]`. `_advance_song` treats them as "continue to next step" without recording the skip. So `_print_stats` has no data to surface these songs.

End state: after a batch run, the summary lists every song that requires manual attention — failed steps AND skipped steps that represent a data gap the user must remediate (missing lyrics, missing recording, no sections for components). Each listed song shows its name, song ID, and the specific reason.

## Approach

### Step 1 — Record LRC skip reasons in `results`

In `_submit_lrc_for_song` (audio.py:7343), the two skip paths that return without writing to `results` are:
- `skipped_no_recording` (line 7385) — no recording found
- `skipped_no_lyrics` (line 7391) — no lyrics available

Add `results[song_id]["lrc"] = "skipped_no_recording"` / `"skipped_no_lyrics"` and a human-readable `results[song_id]["lrc_error"]` in each path, mirroring how `skipped_r2` writes `results[song_id]["lrc"] = "completed"` and `results[song_id]["lrc_source"] = "r2_preexisting"`.

This is safe because `_advance_song` (line 8196) already handles these statuses in its `continue` branch — it checks the **return value** `status`, not `results[song_id][step]`, so writing to results does not change control flow. The existing `results.get(song_id, {}).get(step) in ("completed", "failed")` guard at line 8172 also won't match the new `skipped_*` values, so no double-submit.

### Step 2 — Add a "Songs requiring manual intervention" section to `_print_stats`

After the existing per-step failure sections (audio.py:9895–9954), add a new consolidated section that collects all songs with a step status in a manual-intervention set. This section catches **both** failures and the data-gap skips:

For each song in `results`, check each step key (`download`, `lrc`, `analyze`, `embedding`, `components`) for:
- `"failed"` — already listed individually above, but include in the consolidated section too for a single actionable list
- `"skipped_no_recording"` — no recording exists; user must run `audio download`
- `"skipped_no_lyrics"` — no lyrics in catalog; user must fix the song's lyrics
- `"skipped_no_sections"` — components can't run; user must run `audio lrc` or `audio analyze` first

Render as:

```
Songs requiring manual intervention:
 - <song_title> [<song_id>]
     lrc: no lyrics in catalog
     components: no sections or LRC available
```

One bullet per song, with indented sub-lines per affected step. Only print the section if at least one song qualifies. Use `db_client.get_song(song_id)` for the title (same pattern as the existing failure sections at lines 9902, 9913, etc.).

Group by song, not by step — a song may have multiple issues (e.g. no lyrics blocks both LRC and components). Deduplicate: each song appears once with all its issues listed.

### Step 3 — Add `components` to the skipped-status check in `_print_stats`

The components step already records `results[song_id]["components"] = "skipped_no_recording"` and `"skipped_no_sections"` (lines 7792, 7811). These are already in results — they just need to be surfaced. No change to `_submit_components_for_song` needed; the change is purely in `_print_stats` (Step 2 covers this).

### Step 4 — Map status values to human-readable reasons

Define a small dict at module scope or inside `_print_stats`:

```python
_MANUAL_INTERVENTION_STATUSES = {
    "failed": "failed",
    "skipped_no_recording": "no recording — run 'audio download <song_id>'",
    "skipped_no_lyrics": "no lyrics in catalog",
    "skipped_no_sections": "no sections or LRC — run 'audio lrc <song_id>' first",
}
```

Step labels: `{"download": "download", "lrc": "lrc", "analyze": "analysis", "embedding": "embedding", "components": "components"}`.

For the `failed` status, pull the error message from `results[song_id].get(f"{step}_error")` and append it (e.g. `lrc: failed — <error message>`). For skip statuses, use the mapped reason string.

### Step 5 — Test the new summary section

Add a test class `TestPrintStatsManualIntervention` to `ops/admin-cli/tests/admin/test_audio_batch_v4.py` that:
1. Constructs a `results` dict with a song that has `lrc == "skipped_no_lyrics"` (the exact scenario from the bug report — no failed steps, just a skip).
2. Calls `_print_stats(results, mock_db, console, "rich")` with a `Console(file=io.StringIO(), width=200)` and a mock `db_client` whose `get_song` returns a `Song` with a known title.
3. Asserts the output contains "Songs requiring manual intervention" and the song title + "no lyrics in catalog".

A second test: a song with `lrc == "failed"` and `components == "skipped_no_sections"` — verify both issues appear under the same song bullet, and the LRC error message is shown.

Use the existing `_make_song` helper (line 678) and `Console(file=io.StringIO(), width=200)` pattern (line 1279). The mock `db_client` only needs `get_song` to return the Song; `_print_stats` calls no other db methods in the rich path.

## Critical files & anchors

- **`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`**
  - `_submit_lrc_for_song` (7343–7481): add `results[song_id]["lrc"]` + `lrc_error` in the two skip paths (7385, 7391)
  - `_print_stats` (9743–9954): add consolidated manual-intervention section after line 9954; add `_MANUAL_INTERVENTION_STATUSES` mapping
- **`ops/admin-cli/tests/admin/test_audio_batch_v4.py`**
  - End of file (after line 1285): add `TestPrintStatsManualIntervention` class with 2 tests

## Verification

```bash
# Run the new tests (no Docker needed — pure unit tests with MagicMock)
cd ops/admin-cli && uv run --python 3.11 --extra admin --extra test pytest \
    tests/admin/test_audio_batch_v4.py::TestPrintStatsManualIntervention -v

# Run the full batch v4 test suite to confirm no regressions
cd ops/admin-cli && uv run --python 3.11 --extra admin --extra test pytest \
    tests/admin/test_audio_batch_v4.py -v
```

Manual smoke test (requires DB + R2 creds): run a batch against an album known to have a song with missing lyrics, e.g.:
```bash
zsh -ic 'sow-admin audio batch --album <album> --lrc --analyze'
```
Confirm the summary now lists the skipped song with "no lyrics in catalog".

## Assumptions & contingencies

- **Assumption**: the `results` dict is the single source of truth for `_print_stats`. The resume path (`_resume_from_manifest`, line 9456) reconstructs results from manifest entries — it only sets `results[song_id][step] = "failed"` for failed entries and leaves completed/skipped entries unset. This means on resume, skipped songs won't appear in the manual-intervention section. This is acceptable: resume is for re-polling active jobs, not re-deriving skips; the original run's summary already surfaced them. If the user wants skip visibility on resume, that's a separate enhancement.
- **Contingency**: if `_submit_lrc_for_song` is ever called with `song_id` not in `results` (e.g. from the eager-LRC path in `_download_worker`), the `results[song_id]` assignment would create a new key. This is safe — `_process_batch` initializes `results = {sid: {} for sid in song_ids}` (line 9113) and `_download_worker` operates on those same song_ids. The `results.setdefault` pattern is unnecessary; direct assignment matches the existing code style (line 7402, 7469).