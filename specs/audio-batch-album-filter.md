# audio batch --album filter fix

## 1. Background

`sow-admin audio batch --album X` produces surprising results when `X` is a
partial album name (e.g. `"聽見這世代的"` when the real `album_name` is
`"聽見這世代的呼喚"`). The user expected all songs in that album — recorded ones
especially — but only got the *unrecorded* songs of the album.

### Reproduction

```
sow_admin audio batch --album "聽見這世代的" --analyze --embedding --dry-run
Dry run mode

Batch ID: 2026-07-02T012124_batch
Selected steps: analyze, embedding
Force: False
Analysis tier: fast
Stale after: 120 minutes
Ordering: recordings.created_at ASC, hash_prefix ASC
Count: 2 song(s)

  • 蒙恩 (no recording - will download)
    Album: 聽見這世代的呼喚
  • 讓我尋見祢 (no recording - will download)
    Album: 聽見這世代的呼喚
```

Recorded songs of the same album are silently dropped.

### Root cause

`_resolve_song_ids`
(`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:4503`) runs in two
phases with **inconsistent `--album` matching semantics**:

| Phase | Purpose | `--album` filter mechanism | Match semantics |
|---|---|---|---|
| Phase 1 (audio.py:4536-4557) | Songs that *have* recordings | Post-filter in Python: `if album and album_name != album: continue` | **Exact, case-sensitive `==`** |
| Phase 2 (audio.py:4559-4569) | Songs *without* recordings; only when no status filter | Pushed to SQL via `list_songs(album=album)` | **Substring, case-insensitive `ILIKE` against `album_name` OR `album_series`** |

With partial input `"聽見這世代的"`:

- Phase 1 returns 0 rows → all recorded songs of the album are dropped.
- Phase 2 matches the 2 unrecorded songs via `ILIKE %...%` → those are the
  only ones selected.
- Net result: only the "missing recording" songs appear, exactly as observed.

### Compounding issues

1. **Phase 1 ignores the SQL `album` parameter** that
   `list_recordings_with_songs` already supports (client.py:795-797, the exact
   same `ILIKE %album% OR album_series ILIKE %album%` rule used in Phase 2).
   It re-implements filtering in Python with strict `==`, breaking
   consistency.
2. **`--album` help string is wrong**: it claims "exact match"
   (audio.py:4224), but Phase 2 already treats it as substring.
   `audio list --album` also uses substring (audio.py:1243 → client.py:795).
   The `batch` command is the lone outlier and the help text is misleading.
3. **Dry-run output doesn't surface the recorded-vs-unrecorded split**: when
   partial album input accidentally empties Phase 1, the dry-run silently
   shows only the missing subset. There's no visual cue that recorded songs
   were filtered out vs. genuinely absent.

## 2. Requirements

| ID | Requirement |
|---|---|
| R1 | `--album X` matches `album_name` OR `album_series` via case-insensitive substring (`ILIKE %X%`). Same rule across Phase 1, Phase 2, and `audio list`. |
| R2 | Without any status filter: batch selects the **union** of (songs of X that have a recording) + (songs of X that have no recording yet → will be downloaded first). |
| R3 | With a status filter (`--download-status`, `--lrc-status`, `--analysis-status`): status filter applies only to recorded songs. Unrecorded songs are NEVER added. Preserves today's behavior. |
| R4 | Dry-run explicitly groups selected songs under two labeled sections: "With recording" and "Missing recording (will download)", each with a per-section count. |
| R5 | `--album` help text corrected to reflect substring semantics. |
| R6 | Out of scope: `--song` filter (already consistent across phases); `audio delete`; `audio probe-batch`'s separate `--album` (different code path, no reported issue). |

## 3. Design

### 3.1 Phase 1: push `album` into SQL

In `_resolve_song_ids` (audio.py:4536-4557), pass `album` straight to the DB
layer and delete the Python post-filter:

```python
rows = db_client.list_recordings_with_songs(
    status=analysis_status,
    lrc_status=lrc_status,
    album=album,            # NEW: ILIKE %album% OR album_series ILIKE %album%
    limit=None,
    sort_by="created",
)

for recording, song_title, album_name, album_series in rows:
    if not recording.song_id:
        continue
    if download_status and recording.download_status != download_status:
        continue
    # REMOVED: if album and album_name != album: continue
    if song and (not song_title or song.lower() not in song_title.lower()):
        continue
    if recording.song_id not in song_ids:
        song_ids.append(recording.song_id)
```

This makes Phase 1 matching identical to Phase 2's matching rule.

### 3.2 Phase 2: no behavior change needed

