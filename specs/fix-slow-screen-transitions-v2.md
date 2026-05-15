# Fix Slow Screen Transitions & Operational Risks in User App — v2

> **Status:** Validated against live code on 2026-05-15.
> Supersedes `specs/fix-slow-screen-transitions.md` (v1).
> All 17 problems from v1 confirmed. v2 corrects four issues in the proposed fixes.

---

## Changes from v1

| # | v1 | v2 |
|---|----|----|
| Fix 1 | 60s staleness check + `SELECT 1` after idle | **Drop proactive health check entirely.** Reconnect on `OperationalError` at query layer. |
| Fix 3 | Store listener as `self._songset_listener` (but only after `add_listener`) | **Store named method before registration** so `remove_listener` can match by identity. |
| Fix 5 | "Same screen type" — ambiguous on different-payload case | **Always replace same type** regardless of payload (confirmed intent). |
| Fix 7 | Two options (A enum, B manual push) | **Option A only:** add `LYRICS_PREVIEW` to `AppScreen` enum, route through `navigate_to`. |
| Fix 10 | `dict[row[0], Recording]` | Key by `recording.hash_prefix` / `recording.song_id` explicitly. |
| Fix 14 | Unscoped | **Read paths only.** Do not wrap write methods. |

---

## Problem Analysis

All problems from v1 are confirmed. References below are to specific code locations verified in the current codebase.

### Problem 1 — 3-second delay on every screen transition

**Confirmed at:** `src/stream_of_worship/db/connection.py:42-50`

`get_connection()` executes `SELECT 1` on every call for a cached, open connection. Because `connection` is a property in both clients (`read_client.py:36-39`, `songset_client.py:48-51`) that calls `get_connection()` on every access, every `self.connection.cursor()` call incurs a full network round-trip to Neon (us-east-1, ~50-200ms).

`autocommit=True` is set at `connection.py:61`, which means there are no idle transactions for Neon's `idle_in_transaction_session_timeout` to kill. The health check is fully redundant overhead.

**Hot path example — `get_songset_with_items` (catalog.py:472-505), N items:**
- `get_songset` → 1 health check + 1 query
- `get_items_raw` → 1 health check + 1 query
- N × `get_recording_by_hash` → N health checks + N queries
- N × `get_song_including_deleted` → N health checks + N queries
- **Total: 2+2N health checks, 2+2N queries**

For 5 items at 100ms/round-trip: **12 wasted health-check round-trips = 1.2 seconds**, before counting actual queries.

### Problem 2 — 20+ second delay with repeated errors

**Confirmed at:** `songset_editor.py:123` (anonymous lambda listener never removed), `on_unmount` at line 132 only clears playback, DOM query errors from calling `_refresh()` before widgets mount.

### Problem 3 — ExportProgressScreen callback leak (CRITICAL)

**Confirmed at:** `export_progress.py:61-62` (register in `on_mount`, no `on_unmount`), `export.py:124-125` (no `unregister_*` methods).

### Problem 4 — navigate_back() desyncs AppState from Textual stack (CRITICAL)

**Confirmed at:** `state.py:102-123`. `previous_screen` is a single slot; after two navigations, `state.current_screen` diverges from Textual's actual displayed screen.

### Problem 5 — LyricsPreviewScreen bypasses navigate_to() (CRITICAL)

**Confirmed at:** `songset_editor.py:441-447`. Calls `self.app.push_screen()` directly; AppState never updated.

### Problem 6 — LyricsPreviewScreen playback callbacks never unregistered (HIGH)

**Confirmed at:** `lyrics_preview.py:120-124`. No `on_unmount` defined. `PlaybackService.set_callbacks()` is overwrite-only, so the leak self-heals only if a subsequent screen also sets callbacks — not guaranteed.

### Problem 7 — All DB queries block the main thread (HIGH)

**Confirmed:** Zero `run_worker` / `Worker` usages anywhere in `src/stream_of_worship/app/screens/`.

### Problem 8 — R2 downloads block the main thread (HIGH)

