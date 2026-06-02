# Admin Interactive LRC Editor v3

## Summary

Add an admin-facing interactive LRC editor launched as:

```bash
uv run --extra admin sow-admin audio edit-lrc SONG_ID
```

The editor lets an admin download/cache a song's recording and canonical LRC, play the
recording, edit lyric text and timestamps, autosave recovery state, review validation
results and a unified diff, then either save a timestamped local draft or upload the
revision as the canonical R2 LRC.

This v3 keeps the v2 editor scope but closes the main data-loss and operational risks:
stale-session overwrites are blocked, active LRC generation races are handled, backups
are stored both locally and in R2, local drafts are never overwritten, crash recovery is
autosaved, and unknown canonical LRC content is preserved where possible.

## Key Changes

- Add `textual` to the existing `admin` optional dependency so the editor works with
  `--extra admin` and does not require the end-user `app` extra.
- Add a new Typer command under the existing audio command group:
  `sow-admin audio edit-lrc SONG_ID`.
- Keep the interactive editor implementation admin-owned, not a direct dependency on
  `src/stream_of_worship/app/screens/lyrics_preview.py`.
- Reuse existing admin DB, R2, cache, playback, LRC parsing, and upload behavior where
  possible.
- Add or centralize LRC parsing/serialization that writes canonical `[mm:ss.xx]`
  centisecond lyric timestamps while preserving supported metadata or unknown lines.
- Force-refresh the canonical LRC from R2 on editor open when
  `{hash_prefix}/lyrics.lrc` exists; do not use stale cached LRC as the upload base.
- Capture canonical object identity at editor open, such as ETag/LastModified plus a
  content hash, and re-check it immediately before upload.
- Block upload when the canonical R2 LRC changed, appeared, or disappeared since editor
  open.
- Detect active LRC generation as a concurrent writer and block upload until the job is
  no longer active.
- Before upload, create both:
  - a local timestamped backup under `{cache_dir}/{hash_prefix}/lrc/`;
  - an R2 timestamped backup under `{hash_prefix}/backups/`.
- Add autosave recovery so dirty edits survive process crashes, terminal disconnects,
  and accidental exits.
- Save explicit local drafts with timestamped filenames so drafts never overwrite older
  work.
- Add quality validation before upload: monotonic timestamps, no unresolved all-zero
  draft, duplicate timestamp warnings, duration sanity checks, and preservation warnings
  for unsupported LRC content.
- Add a timestamping workflow that supports fast live alignment without seeking backward
  on every line advance.

## Command Behavior

`sow-admin audio edit-lrc SONG_ID` should:

- Load admin config and DB client using the same patterns as existing audio commands.
- Resolve `SONG_ID` to a song and recording via the existing DB accessors.
- Fail clearly if no recording exists for the song.
- Initialize `R2Client` and `AssetCache` with the configured admin cache directory.
- Download/cache the recording audio from `{hash_prefix}/audio.mp3`.
- Fail before opening the editor if audio cannot be downloaded because alignment
  requires playback.
- Load current recording metadata, including `hash_prefix`, `r2_lrc_url`,
  `lrc_status`, `lrc_job_id`, and audio duration metadata if available.
- Force-refresh the canonical LRC from R2 at `{hash_prefix}/lyrics.lrc` when present,
  writing it to `{cache_dir}/{hash_prefix}/lrc/lyrics.lrc`.
- Capture the canonical LRC session token:
  - object exists or does not exist;
  - ETag and LastModified when available;
  - SHA-256 of downloaded canonical content when present.
- If no R2 LRC exists, create a draft from catalog lyrics using `songs.lyrics_lines` or
  `lyrics_raw`.
- Check for an existing autosave recovery file for the same song/hash. If present, ask
  whether to resume it, discard it, or save it aside before starting fresh.
- Launch the Textual editor with song metadata, audio path, original canonical content,
  editable line state, preservation state, and canonical session token.

## Editor Behavior

### Layout

The editor should provide:

- A main lyrics preview area showing the current lyric prominently and the next lyric
  below it.
