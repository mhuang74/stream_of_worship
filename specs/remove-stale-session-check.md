# Remove Stale-Session Check from LRC Editor Upload

## Problem

The LRC editor's upload flow blocks uploads with the error:

> "Canonical LRC disappeared from R2 since editor open"

This is caused by two bugs:

1. **Technical bug**: `check_canonical_changed()` in `upload.py:127` calls
   `r2_client.get_lrc_identity(original_identity.hash_prefix)` but
   `R2ObjectIdentity` has no `hash_prefix` field. The `hasattr` fallback
   passes `""`, so `get_lrc_identity("")` always returns `exists=False`,
   falsely triggering the "disappeared" error on every upload.

2. **Conceptual bug**: The stale-session check guards the wrong resource.
   It checks whether the **R2 LRC file** (`{hash_prefix}/lyrics.lrc`)
   changed since editor open — but that's the *output* being uploaded.
   The check was intended to detect concurrent writers, but
   `check_active_lrc_job()` already handles that case via
   `lrc_status`/`lrc_job_id` in the Recordings table.

The chances of canonical lyrics (from the Songs table) changing during
an editing session are negligible. This entire check is over-engineering
and should be removed.

## Decision

Remove all `R2ObjectIdentity`-based stale-session detection from the
LRC editor. Do **not** replace it with a canonical-lyrics hash check.
The only upload guard that remains is `check_active_lrc_job()`.

## Changes

### 1. `src/stream_of_worship/admin/editor/upload.py`

- Delete `check_canonical_changed()` function (lines 112–142)
- Remove the `check_canonical_changed` call from `upload_revised_lrc()`
  (lines 206–211)
- Remove `R2ObjectIdentity` from imports (line 15)

### 2. `src/stream_of_worship/admin/editor/state.py`

- Remove `canonical_identity: R2ObjectIdentity` field (line 54)
- Remove `R2ObjectIdentity` import (line 15)
- Remove docstring attribute reference (line 40)

### 3. `src/stream_of_worship/admin/editor/autosave.py`

- Remove `canonical_identity` from `AutosaveState` dataclass (line 37)
- Remove `canonical_identity` from `to_dict()` (lines 51–56)
- Remove `canonical_identity` from `from_dict()` (lines 71–77, 81)
- Remove `R2ObjectIdentity` import (line 16)
- Remove docstring attribute reference (line 30)

### 4. `src/stream_of_worship/admin/editor/screen.py`

- Remove `canonical_identity=self.state.canonical_identity` from
  autosave call (line 277)

### 5. `src/stream_of_worship/admin/commands/audio.py`

- Remove `canonical_identity` variable and `r2_client.get_lrc_identity()`
  call (lines 3400–3414)
- Remove `canonical_identity` from autosave resume path (line 3440)
- Remove `canonical_identity` parameter from `_build_fresh_editor_state()`
  signature and body (lines 3508, 3537)
- Remove `canonical_identity` from all `_build_fresh_editor_state()` call
  sites (lines 3453, 3460, 3473, 3478)
- Remove `R2ObjectIdentity` from imports (line 49)
- Remove `compute_lrc_hash` import if no longer used

### 6. `src/stream_of_worship/admin/services/r2.py`

- Remove `R2ObjectIdentity` dataclass (lines 20–33)
- Remove `get_lrc_identity()` method (lines 228–252)
- Keep `download_lrc_content()` — still needed to load existing LRC for
  editing

### 7. `src/stream_of_worship/admin/services/lrc_parser.py`

- Remove `compute_lrc_hash()` (lines 255–264) — only consumer was the
  stale-session check

### 8. `tests/admin/services/test_lrc_editor.py`

- Remove `R2ObjectIdentity` import (line 13)
- Remove `canonical_identity=R2ObjectIdentity(...)` from all
  `AutosaveState` and `EditorState` constructions
- Remove the `canonical_identity.etag` assertion (line 57)

### 9. `tests/admin/test_r2.py`

- Remove `R2ObjectIdentity` from import (line 10)
- Remove `TestGetLrcIdentity` class (lines 419–451)

## What Stays

- `check_active_lrc_job()` — blocks upload when `lrc_status="processing"`
- `download_lrc_content()` — loads existing LRC for editing
- `upload_lrc()` — the actual R2 upload
- Local/R2 backup logic — creates backups before upload
- Autosave — saves/restores `timed_lines`, `preserved_lines`, `dirty`,
  `source_mode` (without `canonical_identity`)

## Verification

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/services/test_lrc_editor.py \
  tests/admin/test_r2.py \
  -v
```
