# audio batch --album filter fix (v2)

> Revised plan addressing review findings across Admin CLI arg consistency, logic correctness, operational concerns, and runtime issues.

## Changelog from v1

| Item | v1 | v2 |
|---|---|---|
| `--album` help text | Fixed only `batch` | Fixed `batch`, `audio list`, `catalog list`, and `probe-batch` |
| `probe-batch --album` | Out of scope | **In scope**: replace N+1 Python filter with batched `list_songs` lookup that matches both `album_name` and `album_series` |
| `_resolve_song_ids` docstring | Not mentioned | Updated to reflect substring semantics |
| Test strategy | Integration-style tests added to `test_audio_batch_v4.py` (real DB fixtures) | Lightweight **unit tests** for `_resolve_song_ids` with `MagicMock` db_client; one regression test for `probe-batch` |
| Test file path | `ops/admin-cli/tests/test_audio_batch_v4.py` (wrong path) | Corrected to `ops/admin-cli/tests/admin/test_audio_batch_v4.py` |
| Dry-run ordering label | Unchanged | Updated to mention the two-phase ordering |
| N+1 in dry-run / Phase 2 | Not mentioned | Documented as pre-existing runtime issues (not fixed to keep scope tight) |
| `probe-batch --limit` vs filtering | Not mentioned | Flagged as pre-existing operational quirk |

---

## 1. Review Findings

### 1.1 Admin CLI arg consistency

- **`batch --album`** help text claims "exact match" but Phase 2 already does `ILIKE` substring. This is the primary bug.
- **`audio list --album`** and **`catalog list --album`** both say "Filter by album name" with no substring hint, yet their SQL layers already implement substring matching (`ILIKE %album%` against `album_name` OR `album_series`). The help text is silently misleading.
- **`probe-batch --album`** help text says "Filter by album name", but the implementation only checks `album_name` (Python-side `in` operator) and ignores `album_series`. It also performs an N+1 `get_song()` call per recording.
- **`--song`** help text is consistent across `batch` and `probe-batch` (both say "partial match"). No issue there.

**Resolution in v2**: Update all four `--album` help texts in one sweep. Include `probe-batch` logic fix.

### 1.2 Logic correctness

- **Phase 1 / Phase 2 mismatch** is exactly as described in v1 §1. Pushing `album` to the SQL layer in Phase 1 fixes it. ✓
- **R3 (status filters)** is correctly described. When any status filter is present, `has_status_filters` blocks Phase 2, so unrecorded songs are never added. ✓
- **`_resolve_song_ids` docstring** at line 4517 says "Filter by album name (exact match)". This docstring must be updated or it will lie about the new behavior.
- **`probe-batch --album` only matches `album_name`**, not `album_series`. If a user passes `--album "敬拜讚美"` and a recording's song has `album_series="敬拜讚美15"` but `album_name="Soaking Album"`, `probe-batch` silently excludes it. This is inconsistent with `batch`, `audio list`, and `catalog list`.
- **`probe-batch` applies `--limit` BEFORE `--album` filtering**. If a user runs `--limit 10 --album "X"` and the first 10 recordings don't belong to album X, the result is empty even if later recordings do match. This is pre-existing but worth noting.

### 1.3 Operational concerns

- **Ordering label in dry-run is misleading**: the header prints `Ordering: recordings.created_at ASC, hash_prefix ASC`. This only applies to the recorded subset. Unrecorded songs (appended from Phase 2) are actually ordered by `album_name, title` from `list_songs`. The dry-run should not claim a single ordering rule that only covers half the list.
- **Data integrity edge case in dry-run**: the current `_print_dry_run_v4` handles three states: `(song, recording)`, `(song, no recording)`, and `(no song)`. The grouped output design in v1 only showed two groups. A third group for "Song not found" should be preserved.
- **No DB schema migration needed** — the fix is purely in the application layer. ✓

### 1.4 Runtime issues (pre-existing, not in scope of this fix)