**Confirmed at:** `asset_cache.py:134-161, 194-220`. Both `download_audio` and `download_lrc` are synchronous. R2 client at `admin/services/r2.py:147` signature: `download_file(self, s3_key: str, dest_path: Path) -> Path` — no timeout parameter. boto3 client at `r2.py:54-60` has no `botocore.config.Config` (defaults to 60s connect + 60s read + retries).

### Problem 9 — AudioEngine.preview_transition() blocks main thread (HIGH)

**Confirmed at:** `audio_engine.py:248-306`. pydub decode + process + export is synchronous on the main thread.

### Problem 10 — N+1 query patterns (HIGH)

**Confirmed:**
- `songset_list.py:85` — `get_item_count(songset.id)` called per row in a for-loop
- `catalog.py:186-197` — `get_recording_by_song_id(song.id)` called per song
- `catalog.py:472-505` — `get_recording_by_hash` + `get_song_including_deleted` per item

### Problem 11 — Unbounded screen stack growth (HIGH)

**Confirmed at:** `app.py:156-171`. `navigate_to` always calls `push_screen(_create_screen(screen))`.

### Problem 12 — No timeout on R2 downloads (MEDIUM)

**Confirmed.** See Problem 8 above.

### Problem 13 — No timeout on FFmpeg subprocess (MEDIUM)

**Confirmed at:** `video_engine.py:906`. `process.wait()` has no timeout argument. stderr is `subprocess.DEVNULL` so a hung FFmpeg is silent.

### Problem 14 — Temp files from preview_transition never cleaned up (MEDIUM)

**Confirmed at:** `audio_engine.py:296-298`. `NamedTemporaryFile(delete=False)` with no tracking or atexit cleanup.

### Problem 15 — ExportService callback lists grow without bound (MEDIUM)

**Confirmed at:** `export.py:124-125, 129-143`. Registration-only API, no unregister methods.

### Problem 16 — _notify silently swallows all exceptions (MEDIUM)

**Confirmed at:** `state.py:93-100`. `except Exception: pass` hides stale-listener crashes.

### Problem 17 — No DB error handling (MEDIUM)

**Confirmed:** `psycopg.OperationalError` from transient drops propagates as unhandled exceptions with no user-visible notification.

---

## Fix Plan

### Fix 1 — Remove proactive health check; use reactive reconnection

**File:** `src/stream_of_worship/db/connection.py`

**Rationale:** With `autocommit=True`, dropped connections raise `psycopg.OperationalError` on the next real query — not silently. There is no need to spend a network round-trip probing the connection before every query. Replace the `SELECT 1`-on-every-call pattern with a single try/reconnect wrapper. A broken connection surfaces naturally; we reconnect once and retry.

```python
import threading
import time
from typing import Optional

import psycopg


class ConnectionProvider:
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()

    def get_connection(self) -> psycopg.Connection:
        with self._lock:
            if self._connection is None or self._connection.closed:
                self._connection = self._connect_with_retry()
            return self._connection

    def invalidate(self) -> None:
        """Force next get_connection() to reconnect (call on OperationalError)."""
        with self._lock:
            if self._connection and not self._connection.closed:
                try:
                    self._connection.close()
                except Exception:
                    pass
            self._connection = None

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
                conn.execute("SELECT 1")  # verify new connection only
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

Add a helper to both clients that executes a query with one reconnect-on-`OperationalError` retry:

```python
# In ReadOnlyClient and SongsetClient (base class or mixin)
def _execute_with_retry(self, fn):
    """Run fn(conn) once; on OperationalError, invalidate and retry once."""
    try:
        return fn(self.connection)
    except psycopg.OperationalError:
        self.connection_provider.invalidate()
        return fn(self.connection)
