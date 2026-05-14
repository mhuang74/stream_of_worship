# Fix Slow Screen Transitions in User App

## Overview

This plan addresses the slow transition between Songset Manager and Songset Editor screens, which takes 3-20+ seconds. The root cause is a combination of aggressive database health checks and zombie screen listeners.

## Problem Analysis

### Symptom 1: 3-second delay on every screen transition

From logs:
```
2026-05-15 06:10:17.f | INFO  | Screen pushed, stack depth: 3
2026-05-15 06:10:17.f | INFO  | SongsetEditorScreen mounted
2026-05-15 06:10:20.f | INFO  | SongsetEditorScreen resumed, refreshing items
```

**Root Cause:** The `SELECT 1` health check added to `ConnectionProvider.get_connection()` runs on every single `self.connection` property access. Since `connection` is a property that calls `get_connection()` every time, every DB method triggers a health check.

**Call chain for a songset with N items:**
1. `action_edit_songset` → `get_songset()` → `self.connection` → **SELECT 1** + query
2. `_load_items()` → `get_items_raw()` → `self.connection` → **SELECT 1** + query
3. For each item: `get_recording_by_hash()` → **SELECT 1** + query
4. For each item: `get_song_including_deleted()` → **SELECT 1** + query

**Total health checks:** 2 + 2N. Each is a ~200ms round-trip to remote Neon (us-east-1).

For 5 items: 12 × 200ms = **~2.4 seconds just in health checks**.

### Symptom 2: 20+ second delay with repeated errors

From logs:
```
2026-05-15 06:05:22.f | ERROR | Failed to update input fields: No nodes match '#input_name'
2026-05-15 06:05:25.f | ERROR | Failed to update input fields: No nodes match '#input_name'
... (repeated every ~3 seconds for 14 seconds)
```

**Root Cause:** Three compounding bugs:

1. **`_refresh()` called before widgets mounted** — `on_mount` calls `_refresh()` before DOM is ready, so `query_one("#input_name")` fails.

2. **State listener never removed** — `self.state.add_listener("selected_songset", lambda _: self._refresh())` is registered but never removed in `on_unmount`. Every SongsetEditorScreen instance ever created keeps listening and calling `_refresh()` → `_load_items()` (DB query) even when covered by other screens.

3. **Duplicate screen stacking** — `navigate_to()` always pushes a fresh screen. When returning from Browse and re-entering the editor, a second SongsetEditorScreen gets stacked on top. Both register their own listener, compounding the problem.

---

## Fix Plan

### Fix 1: Replace aggressive health check with time-based staleness check

**File:** `src/stream_of_worship/db/connection.py`

**Rationale:** With `autocommit=True` (previous fix), there are no idle transactions for Neon to kill. The per-call `SELECT 1` is unnecessary and expensive. Replace with a time-based check that only probes the connection if it hasn't been used in the last 60 seconds.

**Changes:**

```python
import time
from typing import Optional

import psycopg


class ConnectionProvider:
    """Manages a single psycopg connection with auto-reconnect and cold-start retry."""

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0
    STALE_THRESHOLD_SECONDS = 60.0  # Only health-check if idle for 60+ seconds

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()
        self._last_used: float = 0.0  # monotonic time of last successful use

    def get_connection(self) -> psycopg.Connection:
        """Return an open psycopg connection, reconnecting if necessary.
        
        Uses time-based staleness check: only probes the connection if it
        hasn't been used in the last STALE_THRESHOLD_SECONDS.
        """
        with self._lock:
            if self._connection is None or self._connection.closed:
                self._connection = self._connect_with_retry()
                self._last_used = time.monotonic()
            elif self._is_stale():
                try:
                    self._connection.execute("SELECT 1")
                    self._last_used = time.monotonic()
                except Exception:
                    self._connection = self._connect_with_retry()
                    self._last_used = time.monotonic()
            return self._connection

    def _is_stale(self) -> bool:
        """Check if the connection hasn't been used recently."""
        return (time.monotonic() - self._last_used) > self.STALE_THRESHOLD_SECONDS

    def _connect_with_retry(self) -> psycopg.Connection:
        """Attempt to connect with exponential backoff for cold starts."""
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            conn = None
            try:
                conn = psycopg.connect(
                    self.database_url,
                    connect_timeout=10,
                    autocommit=True,
                    sslmode="require",
                )
                conn.execute("SELECT 1")
                return conn
            except Exception as exc:
                if conn:
                    conn.close()
                last_error = exc
                if attempt == self.MAX_RETRIES:
                    break
                time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_error if last_error else RuntimeError("Connection failed without error")
```

**Expected impact:** Reduces transition time from ~3 seconds to <500ms (only actual query latency remains).

---

### Fix 2: Defer `_refresh()` to after DOM mount

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Location:** `on_mount()` method (around line 112-130)

**Rationale:** `on_mount` fires before child widgets are in the DOM. Use `call_after_refresh` to wait for DOM to be ready.

**Change:**

