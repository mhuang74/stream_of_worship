# Fix: `save_songs()` Double-Write Conflict with Remote-Write Architecture

**Date:** 2026-05-05
**Bug:** `sow_admin catalog scrape --force` crashes with `ValueError: Hrana: stream not found` after saving 584 songs
**Root Cause:** `scraper.save_songs()` wraps a local `transaction()` around remote-write `insert_song()` calls
**Related:** `specs/remote_write_local_read_impl_plan_v2.md`

---

## Symptoms

```
Saving 584 songs to database...
Replica sync after write failed (non-fatal): Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: cf45df0b:019df7a9-67e9-7a42-9ee8-d83fcfaff7fb"}``
... (repeated 584 times) ...
ValueError: Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: cf45df0b:019df7a9-67e9-7a42-9ee8-d83fcfaff7fb"}``
```

The scrape succeeds in writing all songs to Turso Cloud via the HTTP API, but:
1. Every `_sync_replica()` call after each `insert_song()` fails with "stream not found"
2. The outer `transaction()` context manager calls `conn.commit()` on the local libsql connection, which also crashes
3. The entire transaction is rolled back, and the user sees a fatal error

---

## Root Cause Analysis

### The problematic code (`scraper.py:237-243`)

```python
def save_songs(self, songs):
    with self.db_client.transaction():       # ← local libsql transaction
        for song in songs:
            self.db_client.insert_song(song)  # ← writes remotely via HTTP
```

### Why it fails

The remote-write architecture (`remote_write_local_read_impl_plan_v2.md`) changed `insert_song()` to write to Turso Cloud via the HTTP API when `is_turso_enabled`:

1. `insert_song()` → `_execute_remote(sql, params)` → HTTP POST to Turso (✓ works)
2. `insert_song()` → `_sync_replica(fatal=False)` → `conn.sync()` on local replica (✗ fails — "stream not found")
3. `transaction()` exit → `conn.commit()` on the libsql connection (✗ crashes — local replica state is inconsistent)

The `transaction()` context manager was designed for local sqlite3 writes. When Turso is enabled, the libsql connection is an **embedded replica** — it should only be used for reads and `sync()`, not for `commit()`. Calling `commit()` on a replica that has no local DML but has been synced mid-transaction causes the Hrana stream to become invalid.

### Why `_sync_replica()` fails per-song

Each `insert_song()` call triggers `_sync_replica()` which calls `conn.sync()`. The libsql Python client opens a Hrana stream for sync. After the first sync, the stream becomes stale or is closed by the server. Subsequent syncs on the same connection (especially within a transaction context) hit "stream not found".

### Why `bulk_insert_songs()` already solves this

`bulk_insert_songs()` was added in the Phase 2 implementation specifically for this use case. It:
- Sends all INSERTs as a single HTTP pipeline transaction (`_execute_remote_transaction()`)
- Calls `_sync_replica()` **once** after the entire bulk operation
- Never touches the local `transaction()` context manager

But `scraper.save_songs()` was never updated to use it.

---

## Fix

### File: `src/stream_of_worship/admin/services/scraper.py`

Replace the `save_songs()` method to branch on `is_turso_enabled`:

```python
def save_songs(self, songs: list[Song]) -> int:
    if not self.db_client:
        logger.warning("No database client configured, songs not saved")
        return 0

    if not songs:
        logger.info("No songs to save")
        return 0

    logger.info(f"Saving {len(songs)} songs to database")

    if self.db_client.is_turso_enabled:
        # Remote-write path: use bulk_insert_songs() which sends all inserts
        # as a single HTTP transaction and syncs once. Never use transaction()
        # with Turso — it calls conn.commit() on the embedded replica which
        # conflicts with the HTTP write path.
        try:
            self.db_client.bulk_insert_songs(songs)
            saved_count = len(songs)
        except Exception as e:
            logger.error(f"Bulk insert failed: {e}")
            saved_count = 0
    else:
        # Local sqlite3 path: transaction() + per-song insert is correct
        saved_count = 0
        with self.db_client.transaction():
            for song in songs:
                try:
                    self.db_client.insert_song(song)
                    saved_count += 1
                except Exception as e:
                    logger.warning(f"Failed to save song {song.id}: {e}")

    logger.info(f"Successfully saved {saved_count}/{len(songs)} songs")
    return saved_count
```

**Key change**: When `is_turso_enabled`, use `bulk_insert_songs()` instead of `transaction()` + per-song `insert_song()`. This:
1. Sends all 584 INSERTs as a single HTTP pipeline request with BEGIN/COMMIT
2. Calls `_sync_replica()` once after the bulk operation
3. Never calls `conn.commit()` on the embedded replica
4. Eliminates the 584 per-song sync failures

### Other callers of `transaction()` with remote-write methods

Search the codebase for any other places that wrap remote-write methods (`_execute_remote()`, `_execute_remote_transaction()`, or methods that call them like `insert_song`, `insert_recording`, etc.) inside `transaction()`. The `transaction()` context manager must **never** be used when Turso is enabled because it calls `conn.commit()` on the embedded replica.

Candidates to audit:
- `update_sync_metadata()` at `client.py:392` — uses `transaction()` but `sync_metadata` is local-only (not replicated). This is safe because it only writes locally, no remote write involved. **No change needed.**
- `reset_database()` at `client.py:489` — uses `transaction()` to drop tables. If Turso is enabled, this should use remote DDL. This is a separate bug (not in scope for this fix, but should be tracked).

---

## Performance Benefit

**Before**: 584 HTTP requests (one per song) + 584 sync attempts
**After**: 1 HTTP request (all songs in one pipeline) + 1 sync attempt

This also dramatically improves scrape performance for large catalogs.

---

## Testing

### Manual verification
```bash
# Should complete without any "stream not found" errors
SOW_TURSO_TOKEN=... uv run --extra admin sow-admin catalog scrape --force

# Verify data persisted
uv run --extra admin sow-admin catalog list --limit 5

# Verify local replica in sync
uv run --extra admin sow-admin db pull
```

### Unit test
Add test for `scraper.save_songs()` with a mocked Turso-enabled `db_client` that verifies:
1. `bulk_insert_songs()` is called (not `insert_song()` in a loop)
2. `transaction()` is never called
3. Error handling: if `bulk_insert_songs()` raises, `saved_count` is 0

### Existing tests
Run existing scraper tests to verify local (non-Turso) path still works:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v -k scraper
```