```

All existing query methods that do `with self.connection.cursor() as cur:` become `self._execute_with_retry(lambda conn: ...)` or keep their structure if the retry wrapper is applied at the `get_connection()` level inside `_execute_with_retry`.

**Note:** The `SELECT 1` in `_connect_with_retry` remains — it verifies a freshly opened connection before returning it, which is correct (cold starts on Neon can fail silently).

**Expected impact:** Every screen transition drops 2+2N round-trips. For 5-item songset: from ~12 × 100-200ms = 1.2-2.4s to 0ms on the hot path.

---

### Fix 2 — Defer `_refresh()` to after DOM mount

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

Replace the direct `self._refresh()` call in `on_mount` with `self.call_after_refresh(self._refresh)` so the DOM is ready before querying widgets.

---

### Fix 3 — Remove state listener in `on_unmount` (fix lambda bug)

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**v1 bug:** The stored lambda `lambda _: self._refresh()` creates a new object at definition time. If `add_listener` is called with this anonymous object, `remove_listener` uses list equality — a second lambda `lambda _: self._refresh()` is a different object and won't match. The listener must be stored as the exact same callable object that was passed to `add_listener`.

**Fix:** Assign a bound method or stored lambda to an instance attribute *before* calling `add_listener`:

```python
def on_mount(self) -> None:
    self.call_after_refresh(self._refresh)
    self.call_after_refresh(self._focus_song_list)
    # Store before add_listener so on_unmount can remove it
    self._songset_listener = self._on_selected_songset_changed
    self.state.add_listener("selected_songset", self._songset_listener)
    self.playback.set_callbacks(
        on_position_changed=self._on_position_changed,
        on_state_changed=self._on_state_changed,
        on_finished=self._on_finished,
    )

def _on_selected_songset_changed(self, _) -> None:
    self._refresh()

def on_unmount(self) -> None:
    self.playback.set_callbacks()
    if self._songset_listener:
        self.state.remove_listener("selected_songset", self._songset_listener)
        self._songset_listener = None
```

Initialize `self._songset_listener = None` in `__init__`.

---

### Fix 4 — Guard `_refresh()` against non-active screens

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

```python
def _refresh(self) -> None:
    if not self.is_current:
        return
    songset = self.state.selected_songset
    if not songset:
        return
    try:
        self.query_one("#input_name", Input).value = songset.name
        self.query_one("#input_description", Input).value = songset.description or ""
    except Exception as e:
        logger.error(f"Failed to update input fields: {e}")
    self._load_items()
```

---

### Fix 5 — Prevent duplicate screen stacking

**File:** `src/stream_of_worship/app/app.py`

Always replace a screen of the same type rather than stacking. This handles all cases: re-navigating to the same screen, or navigating to the same screen type with different state.

```python
def navigate_to(self, screen: AppScreen) -> None:
    logger.info(f"Navigate to: {screen.name} (from {self.state.current_screen.name})")

    if self.playback.is_playing or self.playback.is_paused:
        self.playback.stop()

    # Replace instead of stack if top of stack is same screen type
    if len(self.screen_stack) > 0 and self._is_same_screen_type(self.screen_stack[-1], screen):
        logger.info(f"Replacing duplicate {screen.name} screen")
        self.pop_screen()
        self.state.navigate_back()

    self.state.navigate_to(screen)
    self.push_screen(self._create_screen(screen))

def _is_same_screen_type(self, screen_instance, screen_enum: AppScreen) -> bool:
    screen_type_map = {
        AppScreen.SONGSET_LIST: SongsetListScreen,
        AppScreen.SONGSET_EDITOR: SongsetEditorScreen,
        AppScreen.BROWSE: BrowseScreen,
        AppScreen.EXPORT_PROGRESS: ExportProgressScreen,
        AppScreen.LYRICS_PREVIEW: LyricsPreviewScreen,
    }
    expected_type = screen_type_map.get(screen_enum)
    return expected_type is not None and isinstance(screen_instance, expected_type)
```

---

### Fix 6 — Fix navigate_back() desync — replace single slot with a stack

**File:** `src/stream_of_worship/app/state.py`

Replace `previous_screen` (single slot) with `_nav_stack` (list) so AppState mirrors Textual's screen stack depth.

```python
class AppState:
    def __init__(self):
        self._nav_stack: list[AppScreen] = []
        # existing fields...

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

Remove the `previous_screen` field from `__init__` and the dataclass; it is now a property.

---

### Fix 7 — Route LyricsPreviewScreen through navigate_to() (Option A)