- A playback/progress display.
- A line table showing all lyric rows with timestamp, text preview, current-line
  highlight, dirty/changed status, and warnings for duplicate or suspicious timestamps.
- A selected-line editing panel for text and timestamp edits.
- A visible recovery/draft/upload status indicator.
- A footer listing available keyboard shortcuts.

### Keyboard Controls

- `space`: pause/play audio.
- `left`: seek backward 5 seconds.
- `right`: seek forward 5 seconds.
- `up`: select previous lyric line and seek playback to that line timestamp.
- `down`: select next lyric line and seek playback to that line timestamp.
- `enter`: set selected line timestamp to the current playback time.
- `a`: set selected line timestamp to the current playback time, then advance selection
  to the next line without seeking playback.
- `e`: edit selected lyric text in a focused single-line editor.
- `t`: manually edit selected timestamp.
- `i`: insert a new line after the selected line.
- `I`: insert a new line before the selected line.
- `d`: delete the selected line after confirmation.
- `s`: validate, show diff, and open the save/upload decision prompt.
- `escape` or `q`: exit; warn first if unsaved changes exist.

### Timestamp Rules

- Store timestamps internally as seconds.
- Clamp manual timestamps to `>= 0`.
- Serialize lyric rows as `[mm:ss.xx]` using centisecond rounding.
- Handle centisecond rounding carry correctly, including `59.995` rolling into the next
  minute.
- Keep LRC lyric rows in displayed row order; block upload if displayed row timestamps
  are not monotonic.
- The first implementation does not need dedicated row-reordering commands.

### LRC Preservation Rules

- Parse canonical LRC into editable timed lyric rows plus preserved non-editable content.
- Preserve recognized metadata lines and unsupported non-timestamp lines when
  serializing, unless the admin explicitly deletes them through a future feature.
- Preserve unknown content in its original relative position where feasible.
- If any canonical content cannot be preserved safely, show a high-visibility warning in
  the validation screen and include the removal/change in the diff.
- Do not silently drop malformed lines during upload validation.

### Autosave Recovery

- Maintain an autosave recovery file under
  `{cache_dir}/{hash_prefix}/lrc/lyrics.autosave.json`.
- Update autosave after each meaningful edit or via a short debounce.
- Autosave should include enough state to restore lyric rows, preserved content,
  canonical session token, dirty status, and source mode.
- On clean upload or explicit discard, remove the autosave file.
- On intentional local draft save, keep autosave until the admin exits cleanly or
  uploads, because draft save does not necessarily mean the editing session is complete.

### Draft LRC Fallback

When no existing R2 LRC is available:

- Build draft lines from catalog lyrics.
- Prefer parsed `lyrics_lines` if present.
- Fall back to non-empty lines from `lyrics_raw`.
- Assign default timestamp `00:00.00` to each draft line.
- Mark the editor state as dirty so the admin must consciously save/upload the draft.
- Preserve the no-canonical-at-open session token so upload can be blocked if a canonical
  LRC appears while the editor is open.

## Save And Upload Flow

Saving must not silently overwrite shared LRC content.

When the admin presses `s`:

- Serialize the revised LRC.
- Validate it with the admin LRC parser/serializer.
- Run quality checks:
  - block upload if timestamps are not monotonic in displayed row order;
  - block upload if every non-empty lyric row remains at `00:00.00`;
  - block upload if unknown/malformed canonical content would be silently dropped;
  - warn on duplicate timestamps;
  - warn when revised LRC duration is implausibly short or long compared with recording
    metadata or decoded audio duration.
- Generate a unified diff between the forced-refreshed original serialized LRC and the
  revised serialized LRC.
- Show validation results, preservation warnings, and the diff in the TUI.
- Offer these choices:
  - Save timestamped local draft.
  - Upload to R2.
  - Cancel and return to editing.

### Local Draft Save

- Write to a timestamped path such as
  `{cache_dir}/{hash_prefix}/lrc/lyrics.edited.YYYYMMDD-HHMMSS.lrc`.
- Do not overwrite prior local drafts.
- Do not update R2.
- Do not update the database.
- Notify the admin of the saved path.

### Upload To R2

Before upload:

