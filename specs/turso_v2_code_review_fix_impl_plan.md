# Turso V2 Code Review Fix — Implementation Plan

**Date:** 2026-04-26
**Source:** `reports/turso_v2_implementation_code_review.md`
**Scope:** P0–P2 issues only (items 1–12)

---

## P0 — Fix Immediately (Blocks V2 Deployment)

### 1. C1: JOIN Column Offset Off-by-One in CatalogService

**Severity:** CRITICAL — silent data corruption
**Files:** `src/stream_of_worship/app/services/catalog.py`
**Lines:** 229, 281, 348

#### Problem

`songs` table now has 17 columns (indices 0–16, `deleted_at` at index 16). All three JOIN queries use `SELECT s.*, r.content_hash, ...`, which returns 17 song columns followed by recording columns. The code splits at offset 16:

```python
song = Song.from_row(row_tuple[0:16])         # indices 0–15, misses deleted_at
recording = Recording.from_row(row_tuple[16:])  # starts at s.deleted_at, not r.content_hash
```

`row_tuple[16:]` begins with `s.deleted_at`, shifting every recording column by 1. All `Recording` objects from JOIN queries are silently corrupted.

**Affected methods:**
- `_list_analyzed_songs()` — line 229
- `_list_lrc_songs()` — line 281
- `_search_lrc_songs()` — line 348

#### Implementation Steps

**Step 1: Stop using `SELECT s.*` — enumerate song columns explicitly.**

Define a constant for the song columns used in JOIN queries. This prevents recurrence when columns are added in the future.

In `src/stream_of_worship/app/db/schema.py` (admin) or a shared location accessible to the app, add:

```python
SONG_COLUMNS_FOR_JOIN = """
    s.id, s.title, s.title_pinyin, s.composer, s.lyricist,
    s.album_name, s.album_series, s.musical_key, s.lyrics_raw,
    s.lyrics_lines, s.sections, s.source_url, s.table_row_number,
    s.scraped_at, s.created_at, s.updated_at, s.deleted_at
"""
```

Count: 17 columns. This constant makes the column count explicit and self-documenting.

**Step 2: Replace `SELECT s.*` with the explicit column list in all three methods.**

In `catalog.py`, update three methods:

`_list_analyzed_songs()`:
```python
query = f"""
    SELECT {SONG_COLUMNS_FOR_JOIN},
           r.content_hash, r.hash_prefix, r.song_id, r.original_filename,
           r.file_size_bytes, r.imported_at, r.r2_audio_url, r.r2_stems_url,
           r.r2_lrc_url, r.duration_seconds, r.tempo_bpm, r.musical_key,
           r.musical_mode, r.key_confidence, r.loudness_db, r.beats,
           r.downbeats, r.sections, r.embeddings_shape, r.analysis_status,
           r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.created_at,
           r.updated_at
    FROM songs s
    JOIN recordings r ON s.id = r.song_id
    WHERE r.analysis_status = 'completed' AND r.deleted_at IS NULL
    AND s.deleted_at IS NULL
"""
```

Same pattern for `_list_lrc_songs()` and `_search_lrc_songs()`.

**Step 3: Fix the row split offset to 17.**

In all three methods, change:
```python
# BEFORE (broken):
song = Song.from_row(row_tuple[0:16])
recording = Recording.from_row(row_tuple[16:])

# AFTER (fixed):
song = Song.from_row(row_tuple[0:17])
recording = Recording.from_row(row_tuple[17:])
```

**Note:** Step 2 (explicit columns) makes Step 3 (offset 17) safe against future schema additions. Step 3 alone is the minimum viable fix if Step 2 is deferred, but Step 2 is strongly recommended.

#### Verification

- Add a test that creates a database with the 17-column songs schema, inserts a song and recording, then asserts `SongWithRecording.recording.content_hash` matches the inserted recording's `content_hash`.
- Test `_list_analyzed_songs()`, `_list_lrc_songs()`, and `_search_lrc_songs()` individually.

#### Dependencies

- Depends on M3 (adding `deleted_at` to `Song` model) for `Song.from_row` to accept 17 columns without error. If M3 is not yet implemented, `Song.from_row` will silently ignore index 16. This is acceptable as an intermediate state (the critical fix is the Recording offset), but M3 should be done as part of the same PR.

---

### 2. C2: `Recording.from_row` Doesn't Handle 28-Column Schema

**Severity:** CRITICAL — data loss on every Recording read
**File:** `src/stream_of_worship/admin/db/models.py:200-221`

#### Problem

With `deleted_at` added, `SELECT * FROM recordings` returns 28 columns. The versioned logic handles 25, 26, and 27 columns but has no 28-column case. The `else` branch is hit, which:
- Sets `visibility_status = None` → `is_published` returns `False` for all recordings
- Sets `youtube_url = None` → YouTube URL metadata silently dropped