**File:** `src/stream_of_worship/app/app.py`, `src/stream_of_worship/app/screens/songset_editor.py`

Add `LYRICS_PREVIEW` to `AppScreen` enum. Update `navigate_to` / `_create_screen` to handle it. Update `action_lyrics_preview` to call `self.app.navigate_to(AppScreen.LYRICS_PREVIEW)` after setting any needed state (e.g., `self.state.selected_item = item`).

```python
# In action_lyrics_preview:
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
    self.state.selected_preview_item = item
    self.app.navigate_to(AppScreen.LYRICS_PREVIEW)
```

`_create_screen` reads `state.selected_preview_item` to construct `LyricsPreviewScreen`.

---

### Fix 8 — Add on_unmount to ExportProgressScreen and LyricsPreviewScreen

**File:** `src/stream_of_worship/app/screens/export_progress.py`

```python
def on_unmount(self) -> None:
    self.export_service.unregister_progress_callback(self._on_progress)
    self.export_service.unregister_completion_callback(self._on_complete)
```

**File:** `src/stream_of_worship/app/services/export.py` — add unregister methods:

```python
def unregister_progress_callback(self, callback) -> None:
    try:
        self._progress_callbacks.remove(callback)
    except ValueError:
        pass

def unregister_completion_callback(self, callback) -> None:
    try:
        self._completion_callbacks.remove(callback)
    except ValueError:
        pass
```

**File:** `src/stream_of_worship/app/screens/lyrics_preview.py`

```python
def on_unmount(self) -> None:
    self.playback.set_callbacks()
```

---

### Fix 9 — Run DB queries and network downloads on worker threads

**File:** All screen files

**Scope decision:** Even with Fix 1 eliminating health checks, downloads and export operations can block for 5-30+ seconds. DB queries for ≤20 items may become acceptable (<100ms) after Fix 1 + Fix 10, but should still be offloaded to keep the TUI responsive on slow connections.

**Screens to convert:**
- `SongsetEditorScreen._load_items()`, `action_preview()`, `action_toggle_playback()`
- `SongsetListScreen._load_songsets()`
- `BrowseScreen._load_songs()`, `action_preview()`
- `LyricsPreviewScreen.on_mount()` (LRC download — move off main thread)

**Pattern for DB load:**

> **IMPORTANT:** Worker functions must be synchronous `def`, not `async def`. Always pass `thread=True` so Textual runs them in a thread pool instead of trying to `await` them. Use `self.app.call_from_thread(...)` — NOT `self.call_from_thread(...)` — because `call_from_thread` is an `App` method, not available on `Screen`.

```python
def _load_items(self) -> None:
    self.run_worker(self._load_items_worker, exclusive=True, group="load_items", thread=True)

def _load_items_worker(self) -> None:
    if not self.state.selected_songset:
        return
    details, orphan_count = self.catalog.get_songset_with_items(
        self.state.selected_songset.id, self.songset_client
    )
    self.app.call_from_thread(self._update_items_table, details, orphan_count)

def _update_items_table(self, details, orphan_count) -> None:
    self.items = [d.item for d in details]
    # ... existing table update logic ...
```

**Pattern for download + play:**

```python
def action_toggle_playback(self) -> None:
    if self.playback.is_playing:
        self.playback.stop()
        return
    item = self._get_selected_item()
    if not item:
        self.notify("No song selected", severity="error")
        return
    self.run_worker(lambda: self._play_item_worker(item), group="playback", thread=True)

def _play_item_worker(self, item) -> None:
    try:
        audio_path = self.asset_cache.download_audio(item.recording_hash_prefix)
        if audio_path:
            self.app.call_from_thread(self.playback.play, audio_path)
        else:
            self.app.call_from_thread(self.notify, "Failed to download audio", severity="error")
    except Exception as e:
        self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")
```

**Thread safety note:** `ConnectionProvider._lock` already serializes all DB access, so worker threads calling `get_connection()` concurrently is safe. However, with a single shared connection, DB workers will serialize at the lock — this is acceptable for typical usage.

---

### Fix 10 — Fix N+1 query patterns with batch queries

**File:** `src/stream_of_worship/app/db/songset_client.py`

