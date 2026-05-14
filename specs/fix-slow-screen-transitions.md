# Fix Slow Screen Transitions & Operational Risks in User App

## Overview

This plan addresses slow screen transitions (3-20+ seconds) and multiple operational risks affecting User App usability. The root causes span aggressive database health checks, zombie screen listeners, navigation stack desync, main-thread blocking operations, and N+1 query patterns.

## Problem Analysis

### Problem 1: 3-second delay on every screen transition

**Root Cause:** `SELECT 1` health check in `ConnectionProvider.get_connection()` runs on every `self.connection` property access. Since `connection` is a property calling `get_connection()` every time, every DB method triggers a health check.

**Call chain for a songset with N items:**
1. `action_edit_songset` → `get_songset()` → `self.connection` → **SELECT 1** + query
2. `_load_items()` → `get_items_raw()` → `self.connection` → **SELECT 1** + query
3. For each item: `get_recording_by_hash()` → **SELECT 1** + query
4. For each item: `get_song_including_deleted()` → **SELECT 1** + query

**Total health checks:** 2 + 2N. Each is ~200ms round-trip to remote Neon (us-east-1).
For 5 items: 12 × 200ms = **~2.4 seconds just in health checks**.

**Why it was added carelessly:** Commit `6cdc998` added the per-call `SELECT 1` as a defensive fix for Neon's `idle_in_transaction_session_timeout`. The same commit also switched to `autocommit=True`, which eliminates the root cause (no idle transactions for Neon to kill). The health check was redundant overhead — the "fix" (autocommit) already solved the problem.

### Problem 2: 20+ second delay with repeated errors

**Root Cause:** Three compounding bugs:
1. `_refresh()` called before widgets mounted — `on_mount` calls `_refresh()` before DOM is ready
2. State listener never removed — anonymous lambda registered but never cleaned up in `on_unmount`
3. Duplicate screen stacking — `navigate_to()` always pushes a fresh screen

### Problem 3: ExportProgressScreen callback leak (CRITICAL)

**File:** `src/stream_of_worship/app/screens/export_progress.py:61-62`

`register_progress_callback` and `register_completion_callback` are called in `on_mount` with no `on_unmount` cleanup. Navigating back during an export causes zombie callbacks that crash on dead widgets. `ExportService._progress_callbacks` and `_completion_callbacks` lists only grow, never shrink.

### Problem 4: navigate_back() desyncs AppState from Textual stack (CRITICAL)

**File:** `src/stream_of_worship/app/app.py:173-189`, `src/stream_of_worship/app/state.py:112-123`

`AppState.previous_screen` is a single slot, not a stack. After 2+ back navigations, `state.current_screen` diverges from the actual displayed screen. Reproduction: SongsetList → SongsetEditor → Browse → Back → Back. After the second back, `AppState.current_screen` is stale.

### Problem 5: LyricsPreviewScreen bypasses navigate_to() (CRITICAL)

**File:** `src/stream_of_worship/app/screens/songset_editor.py:441-447`

Calls `self.app.push_screen()` directly instead of `self.app.navigate_to()`. AppState is never updated, playback isn't stopped, and the Textual stack has a screen that AppState doesn't know about.

### Problem 6: LyricsPreviewScreen playback callbacks never unregistered (HIGH)

**File:** `src/stream_of_worship/app/screens/lyrics_preview.py:120-124`

No `on_unmount` to clear playback callbacks. After the screen is popped, callbacks still reference the dead screen.

### Problem 7: All DB queries block the main thread (HIGH)

**File:** All screen files

Every database call is synchronous, called from Textual event handlers. The TUI freezes during queries. With N+1 patterns and health checks, a single screen load can block for seconds.

### Problem 8: R2 network downloads block the main thread (HIGH)

**File:** `src/stream_of_worship/app/services/asset_cache.py:134-161, 194-220`

`download_audio()` and `download_lrc()` make synchronous HTTP requests from UI actions. TUI freezes during downloads (5-30+ seconds on slow connections).

### Problem 9: AudioEngine.preview_transition() blocks main thread (HIGH)

**File:** `src/stream_of_worship/app/services/audio_engine.py:248-306`