Current schema column order for the trailing columns:
| Index | Column |
|-------|--------|
| 23 | created_at |
| 24 | updated_at |
| 25 | youtube_url |
| 26 | visibility_status |
| 27 | deleted_at |

#### Implementation Steps

**Step 1: Add 28-column case to `Recording.from_row`.**

In `src/stream_of_worship/admin/db/models.py`, modify `Recording.from_row`:

```python
row_len = len(row)

if row_len == 28:
    created_at = row[23]
    updated_at = row[24]
    youtube_url = row[25]
    visibility_status = row[26]
    deleted_at = row[27]
elif row_len == 27:
    created_at = row[23]
    updated_at = row[24]
    youtube_url = row[25]
    visibility_status = row[26]
    deleted_at = None
elif row_len == 26:
    visibility_status = None
    created_at = row[23]
    updated_at = row[24]
    youtube_url = row[25]
    deleted_at = None
else:
    visibility_status = None
    created_at = row[23] if row_len > 23 else None
    updated_at = row[24] if row_len > 24 else None
    youtube_url = None
    deleted_at = None
```

**Step 2: Add `deleted_at` field to Recording dataclass (see M3).**

Add `deleted_at: Optional[str] = None` to the `Recording` dataclass and pass it in the constructor call within `from_row`.

**Step 3: Update `to_dict()` to include `deleted_at`.**

#### Verification

- Unit test: construct a 28-element tuple with known values, call `Recording.from_row`, assert all fields including `visibility_status`, `youtube_url`, and `deleted_at` are correct.
- Test `is_published` returns `True` when `visibility_status = "published"` on a 28-column row.
- Regression test: 27-column and 26-column rows still parse correctly.

---

### 3. H6: `run_worker` Called with Result Instead of Callable

**Severity:** HIGH — background sync completely non-functional
**File:** `src/stream_of_worship/app/app.py:146`

#### Problem

```python
self.run_worker(do_sync(), exclusive=True)
```

`do_sync()` is called immediately (returning `None`), so:
1. Sync runs synchronously at the call site, freezing the UI.
2. `run_worker` receives `None` instead of a callable → no background worker created.

#### Implementation Steps

**Step 1: Fix the `action_sync_catalog` method.**

In `src/stream_of_worship/app/app.py`, line 146:

```python
# BEFORE:
self.run_worker(do_sync(), exclusive=True)

# AFTER:
self.run_worker(do_sync, thread=True, exclusive=True)
```

The `thread=True` parameter is required because `do_sync` is a synchronous blocking function (it calls `self.sync_service.execute_sync()` which performs network I/O). Without `thread=True`, Textual would try to run it on the event loop, which would block.

**Step 2: Also fix `_sync_in_background` on line 118.**

The `on_mount` method calls:
```python
self.run_worker(self._sync_in_background(), exclusive=True)
```

This has the same bug — `_sync_in_background()` is called immediately (an async function call returns a coroutine object, which `run_worker` may interpret differently). Additionally, `_sync_in_background` itself calls `self.sync_service.execute_sync()` synchronously, which blocks the event loop (see H5).

The fix for `on_mount` is to call the method without invoking it:
```python
# BEFORE:
self.run_worker(self._sync_in_background(), exclusive=True)

# AFTER:
self.run_worker(self._sync_in_background, exclusive=True)
```

But since `_sync_in_background` is `async` and calls blocking code, it needs a different approach (see H5 below). As an interim fix for P0, the minimal change is:

```python
self.run_worker(self._sync_in_background, exclusive=True)
```

This at least makes the worker start correctly. H5 will properly address the blocking issue.

#### Verification

- Manual test: launch the app with Turso configured, press `S` to trigger sync. Verify the UI remains responsive during sync (cursor blinks, key events processed).
- Check app logs for "Background sync completed" message.

---

## P1 — Fix Before Production / Upgrade Path

### 4. H2: No ALTER TABLE Migration for `deleted_at` Column

**Severity:** HIGH — runtime crash on upgrade
**Files:** `src/stream_of_worship/admin/db/client.py`, `src/stream_of_worship/app/db/read_client.py`, `src/stream_of_worship/admin/commands/db.py`

#### Problem

`deleted_at` is only in `CREATE TABLE IF NOT EXISTS`, which is a no-op when the table already exists. Existing databases never get the column, causing `no such column: deleted_at` on every query that filters by it.

The admin `DatabaseClient.initialize_schema()` already has a pattern for ALTER TABLE migrations (see lines 198–215 for `youtube_url` and `visibility_status`), but it's missing the `deleted_at` migration.

