# Fix AdminShutdown DB Connection Error in LRC Editor

## Problem

The LRC editor TUI crashes with `AdminShutdown: terminating connection due to administrator command`
when PostgreSQL terminates an idle connection. The cursor becomes `[BAD]` and any subsequent
`cursor.execute()` raises the error.

**Error path:**
1. `upload.py:152` → `check_active_lrc_job()` calls `db_client.get_recording_by_hash()`
2. `client.py:527` → `cursor.execute()` on a stale connection → `AdminShutdown`

**Root cause:** `DatabaseClient` (admin) has **no retry/reconnection logic** for stale connections,
unlike the app-side clients (`ReadOnlyClient`, `SongsetClient`) which already implement
`_execute_with_retry()`.

## Existing Pattern (app-side)

`ReadOnlyClient` and `SongsetClient` in `src/stream_of_worship/app/db/` both use:

```python
def _execute_with_retry(self, fn):
    try:
        return fn(self.connection)
    except psycopg.OperationalError:
        self.connection_provider.invalidate()
        return fn(self.connection)
```

`ConnectionProvider.invalidate()` forces reconnection on the next `get_connection()` call.

## Fix

### 1. Add `_execute_with_retry()` to `DatabaseClient`

**File:** `src/stream_of_worship/admin/db/client.py`

Add method mirroring the app-side pattern:

```python
def _execute_with_retry(self, fn):
    try:
        return fn(self.connection)
    except psycopg.OperationalError:
        self.connection_provider.invalidate()
        return fn(self.connection)
```

### 2. Wrap `get_recording_by_hash()` with retry

**File:** `src/stream_of_worship/admin/db/client.py` (line 508)

Refactor the cursor operations into a lambda passed to `_execute_with_retry`:

```python
def get_recording_by_hash(self, hash_prefix: str, include_deleted: bool = False) -> Optional[Recording]:
    def _query(conn):
        cursor = conn.cursor()
        if include_deleted:
            cursor.execute("SELECT * FROM recordings WHERE hash_prefix = %s", (hash_prefix,))
        else:
            cursor.execute("SELECT * FROM recordings WHERE hash_prefix = %s AND deleted_at IS NULL", (hash_prefix,))
        row = cursor.fetchone()
        return Recording.from_row(tuple(row)) if row else None

    return self._execute_with_retry(_query)
```

### 3. Wrap `update_recording_lrc()` with retry

**File:** `src/stream_of_worship/admin/db/client.py`

The current implementation uses `self.transaction()` which calls `self.connection` internally.
Inside `_execute_with_retry`, the lambda receives a connection from the retry logic — after
`invalidate()`, `self.transaction()` would fetch a *different* (stale) connection. To avoid
this mismatch, the lambda must use `conn.transaction()` directly on the connection passed to it,
not `self.transaction()`.

```python
def update_recording_lrc(self, hash_prefix: str, r2_lrc_url: str) -> None:
    def _query(conn):
        with conn.transaction():
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings SET
                    r2_lrc_url = %s,
                    lrc_status = 'completed',
                    visibility_status = COALESCE(visibility_status, 'published'),
                    updated_at = NOW()
                WHERE hash_prefix = %s
                """,
                (r2_lrc_url, hash_prefix),
            )

    self._execute_with_retry(_query)
```

**Why not `self.transaction()`?** `self.transaction()` calls `self.connection` internally,
which returns the provider's cached connection. After `invalidate()`, the retry's second call
to `fn(self.connection)` triggers `get_connection()` which returns a fresh connection — but
if the lambda calls `self.transaction()`, it fetches *another* connection from the provider,
bypassing the one `_execute_with_retry` intended. Using `conn.transaction()` on the passed
connection avoids this race.

**Idempotency:** The UPDATE is idempotent — retrying after a stale connection is safe because
the same values are written and `COALESCE(visibility_status, 'published')` is stable across
retries (if the first attempt committed, visibility_status is already `'published'`).

### 4. Graceful degradation in `check_active_lrc_job()`

**File:** `src/stream_of_worship/admin/editor/upload.py` (line 142)

Add `try/except OperationalError` so the editor doesn't crash if DB is unreachable:

```python
def check_active_lrc_job(db_client: DatabaseClient, hash_prefix: str) -> Tuple[bool, str]:
    try:
        recording = db_client.get_recording_by_hash(hash_prefix)
        if recording and recording.lrc_status == "processing" and recording.lrc_job_id:
            return True, recording.lrc_job_id
    except psycopg.OperationalError:
        logger.warning("DB unreachable while checking active LRC job; skipping check")
    return False, ""
```

This is safe: returning `(False, "")` means "no active job blocking upload" — the ETag
stale-session check in `check_transcribed_changed()` still protects against conflicts.

## Scope

Minimal — only the two `DatabaseClient` methods used by the editor upload flow:
- `get_recording_by_hash()` (used in `check_active_lrc_job` and `action_insert_canonical`)
- `update_recording_lrc()` (used in `upload_revised_lrc`)

The broader refactor (all ~58 cursor operations in `DatabaseClient`) can be done separately.

## Files Modified

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/db/client.py` | Add `_execute_with_retry()`; wrap `get_recording_by_hash()` and `update_recording_lrc()` |
| `src/stream_of_worship/admin/editor/upload.py` | Add `try/except OperationalError` in `check_active_lrc_job()` |

## Risk

- `AdminShutdown` is a subclass of `OperationalError`, so catching `OperationalError` covers it.
- The `invalidate()` + retry pattern is already proven in the app-side clients.
- For `check_active_lrc_job`, returning `(False, "")` on DB failure is safe — the ETag check still protects against conflicts.
- `update_recording_lrc()` must use `conn.transaction()` (not `self.transaction()`) inside the `_execute_with_retry` lambda to ensure the retry uses the fresh connection after `invalidate()`.
- The UPDATE in `update_recording_lrc()` is idempotent, so a retry after a stale connection does not risk data corruption.
- **Edge case:** if DB is unreachable and there *is* an active LRC generation job, `check_active_lrc_job()` returns `(False, "")` and the upload proceeds. The job could later overwrite the user's uploaded LRC. This is low probability — the ETag check provides R2-level conflict protection, and the same risk exists if the DB query simply times out.
