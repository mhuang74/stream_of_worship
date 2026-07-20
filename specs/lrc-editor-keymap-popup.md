# LRC Editor Keymap Popup

## Summary

Add a new `?` action key to the LRC editor that opens a modal popup listing
**every** action key mapping, grouped the same way the footer groups them
(Playback, Lyrics, Timecode, General). The existing single-line footer is left
unchanged — this is purely an additive discoverability feature so users can see
bindings that the footer truncates (e.g. `I = Insert Canonical`).

## Motivation

The `GroupedFooter` (`ops/admin-cli/src/stream_of_worship/admin/editor/footer.py`)
extends `Horizontal` and lays out its 4 binding groups side-by-side on a single
row. Each `_BindingGroup` child is locked to `height: 1` with `overflow: hidden`
and its Rich `Text` content has `no_wrap = True` and `overflow="ellipsis"`. The
"Lyrics" group alone has 8 bindings, so on any normal terminal width the row is
ellipsized and keys like `I=Insert Canonical` are invisibly truncated. The
footer's `height: 3` mostly sits empty as a result.

Rather than reflow the footer (which would either consume more vertical space
or risk wrapping unpredictably across terminal widths), we add a dedicated
"show keymap" action that pops up a full modal listing every binding. This
matches the user's stated preference: keep the current footer as-is, add a new
action key that opens a popup where all action key mappings are shown.

## Design

### Action key

- New binding: `Binding("?", "show_keymap", "Keymap")`
- Added to `LRCEditorScreen.BINDINGS` after the existing `q` quit binding.
- `"show_keymap"` appended to `BINDING_GROUPS["General"]` so the new entry
  appears on the footer's General group (one short addition, well within the
  existing single-line width).