pydub decode + process + write is synchronous. UI freezes for several seconds during preview generation.

### Problem 10: N+1 query patterns (HIGH)

| Location | Pattern | Round trips (with health checks) |
|----------|---------|----------------------------------|
| `songset_list.py:85` | `get_item_count()` per songset | 1 + 2N |
| `catalog.py:186-197` | `get_recording_by_song_id()` per song | 1 + 2N |
| `catalog.py:472-503` | `get_recording_by_hash()` + `get_song_including_deleted()` per item | 3 + 4N |

### Problem 11: Unbounded screen stack growth (HIGH)

**File:** `src/stream_of_worship/app/app.py:156-171`

`navigate_to()` always pushes, never replaces duplicates. With zombie listeners, popped screens may not be GC'd.

### Problem 12: No timeout on R2 downloads (MEDIUM)

**File:** `src/stream_of_worship/app/services/asset_cache.py`

Stalled download hangs the app forever.

### Problem 13: No timeout on FFmpeg subprocess (MEDIUM)

**File:** `src/stream_of_worship/app/services/video_engine.py:875-906`

Hung export thread becomes zombie, `is_exporting` stays True forever.

### Problem 14: Temp files from preview_transition never cleaned up (MEDIUM)

**File:** `src/stream_of_worship/app/services/audio_engine.py:296-298`

`NamedTemporaryFile(delete=False)` leaks MP3s to system temp directory.

### Problem 15: ExportService callback lists grow without bound (MEDIUM)

**File:** `src/stream_of_worship/app/services/export.py:124-125`

No unregister method. Callback lists grow with each export.

### Problem 16: _notify silently swallows all exceptions (MEDIUM)

**File:** `src/stream_of_worship/app/state.py:93-100`

`except Exception: pass` hides bugs. Zombie listener crashes are invisible.

### Problem 17: No DB error handling (MEDIUM)

**File:** `src/stream_of_worship/app/db/read_client.py`, `src/stream_of_worship/app/db/songset_client.py`

Transient connection drops propagate as uncaught exceptions. No retry, no user-friendly error messages.

---

## Fix Plan

### Fix 1: Replace aggressive health check with time-based staleness check

**File:** `src/stream_of_worship/db/connection.py`

**Rationale:** With `autocommit=True`, there are no idle transactions for Neon to kill. The per-call `SELECT 1` is unnecessary and expensive. Replace with a time-based check that only probes the connection if it hasn't been used in the last 60 seconds.

**Changes:**

```python
import time
from typing import Optional

import psycopg


class ConnectionProvider:
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0
    STALE_THRESHOLD_SECONDS = 60.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()
        self._last_used: float = 0.0

    def get_connection(self) -> psycopg.Connection:
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
        return (time.monotonic() - self._last_used) > self.STALE_THRESHOLD_SECONDS

    def _connect_with_retry(self) -> psycopg.Connection:
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

**Expected impact:** Reduces transition time from ~3 seconds to <500ms.

---

### Fix 2: Defer `_refresh()` to after DOM mount

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Change in `on_mount()`:**

```python
def on_mount(self) -> None:
    logger.info(
        f"SongsetEditorScreen mounted (songset: {self.state.selected_songset.id if self.state.selected_songset else 'None'})"
    )
    self.call_after_refresh(self._refresh)
    self.call_after_refresh(self._focus_song_list)

    self._songset_listener = lambda _: self._refresh()
    self.state.add_listener("selected_songset", self._songset_listener)

    self.playback.set_callbacks(
        on_position_changed=self._on_position_changed,
        on_state_changed=self._on_state_changed,
        on_finished=self._on_finished,
    )
```

---

### Fix 3: Remove state listener in `on_unmount`

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Add to `__init__`:**

```python
self._songset_listener = None
```

**Change `on_unmount()`:**

```python
def on_unmount(self) -> None:
    self.playback.set_callbacks()
    if hasattr(self, '_songset_listener') and self._songset_listener:
        self.state.remove_listener("selected_songset", self._songset_listener)
```

---

### Fix 4: Guard `_refresh()` against non-active screens

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

```python
def _refresh(self) -> None:
    if not self.is_current:
        return

    songset = self.state.selected_songset
    if not songset:
        return

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

