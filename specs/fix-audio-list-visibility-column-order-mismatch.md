# Plan: Fix `audio list` Visibility Column-Order Mismatch

## Context

`sow_admin audio list --visibility published` returns "No recordings found", and `--visibility review` returns rows whose Visibility column renders as `- none` despite the underlying database holding the correct `review` value.

**This is a display/parsing bug, not data corruption.** Live DB query confirms 218 recordings are genuinely `review` and 0 are `published` — the data is intact and spec-compliant. The `audio list` command mis-parses `visibility_status` as `None` due to a physical-vs-canonical column order mismatch that a prior fix (`be5f762`) missed in one query path.

## Root Cause

`list_recordings_with_songs` (`db/client.py:804`) issues `SELECT r.*, s.title ...`, which returns columns in **physical** DB order. But `Recording.from_row` (`db/models.py:246-258`, the `row_len >= 34` branch) expects **canonical** order defined by `RECORDING_COLUMNS_SELECT` (`db/schema.py:243`).

The two orders diverge because the key-accuracy-v2 columns (`key_algorithm_version`, `key_score_margin`, `key_window_agreement`, `key_candidates`, `key_detected_at`) were appended at the **end** of the physical table via ALTER TABLE migration (physical positions 29-33), but in the canonical list they sit at positions 14-18.

| field | physical idx | canonical idx (`from_row` expects) |
|---|---|---|
| loudness_db | 14 | 19 |
| visibility_status | 26 | **31** |
| key_window_agreement | 31 | 16 |

So `from_row` reads `row[31]` for visibility — which in physical order is `key_window_agreement` (NULL for recordings not re-analyzed with key v2) → parsed as `None` → rendered as `- none`.

This matches the observed symptom exactly: every `--visibility review` row (real `review` in DB) shows `- none`, and `--visibility published` finds nothing because the actual value is `review`.

### Why it wasn't caught

Commit `be5f762` ("use explicit column lists to avoid from_row physical-order mismatch", Jul 5) converted the other `SELECT * FROM recordings` / `SELECT r.*` queries to `RECORDING_COLUMNS_SELECT` / `RECORDING_COLUMNS_FOR_JOIN`, with regression tests for canonical-order 34-column rows — but it **missed `list_recordings_with_songs`**. Two other `SELECT r.*` queries were also missed (latent same bug).

## Evidence (live DB, read-only)

```
visibility   lrc_status   count
(null)       pending      1
(null)       processing   220
hold         completed    2
hold         failed       66
review       completed    218
published    —            0
```

Physical column order (from `information_schema.columns`, 34 cols):

```
0  content_hash          14 loudness_db          28 deleted_at
1  hash_prefix           15 beats                29 key_algorithm_version
2  song_id               16 downbeats            30 key_score_margin
3  original_filename     17 sections             31 key_window_agreement
4  file_size_bytes       18 embeddings_shape     32 key_candidates
5  imported_at           19 analysis_status      33 key_detected_at
6  r2_audio_url          20 analysis_job_id
7  r2_stems_url          21 lrc_status
8  r2_lrc_url            22 lrc_job_id
9  duration_seconds      23 created_at
10 tempo_bpm             24 updated_at
11 musical_key           25 youtube_url
12 musical_mode          26 visibility_status
13 key_confidence        27 download_status
```

## Implementation Steps

### 1. Fix `list_recordings_with_songs` (the reported symptom)
**File:** `ops/admin-cli/src/stream_of_worship/admin/db/client.py` (line 804)

Replace `SELECT r.*, s.title ...` with the canonical join column list:

```python
query = f"""
    SELECT {RECORDING_COLUMNS_FOR_JOIN}, s.title as song_title, s.album_name, s.album_series
    FROM recordings r
    LEFT JOIN songs s ON r.song_id = s.id
    WHERE 1=1
"""
```