- Glyph for `?` is not normalized (it's a literal printable character), so
  `format_key_display("?")` returns `"?"` unchanged.

### Modal dialog

A new nested `KeymapDialog(ModalScreen[None])` class, defined inside
`action_show_keymap` (mirroring the existing nested-`ModalScreen` pattern used
by `action_quit_editor` at `screen.py:1336` and `_show_save_upload_prompt` at
`screen.py:1192`).

- Constructor takes `groups: dict[str, list[str]]` and `bindings: list[Binding]`
  (decoupled from the parent screen class for testability).
- `compose()` yields a `Vertical(id="keymap-container")` with:
  - `Label("Keymap", classes="dialog-title")`
  - For each group: a heading `Label(f"[bold]{group_label}[/bold]")`, then one
    `Label` per binding printing
    `f"[dim]{format_key_display(b.key)}[/dim]={b.description}"`, then a blank
    `Label("")` separator between groups.
  - Closing hint `Label("[d]Press [bold]?[/bold] or [bold]Esc[/bold] to close[/]")`.
- `BINDINGS = [Binding("escape", "close", "Close"), Binding("?", "close", "Close")]`
- `action_close(self) -> None: self.dismiss(None)`

The dialog reuses the existing `dialog-container` / `dialog-title` convention
already used by `SaveUploadDialog` (`screen.py:1221-1222`). No custom CSS is
required — Textual's default `ModalScreen` styling centers/dims the Vertical.

### Glyph helper extraction

The glyph normalization logic currently inlined in
`_BindingGroup._format_content` (`footer.py:23-41`, the long `if/elif` chain
that turns `ctrl+c` → `^C`, `shift+left` → `⇧←`, `space` → `⎵`, etc.) is
extracted into a module-level function:

```python
def format_key_display(key: str) -> str:
    if key.startswith("ctrl+"):
        return "^" + key[5:].upper()
    if key == "shift+left":
        return "⇧←"
    if key == "shift+right":
        return "⇧→"
    if key == "left":
        return "←"
    if key == "right":
        return "→"
    if key == "up":
        return "↑"
    if key == "down":
        return "↓"
    if key == "space":
        return "⎵"
    if key == "escape":
        return "Esc"
    return key
```

`_BindingGroup._format_content` calls the helper, producing identical output
(existing glyphs preserved). The keymap dialog imports and reuses the same
helper so glyphs are consistent between footer and popup — no duplication.

### Edge case: active row edit

When the row-edit overlay `Input` is focused, Textual dispatches keystrokes to
the focused widget first, so `?` types a literal `?` into the cell rather than
opening the dialog. This is the desired behavior (matches how `e` edit-text
already consumes its key in overlay mode via `_is_edit_active()` short-circuits).
For consistency with `action_quit_editor`, `action_show_keymap` begins with:

```python
if self._is_edit_active():
    return  # let `?` go into the input
```

## Changes

### 1. `ops/admin-cli/src/stream_of_worship/admin/editor/footer.py`

- Extract inline glyph normalization (lines 23-41) into module-level
  `format_key_display(key: str) -> str`.
- `_BindingGroup._format_content` calls `format_key_display(b.key)` — behavior
  unchanged.
- `format_key_display` is exported (module-level function, importable).

### 2. `ops/admin-cli/src/stream_of_worship/admin/editor/screen.py`

- Import `format_key_display` from `footer.py`.
- Add `Binding("?", "show_keymap", "Keymap")` to `BINDINGS` (after the `q` quit
  binding, line 246).
- Append `"show_keymap"` to `BINDING_GROUPS["General"]` (line 272-279).
- Implement `action_show_keymap(self) -> None:` following the existing
  nested-`ModalScreen` pattern:
  - Short-circuit `if self._is_edit_active(): return`.
  - Define nested `KeymapDialog(ModalScreen[None])` class (see Design above).
  - Push via `self.app.push_screen(KeymapDialog(self.BINDING_GROUPS, self.BINDINGS))`.
    No result callback needed.

### 3. Test: `ops/admin-cli/tests/admin/services/test_lrc_editor_screen.py`

Add `test_show_keymap_dialog_lists_all_bindings_grouped()` that:

- Builds the app via the existing test fixtures (same pattern as
  `test_small_terminal_layout_keeps_footer_out_of_lyrics_viewport`, line 135).
- Calls `app.screen.action_show_keymap()`.
- Queries the now-active `ModalScreen` for the keymap container.
- Asserts that every group label ("Playback", "Lyrics", "Timecode", "General")
  and every binding description (e.g. "Insert Canonical" for the `I` key) is
  present as a label.
- Dismisses (calls `action_close()`).

## Files Modified

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/editor/footer.py` | Extract `format_key_display` helper; `_BindingGroup` calls it (no behavior change) |
| `ops/admin-cli/src/stream_of_worship/admin/editor/screen.py` | Add `?` binding, `show_keymap` group entry, `action_show_keymap` + nested `KeymapDialog` |
| `ops/admin-cli/tests/admin/services/test_lrc_editor_screen.py` | Add `test_show_keymap_dialog_lists_all_bindings_grouped` |

## Not Changed

- Existing footer layout, CSS, or glyph styling.
- Existing `BINDINGS` list (other than appending the new `?` entry).
- Existing `BINDING_GROUPS` (other than appending `"show_keymap"` to General).
- Non-editor screens/components.

## Verification

```bash
# Lint + format (Black/Ruff with line length 100, py311)
uv run --project ops/admin-cli --python 3.11 --extra admin ruff check ops/admin-cli/src/stream_of_worship/admin/editor/
uv run --project ops/admin-cli --python 3.11 --extra admin black --check ops/admin-cli/src/stream_of_worship/admin/editor/

# Editor tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v ops/admin-cli/tests/admin/services/test_lrc_editor_screen.py

# Manual smoke test (interactive — verify popup opens with `?`, dismisses with `?`/`Esc`,
# all 4 groups + every binding including "Insert Canonical" visible)
uv run --project ops/admin-cli --extra admin sow-admin <edit-lrc subcommand>
```
