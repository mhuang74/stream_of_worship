# Fix Stale-Session Check & Rename Canonical → Transcribed

## Problem

Two issues in the LRC editor upload flow:

### 1. Terminology: "canonical" is misused

The codebase uses **"canonical"** to refer to the R2 LRC file
(`{hash_prefix}/lyrics.lrc`), but the correct terminology is:

- **Canonical lyrics** — lyrics scraped from sop.org, stored in
  `songs.lyrics_raw` / `songs.lyrics_lines` (the Songs table)
- **Transcribed lyrics** — the timed LRC file on R2
  (`{hash_prefix}/lyrics.lrc`), produced by the analysis service or
  manually edited via the LRC editor

All "canonical" references in the editor code actually refer to the R2
transcribed LRC, not the Songs table lyrics. This must be renamed.

### 2. Stale-session check is broken (but should be fixed, not removed)

`check_canonical_changed()` in `upload.py:127` calls
`r2_client.get_lrc_identity(original_identity.hash_prefix)` but
`R2ObjectIdentity` has no `hash_prefix` field. The `hasattr` fallback
passes `""`, so `get_lrc_identity("")` always returns `exists=False`,
falsely triggering the "disappeared" error on every upload.

The previous spec (`remove-stale-session-check.md`) proposed removing
the check entirely. However, the stale-session check serves a valid
purpose: **optimistic concurrency control**. If another editor uploaded
a revised LRC while I was editing, my upload should be blocked because
I'm working from an outdated starting point. The fix is to pass
`hash_prefix` as a separate parameter instead of trying to extract it
from the identity object.

`check_active_lrc_job()` only guards against automated LRC generation
jobs (`lrc_status="processing"`). It does **not** guard against a
second human editor who opens the same recording and uploads first.
The stale-session check fills that gap.

## Decision