Ensure `RECORDING_COLUMNS_FOR_JOIN` is imported (it is already imported alongside `RECORDING_COLUMNS_SELECT` at `client.py:19`).

### 2. Fix `list_soft_deleted_recordings_with_counts` (latent same bug)
**File:** `ops/admin-cli/src/stream_of_worship/admin/db/client.py` (line 1556)

Replace `SELECT r.*, COUNT(si.id) ...` with:

```python
sql = f"""
    SELECT {RECORDING_COLUMNS_FOR_JOIN}, COUNT(si.id) AS songset_reference_count
    FROM recordings r
    LEFT JOIN songset_items si ON si.recording_hash_prefix = r.hash_prefix
    WHERE r.deleted_at IS NOT NULL
    GROUP BY r.content_hash
    ORDER BY r.deleted_at DESC, r.hash_prefix ASC
"""
```

This query explicitly feeds `Recording.from_row(values[:RECORDING_COLUMN_COUNT])` (line 1571), so it has the identical physical-order mismatch.

### 3. Fix inline "pending recordings" query (latent same bug)
**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` (line 3064)

Replace `SELECT r.*, s.title ...` with:

```python
cursor.execute(f"""
    SELECT {RECORDING_COLUMNS_FOR_JOIN}, s.title as song_title
    FROM recordings r
    LEFT JOIN songs s ON r.song_id = s.id
    WHERE (r.analysis_status != 'completed' OR r.lrc_status != 'completed')
      AND r.deleted_at IS NULL
      AND (s.deleted_at IS NULL OR s.id IS NULL)
    ORDER BY r.imported_at DESC
    """)
```

Add `RECORDING_COLUMNS_FOR_JOIN` to the existing import at `audio.py:36` (which currently imports `RECORDING_COLUMNS_SELECT`).

### 4. Add regression test
**File:** `ops/admin-cli/tests/admin/test_models.py`

Add a test mirroring the existing 34-column canonical-order test, but exercising the physical-order mismatch scenario:
- Construct a row tuple in **physical** order (visibility_status at index 26, key_window_agreement at index 31) and assert that `Recording.from_row` does **not** silently mis-map `visibility_status` to `key_window_agreement`.
- This documents the contract that callers must supply canonical-order rows (via `RECORDING_COLUMNS_SELECT`/`RECORDING_COLUMNS_FOR_JOIN`), not `r.*`.

Optionally add a DB-client-level test (if test infra permits) that `list_recordings_with_songs` returns `Recording` objects whose `visibility_status` matches the column value.

## Files to Modify

| File | Changes |
|------|---------|
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Lines 804, 1556: replace `r.*` with `RECORDING_COLUMNS_FOR_JOIN` |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Line 3064: replace `r.*` with `RECORDING_COLUMNS_FOR_JOIN`; extend import at line 36 |
| `ops/admin-cli/tests/admin/test_models.py` | Add regression test for physical-vs-canonical order |

## Verification

1. **Display fix:**
   ```bash
   sow_admin audio list --visibility review      # 218 rows, Visibility column shows "● review"
   sow_admin audio list --visibility published   # legitimately empty (0 published)
   sow_admin audio list                          # all rows show correct visibility (review/hold/none)
   ```
2. **Soft-deleted list:** `sow_admin maintenance ...` (soft-deleted recordings path) shows correct visibility.
3. **Pending recordings:** the pending-recordings view still renders correctly.
4. **Tests:**
   ```bash
   uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_models.py -v
   ```
5. **No data migration required** — the database is already correct; this is purely a read-path parsing fix.

## Out of Scope

- No changes to `update_recording_lrc` visibility semantics (the `review`-on-completion behavior is intended per `add_visibility_status.md` spec).
- No bulk republish of `review` recordings to `published` (separate concern; would be a deliberate operational decision, not a bug fix).
- No changes to the read-only app client (`read_client.py`) — it was already migrated to `RECORDING_COLUMNS_SELECT` by `be5f762`.