#### Implementation Steps

**Step 1: Add `deleted_at` migration to `DatabaseClient.initialize_schema()`.**

In `src/stream_of_worship/admin/db/client.py`, inside `initialize_schema()` after the existing migrations (after line 215):

```python
# Migration: add deleted_at column to songs if it doesn't exist
try:
    cursor.execute("ALTER TABLE songs ADD COLUMN deleted_at TIMESTAMP")
except sqlite3.OperationalError:
    pass

# Migration: add deleted_at column to recordings if it doesn't exist
try:
    cursor.execute("ALTER TABLE recordings ADD COLUMN deleted_at TIMESTAMP")
except sqlite3.OperationalError:
    pass
```

This follows the existing idempotent migration pattern in the codebase.

**Step 2: Add migration to `ReadOnlyClient.connection` initialization.**

In `src/stream_of_worship/app/db/read_client.py`, the `connection` property lazily creates a connection. After the connection is established, run the migration.

Add a method `_migrate_schema` to `ReadOnlyClient`:

```python
def _migrate_schema(self) -> None:
    """Run schema migrations for the read-only catalog replica."""
    cursor = self.connection.cursor()
    for table in ("songs", "recordings"):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP")
        except Exception:
            pass  # Column already exists or connection doesn't support ALTER
```

Call this in the `connection` property after establishing the connection:

```python
@property
def connection(self):
    if self._connection is None:
        # ... existing connection setup ...
        self._migrate_schema()
    return self._connection
```

**Important:** For libSQL connections (Turso replica), ALTER TABLE is executed locally on the replica file. Since this is a local-only DDL change, it doesn't require write access to the remote. The column definition already exists in the remote schema (applied by admin bootstrap), so the local replica just needs the column added for compatibility.

**Step 3: Add migration to `turso-bootstrap`.**

In `src/stream_of_worship/admin/commands/db.py`, inside the `turso_bootstrap` command, after schema creation (line 463), add the same ALTER TABLE statements:

```python
# Run migrations (idempotent)
for table in ("songs", "recordings"):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP")
    except Exception:
        pass
```

**Step 4: Add `deleted_at` index migrations.**

The schema defines indexes on `deleted_at`:
```sql
CREATE INDEX IF NOT EXISTS idx_songs_deleted_at ON songs(deleted_at);
CREATE INDEX IF NOT EXISTS idx_recordings_deleted_at ON recordings(deleted_at);
```

These are already in `CREATE_INDEXES` and are created by `initialize_schema()` via `CREATE INDEX IF NOT EXISTS`. After Step 1 adds the column, these indexes will succeed. No additional work needed.

#### Verification

- Test: start with a V1 database (no `deleted_at` column), call `DatabaseClient.initialize_schema()`, then run a query with `WHERE deleted_at IS NULL` — should not crash.
- Test: same for `ReadOnlyClient` with a V1 replica file.
- Test: `turso-bootstrap` with an existing remote that lacks `deleted_at`.

---

### 5. H4: Redundant sqlite3 Connection While libSQL Replica Is Open