### Fix 5: Prevent duplicate screen stacking

**File:** `src/stream_of_worship/app/app.py`

```python
def navigate_to(self, screen: AppScreen) -> None:
    logger.info(f"Navigate to: {screen.name} (from {self.state.current_screen.name})")

    if self.playback.is_playing or self.playback.is_paused:
        logger.debug("Stopping playback before navigation")
        self.playback.stop()

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

### Fix 6: Fix navigate_back() desync — use a stack in AppState

**File:** `src/stream_of_worship/app/state.py`

**Rationale:** `previous_screen` is a single slot. Replace with a navigation stack that mirrors Textual's screen stack.

**Changes:**

```python
class AppState:
    def __init__(self):
        self._nav_stack: list[AppScreen] = []

    def navigate_to(self, screen: AppScreen) -> None:
        self._nav_stack.append(screen)
        self.current_screen = screen
        self._notify("current_screen", screen)

    def navigate_back(self) -> bool:
        if len(self._nav_stack) <= 1:
            return False
        self._nav_stack.pop()
        self.current_screen = self._nav_stack[-1]
        self._notify("current_screen", self.current_screen)
        return True

    @property
    def previous_screen(self) -> Optional[AppScreen]:
        return self._nav_stack[-2] if len(self._nav_stack) >= 2 else None
```

---

### Fix 7: Fix LyricsPreviewScreen to use navigate_to()

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Rationale:** `push_screen()` bypasses AppState tracking and playback management.

**Option A (recommended):** Add `LYRICS_PREVIEW` to `AppScreen` enum and route through `navigate_to()`.

**Option B (minimal):** Keep `push_screen()` but manually update state and stop playback:

```python
def action_lyrics_preview(self) -> None:
    item = self._get_selected_item()
    if not item:
        self.notify("No song selected", severity="warning")
        return
    if not item.recording_hash_prefix:
        self.notify("Song has no recording", severity="warning")
        return

    lrc_path = self.asset_cache.download_lrc(item.recording_hash_prefix)
    if not lrc_path:
        self.notify("No lyrics available for this song", severity="warning")
        return

    if self.playback.is_playing or self.playback.is_paused:
        self.playback.stop()

    self.app.push_screen(
        LyricsPreviewScreen(
            item=item,
            playback=self.playback,
            asset_cache=self.asset_cache,
        )
    )
```

---

### Fix 8: Add on_unmount to ExportProgressScreen and LyricsPreviewScreen

**File:** `src/stream_of_worship/app/screens/export_progress.py`

```python
def on_unmount(self) -> None:
    self.export_service.unregister_progress_callback(self._on_progress)
    self.export_service.unregister_completion_callback(self._on_complete)
```

**File:** `src/stream_of_worship/app/services/export.py` — add unregister methods:

```python
def unregister_progress_callback(self, callback):
    if callback in self._progress_callbacks:
        self._progress_callbacks.remove(callback)

def unregister_completion_callback(self, callback):
    if callback in self._completion_callbacks:
        self._completion_callbacks.remove(callback)
```

**File:** `src/stream_of_worship/app/screens/lyrics_preview.py`

```python
def on_unmount(self) -> None:
    self.playback.set_callbacks()
```

---

### Fix 9: Run DB queries and network downloads on worker thread

**File:** All screen files

**Rationale:** Synchronous DB and network calls freeze the TUI. Use Textual's `run_worker()` to offload blocking operations.

**Pattern for DB queries:**

```python
from textual.worker import Worker

def _load_items(self) -> None:
    self.run_worker(self._load_items_worker, exclusive=True, group="load_items")

def _load_items_worker(self) -> None:
    """Worker that loads items off the main thread."""
    if not self.state.selected_songset:
        return

    details, orphan_count = self.catalog.get_songset_with_items(
        self.state.selected_songset.id, self.songset_client
    )
    # Update UI on main thread
    self.call_from_thread(self._update_items_table, details)

def _update_items_table(self, details) -> None:
    """Update the DataTable on the main thread."""
    self.items = [d.item for d in details]
    # ... existing table update logic ...
