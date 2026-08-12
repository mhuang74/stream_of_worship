# Component Metadata Editor — User's Guide

The **Component Metadata Editor** is an interactive terminal app in the Admin CLI (`sow-admin`) for **viewing, reviewing, and correcting** the analysis metadata of songs — specifically the *entry* and *exit* Chorus components produced by the Component Analysis job.

Before the songset constructor generates transitions between songs, it consumes this metadata (tempo, key, energy, groove, theme, vocal posture, and more). The editor lets you spot-check the AI-derived values, correct any that are wrong, and listen to the audio to verify — all in one screen.

## Starting the Editor

```
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_1> [<song_id_2> ...] [--config PATH]
```

- Pass **one or more song IDs**. Each song must already have component analysis run on it; otherwise it is skipped with a warning.
- The `--extra admin` flag provides the Textual TUI dependencies.
- The song's audio is downloaded and cached automatically for playback on first use.
- If none of the given songs have component analysis, the editor exits with an error.

## Major Use Cases

### 1. Review and correct AI-derived metadata
The core purpose. The editor displays each song's entry/exit Chorus metadata in a table plus an always-visible summary panel. You inspect the values and override any that the analysis got wrong. Only **four fields** are user-editable:

| Field | Type | Allowed values |
|-------|------|----------------|
| Theme (`*Theme`) | Enum | 讚美, 感恩, 敬拜, 奉獻, 認罪, 差遣, 信心, 祈禱, 復興, 聖靈, 十字架, 跟隨 |
| Vocal posture (`*Posture`) | Enum | To God, About God, To Congregation |
| Energy level (`*Energy`) | Float (dB) | −60.0 … 0.0 |
| Groove density (`*Groove`) | Float | 0.0 … 2.0 |

Everything else in the table (BPM, key, timings, confidences, reasoning, timestamps) is read-only.

### 2. Audibly verify a component
Because the metadata is only useful if it's *right at the right moment*, the editor downloads the song's audio and provides playback controls. You can play/pause, seek, and **jump straight to the highlighted component's start** so you can hear the exact Chorus segment you're reviewing.

### 3. Audit the analysis with confidence + reasoning
Each editable field carries a confidence score and, for Theme and Posture, the LLM's textual reasoning. The full reasoning text is shown in the summary panel so you can judge whether to trust or override each value. In the table, reasoning cells are dimmed and truncated — the full text is always in the panel.

### 4. Review multiple songs in one session
Pass several song IDs at launch. Use `n` / `p` to move between songs. The breadcrumb and status bar show which song you're on (e.g. `Song 2 / 5`). Each song tracks its own unsaved edits, history, and save state.

### 5. Safe, crash-resistant editing
Your edits are continuously protected (autosaved to disk) so a crash, disconnect, or accidental exit doesn't lose your work. When you relaunch the same song, any unsaved edits are recovered automatically and you're told what was restored. Press `s` to save and clear the pending state.

### 6. Keep your edit timeline consistent
Pressing `s` persists all four editable fields for both entry and exit components at once, so the values that downstream transition generation reads stay consistent across the two rows.

## The Screen Layout

From top to bottom, the editor shows:

1. **Header** — App title bar.
2. **Song breadcrumb** — `Song <index> / <total> — [<song_id>] <title> — hash_prefix=<...>`. Where you are in the batch.
3. **Playback bar** — Transport icon, current position, total duration, and a progress bar.
4. **Hero panel** *(summary)* — Always-visible digest of the highlighted component:
   - Role line: `▶ ENTRY CHORUS — Occurrence <n> — [start → end]`
   - Primary metrics: BPM, Key, Energy (dB), Groove, Backbeat
   - Editable line: `Theme: <value>  Vocal posture: <value>`
   - Full Theme and Posture reasoning text (no truncation)
5. **Component metadata table** — One **row per role** (entry = row 0, exit = row 1), many columns. The cursor is a **cell** cursor (row = role, column = field). Columns are grouped:
   - **Transition cluster** (left): Role, Type, Occ, Start, End, BPM, Key, then the four editable fields (`*Theme, *Posture, *Energy, *Groove`), then Backbeat.
   - **Audit cluster**: per-field confidence scores and the truncated reasoning cells.
   - **Meta cluster**: Created / Updated timestamps.
6. **Status indicator** — `Dirty` marker (red `*` when there are unsaved edits, green `✓` when clean), autosave status, current song index, and any save warnings.
7. **Footer** — Color-coded keyboard shortcuts (grouped by Playback / Songs / Edit / General).

## Navigation

The table cursor moves independently by **row** (entry / exit) and **column** (field).

### Selecting rows and columns

| Action | Key |
|--------|-----|
| Move cursor up / down a row (entry ↔ exit) | `↑` / `↓` |
| Page up / down within the table | `PgUp` / `PgDn` |
| Move column to the right | `Tab` |
| Move column to the left | `Shift+Tab` |

> When a numeric edit box is open, row/column navigation is blocked until you finish that edit.

### Switching songs

