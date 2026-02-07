# Navigation Integration Tests

This test suite uses Textual's **SVG screenshot** feature to verify that navigation flows work correctly by inspecting what the user actually sees on screen.

## Why SVG Screenshots?

Testing TUI applications is challenging because:
1. Internal state (like `app.state.current_screen`) can be correct while the display is wrong
2. Screen caching bugs can cause visual freezes that don't trigger state errors
3. Focus and keyboard issues may not be detectable via internal state checks

**SVG screenshots solve this** by capturing the actual rendered output as XML, which we can parse to verify:
- What text is displayed
- What widgets are visible
- Whether the screen actually changed

## How It Works

### 1. Take Screenshots During Navigation
```python
screenshot = tmp_path / "editor.svg"
pilot.app.save_screenshot(screenshot, title="Editor Screen")
```

### 2. Parse SVG XML to Extract Text
```python
def get_svg_text_content(svg_path: Path) -> str:
    """Extract all text content from SVG screenshot."""
    tree = ET.parse(svg_path)
    # Find all <text> elements in SVG namespace
    # Return concatenated text content
```

### 3. Assert on Visual Content
```python
# Check that specific UI elements are present
assert_screen_shows(screenshot, "Songset Editor", "Add Songs")

# Detect visual freeze (screen didn't change)
if before_content == after_content:
    raise AssertionError("Visual freeze detected!")
```

## The Bug These Tests Catch

### Screen Caching Bug

**What happened:**
- First navigation: List → Editor ✓ (works)
- Go back: Editor → List ✓ (works)
- Second navigation: List → Editor ✗ (internal state changes, display frozen)

**Why it happened:**
- App cached screen instances in `self._screens` dict
- Pushing the same screen instance twice doesn't trigger re-render in Textual
- Logs showed correct state, but user saw frozen display

**How tests catch it:**

#### Test: `test_visual_freeze_detection`
```python
# Take screenshot before navigation
before_screenshot = tmp_path / "before_second_edit.svg"
pilot.app.save_screenshot(before_screenshot)
before_content = get_svg_text_content(before_screenshot)

# Navigate (THE BUG HAPPENS HERE)
await pilot.press("e")
await pilot.pause()

# Take screenshot after navigation
after_screenshot = tmp_path / "after_second_edit.svg"
pilot.app.save_screenshot(after_screenshot)
after_content = get_svg_text_content(after_screenshot)

# Screen content MUST be different
if before_content == after_content:
    raise AssertionError("Visual freeze detected!")
```

**This test FAILS with the bug because:**
- `before_content` contains "Your Songsets" (list screen)
- `after_content` ALSO contains "Your Songsets" (frozen, should be editor)
- Content is identical → visual freeze detected!

#### Test: `test_songset_list_to_editor_and_back`
```python
# Second editor visit
await pilot.press("e")
await pilot.pause()
screenshot = tmp_path / "04_second_editor.svg"
pilot.app.save_screenshot(screenshot, title="Second Editor Visit - CRITICAL")

# This would FAIL with the bug - SVG contains "Your Songsets" not "Songset Editor"
assert_screen_shows(screenshot, "Songset Editor", "Add Songs")
```

**Failure message with bug:**
```
AssertionError: SVG screenshot missing expected text: ['Songset Editor']
Screenshot saved at: /tmp/pytest-*/04_second_editor.svg
```

Opening the SVG shows the list screen instead of editor!

#### Test: `test_screen_instances_are_fresh`
```python
first_editor = app.screen
# ... navigation ...
second_editor = app.screen

# Direct instance check
assert first_editor is not second_editor  # FAILS with caching
```

**This test fails immediately** because cached screens return the same object reference.

## Running the Tests

### Run All Navigation Tests
```bash
pytest tests/app/test_navigation.py -v
```

### Run Specific Test
```bash
pytest tests/app/test_navigation.py::TestNavigationFlow::test_visual_freeze_detection -v
```

### Save Screenshots for Visual Inspection
```bash
pytest tests/app/test_navigation.py --tmp-path=/tmp/nav-test -v
```

Then open SVG files in browser:
```bash
open /tmp/nav-test/*/test_visual_freeze_detection/*.svg
```

## Test Coverage

### Critical Paths
- ✓ **Visual freeze detection** - catches screen caching bugs
- ✓ **Multiple navigation cycles** - detects caching after repeated navigation
- ✓ **Key binding persistence** - verifies 'e' key works after resume
- ✓ **Deep navigation stacks** - tests List → Editor → Browse → back path

### Edge Cases
- ✓ Pressing same key multiple times in sequence
- ✓ Different navigation patterns (n→escape vs e→escape)
- ✓ All key bindings on each screen
- ✓ Screen instance uniqueness

## Benefits Over Internal State Testing

| Approach | Detects Visual Freeze? | Detects Focus Issues? | Debuggable? |
|----------|------------------------|----------------------|-------------|
| Internal state checks | ❌ No | ❌ No | ❌ No visual artifact |
| SVG screenshots | ✅ Yes | ✅ Yes | ✅ Visual proof saved |

## Example: Debugging with Screenshots

When a test fails, you get:
1. **Error message** with exact screenshot path
2. **Visual artifact** (SVG file) showing what user saw
3. **Text content** printed in assertion output

```
AssertionError: Screen caching bug detected!
Second navigation shows list instead of editor.
Screenshot: /tmp/pytest-abc123/04_second_editor.svg

=== SVG Content ===
Stream of Worship
Your Songsets
Press 'n' for new, 'e' or Enter to edit
New Songset | | 0 | Never
==================
```

You can immediately see that:
- Screen shows "Your Songsets" (wrong!)
- Should show "Songset Editor"
- User sees list, not editor

## Future Enhancements

### Screenshot Comparison
```python
def assert_screens_different(before: Path, after: Path):
    """Assert two screenshots are visually different."""
    # Could use image diffing for more sophisticated checks
```

### Visual Regression Testing
```python
def compare_with_baseline(screenshot: Path, baseline: Path):
    """Compare screenshot with known good baseline."""
    # Detect unintended UI changes
```

### Performance Testing
```python
@pytest.mark.benchmark
def test_navigation_performance():
    """Measure navigation speed."""
    # Ensure transitions are fast
```

## Maintenance

When adding new screens or navigation paths:
1. Add test to appropriate test class
2. Use `tmp_path / "descriptive_name.svg"` for screenshots
3. Add `title=` parameter to describe the step
4. Assert on unique text that identifies the screen
5. Check that screen changed from previous state

## References

- [Textual Testing Guide](https://textual.textualize.io/guide/testing/)
- [Textual Pilot API](https://textual.textualize.io/api/pilot/)
- [SVG Specification](https://www.w3.org/Graphics/SVG/)