**Severity:** HIGH — database locking / corruption risk
**File:** `src/stream_of_worship/app/app.py:445` (from PR #41)

#### Problem

The review references a redundant `sqlite3.connect(config.db_path)` connection in `app.py`. Looking at the current code, there is no such line in the current `app.py`. The `SowApp.__init__` creates only:
- `ReadOnlyClient` (which manages its own libSQL connection)
- `SongsetClient` (which connects to `songsets_db_path`, a different file)

**Current assessment:** The redundant connection may have been removed in a prior commit, or the PR #41 review was based on an earlier version. If no redundant `sqlite3.connect(config.db_path)` exists in the current code, this issue is already resolved.

#### Implementation Steps

**Step 1: Verify the issue exists.**

Search `app.py` and all app-level code for any `sqlite3.connect` that references the catalog database path (`config.db_path`). Also search for any code that creates a secondary connection to the same file the `ReadOnlyClient` uses.

**Step 2: If found, remove the redundant connection.**

Replace all direct `sqlite3.connect` calls to the catalog database with reads through `self.read_client.connection`.

**Step 3: If any operation specifically requires a `sqlite3.Connection` (e.g., `backup()`), open it only for the duration of that operation.**

```python
def backup_catalog(self, backup_path: Path) -> None:
    backup_conn = sqlite3.connect(backup_path)
    try:
        self.read_client.connection.backup(backup_conn)
    finally:
        backup_conn.close()
```

#### Verification

- Search the entire `app/` directory for `sqlite3.connect` calls that reference the catalog DB path.
- Ensure no test creates two connections to the same SQLite file simultaneously.

---

### 6. H5: `_sync_in_background` Blocks the Textual Event Loop

**Severity:** HIGH — UI freeze during sync
**File:** `src/stream_of_worship/app/app.py:123-131`

#### Problem

`_sync_in_background` is `async` but calls `self.sync_service.execute_sync()` which is synchronous and blocking (network I/O, file I/O). Even if the worker starts correctly (after H6 fix), the blocking call freezes the Textual event loop.

#### Implementation Steps

**Step 1: Convert `_sync_in_background` to a synchronous function.**

Since it will run on a thread (via `run_worker(..., thread=True)`), it should not be `async`:

```python
def _sync_in_background(self) -> None:
    """Run sync in background thread with error handling."""
    try:
        result = self.sync_service.execute_sync()
        logger.info(f"Background sync completed: {result.message}")
    except Exception as e:
        logger.warning(f"Background sync failed: {e}")
```

**Step 2: Update `on_mount` to run the sync worker on a thread.**

```python
def on_mount(self) -> None:
    if self.config.sync_on_startup and self.config.is_turso_configured:
        self.run_worker(self._sync_in_background, thread=True, exclusive=True)
    self.navigate_to(AppScreen.SONGSET_LIST)
```

**Step 3: Update `action_sync_catalog` similarly.**

```python
def action_sync_catalog(self) -> None:
    if not self.config.is_turso_configured:
        self.notify("Turso sync not configured", severity="warning")
        return

    def do_sync():
        try:
            result = self.sync_service.execute_sync()
            return result
        except Exception as e:
            return e

    def on_sync_done(result):
        if isinstance(result, Exception):
            self.notify(f"Sync failed: {result}", severity="error")
        else:
            self.notify(f"Sync completed: {result.message}")

    self.run_worker(do_sync, thread=True, exclusive=True, callback=on_sync_done)
```

**Note:** Textual's `run_worker` with `thread=True` runs the callable in a separate thread and can report results via `Worker` events. The exact callback mechanism depends on the Textual version. At minimum, use `self.call_from_thread` to safely update UI from the worker thread:

```python
def do_sync():
    try:
        result = self.sync_service.execute_sync()
        self.call_from_thread(self.notify, f"Sync completed: {result.message}")
    except Exception as e:
        self.call_from_thread(self.notify, f"Sync failed: {e}", severity="error")
```

#### Verification

- Launch the app, trigger sync. Verify the UI remains responsive (can navigate, type) during the sync operation.
- Verify sync completes successfully and a notification appears.

---

### 7. H1 + M3: Add `deleted_at` to Song and Recording Models; Fix Orphan Detection

**Severity:** HIGH (H1) + MEDIUM (M3)
**Files:** `src/stream_of_worship/admin/db/models.py`, `src/stream_of_worship/app/services/catalog.py`

#### Problem

Neither `Song` nor `Recording` has a `deleted_at` attribute. `from_row()` discards the column. This makes it impossible to check deletion status through the model layer, causing soft-deleted items to appear as fully active in the UI (H1).

#### Implementation Steps

**Step 1: Add `deleted_at` field to `Song` dataclass.**

In `src/stream_of_worship/admin/db/models.py`:

```python
@dataclass
class Song:
    id: str
    title: str
    source_url: str
    scraped_at: str
    title_pinyin: Optional[str] = None
    composer: Optional[str] = None
    lyricist: Optional[str] = None
    album_name: Optional[str] = None
    album_series: Optional[str] = None
    musical_key: Optional[str] = None
    lyrics_raw: Optional[str] = None
    lyrics_lines: Optional[str] = None
    sections: Optional[str] = None
    table_row_number: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None       # <-- NEW
```

**Step 2: Update `Song.from_row()` to read `deleted_at` from index 16.**

```python
@classmethod
def from_row(cls, row: tuple) -> "Song":
    row_len = len(row)
    return cls(
        id=row[0],
        title=row[1],
        title_pinyin=row[2],
        composer=row[3],
        lyricist=row[4],
        album_name=row[5],
        album_series=row[6],
        musical_key=row[7],
        lyrics_raw=row[8],
        lyrics_lines=row[9],
        sections=row[10],
        source_url=row[11],
        table_row_number=row[12],
        scraped_at=row[13],
        created_at=row[14],
        updated_at=row[15],
        deleted_at=row[16] if row_len > 16 else None,
    )
```

**Step 3: Update `Song.to_dict()` to include `deleted_at`.**

```python
def to_dict(self) -> dict[str, Any]:
    return {
        # ... existing fields ...
        "updated_at": self.updated_at,
        "deleted_at": self.deleted_at,
    }
```

**Step 4: Add `deleted_at` field to `Recording` dataclass.**

```python
@dataclass
class Recording:
    # ... existing fields ...
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None       # <-- NEW
```

**Step 5: Update `Recording.from_row()` to handle `deleted_at` in all schema versions.**

This is integrated into the C2 fix (item 2 above). For the 28-column case:
```python
if row_len == 28:
    deleted_at = row[27]
```

For 27- and 26-column cases, `deleted_at = None`.

**Step 6: Update `Recording.to_dict()` to include `deleted_at`.**

**Step 7: Fix `is_orphan` in `SongsetItemWithDetails` (H1).**

In `src/stream_of_worship/app/services/catalog.py`:

```python
@dataclass
class SongsetItemWithDetails:
    item: SongsetItem
    song: Optional[Song] = None
    recording: Optional[Recording] = None

    @property
    def is_orphan(self) -> bool:
        """Check if this item is orphaned (missing or soft-deleted reference)."""
        if self.song is None or self.recording is None:
            return True
        if self.song.deleted_at is not None:
            return True
        if self.recording.deleted_at is not None:
            return True
        return False

    @property
    def display_title(self) -> str:
        """Get the title to display."""
        if self.song:
            if self.song.deleted_at is not None:
                return f"Removed: {self.song.title}"
            return self.song.title
        return "Unknown"
```

This implements the spec: "Missing references shown as 'Removed: <title>'".

#### Verification

- Test `Song.from_row` with a 17-element tuple: `deleted_at` should be populated.
- Test `Song.from_row` with a 16-element tuple (legacy): `deleted_at` should be `None`.
- Test `Recording.from_row` with 28-element tuple: all fields including `deleted_at` correct.
- Test `SongsetItemWithDetails.is_orphan` with a soft-deleted recording: returns `True`.
- Test `display_title` with soft-deleted song: returns "Removed: <title>".

---

### 8. H3: `import_songset` Bypasses SongsetClient API

**Severity:** HIGH — integrity bypass + transaction safety
**File:** `src/stream_of_worship/app/services/songset_io.py:151-243`
**Sources:** This review + PR #41

#### Problem

`import_songset` directly executes raw SQL instead of using `SongsetClient.create_songset()` and `SongsetClient.add_item()`. This bypasses:
- Recording validation (`validate_recording_exists`)
- Position auto-assignment
- Transaction safety (no rollback on partial import)
- Future business logic in `add_item()`

#### Implementation Steps

**Step 1: Add `id` parameter to `SongsetClient.create_songset()`.**

In `src/stream_of_worship/app/db/songset_client.py`:

```python
def create_songset(
    self,
    name: str,
    description: Optional[str] = None,
    id: Optional[str] = None,
) -> Songset:
    """Create a new songset.

    Args:
        name: Display name for the songset
        description: Optional description
        id: Optional ID to use (for import); generated if None

    Returns:
        Created Songset instance
    """
    songset = Songset(
        id=id or Songset.generate_id(),
        name=name,
        description=description,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    # ... rest unchanged ...
```

**Step 2: Refactor `import_songset` to use `SongsetClient` methods.**

In `src/stream_of_worship/app/services/songset_io.py`, replace the raw SQL block (lines 180–243) with:

```python
# Create songset via SongsetClient (preserves imported ID)
songset = self.songset_client.create_songset(
    name=songset_data["name"],
    description=songset_data.get("description"),
    id=songset_data["id"],
)

# Import items via SongsetClient.add_item()
imported_count = 0
orphaned_count = 0
warnings = []

for item_data in items_data:
    recording_hash = item_data.get("recording_hash_prefix")

    # Validate recording exists
    if recording_hash and self.get_recording:
        recording = self.get_recording(recording_hash)
        if not recording:
            warnings.append(f"Recording not found: {recording_hash}, importing as orphan")
            orphaned_count += 1

    try:
        self.songset_client.add_item(
            songset_id=songset.id,
            song_id=item_data["song_id"],
            recording_hash_prefix=recording_hash,
            position=item_data["position"],
            gap_beats=item_data.get("gap_beats", 2.0),
            get_recording=self.get_recording,
        )
        imported_count += 1
    except MissingReferenceError as e:
        warnings.append(str(e))
        orphaned_count += 1
```

**Transaction safety:** Each `add_item()` call uses `self.songset_client.transaction()`, which provides per-item atomicity. The `create_songset()` call is also transactional. If any `add_item()` fails, the songset exists but is partially populated — this is the same behavior as the current code, but now with validation and proper error handling.

**Step 3: Handle `on_conflict="replace"` with SongsetClient.**

The current code deletes the existing songset and recreates it. With the refactored approach:

```python
if on_conflict == "replace":
    existing = self.songset_client.get_songset(songset_data["id"])
    if existing:
        self.songset_client.delete_songset(existing.id)
```

This is already handled before the `create_songset` call (lines 154–169). No change needed here.

**Step 4: Remove the `import sqlite3` and direct SQL usage from `import_songset`.**

Remove lines 181–243 (the raw SQL block) and the `import sqlite3` statement.

**Step 5: Add `crossfade_enabled`, `crossfade_duration_seconds`, `key_shift_semitones`, `tempo_ratio` to `add_item()`.**

Currently, `add_item()` does not support these fields. Add optional parameters:

```python
def add_item(
    self,
    songset_id: str,
    song_id: str,
    recording_hash_prefix: Optional[str] = None,
    position: Optional[int] = None,
    gap_beats: float = 2.0,
    crossfade_enabled: bool = False,
    crossfade_duration_seconds: Optional[float] = None,
    key_shift_semitones: int = 0,
    tempo_ratio: float = 1.0,
    get_recording: Optional[Callable[[str], Optional]] = None,
) -> SongsetItem:
```

Update the INSERT statement in `add_item()` to include the new columns.

#### Verification

- Import a songset JSON file with valid recordings → all items imported with validation.
- Import a songset JSON with a missing recording → `MissingReferenceError` raised, item skipped with warning.
- Import with `on_conflict="replace"` → existing songset deleted and recreated.
- Verify transaction safety: if `add_item()` fails mid-import, the songset still exists with the items that succeeded (graceful degradation, not rollback of everything).

---

## P2 — Fix Before Relying on Affected Features

### 9. M1: `snapshot_db` Uses File-Copy on a Live SQLite Database

**Severity:** MEDIUM — corrupt backup under concurrent writes
**File:** `src/stream_of_worship/app/db/songset_client.py:288`
**Sources:** This review + PR #41 — independently flagged by both

#### Problem

`shutil.copy2()` copies the database file at the filesystem level while SQLite may have in-flight WAL pages. The resulting backup may fail integrity checks or lose the last committed transaction.

#### Implementation Steps

**Step 1: Replace `shutil.copy2` with SQLite backup API.**

In `src/stream_of_worship/app/db/songset_client.py`, modify `snapshot_db()`:

```python
def snapshot_db(self, retention: int = 5) -> Path:
    if not self.db_path.exists():
        raise FileNotFoundError(f"Database not found: {self.db_path}")

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = self.db_path.parent / f"{self.db_path.name}.bak-{timestamp}"

    # Use SQLite backup API for consistent snapshot
    backup_conn = sqlite3.connect(str(backup_path))
    try:
        self.connection.backup(backup_conn)
    finally:
        backup_conn.close()

    # Prune old backups (unchanged)
    backup_pattern = f"{self.db_path.name}.bak-*"
    backups = sorted(
        self.db_path.parent.glob(backup_pattern),
        key=lambda p: p.stat().st_mtime,
    )
    while len(backups) > retention:
        oldest = backups.pop(0)
        oldest.unlink()

    return backup_path
```

**Important:** `sqlite3.Connection.backup()` is available in Python 3.7+. It safely copies the database content, handling WAL pages and concurrent writes correctly.

**Step 2: Remove `import shutil` from songset_client.py.**

The `shutil` import is only used by `snapshot_db`. After replacing with the backup API, remove it.

#### Verification

- Create a songset, add items, call `snapshot_db()`. Open the backup file with `sqlite3` and run `PRAGMA integrity_check` — should return `ok`.
- Test under concurrent writes: start a write transaction in another connection, then call `snapshot_db()` — backup should be consistent (contains pre-transaction state).

---

### 10. M6 + M7: Use `executemany` for Bootstrap Seeding and Migration UPDATEs

**Severity:** MEDIUM — unacceptable performance for large catalogs
**Files:** `src/stream_of_worship/admin/commands/db.py` (M6), `src/stream_of_worship/admin/commands/migrate.py` (M7)
**Source:** PR #41

#### Problem

Both the bootstrap seeding loop and the migration UPDATE loop execute individual INSERT/UPDATE statements per row. For large catalogs (thousands of rows), this is orders of magnitude slower than `executemany`.

#### Implementation Steps — M6 (Bootstrap Seeding)

**Step 1: Replace the per-row INSERT loop with `executemany`.**

In `src/stream_of_worship/admin/commands/db.py`, inside `turso_bootstrap`, replace the seeding loops:

```python
# BEFORE (per-row):
for song in songs:
    columns = ", ".join(song.keys())
    placeholders = ", ".join(["?" for _ in song.keys()])
    cursor.execute(
        f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})",
        tuple(song),
    )

# AFTER (batched):
if songs:
    columns = ", ".join(songs[0].keys())
    placeholders = ", ".join(["?" for _ in songs[0].keys()])
    sql = f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})"
    cursor.executemany(sql, [tuple(song) for song in songs])
```

Apply the same pattern for `recordings` and `sync_metadata` loops.

#### Implementation Steps — M7 (Migration UPDATEs)

**Step 1: Replace the per-row UPDATE loop with `executemany`.**

In `src/stream_of_worship/admin/commands/migrate.py`, replace:

```python
# BEFORE:
for old_id, new_id in id_map.items():
    cursor.execute(
        "UPDATE recordings SET song_id = ? WHERE song_id = ?",
        (new_id, old_id),
    )

# AFTER:
cursor.executemany(
    "UPDATE recordings SET song_id = ? WHERE song_id = ?",
    [(new_id, old_id) for old_id, new_id in id_map.items()],
)
```

Apply the same pattern for the `songset_items` UPDATE loop and the `songs.id` UPDATE loop.

**Note:** The `executemany` approach removes the ability to show per-row progress via `progress.advance(task)`. Consider updating the progress bar to show table-level progress instead, or use a chunked approach:

```python
items = list(id_map.items())
chunk_size = 100
for i in range(0, len(items), chunk_size):
    chunk = items[i:i + chunk_size]
    cursor.executemany(
        "UPDATE recordings SET song_id = ? WHERE song_id = ?",
        [(new_id, old_id) for old_id, new_id in chunk],
    )
    progress.advance(task, advance=len(chunk))
```

#### Verification

- Benchmark: seed a database with 1000+ songs using the old per-row approach and the new `executemany` approach. Verify the new approach is significantly faster (expect 10x+ improvement).
- Verify all data is correctly inserted/updated by comparing row counts and spot-checking content.

---

### 11. M4: No User-Side Songset ID Migration Path

**Severity:** MEDIUM — orphaned songset items for all existing users
**File:** `src/stream_of_worship/admin/commands/migrate.py:180-181`

#### Problem

`migrate-song-ids` updates admin's `songsets.db` but not user-side databases. The warning message offers no tooling for users to perform this migration.

#### Implementation Steps

**Step 1: Create a `migrate-songset-ids` command in the app CLI.**

Add a new command to the app's CLI (wherever app commands are registered). The logic mirrors the admin's `migrate-song-ids` but operates on the user's songsets.db:

```python
@app.command("migrate-songset-ids")
def migrate_songset_ids(
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
) -> None:
    """Migrate songset_items.song_id references to new content-hash format.

    This should be run once after upgrading to V2, after the catalog
    has been synced (so new song IDs are available in the local replica).
    """
    songset_client = SongsetClient(config.songsets_db_path)
    read_client = ReadOnlyClient(config.db_path, ...)

    # Build old->new ID map from the catalog
    cursor = read_client.connection.cursor()
    cursor.execute("SELECT id, title, composer, lyricist FROM songs")
    songs = cursor.fetchall()

    id_map = {}
    for old_id, title, composer, lyricist in songs:
        new_id = _compute_new_song_id(title, composer, lyricist)
        if old_id != new_id:
            id_map[old_id] = new_id

    if not id_map:
        console.print("[green]No migration needed.[/green]")
        return

    # Update songset_items
    with songset_client.transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "UPDATE songset_items SET song_id = ? WHERE song_id = ?",
            [(new_id, old_id) for old_id, new_id in id_map.items()],
        )

    console.print(f"[green]Migrated {len(id_map)} song ID references.[/green]")
```

**Step 2: Add a first-sync migration hook as a fallback.**

In `AppSyncService.execute_sync()`, after the sync completes, check if any songset items reference old-format IDs and migrate them:

```python
def _migrate_songset_ids_if_needed(self) -> int:
    """Migrate songset_items.song_id references if needed.

    Returns the number of migrated references.
    """
    # Check if there are any unmigrated items
    # ... detection logic ...
    pass
```

This is a softer approach that automatically fixes orphaned items when the user syncs for the first time after upgrading.

**Step 3: Share the `_compute_new_song_id` function.**

The ID computation logic in `migrate.py` should be importable from a shared location. Move it from `src/stream_of_worship/admin/commands/migrate.py` to a shared module (e.g., `src/stream_of_worship/admin/db/id_utils.py`).

#### Verification

- Create a songsets.db with old-format IDs, run the migration command, verify songset_items.song_id is updated.
- Run the command again — should be a no-op (idempotent).
- Test with `--dry-run`.

---

### 12. M2: SongsetItem Export Includes Always-Null Joined Fields

**Severity:** MEDIUM — bloated/confusing exports
**File:** `src/stream_of_worship/app/db/models.py:174-201`

#### Problem

`SongsetItem.to_dict()` exports 8 joined fields that are always `None` when using `get_items_raw()` (the path used by export). The JSON format shouldn't include these fields.

#### Implementation Steps

**Step 1: Add `include_joined` parameter to `to_dict()`.**

In `src/stream_of_worship/app/db/models.py`:

```python
def to_dict(self, include_joined: bool = False) -> dict[str, Any]:
    """Convert SongsetItem to dictionary.

    Args:
        include_joined: Whether to include joined fields (song_title, etc.)
    """
    base = {
        "id": self.id,
        "songset_id": self.songset_id,
        "song_id": self.song_id,
        "recording_hash_prefix": self.recording_hash_prefix,
        "position": self.position,
        "gap_beats": self.gap_beats,
        "crossfade_enabled": self.crossfade_enabled,
        "crossfade_duration_seconds": self.crossfade_duration_seconds,
        "key_shift_semitones": self.key_shift_semitones,
        "tempo_ratio": self.tempo_ratio,
        "created_at": self.created_at,
    }

    if include_joined:
        base.update({
            "song_title": self.song_title,
            "song_key": self.song_key,
            "duration_seconds": self.duration_seconds,
            "tempo_bpm": self.tempo_bpm,
            "recording_key": self.recording_key,
            "loudness_db": self.loudness_db,
            "song_composer": self.song_composer,
            "song_lyricist": self.song_lyricist,
            "song_album_name": self.song_album_name,
        })

    return base
```

**Step 2: Update `export_songset` to use `include_joined=False` (default).**

In `src/stream_of_worship/app/services/songset_io.py`, line 82:

```python
"items": [item.to_dict(include_joined=False) for item in items],
```

This is already the default, but making it explicit documents the intent.

**Step 3: Update any other callers of `to_dict()` that need joined fields.**

Search the codebase for `.to_dict()` calls on `SongsetItem` and verify they either use the default (`include_joined=False`) or explicitly set `include_joined=True` if they need joined data.

#### Verification

- Export a songset to JSON, inspect the output — joined fields should not be present.
- Import the exported file back — should succeed without errors.

---

## Implementation Order and Dependencies

```
P0-1 (C1: JOIN offset) ──── depends on ──► P1-7 (M3: deleted_at on Song)
                                                      │
P0-2 (C2: Recording 28-col) ─ depends on ──► P1-7 (M3: deleted_at on Recording)
                                                      │
P0-3 (H6: run_worker callable) ── independent         │
                                                       │
P1-4 (H2: ALTER TABLE migration) ── independent        │
                                                       │
P1-5 (H4: redundant sqlite3 conn) ── independent       │
                                                       │
P1-6 (H5: blocking sync) ── depends on ──► P0-3 (H6) │
                                                       │
P1-7 (H1+M3: deleted_at models + orphan)              │
      │                                               │
P1-8 (H3: import_songset) ── depends on ──► P1-7 (need id param in create_songset)
                                                       │
P2-9 (M1: backup API) ── independent                   │
                                                       │
P2-10 (M6+M7: executemany) ── independent              │
                                                       │
P2-11 (M4: user migration) ── depends on ──► shared _compute_new_song_id
                                                       │
P2-12 (M2: export null fields) ── independent
```

### Recommended PR Structure

**PR 1 — P0 + M3 (models):** Items 1, 2, 7 (C1, C2, H1+M3)
- These are tightly coupled: C1 needs M3's `Song.from_row` to handle 17 columns, C2 needs M3's `Recording.deleted_at` field.
- This is the smallest PR that makes the app non-corrupting.

**PR 2 — P0 + P1 sync fixes:** Items 3, 6 (H6, H5)
- H5 depends on H6 being fixed first. Both are in `app.py`.

**PR 3 — P1 migration + DB safety:** Items 4, 5 (H2, H4)
- Schema migration and connection cleanup.

**PR 4 — P1 import refactor:** Item 8 (H3)
- Depends on `create_songset(id=...)` from PR 1's SongsetClient changes.

**PR 5 — P2 fixes:** Items 9, 10, 11, 12 (M1, M6+M7, M4, M2)
- These are lower priority and can be batched.

### Testing Strategy per PR

| PR | Required Tests |
|----|----------------|
| PR 1 | `Song.from_row` 16/17 col, `Recording.from_row` 25-28 col, JOIN offset, `is_orphan` with soft-deleted, `display_title` with soft-deleted |
| PR 2 | Sync runs in thread, UI responsive during sync, `run_worker` receives callable not result |
| PR 3 | V1→V2 upgrade migration, `deleted_at` column present after migration, no redundant connections |
| PR 4 | Import with valid/invalid recordings, `create_songset(id=...)` preserves ID, transaction rollback on partial import |
| PR 5 | Backup integrity check, `executemany` benchmark, user-side songset ID migration, export without null joined fields |