```python
def get_item_counts_batch(self, songset_ids: list[str]) -> dict[str, int]:
    conn = self.connection
    with conn.cursor() as cur:
        cur.execute(
            "SELECT songset_id, COUNT(*) FROM songset_items "
            "WHERE songset_id = ANY(%s) GROUP BY songset_id",
            (songset_ids,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
```

**File:** `src/stream_of_worship/app/db/read_client.py`

```python
def get_recordings_by_song_ids(self, song_ids: list[str]) -> dict[str, Recording]:
    conn = self.connection
    with conn.cursor(row_factory=psycopg.rows.class_row(Recording)) as cur:
        cur.execute(
            "SELECT * FROM recordings WHERE song_id = ANY(%s)",
            (song_ids,),
        )
        return {r.song_id: r for r in cur.fetchall()}

def get_recordings_by_hashes(self, hash_prefixes: list[str]) -> dict[str, Recording]:
    conn = self.connection
    with conn.cursor(row_factory=psycopg.rows.class_row(Recording)) as cur:
        cur.execute(
            "SELECT * FROM recordings WHERE hash_prefix = ANY(%s)",
            (hash_prefixes,),
        )
        return {r.hash_prefix: r for r in cur.fetchall()}
```

**Note:** Use `recording.song_id` and `recording.hash_prefix` as dict keys — not positional row index.

**File:** `src/stream_of_worship/app/services/catalog.py`

Update `get_songset_with_items` to:
1. Fetch all items in one query
2. Collect all hash prefixes
3. Batch-fetch all recordings via `get_recordings_by_hashes`
4. Batch-fetch all songs via a new `get_songs_by_ids` (or equivalent)
5. Assemble results in memory

Update `list_songs_with_recordings` to use `get_recordings_by_song_ids` instead of per-song lookups.

---

### Fix 11 — Add timeouts to R2 downloads and FFmpeg

**File:** `src/stream_of_worship/admin/services/r2.py`

Add boto3 `Config` with timeouts to the client constructor:

```python
from botocore.config import Config

self._client = boto3.client(
    "s3",
    endpoint_url=endpoint_url,
    region_name=region,
    config=Config(
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 2},
    ),
)
```

**File:** `src/stream_of_worship/app/services/video_engine.py`

```python
try:
    process.wait(timeout=300)  # 5-minute max
except subprocess.TimeoutExpired:
    process.kill()
    raise RuntimeError("FFmpeg timed out after 5 minutes")
```

---

### Fix 12 — Clean up temp files from preview_transition

**File:** `src/stream_of_worship/app/services/audio_engine.py`

```python
def __init__(self, ...):
    self._preview_temp_files: list[Path] = []

def preview_transition(self, from_item, to_item) -> Optional[Path]:
    for f in self._preview_temp_files:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    self._preview_temp_files.clear()

    # ... existing logic ...
    temp_path = Path(temp_file.name)
    self._preview_temp_files.append(temp_path)
    return temp_path
```

---

### Fix 13 — Log exceptions in _notify instead of swallowing

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

### Fix 14 — Add DB error handling on read paths

**Files:** `src/stream_of_worship/app/db/read_client.py`, `src/stream_of_worship/app/db/songset_client.py`

**Scope: read paths only.** Define a `DatabaseError` in a shared module. Wrap public read methods (list/get) to catch `psycopg.OperationalError` and raise `DatabaseError(user_friendly_message)`. Screens catch `DatabaseError` in their worker methods and call `self.call_from_thread(self.notify, str(e), severity="error")`.

Do not wrap write methods (create, update, delete) — those errors should propagate with full context for debugging.

---

## Summary of Files Changed