```

**Pattern for R2 downloads:**

```python
def action_toggle_playback(self) -> None:
    if self.playback.is_playing:
        self.playback.stop()
        self.notify("Playback stopped")
        return

    item = self._get_selected_item()
    if not item:
        self.notify("No song selected", severity="error")
        return

    self.run_worker(self._play_item_worker, item, group="playback")

def _play_item_worker(self, item) -> None:
    try:
        audio_path = self.asset_cache.download_audio(item.recording_hash_prefix)
        if audio_path:
            self.call_from_thread(self.playback.play, audio_path)
            self.call_from_thread(self.notify, f"Playing: {item.song_title}")
        else:
            self.call_from_thread(self.notify, "Failed to download audio", severity="error")
    except Exception as e:
        self.call_from_thread(self.notify, f"Error: {e}", severity="error")
```

**Scope:** Apply to:
- `SongsetEditorScreen._load_items()`, `action_preview()`, `action_toggle_playback()`
- `SongsetListScreen._load_songsets()`
- `BrowseScreen._load_songs()`, `action_preview()`
- `LyricsPreviewScreen.on_mount()` (LRC download)

---

### Fix 10: Fix N+1 query patterns with batch queries

**File:** `src/stream_of_worship/app/db/songset_client.py`

Add `get_item_counts_batch()`:

```python
def get_item_counts_batch(self, songset_ids: list[str]) -> dict[str, int]:
    """Get item counts for multiple songsets in a single query."""
    conn = self.connection
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT songset_id, COUNT(*) as count
            FROM songset_items
            WHERE songset_id = ANY(%s)
            GROUP BY songset_id
            """,
            (songset_ids,),
        )
        rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}
```

**File:** `src/stream_of_worship/app/db/read_client.py`

Add `get_recordings_by_song_ids()` and `get_recordings_by_hashes()`:

```python
def get_recordings_by_song_ids(self, song_ids: list[str]) -> dict[str, Recording]:
    """Fetch recordings for multiple songs in a single query."""
    conn = self.connection
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.* FROM recordings r
            WHERE r.song_id = ANY(%s)
            """,
            (song_ids,),
        )
        rows = cur.fetchall()
    return {row[0]: Recording(**row) for row in rows}

