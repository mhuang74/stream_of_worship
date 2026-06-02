# Admin Interactive LRC Editor v2

## Summary

Add an admin-facing interactive LRC editor launched as:

```bash
uv run --extra admin sow-admin audio edit-lrc SONG_ID
```

The tool lets an admin download/cache a song's audio and current LRC, listen to the
recording, see synchronized lyric highlighting, edit lyric lines and timestamps, then
review a unified diff before choosing either a local draft save or an R2 upload.

This v2 keeps the original editor scope, but strengthens canonical overwrite safety,
stale-cache handling, timestamping UX, and LRC quality validation before upload.

## Key Changes

- Add `textual` to the existing `admin` optional dependency so the editor works with
  `--extra admin` and does not require the end-user `app` extra.
- Add a new Typer command under the existing audio command group:
  `sow-admin audio edit-lrc SONG_ID`.
- Keep the interactive editor implementation admin-owned, not a direct dependency on
  `src/stream_of_worship/app/screens/lyrics_preview.py`.
- Reuse existing admin DB, R2, cache, playback, LRC parsing, and upload behavior where
  possible.
- Add or centralize LRC serialization that writes canonical `[mm:ss.xx]` centisecond
  timestamps.
- Force-refresh the canonical LRC from R2 on editor open when `{hash_prefix}/lyrics.lrc`
  exists; do not use stale cached LRC as the upload base.
- Before upload, create a timestamped backup of the current canonical LRC content, then
  upload the revised file to `{hash_prefix}/lyrics.lrc`.
- Add quality validation before upload: monotonic timestamps, no unresolved all-zero
  draft, duplicate timestamp warnings, and revised duration sanity checks against the
  recording/audio duration.
- Add a timestamping workflow that supports fast live alignment without seeking backward
  on every line advance.

## Command Behavior

`sow-admin audio edit-lrc SONG_ID` should:

- Load admin config and DB client using the same patterns as existing audio commands.
- Resolve `SONG_ID` to a song and recording via the existing DB accessors.
- Fail clearly if no recording exists for the song.
- Initialize `R2Client` and `AssetCache` with the configured admin cache directory.
- Download/cache the recording audio from `{hash_prefix}/audio.mp3`.
- Force-refresh the existing LRC from R2 at `{hash_prefix}/lyrics.lrc` when present,
  writing it to the local cache path `{cache_dir}/{hash_prefix}/lrc/lyrics.lrc`.
- If no R2 LRC exists, create a draft from catalog lyrics using `songs.lyrics_lines` or
  `lyrics_raw`.
- Launch the Textual editor with song metadata, audio path, original LRC content, and
  editable line state.

If audio cannot be downloaded, the command should fail before opening the editor because
alignment requires playback.

## Editor Behavior

### Layout

The editor should provide:

- A main lyrics preview area showing the current lyric prominently and the next lyric
  below it.
- A playback/progress display.
- A line table showing all lyric rows with timestamp, text preview, current-line
  highlight, and dirty/changed status.
- A selected-line editing panel for text and timestamp edits.
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
- Serialize all saved/uploaded output as `[mm:ss.xx]`.
- Use centisecond rounding, not milliseconds.
- Keep LRC rows sorted by their displayed order unless the admin explicitly moves or
  edits rows in a way that changes row timestamps. The first implementation does not
  need dedicated row-reordering commands.

### Draft LRC Fallback

When no existing R2 LRC is available:

- Build draft lines from catalog lyrics.
- Prefer parsed `lyrics_lines` if present.
- Fall back to non-empty lines from `lyrics_raw`.
- Assign default timestamp `00:00.00` to each draft line.
- Mark the editor state as dirty so the admin must consciously save/upload the draft.

## Save And Upload Flow

Saving must not silently overwrite the shared LRC.

When the admin presses `s`:

- Serialize the revised LRC.
- Validate it with the admin LRC parser.
- Run quality checks:
  - Block upload if timestamps are not monotonic in displayed row order.
  - Block upload if every non-empty lyric row remains at `00:00.00`.
  - Warn on duplicate timestamps.
  - Warn when revised LRC duration is implausibly short or long compared with recording
    metadata or decoded audio duration.
- Generate a unified diff between the forced-refreshed original serialized LRC and the
  revised serialized LRC.
- Show validation results and the diff in the TUI.
- Offer these choices:
  - Save local draft.
  - Upload to R2.
  - Cancel and return to editing.

Local draft save:

- Write to `{cache_dir}/{hash_prefix}/lrc/lyrics.edited.lrc`.
- Do not update R2.
- Do not update the database.
- Notify the admin of the saved path.

Upload to R2:

- If an existing canonical LRC was loaded from R2, save a timestamped backup such as
  `{cache_dir}/{hash_prefix}/lrc/lyrics.backup.YYYYMMDD-HHMMSS.lrc` before upload.
- Use the same target as `sow-admin audio upload-lrc`: `{hash_prefix}/lyrics.lrc`.
- Upload through `R2Client.upload_lrc`.
- Update the recording through `db_client.update_recording_lrc(...)`.
- Mark the recording LRC status as completed using the existing DB update behavior.
- Notify the admin with song title, line count, duration, R2 URL, and backup path if a
  backup was created.

## Testing Plan

- Unit test LRC serialization:
  - centisecond formatting,
  - rounding carry behavior,
  - timestamp clamping,
  - inserted/deleted lines,
  - empty catalog-draft handling.
- Unit test command setup with mocked DB/R2/cache:
  - valid song and recording opens editor,
  - missing recording fails,
  - audio download failure fails before editor launch,
  - stale cached LRC is ignored when R2 has canonical LRC,
  - catalog lyrics fallback is used when no R2 LRC exists.
- Unit test upload safety:
  - diff base is the forced-refreshed canonical content,
  - canonical backup is written before upload,
  - upload uses `{hash_prefix}/lyrics.lrc`.
- Unit test quality checks:
  - non-monotonic timestamps block upload,
  - all-zero draft blocks upload,
  - duplicate timestamps warn,
  - duration outliers warn or require explicit override.
- Textual behavior tests for:
  - stamping selected line with current playback time,
  - stamping and advancing without seeking,
  - 5-second left/right seeks,
  - up/down line selection and seeking,
  - edit text,
  - edit timestamp,
  - insert before/after,
  - delete with confirmation,
  - dirty-exit warning,
  - diff/save prompt.
- Upload-path test with mocked `R2Client.upload_lrc` and DB update to confirm parity
  with `audio upload-lrc`.
- Run:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
```

## Assumptions

- This is an admin-only tool.
- Auto-publish behavior from `update_recording_lrc(...)` is intentionally unchanged.
- R2 `{hash_prefix}/lyrics.lrc` remains the canonical shared LRC path.
- The first version uses a line-editor UX, not inline spreadsheet-style cell editing.
- Local draft save is for experimentation and never updates shared state.
- Upload requires explicit confirmation after reviewing validation results and the
  unified diff.
- The uploaded revised LRC becomes the canonical shared file at
  `{hash_prefix}/lyrics.lrc`.
- The end-user lyrics preview screen should not be changed except for safe shared parser
  or serialization utility reuse.
