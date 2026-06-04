# Clustered Key Bindings for LRC Editor

## Summary

Reorganize the LRC editor TUI's flat key binding list into 4 labeled clusters
displayed in a custom `GroupedFooter` widget, making it easier to find related
actions. Also consolidate stamping actions and rename timecode shift labels.

## Motivation

The original 24 bindings were displayed as a flat list in Textual's default
Footer, making it hard to discover related actions. Grouping them by function
(Playback, Lyrics Edit, Timecode, General) improves discoverability and
reduces cognitive load.

## Changes

### 1. Stamp consolidation

- **Removed** `stamp_line` action (was bound to `Enter`)
- **Remapped** `Enter` → `stamp_and_advance`
- Rationale: Stamp+Advance is the primary workflow; standalone stamp without
  advance is redundant since you can always navigate back with `↑`

### 2. Timecode shift label rename

- `shift+left` / `show_earlier`: label changed from `"Pad Earlier"` → `"Earlier"`
- `shift+right` / `show_later`: label changed from `"Pad Later"` → `"Later"`
- "Earlier" = makes timecode smaller (shifts display earlier)
- "Later" = makes timecode larger (shifts display later)
- No logic changes — `adjust_padding(-1)` / `adjust_padding(+1)` unchanged

### 3. Binding cluster layout

| Cluster | Key | Action | Display Label |
|---|---|---|---|
| **Playback** | `space` | `toggle_playback` | Play/Pause |
| | `←` | `seek_backward` | Seek -5s |
| | `→` | `seek_forward` | Seek +5s |
| | `↑` | `select_prev` | Prev Line |
| | `↓` | `select_next` | Next Line |
| | `j` | `jump_to_line` | Jump |
| **Lyrics** | `ctrl+c` | `copy_line` | Copy |
| | `ctrl+v` | `paste_after` | Paste |
| | `i` | `insert_after` | Insert Blank |
| | `I` | `insert_canonical` | Insert Canonical |
| | `d` | `delete_line` | Delete |
| | `e` | `edit_text` | Edit Text |
| **Timecode** | `enter` | `stamp_and_advance` | Stamp+Advance |
| | `shift+←` | `show_earlier` | Earlier |
| | `shift+→` | `show_later` | Later |
| | `t` | `edit_timestamp` | Edit Time |
| **General** | `p` | `preview_single` | Preview Line |
| | `P` | `preview_continuous` | Preview All |
| | `s` | `save_upload` | Save/Upload |
| | `ctrl+z` | `undo` | Undo |
| | `ctrl+y` | `redo` | Redo |
| | `esc`/`q` | `quit_editor` | Quit |

23 bindings total (down from 24).

### 4. GroupedFooter widget

New file: `src/stream_of_worship/admin/editor/footer.py`

- `GroupedFooter(Horizontal)` replaces Textual's default `Footer`
- Reads `BINDING_GROUPS` from the active screen to determine cluster membership
- Each cluster rendered as a `_BindingGroup(Static)` with bold header + key=label pairs
- Key display normalization: `ctrl+c` → `^C`, `shift+left` → `⇧←`, `space` → `⎵`, etc.
- Falls back to default `Footer` if `BINDING_GROUPS` not defined on screen

### 5. Screen changes

- `BINDINGS` list reordered by cluster
- `BINDING_GROUPS` class variable added (maps group label → list of action names)
- `action_stamp_line()` method deleted
- `Footer` import removed; `GroupedFooter` imported and used in `compose()`

## Files Modified

| File | Change |
|---|---|
| `src/stream_of_worship/admin/editor/footer.py` | **New** — GroupedFooter + _BindingGroup widgets |
| `src/stream_of_worship/admin/editor/screen.py` | Reorder BINDINGS, add BINDING_GROUPS, remap Enter, delete stamp_line, swap Footer |
| `src/stream_of_worship/admin/editor/app.py` | Possible CSS tweak for footer height |

## Not Changed

- All action handler logic (except `action_stamp_line` deletion)
- Key assignments (only `enter` remapped from `stamp_line` → `stamp_and_advance`)
- Modal dialogs (`SaveUploadDialog`, `QuitConfirmDialog`) still use default Footer
