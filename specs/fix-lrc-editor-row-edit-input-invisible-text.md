# Fix: LRC Editor row-edit-input invisible text

## Problem

In the admin LRC editor (`ops/admin-cli/src/stream_of_worship/admin/editor/screen.py`), pressing `e` (Edit Text) or `t` (Edit Timestamp) opens the `#row-edit-input` overlay `Input` on top of the selected table cell. The text contents and cursor are visually invisible — the cell looks "blank", giving the appearance of text color matching the background.

## Root Cause

The `#row-edit-input` CSS override (screen.py:202-217) sets `height: 1` but does **not** disable Textual's default `Input` border (`border: tall …` from `_input.py:184-218`). A `tall` border consumes 1 row on top + 1 row on bottom, so with `height: 1` the content area collapses to `height: 1 − 2 = 0` rows.

Empirical confirmation via the in-process test harness (`app.run_test`):
- Before fix → `content_size: Size(width=2, height=0)` — no room for text/cursor.
- After toggling `Input.compact = True` → `content_size: Size(width=8, height=1)` — text and cursor render correctly.

The Input's own colors are correct and not the source of the issue:
- `color = Color(224, 224, 224)` (light gray, visible on dark)
- `background = Color(30, 30, 30)` (dark surface)

## Fix

Add `compact=True` to the `Input(...)` instantiation in `LRCEditorScreen.compose` (screen.py:324-328).

```python
# ops/admin-cli/src/stream_of_worship/admin/editor/screen.py
# in LRCEditorScreen.compose()
yield Input(
    id="row-edit-input",
    placeholder="Edit selected cell",
    select_on_focus=False,
    compact=True,
)
```

### Why this works

Textual's `Input` exposes a reactive `compact` property (`textual/widgets/_input.py:277`):

```python
compact = reactive(False, toggle_class="-textual-compact")
```

When `compact=True`, the widget gets the `-textual-compact` class, which applies the built-in CSS rule (`_input.py:191`):

```css
&.-textual-compact {
    border: none !important;
    height: 1;
    padding: 0;
    &.-invalid {
        background-tint: $error 20%;
    }
}
```

With `border: none` and `padding: 0`, the existing `height: 1` override leaves a 1-row content area where text and cursor can render.

### Why compact mode (chosen) over alternatives

| Option | Description | Result |
|---|---|---|
| `compact=True` (chosen) | Use Textual's built-in compact class. | Smallest idiomatic change; reuses the library's intended mechanism for height=1 inputs. |
| CSS `border: none; padding: 0;` in `#row-edit-input` block | Mirror the compact rule in our own CSS. | Equivalent outcome but duplicates the library's logic. |
| Increase Input height to 3 | Keep the tall border, give it room. | Visually different — input becomes a 3-row bordered box rather than a flat inline overlay matching the table cell. |

## Scope

**Single file edited:**
- `ops/admin-cli/src/stream_of_worship/admin/editor/screen.py` — one line added to the `Input(...)` constructor in `compose()`.

**No other files touched.** No other module references the `#row-edit-input` widget's border, height, or compact state. The Input's color/background come from the default theme and are already correct.

## Files Affected

- `ops/admin-cli/src/stream_of_worship/admin/editor/screen.py:324-328` — add `compact=True` to `Input(...)`.
- `ops/admin-cli/src/stream_of_worship/admin/editor/screen.py:202-217` — `#row-edit-input` CSS block remains unchanged (`height: 1`, `display: none` initially, `layer: overlay`); these still work correctly with compact mode.

## Verification

### Manual

1. Launch the LRC editor on any recording:
   ```bash
   uv run --project ops/admin-cli --extra admin sow-admin lrc edit <hash-prefix>
   ```
2. With cursor on any row, press `e` (Edit Text). The existing lyric text should now be visible in the overlay input, with the cursor blinking at column 0.
3. Type to edit; press `Enter` to commit (existing `on_input_submitted` handler at screen.py:1022).
4. Press `t` (Edit Timestamp) on any row; the formatted timestamp should be visible and editable.
5. Press `Esc` to cancel an in-progress edit; table focus returns (existing `action_quit_editor` handler at screen.py:1335).

### Automated

Run the existing editor screen test suite — these tests exercise edit text, edit timestamp, overlay positioning on refresh, and ESC-cancel behavior, and assert the value is captured into state. None of them assert border or content size, so they should remain green:

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/services/test_lrc_editor_screen.py -v
```

Relevant existing tests that should still pass:
- `test_edit_text_uses_overlay_and_updates_captured_row`
- `test_edit_text_overlay_starts_at_beginning_of_long_existing_lyric`
- `test_edit_text_defers_overlay_until_after_refresh`
- `test_resize_defers_overlay_reposition_until_after_refresh`
- `test_edit_timestamp_invalid_keeps_overlay_open_without_autosave`
- `test_escape_cancels_overlay_edit_and_restores_table_focus`
- `test_down_navigation_does_not_change_line_while_editing_text`

### Regression check (optional, for confidence)

In an `app.run_test()` harness, after pressing `e`, assert `Input.content_size.height >= 1` (before fix it was 0). This is a sanity check, not a required test — the manual verification above is sufficient.

## Risks

- **Low risk:** `compact` is a stable, documented Input property in Textual (present since at least 0.44; current project uses 8.2.7). It only affects border/padding, not value handling, focus, or message dispatch.
- **No behavioral change** to submit/cancel/autosave flow — only visual rendering of the edit overlay changes.
- **Existing tests already green** with the current layout; compact mode only expands the content area from 0 to 1, which doesn't affect any assertion they make.