| Action | Key |
|--------|-----|
| Go to next song | `n` |
| Go to previous song | `p` |

Song switching wraps around the batch. If the current song has unsaved edits, it is autosaved before you switch.

### Playback

| Action | Key |
|--------|-----|
| Play / Pause (anchored to component) | `Space` |
| Seek backward 5s | `←` |
| Seek forward 5s | `→` |
| Jump to highlighted component start | `j` |

When you press `Space` to play, the editor seeks to the highlighted component's `start_time` first — unless you are already inside that component's time range — so you always hear the Chorus you're reviewing.

### Editing fields

The four editable columns use two different editing styles:

| Field | Editing style | How |
|-------|---------------|-----|
| `*Theme` | Enum cycle | Position cursor on the column, press `]` to advance or `[` to go back through the 12 values |
| `*Posture` | Enum cycle | Same as Theme (3 values) |
| `*Energy` | Numeric input | Position cursor on the column, press `e`, type a value in **dB**, press `Enter` |
| `*Groove` | Numeric input | Position cursor on the column, press `e`, type a value 0.0–2.0, press `Enter` |

- Enum cycling wraps around at the ends.
- For numeric fields, invalid or out-of-range values are rejected with a warning and you can retype.
- `Esc` cancels an in-progress numeric edit without applying it.

### Undo / Redo

| Action | Key |
|--------|-----|
| Undo last field edit | `Ctrl+Z` |
| Redo last undone edit | `Ctrl+Y` |

Undo/redo operates at the field level and is tracked **per song**. History is cleared once you save that song.

### Saving

| Action | Key |
|--------|-----|
| Save current song's edits | `s` |

- Save is only meaningful when the song has unsaved edits (the status bar shows a red `*`). If there's nothing to save, `s` does nothing.
- After a successful save, the status returns to a clean `✓`, the autosave is cleared, and the table reloads the newly persisted values.
- If saving partially fails, the status bar shows a **yellow warning** (e.g. "R2 pending — press s to retry"). Your edits are kept safe and **not lost**; press `s` again to retry the save.

### Dialogs

| Action | Key |
|--------|-----|
| Show the full on-screen keymap | `?` (close with `?` or `Esc`) |
| Quit the editor | `q` or `Esc` |
| — Confirm quit | `y` (yes) / `n` (no) / `Esc` (no) |

- The **Keymap dialog** groups every shortcut by Playback / Songs / Edit / General.
- The **Quit dialog** warns if there are unsaved changes. Even so, your edits are autosaved, so quitting is safe — relaunch the same song to recover them.
- If you press `q`/`Esc` while a numeric edit is open, it simply cancels the edit first rather than quitting.

## Keybinding Quick Reference

| Category | Key | Action |
|----------|-----|--------|
| Playback | `Space` | Play / Pause (anchored to component) |
| Playback | `←` | Seek −5s |
| Playback | `→` | Seek +5s |
| Playback | `j` | Jump to component start |
| Songs | `n` | Next song |
| Songs | `p` | Previous song |
| Navigation | `Tab` | Move column right |
| Navigation | `Shift+Tab` | Move column left |
| Navigation | `↑` / `↓` / `PgUp` / `PgDn` | Move table cursor |
| Edit | `[` | Cycle enum value backwards |
| Edit | `]` | Cycle enum value forwards |
| Edit | `e` | Edit numeric field (Energy / Groove) |
| Edit | `Ctrl+Z` | Undo |
| Edit | `Ctrl+Y` | Redo |
| General | `s` | Save |
| General | `?` | Show keymap |
| General | `q` / `Esc` | Quit |

## Tips & Worked Example

**Typical review flow:**

1. Launch with the song(s) to review:
   ```
   uv run --project ops/admin-cli --extra admin sow-admin audio review-components SONG_ID_A SONG_ID_B
   ```
2. Check the breadcrumb to confirm which song you're on; the Hero panel shows the current component's digest.
3. Move the cursor (`↑`/`↓`, `Tab`/`Shift+Tab`) to a row and column.
4. Press `Space` to hear the exact Chorus segment; use `j`, `←`, `→` to navigate within the audio.
5. To correct a value:
   - **Theme / Posture**: select the column, press `]` / `[` to cycle to the right value.
   - **Energy / Groove**: select the column, press `e`, type the value, press `Enter`.
6. Use `Ctrl+Z` / `Ctrl+Y` to fix mistakes while editing.
7. Move to the next song with `n` (or back with `p`).
8. Press `s` to save each song's edits. Watch the status bar turn clean (`✓`).
9. Press `q` to quit (confirm with `y`). If you left something unsaved, it is autosaved and will be recovered next launch.

**Best practices:**

- Always save (`s`) before switching away from a song you've finished with, so downstream tools read the corrected values.
- Use the Hero panel's full reasoning text to confirm whether an override is warranted — don't trust a low-confidence value blindly, and don't override a high-confidence one without listening (`Space`) first.
- If you see the yellow *R2 pending* warning after saving, don't quit yet — press `s` once more to complete the save. Your edits are safe in the meantime.
