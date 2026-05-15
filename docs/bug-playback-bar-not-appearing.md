# Bug: Playback Bar Not Appearing in Songset Editor

**Date:** 2026-05-15
**Status:** Analysis Complete, Fix Pending

## Problem Description

In the Songset Editor screen, pressing `space` to play a song starts audio playback correctly, but the playback progress bar does not appear. The song plays, but there's no visual indication of playback progress.

## Affected Files

- `src/stream_of_worship/app/screens/songset_editor.py` - Songset Editor screen
- `src/stream_of_worship/app/widgets/playback_bar.py` - PlaybackBar widget
- `src/stream_of_worship/app/services/playback.py` - PlaybackService

## Root Cause Analysis

### Issue 1: PlaybackBar Initial State Ambiguity

**Location:** `playback_bar.py:36`

The `PlaybackBar` widget is initialized without the `hidden` CSS class:

```python
super().__init__("", id=id, classes=classes)  # classes defaults to None
```

This means the bar starts **visible but empty** (content is `""`). The visibility is managed entirely through the `update_visibility()` method, which only runs from callbacks. Since the initial playback state is `STOPPED`, the bar should logically start hidden, but it doesn't.

### Issue 2: Redundant STOPPED Callbacks in PlaybackService.stop()

**Location:** `playback.py:470-479`

The `stop()` method directly sets the state and fires the callback without using the state-change guard:

```python
def stop(self, clear_source: bool = True) -> None:
    # ...
    with self._lock:
        self._state = PlaybackState.STOPPED  # Direct assignment
        # ...
    
    if self._on_state_changed:
        self._on_state_changed(PlaybackState.STOPPED)  # Always fires!
```

Compare with `_set_state()` which has a guard:

```python
def _set_state(self, new_state: PlaybackState) -> None:
    with self._lock:
        old_state = self._state
        self._state = new_state
    if old_state != new_state and self._on_state_changed:  # Guard!
        self._on_state_changed(new_state)
```

### Issue 3: Double-stop() During play()

**Location:** `playback.py:291-324`

When `play()` is called, it triggers multiple `stop()` calls:

1. `play()` calls `self.stop(clear_source=False)` at line 315
2. `play()` calls `self.load()` at line 322
3. `load()` calls `self.stop(clear_source=True)` at line 194

This results in **two STOPPED callbacks** being fired before the PLAYING callback.

### Callback Sequence During play()

When `play()` is called via `call_from_thread`:

1. `stop(clear_source=False)` → `_on_state_changed(STOPPED)` → `call_after_refresh(add_class("hidden"))`
2. `load()` → `stop(clear_source=True)` → `_on_state_changed(STOPPED)` → `call_after_refresh(add_class("hidden"))` (redundant!)
3. `_set_state(PLAYING)` → `_on_state_changed(PLAYING)` → `call_after_refresh(remove_class("hidden") + update_display)`

All three `call_after_refresh` calls are scheduled before any refresh occurs. The final state *should* be visible, but the redundant STOPPED callbacks may cause issues with Textual's refresh batching.

## Comparison with Working Screen (Lyrics Preview)

The Lyrics Preview screen works correctly. Key differences:

| Aspect | Lyrics Preview | Songset Editor |
|--------|---------------|----------------|
| `play()` call location | Main thread via `call_after_refresh` | Worker thread via `call_from_thread` |
| Initial PlaybackBar state | Hidden (via `update_visibility` on mount) | Visible but empty |
| Callback timing | Synchronous within same event loop | Asynchronous across thread boundary |

In Lyrics Preview, `play()` is called directly on the main thread. In Songset Editor, `play()` is dispatched via `call_from_thread`, which may affect how `call_after_refresh` callbacks are processed.

## Proposed Fixes

### Fix 1: Initialize PlaybackBar with hidden class

**File:** `playback_bar.py:36`

```python
# Before
super().__init__("", id=id, classes=classes)

# After
super().__init__("", id=id, classes=classes or "hidden")
```