Phase 2 (audio.py:4559-4569) already uses `list_songs(album=album)` with
ILIKE. After R3 (status filter ⇒ no Phase 2), its `has_status_filters` guard
stays. The `existing_recording` lookup and dedup
`if s.id in song_ids: continue` stay.

### 3.3 Dry-run: grouped output

Refactor `_print_dry_run_v4` (audio.py:4604-4649) to split its iteration into
two passes:

```
Batch ID: 2026-07-02T012124_batch
Selected steps: analyze, embedding
Force: False
Analysis tier: fast
Stale after: 120 minutes
Ordering: recordings.created_at ASC, hash_prefix ASC
Count: 5 song(s)  (3 with recording, 2 missing)

With recording (3):
  • 恩典之路 (a1b2c3)
    Album: 聽見這世代的呼喚
    Download: completed
    LRC: completed
    Analysis: completed
  • ...

Missing recording — will download (2):
  • 蒙恩
    Album: 聽見這世代的呼喚
  • 讓我尋見祢
    Album: 聽見這世代的呼喚
```

Implementation: build two lists first (`with_recording`, `missing`), then
print each with a header and per-section count. The line content per song is
unchanged from today.

### 3.4 Help text correction

`audio.py:4224`:

```python
album: Optional[str] = typer.Option(
    None, "--album",
    help="Filter by album name (substring, case-insensitive; matches album_name or album_series)",
),
```

## 4. Files to Change

| File | LOC | Change |
|---|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4536-4557 | Phase 1: pass `album=album` to SQL; drop Python `!=` post-filter. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4604-4649 | `_print_dry_run_v4`: grouped dry-run output. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4224 | `--album` help string. |
| `ops/admin-cli/tests/admin/test_audio_batch_v4.py` | (new tests) | Regression tests (see §5). |

No DB layer changes — `list_recordings_with_songs(album=...)` already supports
the correct ILIKE rule (client.py:795-797).

## 5. Test Plan

Add to `ops/admin-cli/tests/admin/test_audio_batch_v4.py`. Use real DB
fixtures already in the test harness (other tests in that file exercise
`_resolve_song_ids` indirectly via the `batch` Typer command).

| Test | Setup | Assertion |
|---|---|---|
| `test_batch_album_substring_includes_recorded_songs` | Album `"聽見這世代的呼喚"` with 2 songs: 1 with a recording, 1 without. Invoke `batch --album "聽見這世代的" --analyze --dry-run`. | Song IDs returned include BOTH songs (recorded + unrecorded). |
| `test_batch_album_exact_match_still_works` | Same album, exact full name passed. | Both songs returned (no regression for exact-name users). |
| `test_batch_album_match_album_series` | Song whose `album_series` contains substring "敬拜讚美15" but `album_name` does not contain "敬拜讚美". `--album "敬拜讚美"`. | Song matched via album_series substring. |
| `test_batch_album_with_status_filter_excludes_unrecorded` | Album X, 2 songs (1 recorded + analysis-completed, 1 unrecorded). `--album X --analysis-status incomplete`. | Only the song with incomplete analysis_status returned; unrecorded song NOT added. |
| `test_batch_album_no_match_empty` | `--album "NONEXISTENT"`. | Exit 0, "No songs found" message. |
| `test_batch_dry_run_grouped_output` | 3 recorded + 2 missing for album X. Invoke dry-run and capture console output. | Output contains both group headers and per-section counts "3 with recording, 2 missing". |
| `test_batch_album_help_text` (smoke) | `sow-admin audio batch --help`. | Help text mentions "substring, case-insensitive". |

### Test commands

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests/test_audio_batch_v4.py -v
```

## 6. Migration / Backward Compatibility

- **Behavior change**: users who previously passed a partial album string and
  observed the "only-unrecorded quirk" will now see the full union. This is
  the intended, documented fix.
- **No DB schema migration** — no SQL changes.
- **No config migration** — `album` is a CLI flag.
- Help text change is the only user-visible wording change.

## 7. Out of Scope

- `--song` filter refactor (already consistent substring matching in both
  phases).
- `audio delete` / `audio probe-batch` `--album` (separate code paths, no
  reported defect).
- `audio list --album` (already correct).
- Any change to status-filter semantics (R3 preserves today's behavior).

## 8. Verification (post-implementation)

```bash
# Lint + type-check (per AGENTS.md — Black line 100, Ruff py311)
ruff check ops/admin-cli/src/stream_of_worship/admin/commands/audio.py
black --check ops/admin-cli/src/stream_of_worship/admin/commands/audio.py

# Tests
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -v

# Manual smoke (the original failing case)
sow_admin audio batch --album "聽見這世代的" --analyze --embedding --dry-run
# Expected: now shows recorded songs under "With recording" + the 2 unrecorded
# songs under "Missing recording — will download".
```