| File | Fixes |
|------|-------|
| `src/stream_of_worship/db/connection.py` | Fix 1: Remove proactive health check, add `invalidate()` |
| `src/stream_of_worship/app/db/read_client.py` | Fix 1, 10, 14: Retry wrapper, batch queries, error handling |
| `src/stream_of_worship/app/db/songset_client.py` | Fix 1, 10: Retry wrapper, batch queries |
| `src/stream_of_worship/app/state.py` | Fix 6, 13: Navigation stack, log listener errors |
| `src/stream_of_worship/app/app.py` | Fix 5, 6, 7: Dedup screens, nav stack, LyricsPreview routing |
| `src/stream_of_worship/app/screens/songset_editor.py` | Fix 2, 3, 4, 7, 9: Defer refresh, named listener, guard, route preview, workers |
| `src/stream_of_worship/app/screens/songset_list.py` | Fix 9, 10: Worker, batch item count |
| `src/stream_of_worship/app/screens/browse.py` | Fix 9: Workers for load + preview |
| `src/stream_of_worship/app/screens/lyrics_preview.py` | Fix 8, 9: Unmount cleanup, worker for LRC download |
| `src/stream_of_worship/app/screens/export_progress.py` | Fix 8: Unregister callbacks in `on_unmount` |
| `src/stream_of_worship/app/services/catalog.py` | Fix 10: Use batch queries |
| `src/stream_of_worship/app/services/export.py` | Fix 8: Add `unregister_*` methods |
| `src/stream_of_worship/admin/services/r2.py` | Fix 11: boto3 Config with timeouts |
| `src/stream_of_worship/app/services/asset_cache.py` | Fix 11: Propagate timeout behavior |
| `src/stream_of_worship/app/services/audio_engine.py` | Fix 12: Temp file cleanup |
| `src/stream_of_worship/app/services/video_engine.py` | Fix 11: FFmpeg timeout |

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Screen transition time | 3-20+ seconds | <500ms |
| "No nodes match" errors | Frequent | None |
| Health check round-trips per screen load | 2+2N | 0 (hot path) |
| N+1 query round trips (5 items) | ~12 queries | ~4 queries |
| Zombie DB queries after back-navigation | Yes | No |
| Duplicate screen stacking | Yes | No |
| AppState/Textual stack desync | Yes | No |
| UI freezes during DB/network I/O | Yes | No (worker threads) |
| Temp file leaks | Yes | No |
| Silent listener errors | Yes | Logged |
| Stale listener accumulation | Yes | Cleaned in unmount |
| Export callback leaks | Yes | Cleaned in unmount |

---

## Implementation Order

1. **Fix 1** (`connection.py`) — biggest bang, fully standalone, no screen changes
2. **Fix 13** (`state.py`) — observability first, helps diagnose remaining issues
3. **Fix 2, 3, 4** (`songset_editor.py`) — fixes the 20s delay, zero API surface change
4. **Fix 6** (`state.py`) — navigation stack (required before Fix 5)
5. **Fix 5** (`app.py`) — duplicate screen prevention
6. **Fix 8** (`export_progress.py`, `lyrics_preview.py`, `export.py`) — prevents crashes
7. **Fix 7** (`songset_editor.py`, `app.py`) — LyricsPreview routing (depends on Fix 5/6)
8. **Fix 10** (`db/`, `catalog.py`) — batch queries (standalone, reduces query count before workers land)
9. **Fix 9** (all screens) — worker threads (largest change; Fix 1 must land first)
10. **Fix 11, 12** — timeouts, temp cleanup (standalone robustness)
11. **Fix 14** (`db/`) — error handling polish

---

## Testing Checklist

- [ ] Navigate from Songset Manager → Songset Editor — should be <1 second
- [ ] No "No nodes match '#input_name'" errors in logs
- [ ] Create new songset, add songs, return to editor — no duplicate screens
- [ ] Navigate SongsetList → SongsetEditor → Browse → Back → Back — no stack desync, correct screen shown
- [ ] Navigate back during active export — no crash, export continues in background
- [ ] Open and close lyrics preview — no zombie callbacks, playback stops
- [ ] Browse songs with 50+ entries — UI remains responsive during load
- [ ] Preview audio transition — UI remains responsive during generation
- [ ] Leave app idle 2+ minutes, then navigate — reconnects transparently
- [ ] Confirm worker threads: UI remains interactive during `_load_items`
- [ ] Run full test suite: `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ --ignore=tests/services/analysis --ignore=services/qwen3/tests --ignore=services/analysis/tests -v`