This ensures the bar starts hidden (matching STOPPED state) and only becomes visible when `update_visibility()` is called with a non-STOPPED state.

### Fix 2: Use _set_state() in stop()

**File:** `playback.py:470-479`

```python
# Before
with self._lock:
    self._state = PlaybackState.STOPPED
    self._position_seconds = 0.0
    self._start_time = None
    self._paused_at = None
    if clear_source:
        self._source = None

if self._on_state_changed:
    self._on_state_changed(PlaybackState.STOPPED)

# After
with self._lock:
    self._position_seconds = 0.0
    self._start_time = None
    self._paused_at = None
    if clear_source:
        self._source = None

self._set_state(PlaybackState.STOPPED)
```

This uses the existing guard in `_set_state()` to skip redundant STOPPED→STOPPED callbacks.

### Fix 3: Skip redundant stop() in load()

**File:** `playback.py:194`

```python
# Before
self.stop(clear_source=True)

# After
if self._state != PlaybackState.STOPPED:
    self.stop(clear_source=True)
```

This avoids the double-stop when `play()` has already called `stop()`.

## Expected Behavior After Fix

1. PlaybackBar starts hidden (correct initial state for STOPPED)
2. When `play()` is called, only one STOPPED callback fires (if transitioning from PLAYING/PAUSED)
3. PLAYING callback fires and removes `hidden` class + updates display
4. PlaybackBar becomes visible with progress indication

## Implementation Summary

**Date Implemented:** 2026-05-15

### Changes Made

| File | Line | Change |
|------|------|--------|
| `playback_bar.py` | 36 | Initialize with `classes="hidden"` by default |
| `playback.py` | 194-195 | Guard `stop()` call in `load()` to skip if already STOPPED |
| `playback.py` | 478 | Use `_set_state()` instead of direct state assignment |

### Detailed Changes

#### 1. `playback_bar.py:36`

```python
# Before
super().__init__("", id=id, classes=classes)

# After
super().__init__("", id=id, classes=classes or "hidden")
```

**Rationale:** The PlaybackBar now starts with the `hidden` CSS class, matching the initial STOPPED state of the playback service. This ensures the bar is hidden on first render and only becomes visible when `update_visibility()` is called with a non-STOPPED state.

#### 2. `playback.py:194-195`

```python
# Before
self.stop(clear_source=True)

# After
if self._state != PlaybackState.STOPPED:
    self.stop(clear_source=True)
```

**Rationale:** The `load()` method is called from `play()`, which already calls `stop()` first. This guard prevents the redundant `stop()` call that was causing duplicate STOPPED callbacks.

#### 3. `playback.py:478`

```python
# Before
with self._lock:
    self._state = PlaybackState.STOPPED
    self._position_seconds = 0.0
    self._start_time = None
    self._paused_at = None
    if clear_source:
        self._source = None

if self._on_state_changed:
    self._on_state_changed(PlaybackState.STOPPED)

# After
with self._lock:
    self._position_seconds = 0.0
    self._start_time = None
    self._paused_at = None
    if clear_source:
        self._source = None

self._set_state(PlaybackState.STOPPED)
```

**Rationale:** Using `_set_state()` instead of directly setting `self._state` and calling the callback ensures the state-change guard is applied. This prevents redundant STOPPED→STOPPED callbacks from firing.

### Test Results

All 233 tests pass after implementation:

```
================== 233 passed, 55 skipped, 1 warning in 7.29s ==================
```

### Expected Behavior After Fix

1. PlaybackBar starts hidden (correct initial state for STOPPED)
2. When `play()` is called, only one state change callback fires (STOPPED→PLAYING)
3. PLAYING callback removes `hidden` class and updates display
4. PlaybackBar becomes visible with progress indication
5. When playback stops, STOPPED callback adds `hidden` class

## Testing Notes

After implementing fixes, test:

1. Press space in Songset Editor → bar should appear
2. Press space again to stop → bar should disappear
3. Navigate away and back → bar should be hidden initially
4. Play from Lyrics Preview → bar should work (regression test)