```python
def on_mount(self) -> None:
    """Handle mount event."""
    logger.info(
        f"SongsetEditorScreen mounted (songset: {self.state.selected_songset.id if self.state.selected_songset else 'None'})"
    )
    # Defer refresh until DOM is ready
    self.call_after_refresh(self._refresh)
    self.call_after_refresh(self._focus_song_list)

    # Listen for state changes (store reference for removal in on_unmount)
    self._songset_listener = lambda _: self._refresh()
    self.state.add_listener("selected_songset", self._songset_listener)

    # Register playback callbacks
    self.playback.set_callbacks(
        on_position_changed=self._on_position_changed,
        on_state_changed=self._on_state_changed,
        on_finished=self._on_finished,
    )
```

---

### Fix 3: Remove state listener in `on_unmount`

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Location:** `on_unmount()` method (around line 132-134)

**Rationale:** Prevent zombie screen instances from continuing to respond to state changes and making DB queries.

**Change:**

```python
def on_unmount(self) -> None:
    """Unregister callbacks to prevent memory leaks and zombie refreshes."""
    self.playback.set_callbacks()
    if hasattr(self, '_songset_listener'):
        self.state.remove_listener("selected_songset", self._songset_listener)
```

**Also add to `__init__`:**

```python
def __init__(self, ...):
    ...
    self._songset_listener = None  # Initialize to None
```

---

### Fix 4: Guard `_refresh()` against non-active screens

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Location:** `_refresh()` method (around line 182)

**Rationale:** Even with listener removal, a race condition could cause `_refresh()` to run on a screen that's no longer the top of the stack. Guard against this.

**Change:**

```python
def _refresh(self) -> None:
    """Refresh the display."""
    # Don't refresh if this screen is not the active screen
    if not self.is_current:
        return
    
    songset = self.state.selected_songset
    if not songset:
        return

    # Update input fields with current values
    try:
        name_input = self.query_one("#input_name", Input)
        name_input.value = songset.name

        desc_input = self.query_one("#input_description", Input)
        desc_input.value = songset.description or ""
    except Exception as e:
        logger.error(f"Failed to update input fields: {e}")

    self._load_items()
```

---

### Fix 5: Prevent duplicate screen stacking (optional enhancement)

**File:** `src/stream_of_worship/app/app.py`

**Location:** `navigate_to()` method (around line 156-171)

**Rationale:** When navigating to a screen type that's already on top of the stack, pop the existing one first to avoid stacking duplicates.

**Change:**

```python
def navigate_to(self, screen: AppScreen) -> None:
    """Navigate to a screen."""
    logger.info(f"Navigate to: {screen.name} (from {self.state.current_screen.name})")

    # Stop playback when switching screens
    if self.playback.is_playing or self.playback.is_paused:
        logger.debug("Stopping playback before navigation")
        self.playback.stop()

    # Prevent pushing duplicate screen types onto the stack
    if len(self.screen_stack) > 0:
        current_screen = self.screen_stack[-1]
        if self._is_same_screen_type(current_screen, screen):
            logger.info(f"Replacing current {screen.name} screen instead of stacking")
            self.pop_screen()
            self.state.navigate_back()

    self.state.navigate_to(screen)
    self.push_screen(self._create_screen(screen))
    logger.debug(f"Screen pushed, stack depth: {len(self.screen_stack)}")

def _is_same_screen_type(self, screen_instance, screen_enum: AppScreen) -> bool:
    """Check if a screen instance matches an AppScreen enum value."""
    screen_type_map = {
        AppScreen.SONGSET_LIST: SongsetListScreen,
        AppScreen.SONGSET_EDITOR: SongsetEditorScreen,
        AppScreen.BROWSE: BrowseScreen,
        AppScreen.EXPORT_PROGRESS: ExportProgressScreen,
    }
    expected_type = screen_type_map.get(screen_enum)
    return expected_type and isinstance(screen_instance, expected_type)
```

---

## Summary of Files Changed

| File | Changes |
|------|---------|
| `src/stream_of_worship/db/connection.py` | Time-based staleness check instead of per-call health check |
| `src/stream_of_worship/app/screens/songset_editor.py` | 1. Defer `_refresh()` with `call_after_refresh`<br>2. Store listener reference and remove in `on_unmount`<br>3. Guard `_refresh()` against non-active screens |
| `src/stream_of_worship/app/app.py` | (Optional) Prevent duplicate screen stacking |

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Screen transition time | 3-20+ seconds | <500ms |
| "No nodes match" errors | Frequent | None |
| Zombie DB queries | Yes | No |
| Duplicate screen stacking | Yes | No (with Fix 5) |

---

## Testing Checklist

- [ ] Navigate from Songset Manager to Songset Editor — should be <1 second
- [ ] No "No nodes match '#input_name'" errors in logs
- [ ] Create new songset, add songs, return to editor — no duplicate screens
- [ ] Navigate back and forth multiple times — no performance degradation
- [ ] Leave app idle for 2+ minutes, then navigate — stale connection is detected and reconnected
- [ ] Run full test suite — all tests pass