- Refresh recording metadata and block upload if `lrc_status`/`lrc_job_id` indicates an
  active LRC generation job.
- Re-check `{hash_prefix}/lyrics.lrc` in R2 and compare it to the canonical session
  token captured on editor open.
- Block upload if canonical R2 object identity changed, if a new canonical LRC appeared
  after opening a no-canonical draft, or if the canonical LRC disappeared unexpectedly.
- If blocked, show a clear stale-session message and offer to save a timestamped local
  draft before exiting or reloading.

If upload is allowed:

- Save a local timestamped backup of the current canonical LRC content when it exists.
- Upload an R2 backup of the current canonical LRC content when it exists, using a key
  such as `{hash_prefix}/backups/lyrics.YYYYMMDD-HHMMSS.lrc`.
- Upload the revised file to `{hash_prefix}/lyrics.lrc` through `R2Client.upload_lrc`.
- Update the recording through `db_client.update_recording_lrc(...)`.
- Keep the existing `update_recording_lrc(...)` auto-publish behavior unchanged.
- If R2 upload succeeds but DB update fails, show the operation as partial success with:
  - revised canonical R2 URL;
  - local backup path;
  - R2 backup URL;
  - explicit instruction to rerun status reconciliation or retry the DB update command
    path once available.
- On full success, clear autosave and notify the admin with song title, line count,
  duration, R2 URL, local backup path, and R2 backup URL.

## Testing Plan

- Unit test LRC serialization:
  - centisecond formatting;
  - rounding carry behavior;
  - timestamp clamping;
  - inserted/deleted lines;
  - empty catalog-draft handling;
  - preservation of metadata and unknown non-timestamp lines.
- Unit test command setup with mocked DB/R2/cache:
  - valid song and recording opens editor;
  - missing recording fails;
  - audio download failure fails before editor launch;
  - stale cached LRC is ignored when R2 has canonical LRC;
  - catalog lyrics fallback is used when no R2 LRC exists;
  - autosave recovery is detected on open.
- Unit test upload safety:
  - diff base is the forced-refreshed canonical content;
  - upload is blocked when canonical R2 object changed since open;
  - upload is blocked when canonical R2 object appears during a draft session;
  - upload is blocked while an LRC job is active;
  - local and R2 canonical backups are written before overwrite;
  - upload uses `{hash_prefix}/lyrics.lrc`;
  - partial DB update failure is reported with recovery details.
- Unit test quality checks:
  - non-monotonic timestamps block upload;
  - all-zero draft blocks upload;
  - duplicate timestamps warn;
  - duration outliers warn or require explicit override;
  - unsupported canonical content cannot be silently dropped.
- Textual behavior tests for:
  - stamping selected line with current playback time;
  - stamping and advancing without seeking;
  - 5-second left/right seeks;
  - up/down line selection and seeking;
  - edit text;
  - edit timestamp;
  - insert before/after;
  - delete with confirmation;
  - autosave after edits;
  - resume/discard autosave prompt;
  - dirty-exit warning;
  - diff/save/upload prompt.
- Upload-path test with mocked `R2Client.upload_lrc` and DB update to confirm parity
  with `audio upload-lrc` plus the new stale-session and backup protections.
- Run:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
```

## Assumptions

- This is an admin-only tool.
- R2 `{hash_prefix}/lyrics.lrc` remains the canonical shared LRC path.
- Uploading edited LRC should keep the existing `update_recording_lrc(...)`
  auto-publish behavior.
- Stale-session upload blocking is mandatory.
- Backups must be stored both locally and in R2.
- Explicit local draft saves use timestamped filenames and never overwrite previous
  drafts.
- Autosave recovery is required for dirty editing state.
- Unknown canonical LRC content should be preserved where possible.
- The first version uses a line-editor UX, not inline spreadsheet-style cell editing.
- Local draft save is for experimentation and never updates shared state.
- Upload requires explicit confirmation after reviewing validation results and the
  unified diff.
- The uploaded revised LRC becomes the canonical shared file at
  `{hash_prefix}/lyrics.lrc`.
- The end-user lyrics preview screen should not be changed except for safe shared parser
  or serialization utility reuse.
