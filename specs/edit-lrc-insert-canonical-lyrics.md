# Edit-LRC: Insert Canonical Lyrics from Songs Table

## Summary

Add a `I` (capital I) keybinding to the `edit-lrc` TUI that inserts all canonical
lyrics lines from the Songs table (scraped lyrics) after the current cursor position.
This allows an admin to bulk-populate an LRC draft with the canonical lyrics text,
then stamp timestamps line-by-line during playback.

## Motivation

When no R2 LRC exists for a recording, the editor initializes from catalog lyrics
(`song.lyrics_lines` / `song.lyrics_raw`). However, if the editor was opened with
an existing R2 LRC that has incorrect or incomplete lyrics text, there is currently
no way to pull in the canonical lyrics from the Songs table without manually
re-typing each line. The `I` binding solves this by bulk-inserting all canonical
lyrics lines after the cursor, so the admin can then delete old lines and stamp
timestamps on the new ones.

## Key Binding

| Key | Action | Description |
|-----|--------|-------------|
| `I` | `insert_canonical` | Insert ALL canonical lyrics lines from Songs table after current cursor |

Lowercase `i` remains unchanged (insert single blank line after cursor).

## Behavior

1. Look up the recording via `db_client.get_recording_by_hash(hash_prefix)` to get `song_id`.
2. If no recording or recording has no `song_id`, show notification: `"No song linked"`.
3. Look up the song via `db_client.get_song(song_id)`.
4. If song not found or song has no lyrics, show notification: `"No canonical lyrics found"`.
5. Get `song.lyrics_list` (prefers `lyrics_lines` JSON, falls back to `lyrics_raw` split by newline).
6. Skip blank/empty lines (consistent with `build_draft_from_catalog`).
7. If no non-blank lines remain, show notification: `"No canonical lyrics found"`.
8. Bulk-insert all non-blank lyrics lines after the current cursor position, each as
   an `LRCLine` with `time_seconds=0.0` (draft timestamp).
9. Move cursor to the **first** inserted line.
10. Refresh table, update displays, autosave.
11. Show notification: `"Inserted N canonical lyrics lines"`.

## Implementation Plan

### File 1: `src/stream_of_worship/admin/editor/state.py`

**Change A: Add `lines` field to `UndoEntry`**

Add an optional `lines` field to support bulk-insert undo:

```python
@dataclass
class UndoEntry:
    action: str
    index: int
    old_text: str = ""
    new_text: str = ""
    old_time: float = 0.0
    new_time: float = 0.0
    line: Optional[LRCLine] = None
    lines: Optional[List[LRCLine]] = None  # NEW: for bulk insert undo
```

**Change B: Add `insert_lines_after()` method to `EditorState`**

New method that bulk-inserts multiple lines after the given index. Pushes a single
undo entry with `action="insert_lines"` so one `Ctrl+Z` undoes the entire batch.

```python
def insert_lines_after(self, index: int, texts: List[str]) -> None:
    """Insert multiple lines after the given index with draft timestamps."""
    new_lines = [
        LRCLine(time_seconds=0.0, text=text, raw_timestamp="[00:00.00]")
        for text in texts
    ]
    insert_at = index + 1
    self._push_undo(UndoEntry(action="insert_lines", index=insert_at, lines=new_lines))
    for i, line in enumerate(new_lines):
        self.timed_lines.insert(insert_at + i, line)
    self.dirty = True
```

**Change C: Update `undo()` to handle `"insert_lines"` action**

After the existing `elif entry.action == "insert":` block, add:

```python
elif entry.action == "insert_lines":
    if entry.lines is not None:
        for _ in range(len(entry.lines)):
            if 0 <= entry.index < len(self.timed_lines):
                self.timed_lines.pop(entry.index)
```

**Change D: Update `redo()` to handle `"insert_lines"` action**

After the existing `elif entry.action == "insert":` block, add:

```python
elif entry.action == "insert_lines":
    if entry.lines is not None:
        for i, line in enumerate(entry.lines):
            self.timed_lines.insert(entry.index + i, line)
```

### File 2: `src/stream_of_worship/admin/editor/screen.py`

**Change A: Add `I` binding to `BINDINGS` list**

Add after the existing `Binding("i", "insert_after", "Insert After")`:

```python
Binding("I", "insert_canonical", "Insert Lyrics"),
```

**Change B: Add `action_insert_canonical()` method**

New action handler on `LRCEditorScreen`:

```python
def action_insert_canonical(self) -> None:
    recording = self.db_client.get_recording_by_hash(self.hash_prefix)
    if not recording or not recording.song_id:
        self.notify("No song linked", severity="warning", timeout=3)
        return

    song = self.db_client.get_song(recording.song_id)
    if not song:
        self.notify("No canonical lyrics found", severity="warning", timeout=3)
        return

    lyrics = song.lyrics_list
    non_blank = [line for line in lyrics if line.strip()]
    if not non_blank:
        self.notify("No canonical lyrics found", severity="warning", timeout=3)
        return

    self.state.insert_lines_after(self.state.selected_index, non_blank)
    self.state.select_line(self.state.selected_index + 1)
    self._refresh_table()
    self._update_displays()
    self._do_autosave()
    self.notify(f"Inserted {len(non_blank)} canonical lyrics lines", timeout=3)
```

### No changes needed to:

- `app.py` — No new parameters needed; `db_client` and `hash_prefix` are already passed through.
- `commands/audio.py` — No changes to the CLI entry point.
- `db/client.py` — `get_recording_by_hash()` and `get_song()` already exist.
- `db/models.py` — `Song.lyrics_list` already exists and skips blank lines.
- `lrc_parser.py` — No changes needed.

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Recording has no `song_id` | Notification: "No song linked" |
| Song not found in DB | Notification: "No canonical lyrics found" |
| Song has empty `lyrics_lines` and `lyrics_raw` | Notification: "No canonical lyrics found" |
| All lyrics lines are blank after stripping | Notification: "No canonical lyrics found" |
| Editor has 0 timed lines (shouldn't happen, but) | `insert_lines_after(-1, ...)` inserts at index 0 |
| User presses `Ctrl+Z` after bulk insert | Single undo removes all inserted lines at once |
| User presses `Ctrl+Y` after undo | Redo re-inserts all lines at once |

## Testing

Manual testing via:

```bash
uv run --extra admin sow-admin audio edit-lrc SONG_ID
```

1. Open editor for a song with canonical lyrics in the Songs table.
2. Press `I` — verify all canonical lyrics lines appear after the cursor.
3. Verify cursor moves to the first inserted line.
4. Verify notification shows correct count.
5. Press `Ctrl+Z` — verify all inserted lines are removed at once.
6. Press `Ctrl+Y` — verify all lines are re-inserted.
7. Test with a recording that has no `song_id` — verify "No song linked" notification.
8. Test with a song that has no lyrics — verify "No canonical lyrics found" notification.