- **N+1 in Phase 2 of `_resolve_song_ids`**: `list_songs(album=album)` is one query, then `db_client.get_recording_by_song_id(s.id)` is called once per song. For albums with 100+ songs this is 100 extra round-trips.
- **N+1 in `_print_dry_run_v4`**: `db_client.get_song(song_id)` + `db_client.get_recording_by_song_id(song_id)` inside a loop = 2N queries.
- **N+1 in `probe-batch --album` (being fixed)**: the current `db_client.get_song(r.song_id)` per recording is an N+1. The v2 fix replaces it with a single `list_songs(album=album)` call.
- **N+1 in `probe-batch --song`**: still exists. Flagged in Out of Scope.
- **N+1 in `probe-batch` dry-run table**: `db_client.get_song(r.song_id)` per recording at line 7212. Pre-existing.

These are noted for awareness but deliberately **not** addressed in this plan to keep the change minimal and focused on the `--album` matching bug.

---

## 2. Background

`sow-admin audio batch --album X` produces surprising results when `X` is a partial album name (e.g. `"聽見這世代的"` when the real `album_name` is `"聽見這世代的呼喚"`). The user expected all songs in that album — recorded ones especially — but only got the *unrecorded* songs.

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

`_resolve_song_ids` (`audio.py:4503`) runs in two phases with **inconsistent `--album` matching semantics**:

| Phase | Purpose | `--album` filter mechanism | Match semantics |
|---|---|---|---|
| Phase 1 (audio.py:4536-4557) | Songs that *have* recordings | Post-filter in Python: `if album and album_name != album: continue` | **Exact, case-sensitive `==`** |
| Phase 2 (audio.py:4559-4569) | Songs *without* recordings; only when no status filter | Pushed to SQL via `list_songs(album=album)` | **Substring, case-insensitive `ILIKE` against `album_name` OR `album_series`** |

With partial input `"聽見這世代的"`:

- Phase 1 returns 0 rows → all recorded songs of the album are dropped.
- Phase 2 matches the 2 unrecorded songs via `ILIKE %...%` → those are the only ones selected.
- Net result: only the "missing recording" songs appear, exactly as observed.

---

## 3. Requirements