1. **Fix** the stale-session check (don't remove it) — pass `hash_prefix`
   as a separate parameter
2. **Rename** all "canonical" references in the editor code to
   "transcribed" where they refer to the R2 LRC file
3. **Simplify** `R2ObjectIdentity` — remove the unused `content_hash`
   field (ETag is the authoritative content hash for S3 objects)
4. **Remove** `compute_lrc_hash()` — its only consumer was the dead
   `content_hash` field
5. **Simplify** the comparison — drop `last_modified` check since ETag
   is sufficient (if content changed, ETag changed; `last_modified` can
   change without content change)

## Changes

### 1. `src/stream_of_worship/admin/services/r2.py`

- Rename docstrings: "canonical LRC" → "transcribed LRC"
- Remove `content_hash` field from `R2ObjectIdentity` dataclass
  (lines 20–33), leaving only `exists`, `etag`, `last_modified`
- Keep `get_lrc_identity()` method (lines 228–252) — still needed
- Keep `download_lrc_content()` — still needed to load existing LRC

### 2. `src/stream_of_worship/admin/services/lrc_parser.py`

- Remove `compute_lrc_hash()` (lines 255–264) — only consumer was the
  removed `content_hash` field

### 3. `src/stream_of_worship/admin/editor/upload.py`

- Rename `check_canonical_changed()` → `check_transcribed_changed()`
  (lines 112–142)
- Fix the bug: change signature to accept `hash_prefix: str` as a
  separate parameter instead of extracting it from `original_identity`:
  ```python
  def check_transcribed_changed(
      r2_client: R2Client,
      hash_prefix: str,
      original_identity: R2ObjectIdentity,
  ) -> Tuple[bool, str]:
      current_identity = r2_client.get_lrc_identity(hash_prefix)
  ```
- Simplify comparison: remove `last_modified` check (lines 137–139).
  ETag is sufficient — it's the MD5 of the object data. If content
  changed, ETag changed.
- Update error messages: "Canonical LRC" → "Transcribed LRC"
- Rename `canonical_content` → `transcribed_content` in
  `save_local_backup()` and `upload_r2_backup()`
- Rename `original_canonical_content` → `original_transcribed_content`
  in `upload_revised_lrc()` signature and body
- Update the `check_transcribed_changed` call in `upload_revised_lrc()`
  to pass `hash_prefix`:
  ```python
  changed, reason = check_transcribed_changed(
      r2_client, hash_prefix, state.transcribed_identity,
  )
  ```
- Remove `R2ObjectIdentity` from imports if no longer directly
  referenced (it's still used in the function signature, so keep it)

### 4. `src/stream_of_worship/admin/editor/state.py`

- Rename `canonical_identity: R2ObjectIdentity` →
  `transcribed_identity: R2ObjectIdentity` (line 54)
- Update docstring: "Session token for stale-session detection" →
  "Session token for stale-session detection of the transcribed LRC on R2"
- Keep `R2ObjectIdentity` import

### 5. `src/stream_of_worship/admin/editor/autosave.py`

- Rename `canonical_identity` → `transcribed_identity` in
  `AutosaveState` dataclass (line 37)
- Rename in `to_dict()`: key `"canonical_identity"` →
  `"transcribed_identity"` (lines 51–56). Drop `content_hash` from
  serialization since the field is removed from `R2ObjectIdentity`.
- Rename in `from_dict()`: read `"transcribed_identity"` key with
  `"canonical_identity"` fallback for backward compatibility with
  existing autosave files on disk (lines 71–77, 81). Drop
  `content_hash` from deserialization.
- Update docstring attribute reference (line 30)
- Keep `R2ObjectIdentity` import

### 6. `src/stream_of_worship/admin/editor/screen.py`

- Rename `canonical_identity=self.state.canonical_identity` →
  `transcribed_identity=self.state.transcribed_identity` in autosave
  call (line 277)

### 7. `src/stream_of_worship/admin/commands/audio.py`

- Rename `canonical_identity` → `transcribed_identity` variable
  (lines 3400–3414)
- Remove `compute_lrc_hash` import (line 43) and the
  `content_hash` enrichment at lines 3408–3413:
  ```python
  # Before:
  canonical_identity = r2_client.get_lrc_identity(recording.hash_prefix)
  if canonical_identity.exists:
      canonical_content = r2_client.download_lrc_content(recording.hash_prefix)
      if canonical_content:
          content_hash = compute_lrc_hash(canonical_content)
          canonical_identity = R2ObjectIdentity(
              exists=True,
              etag=canonical_identity.etag,
              last_modified=canonical_identity.last_modified,
              content_hash=content_hash,
          )
          source_mode = "r2"

  # After:
  transcribed_identity = r2_client.get_lrc_identity(recording.hash_prefix)
  if transcribed_identity.exists:
      transcribed_content = r2_client.download_lrc_content(recording.hash_prefix)
      if transcribed_content:
          source_mode = "r2"
  ```
- Rename `canonical_content` → `transcribed_content` throughout the
  editor launch section
- Rename `canonical_identity` → `transcribed_identity` in autosave
  resume path (line 3440)
- Rename `canonical_identity` parameter in `_build_fresh_editor_state()`
  signature and body (lines 3508, 3537) → `transcribed_identity`
- Rename at all `_build_fresh_editor_state()` call sites (lines 3453,
  3460, 3473, 3478)
- Rename `original_canonical_content` → `original_transcribed_content`
  where passed to `LRCEditorApp`
- Remove `R2ObjectIdentity` from imports if no longer directly
  constructed (line 49) — check if the `R2ObjectIdentity(...)` reconstr
  at line 3409 is the only construction site; if so, remove import
- Remove `compute_lrc_hash` import (line 43)

### 8. `tests/admin/services/test_lrc_editor.py`

- Rename `canonical_identity=R2ObjectIdentity(...)` →
  `transcribed_identity=R2ObjectIdentity(...)` in all `AutosaveState`
  and `EditorState` constructions
- Remove `content_hash` from any `R2ObjectIdentity(...)` constructions
- Remove the `canonical_identity.etag` assertion (line 57) — update to
  `transcribed_identity.etag`
- Keep `R2ObjectIdentity` import (still used in test constructions)

### 9. `tests/admin/test_r2.py`

- Update `TestGetLrcIdentity` docstrings: "canonical" → "transcribed"
- Keep the test class — `get_lrc_identity()` is still used
- Remove `content_hash` from any `R2ObjectIdentity` assertions if
  present

### 10. `tests/admin/services/test_lrc_parser.py`

- Remove `compute_lrc_hash` import (line 11)
- Remove `TestComputeLrcHash` class (lines 199–209)

## What Stays

- `check_active_lrc_job()` — blocks upload when `lrc_status="processing"`
- `download_lrc_content()` — loads existing LRC for editing
- `upload_lrc()` — the actual R2 upload
- Local/R2 backup logic — creates backups before upload
- Autosave — saves/restores `timed_lines`, `preserved_lines`, `dirty`,
  `source_mode`, `transcribed_identity`
- `source_mode` field — still `"r2"` or `"catalog"`
- `R2ObjectIdentity` dataclass — simplified (no `content_hash`)

## Optimistic Concurrency Flow (After Fix)

```
1. EDITOR OPEN
   ├── R2: get_lrc_identity(hash_prefix) → R2ObjectIdentity (exists, etag)
   ├── if exists: R2: download_lrc_content(hash_prefix) → transcribed_content
   │   └── source_mode = "r2"
   ├── if not exists:
   │   └── build draft from catalog song.lyrics_lines/lyrics_raw
   │       └── source_mode = "catalog"
   └── Build EditorState(..., transcribed_identity, source_mode, ...)

2. EDITING
   └── Mutations → state.dirty = True

3. UPLOAD (upload_revised_lrc)
   ├── Check stale session: transcribed_identity (session) vs current R2 identity
   │   ├── get_lrc_identity(hash_prefix) → current_identity
   │   ├── Compare exists/etag against session's transcribed_identity
   │   └── Block if changed → "Transcribed LRC changed since editor open"
   ├── Check active LRC job: DB lrc_status == "processing"
   │   └── Block if job active
   ├── Local backup of current transcribed content
   ├── R2 backup of current transcribed content
   ├── Upload revised LRC → {hash_prefix}/lyrics.lrc
   └── Update DB: recording.r2_lrc_url
```

## Autosave Backward Compatibility

Existing autosave files on disk contain a `"canonical_identity"` key.
After the change, `from_dict()` will:

1. Try to read `"transcribed_identity"` key first
2. Fall back to `"canonical_identity"` key for old files
3. Either way, construct `R2ObjectIdentity` without `content_hash`

This ensures old autosave files load without error. The extra
`"canonical_identity"` key in old files is simply ignored.

## Verification

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/services/test_lrc_editor.py \
  tests/admin/services/test_lrc_parser.py \
  tests/admin/test_r2.py \
  -v
```