def get_recordings_by_hashes(self, hash_prefixes: list[str]) -> dict[str, Recording]:
    """Fetch recordings by hash prefixes in a single query."""
    conn = self.connection
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM recordings
            WHERE hash_prefix = ANY(%s)
            """,
            (hash_prefixes,),
        )
        rows = cur.fetchall()
    return {row[0]: Recording(**row) for row in rows}
```

**File:** `src/stream_of_worship/app/services/catalog.py`

Update `get_songset_with_items()` and `list_songs_with_recordings()` to use batch queries.

---

### Fix 11: Add timeouts to R2 downloads and FFmpeg

**File:** `src/stream_of_worship/app/services/asset_cache.py`

Add a `timeout` parameter to download methods (default 30s):

```python
def download_audio(self, hash_prefix: str, timeout: int = 30) -> Optional[Path]:
    try:
        return self.r2_client.download_file(
            bucket=self.r2_bucket,
            key=f"audio/{hash_prefix}.mp3",
            local_path=audio_path,
            timeout=timeout,
        )
    except TimeoutError:
        logger.error(f"Timeout downloading audio for {hash_prefix}")
        return None
```

**File:** `src/stream_of_worship/app/services/video_engine.py`

Add timeout to `process.wait()`:

```python
try:
    process.wait(timeout=300)  # 5 minute max per FFmpeg call
except subprocess.TimeoutExpired:
    process.kill()
    raise RuntimeError("FFmpeg timed out")
```

---

### Fix 12: Clean up temp files from preview_transition

**File:** `src/stream_of_worship/app/services/audio_engine.py`

Track temp files and clean up on next preview or on service shutdown:

```python
def __init__(self, ...):
    self._temp_files: list[Path] = []

def preview_transition(self, from_item, to_item) -> Optional[Path]:
    # Clean up previous temp files
    for f in self._temp_files:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    self._temp_files.clear()

    # ... existing logic ...
    self._temp_files.append(Path(temp_path))
    return Path(temp_path)
```

---

### Fix 13: Log exceptions in _notify instead of silently swallowing

**File:** `src/stream_of_worship/app/state.py`

```python
def _notify(self, key: str, value: Any) -> None:
    for cb in self._listeners.get(key, []):
        try:
            cb(value)
        except Exception as e:
            logger.error(f"Listener error for '{key}': {e}")
```

---

### Fix 14: Add DB error handling with user-friendly messages

**File:** `src/stream_of_worship/app/db/read_client.py`, `src/stream_of_worship/app/db/songset_client.py`

Wrap critical query methods with try/except that catches `psycopg.OperationalError` and raises a custom `DatabaseError` with user-friendly message. Screens can catch `DatabaseError` and show a notification.

---

## Summary of Files Changed

| File | Fixes |
|------|-------|
| `src/stream_of_worship/db/connection.py` | Fix 1: Time-based staleness check |
| `src/stream_of_worship/app/screens/songset_editor.py` | Fix 2, 3, 4, 7, 9: Defer refresh, remove listener, guard, LyricsPreview nav, worker threads |
| `src/stream_of_worship/app/app.py` | Fix 5, 6: Duplicate screen prevention, nav stack |
| `src/stream_of_worship/app/state.py` | Fix 6, 13: Navigation stack, log listener errors |
| `src/stream_of_worship/app/screens/export_progress.py` | Fix 8: Unregister callbacks |
| `src/stream_of_worship/app/screens/lyrics_preview.py` | Fix 8: Unregister callbacks |
| `src/stream_of_worship/app/services/export.py` | Fix 8: Unregister methods |
| `src/stream_of_worship/app/screens/songset_list.py` | Fix 9, 10: Worker thread, batch queries |
| `src/stream_of_worship/app/screens/browse.py` | Fix 9: Worker thread |
| `src/stream_of_worship/app/db/songset_client.py` | Fix 10, 14: Batch queries, error handling |
| `src/stream_of_worship/app/db/read_client.py` | Fix 10, 14: Batch queries, error handling |
| `src/stream_of_worship/app/services/catalog.py` | Fix 10: Use batch queries |
| `src/stream_of_worship/app/services/asset_cache.py` | Fix 11: Download timeouts |
| `src/stream_of_worship/app/services/video_engine.py` | Fix 11: FFmpeg timeout |
| `src/stream_of_worship/app/services/audio_engine.py` | Fix 12: Temp file cleanup |

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Screen transition time | 3-20+ seconds | <500ms |
| "No nodes match" errors | Frequent | None |
| Zombie DB queries | Yes | No |
| Duplicate screen stacking | Yes | No |
| AppState/Textual stack desync | Yes | No |
| UI freezes during DB/network | Yes | No (worker threads) |
| N+1 query round trips (5 items) | ~35 | ~5 |
| Temp file leaks | Yes | No |
| Silent listener errors | Yes | Logged |

---

## Implementation Order

1. **Fix 1** (connection.py) — biggest bang for buck, standalone
2. **Fix 2, 3, 4** (songset_editor.py) — fixes the 20s delay
3. **Fix 5, 6** (app.py, state.py) — fixes navigation desync
4. **Fix 8** (export_progress, lyrics_preview, export.py) — prevents crashes
5. **Fix 7** (lyrics_preview nav) — consistency fix
6. **Fix 13** (state.py) — observability
7. **Fix 9** (worker threads) — UI responsiveness (largest change)
8. **Fix 10** (batch queries) — performance
9. **Fix 11, 12** (timeouts, temp cleanup) — robustness
10. **Fix 14** (error handling) — polish

---

## Testing Checklist

- [ ] Navigate from Songset Manager to Songset Editor — should be <1 second
- [ ] No "No nodes match '#input_name'" errors in logs
- [ ] Create new songset, add songs, return to editor — no duplicate screens
- [ ] Navigate back and forth 3+ times — no performance degradation, no stack desync
- [ ] Leave app idle for 2+ minutes, then navigate — stale connection detected and reconnected
- [ ] Navigate back during active export — no crash, export continues
- [ ] Open and close lyrics preview — no zombie callbacks
- [ ] Browse songs with 50+ entries — UI remains responsive during load
- [ ] Preview transition — UI remains responsive during generation
- [ ] Run full test suite — all tests pass