| ID | Requirement |
|---|---|
| R1 | `--album X` matches `album_name` OR `album_series` via case-insensitive substring (`ILIKE %X%`). Same rule across Phase 1, Phase 2, `audio list`, `catalog list`, and `probe-batch`. |
| R2 | Without any status filter: batch selects the **union** of (songs of X that have a recording) + (songs of X that have no recording yet → will be downloaded first). |
| R3 | With a status filter (`--download-status`, `--lrc-status`, `--analysis-status`): status filter applies only to recorded songs. Unrecorded songs are NEVER added. Preserves today's behavior. |
| R4 | Dry-run explicitly groups selected songs under two labeled sections: "With recording" and "Missing recording (will download)", each with a per-section count. If any song IDs are orphaned (song row missing), a third "Song not found" group is shown. |
| R5 | **All** `--album` help texts in the Admin CLI reflect substring semantics. |
| R6 | `probe-batch --album` filters via the same `album_name OR album_series` substring rule, and eliminates the N+1 `get_song()` loop. |
| R7 | Out of scope: `--song` filter refactor (already consistent); `audio delete`; any change to status-filter semantics (R3 preserves today's behavior); the N+1 patterns in dry-run and Phase 2. |

---

## 4. Design

### 4.1 Phase 1: push `album` into SQL

In `_resolve_song_ids` (audio.py:4536-4557), pass `album` straight to the DB layer and delete the Python post-filter:

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

### 4.2 Phase 2: no behavior change needed

Phase 2 (audio.py:4559-4569) already uses `list_songs(album=album)` with ILIKE. After R3 (status filter ⇒ no Phase 2), its `has_status_filters` guard stays. The `existing_recording` lookup and dedup `if s.id in song_ids: continue` stay.

### 4.3 Dry-run: grouped output

Refactor `_print_dry_run_v4` (audio.py:4604-4649) to split its iteration into two passes, plus a third catch-all for data-integrity orphans:

```
Batch ID: 2026-07-02T012124_batch
Selected steps: analyze, embedding
Force: False
Analysis tier: fast
Stale after: 120 minutes
Ordering: recordings.created_at ASC, hash_prefix ASC; unrecorded by album, title
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

Implementation: build three lists first (`with_recording`, `missing`, `not_found`), then print each with a header and per-section count. The line content per song is unchanged from today.

### 4.4 Docstring update

Update the `_resolve_song_ids` docstring (audio.py:4517):

```python
        album: Filter by album name (substring, case-insensitive;
            matches album_name or album_series)
```

### 4.5 Help text corrections (all commands)

| Command | File | Line | New help text |
|---|---|---|---|
| `audio batch` | `audio.py` | 4224 | `Filter by album name (substring, case-insensitive; matches album_name or album_series)` |
| `audio list` | `audio.py` | 1180 | `Filter by album name (substring, case-insensitive; matches album_name or album_series)` |
| `catalog list` | `catalog.py` | 526 | `Filter by album name (substring, case-insensitive; matches album_name or album_series)` |
| `audio probe-batch` | `audio.py` | 7137 | `Filter by album name (substring, case-insensitive; matches album_name or album_series)` |

### 4.6 `probe-batch --album` fix

Replace the N+1 Python loop (audio.py:7178-7185) with a batched lookup:

```python
if album:
    # One query for all songs matching the album substring on either field,
    # then filter the recordings list in-memory (O(N), no extra DB round-trips).
    matching_songs = db_client.list_songs(album=album, include_deleted=False)
    allowed_song_ids = {s.id for s in matching_songs}
    recordings = [r for r in recordings if r.song_id in allowed_song_ids]
```

This:
- Eliminates the N+1 `get_song()` calls.
- Uses `list_songs(...)` which already applies `ILIKE %album%` to both `album_name` and `album_series`.
- Maintains the existing `include_deleted=False` behavior (deleted songs are excluded).

Note: The `--song` filter in `probe-batch` (lines 7187-7194) still uses N+1 `get_song()`. It is left untouched per R7.

---

## 5. Files to Change

| File | LOC | Change |
|---|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4224 | `--album` help string (`batch`). |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4517 | `_resolve_song_ids` docstring. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4536-4557 | Phase 1: pass `album=album` to SQL; drop Python `!=` post-filter. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4604-4649 | `_print_dry_run_v4`: grouped dry-run output + updated ordering label. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 7137 | `--album` help string (`probe-batch`). |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 7178-7185 | `probe-batch`: batched `list_songs` album filter, drop N+1 loop. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4629-4631 | Update ordering label text in dry-run output. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 4632 | Update count line to show `(N with recording, M missing)`. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py` | 526 | `--album` help string (`catalog list`). |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | 1180 | `--album` help string (`audio list`). |
| `ops/admin-cli/tests/admin/test_audio_batch_v4.py` | (new tests) | Unit tests for `_resolve_song_ids` and `_print_dry_run_v4` (see §6). |
| `ops/admin-cli/tests/admin/test_audio_batch_v4.py` | (new tests) | One regression test for `probe-batch --album` via patched CLI. |

**No DB layer or schema changes** — `list_recordings_with_songs(album=...)` and `list_songs(album=...)` already support the correct ILIKE rule.

---

## 6. Test Plan

Add tests to `ops/admin-cli/tests/admin/test_audio_batch_v4.py`. All new tests use mocked `db_client` objects (no Docker / testcontainers required).

### 6.1 `_resolve_song_ids` unit tests

| Test | Setup | Assertion |
|---|---|---|
| `test_resolve_album_substring_includes_recorded_and_unrecorded` | `db.list_recordings_with_songs` returns 1 recorded song for album `"聽見這世代的呼喚"`. `db.list_songs` returns 1 unrecorded song for same album. Call `_resolve_song_ids(album="聽見這世代的", ...)` | Returns both song IDs (recorded + unrecorded) |
| `test_resolve_album_exact_match_still_works` | Same as above, pass exact full album name | Both song IDs returned |
| `test_resolve_album_matches_album_series` | Recording whose song has `album_series="敬拜讚美15"` but `album_name` lacks the substring | Song ID returned via `album_series` match |
| `test_resolve_album_with_status_filter_excludes_unrecorded` | 1 recorded (incomplete analysis) + 1 unrecorded. Pass `--album X --analysis-status incomplete` | Only recorded incomplete song returned |
| `test_resolve_album_no_match_empty` | Both mocked DB methods return empty lists | Returns `[]` |
| `test_resolve_song_filter_still_works` | Recorded song whose title contains `"恩典"`; pass `--song "恩典"` | Song ID returned |

### 6.2 Dry-run output tests

| Test | Setup | Assertion |
|---|---|---|
| `test_dry_run_grouped_output` | 3 recorded + 2 missing + 1 orphaned song ID. Patch `audio.console` with `Console(record=True)` and call `_print_dry_run_v4` | Output contains `With recording (3)`, `Missing recording — will download (2)`, `Song not found (1)` headers |
| `test_dry_run_count_line` | Same setup | Count line contains `5 song(s)  (3 with recording, 2 missing)` |

### 6.3 `probe-batch` regression test

| Test | Setup | Assertion |
|---|---|---|
| `test_probe_batch_album_matches_album_series` | Mock `db_client.list_recordings` to return a recording whose song has `album_series="敬拜讚美15"`. Patch `is_ffprobe_available`, `AdminConfig.load`, `get_db_client`. Run `probe-batch --album "敬拜讚美" --dry-run` via `CliRunner` | Output contains the song / recording hash; does not print "No recordings to probe" |

### Test commands

```bash
# All admin-cli tests (mocked, no Docker)
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests/admin/test_audio_batch_v4.py -v

# Full admin test suite
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -v
```

---

## 7. Migration / Backward Compatibility

- **Behavior change**: users who previously passed a partial album string and observed the "only-unrecorded quirk" will now see the full union. This is the intended, documented fix.
- **`probe-batch` behavior change**: users who relied on `probe-batch --album` only matching `album_name` will now also match `album_series`. This aligns the command with all other `--album` options.
- **No DB schema migration** — no SQL changes.
- **No config migration** — `album` is a CLI flag.
- Help text changes are the only user-visible wording changes.

---

## 8. Out of Scope

- `--song` filter refactor (already consistent substring matching in both `batch` phases, but `probe-batch --song` still has N+1 — documented as a separate follow-up).
- `audio delete` `--album` (separate code path, no reported defect).
- Pre-existing N+1 queries in `_print_dry_run_v4` and `probe-batch` dry-run table.
- Pre-existing `--limit` ordering quirk in `probe-batch` (limit applied before album/song filters).
- Any change to status-filter semantics (R3 preserves today's behavior).
- Adding batch-lookup helpers (e.g. `get_songs_by_ids`) to eliminate the remaining N+1 patterns.

---

## 9. Verification (post-implementation)

```bash
# Lint + type-check (per AGENTS.md — Black line 100, Ruff py311)
ruff check ops/admin-cli/src/stream_of_worship/admin/commands/audio.py \
       ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py
black --check ops/admin-cli/src/stream_of_worship/admin/commands/audio.py \
           ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py

# Tests
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests/admin/test_audio_batch_v4.py -v

# Manual smoke (the original failing case)
sow_admin audio batch --album "聽見這世代的" --analyze --embedding --dry-run
# Expected: shows recorded songs under "With recording" + the 2 unrecorded
# songs under "Missing recording — will download".

# probe-batch smoke
sow_admin audio probe-batch --album "聽見這世代的" --dry-run
# Expected: includes recordings whose song matches via album_series too.
```

---

## 10. Appendix: Pre-existing Runtime Notes (for future work)

| Location | Issue | Impact | Suggested fix (future ticket) |
|---|---|---|---|
| `_resolve_song_ids` Phase 2 | `get_recording_by_song_id` per song | N+1 for large albums | Add `get_recordings_by_song_ids([...])` batch method |
| `_print_dry_run_v4` | `get_song` + `get_recording_by_song_id` per song ID | 2N queries per dry-run | Use `get_songs_by_ids` and `get_recordings_by_song_ids` |
| `probe-batch` dry-run | `get_song` per recording | N+1 | Reuse batched lookup from main filter step |
| `probe-batch` | `--limit` applied before filters | May return fewer results than limit | Move limit to after all filters, or push filters to SQL |
