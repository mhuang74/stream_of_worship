# Implementation Plan: Remote-Write + Local-Read Architecture (v2)

**Date:** 2026-05-05  
**Branch:** reconcile_fixes (current) or new branch  
**Supersedes:** `specs/remote_write_local_read_impl_plan.md`, `specs/fix_walconflict_sync_error_opus.md`, `specs/simplify_turso_sync_publish_update.md`  
**Depends on analysis:** `reports/turso_embedded_replica_analysis_v2_2026-05-05.md`

---

## Context

The admin CLI currently writes to the local libsql embedded replica and syncs bidirectionally via `conn.sync()`. This causes WalConflict errors when local and remote WAL frame histories diverge — specifically triggered by pre-sync `ALTER TABLE` migrations and the bidirectional push.

**Decision**: Route all writes (DML and DDL) from the admin to Turso Cloud via the HTTP API (`/v2/pipeline`). Keep the embedded replica for reads only. The replica's `conn.sync()` becomes pull-only because it has no local DML writes to push, eliminating WalConflict at the root cause.

The app (User App) is largely unchanged — it already only reads, and its read-only token prevents push anyway.

---

## Design Constraints from Review

Five issues identified during design review must be addressed during implementation:

1. **Stale-read danger**: `_sync_replica()` must be fatal for read-then-write flows (reconcile, soft-delete), not just a warning. Non-fatal only for pure-write fire-and-forget operations.

2. **DDL idempotency**: Remote `ALTER TABLE` must suppress "duplicate column name" errors per-statement, matching the behavior of the local `apply_column_migrations()`.

3. **HTTP client consistency**: Use `requests` (already in project deps) not `urllib.request`. Match existing patterns in `analysis.py` and `scraper.py`.

4. **`turso_init` not refactored**: `commands/infra.py` currently writes DDL locally via libsql. It must be migrated to use the same HTTP write path.

5. **App-side `_migrate_schema()` is safe**: The app's `apply_column_migrations()` runs locally on the replica, but with a read-only token the push is rejected. No change needed on app side.

---

## Updated CLI Commands (Phase 7)

| Command | Behavior | Notes |
|---------|----------|-------|
| `db pull` | Pull remote changes to local replica via `_sync_replica()` | New command, replaces `db sync` |
| ~~`db sync`~~ | **Dropped** — was bidirectional, now misleading | Users migrate to `db pull` |
| ~~`db publish`~~ | **Dropped** — writes already go to remote | No-op concept was confusing |
| ~~`db update`~~ | **Dropped** — rarely needed | Manual recovery documented in `db pull --help` |

**Recovery documentation** in `db pull` help:
```
If sync fails due to metadata corruption, manually recover:
  1. Delete local DB: rm <db_path> <db_path>-wal <db-path>-shm <db-path>-info
  2. Re-run: sow-admin db pull
```

---

## Files to Modify

| File | What changes |
|------|-------------|
| `src/stream_of_worship/admin/db/client.py` | Add `_execute_remote()`, `_execute_remote_ddl()`, `_execute_remote_pipeline()`, `_sync_replica()`, refactor all write methods, refactor `sync()`, add `http_pipeline_url` property |
| `src/stream_of_worship/admin/db/schema.py` | Add `apply_column_migrations_remote()` helper function |
| `src/stream_of_worship/admin/services/sync.py` | Update `execute_sync()` to use `_sync_replica()` only (no longer wraps `client.sync()`) |
| `src/stream_of_worship/admin/commands/db.py` | Replace `db sync` with `db pull` command; add recovery documentation |
| `src/stream_of_worship/admin/commands/infra.py` | Refactor `turso-init` to use HTTP API for DDL and seeding |
| `src/stream_of_worship/app/db/read_client.py` | Add auto-recovery in `sync()` for WAL/metadata errors (separate from write refactor) |

**No changes to**: `admin/db/models.py`, `admin/db/schema.py` (COLUMN_MIGRATIONS list itself), `app/services/sync.py`

---

## Implementation Order

1. **Phase 1**: HTTP write infrastructure (new methods, no behavior change yet)
2. **Phase 3.1**: `apply_column_migrations_remote()` in schema.py
3. **Phase 2**: Refactor write methods (core change — enables testing)
4. **Phase 3.2/3.3**: Refactor `sync()` and `initialize_schema()`
5. **Phase 4**: Stale-read protection (reconcile + scrape)
6. **Phase 5**: Refactor `turso-init`
7. **Phase 6**: App-side recovery
8. **Phase 7**: CLI commands (`db pull` only)
9. Tests

Phases 1+3.1 can be done without breaking anything (purely additive). Phase 2 is the breaking change — do in one commit with full test coverage.

---

## Phase 1: HTTP Write Infrastructure in `client.py`

### 1.1 New property: `http_pipeline_url`

Add to `DatabaseClient`:

```python
@property
def http_pipeline_url(self) -> Optional[str]:
    """Derive HTTPS pipeline URL from libsql:// URL."""
    if not self.turso_url:
        return None
    url = self.turso_url.replace("libsql://", "https://")
    if not url.startswith("https://"):
        url = "https://" + url.split("://", 1)[-1]
    return url.rstrip("/") + "/v2/pipeline"
```

### 1.2 New method: `_format_param()`

Add as a module-level private function (not a method, reusable):

```python
def _format_param(value) -> dict:
    """Format a Python value for the Turso HTTP API /v2/pipeline."""
    if value is None:
        return {"type": "null"}
    elif isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    elif isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    elif isinstance(value, float):
        return {"type": "float", "value": str(value)}
    elif isinstance(value, str):
        return {"type": "text", "value": value}
    elif isinstance(value, bytes):
        import base64
        return {"type": "blob", "base64": base64.b64encode(value).decode()}
    else:
        return {"type": "text", "value": str(value)}
```

### 1.3 New method: `_execute_remote_pipeline()`

Core HTTP executor. All other remote write methods delegate here.

```python
def _execute_remote_pipeline(
    self,
    requests_payload: list[dict],
    timeout: int = 30,
) -> list[dict]:
    """Execute a pipeline of SQL statements on Turso Cloud via HTTP API.

    Args:
        requests_payload: List of request dicts for /v2/pipeline.
            Each dict is {"type": "execute", "stmt": {"sql": ..., "args": [...]}}
            or {"type": "close"}.
        timeout: HTTP timeout in seconds.

    Returns:
        List of result dicts from the pipeline response.

    Raises:
        SyncError: If HTTP request fails or any statement returns an error.
    """
    import json
    import requests as http_requests

    url = self.http_pipeline_url
    if not url:
        raise SyncError("Turso not configured: no URL available")

    payload = {"requests": requests_payload}
    try:
        response = http_requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.turso_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
    except http_requests.exceptions.Timeout:
        raise SyncError(
            f"Remote write timed out after {timeout}s. "
            "The write may have succeeded — verify with 'db pull' before retrying."
        )
    except http_requests.exceptions.ConnectionError as e:
        raise SyncError(f"Cannot connect to Turso: {e}")
    except http_requests.exceptions.RequestException as e:
        raise SyncError(f"Remote write failed: {e}")

    results = result.get("results", [])
    return results


def _check_pipeline_results(self, results: list[dict], ignore_sql_errors: set[str] = None) -> None:
    """Check pipeline results for errors, optionally ignoring specific SQL error codes.

    Args:
        results: Results list from _execute_remote_pipeline().
        ignore_sql_errors: Set of SQL error message substrings to suppress (case-insensitive).
            Use for DDL idempotency (e.g., {"duplicate column name", "already exists"}).

    Raises:
        SyncError: If any result is an error that is not in ignore_sql_errors.
    """
    for r in results:
        if r.get("type") == "error":
            error_obj = r.get("error", {})
            msg = error_obj.get("message", "unknown error")
            if ignore_sql_errors:
                msg_lower = msg.lower()
                if any(pattern in msg_lower for pattern in ignore_sql_errors):
                    continue  # Suppress this error (idempotent DDL)
            raise SyncError(f"Remote execute failed: {msg}")
```

### 1.4 New method: `_execute_remote()`

Single-statement write helper:

```python
def _execute_remote(self, sql: str, params: tuple = (), timeout: int = 10) -> dict:
    """Execute a single DML statement on Turso Cloud. Returns result dict."""
    stmt = {"sql": sql, "args": [_format_param(p) for p in params]}
    results = self._execute_remote_pipeline(
        [{"type": "execute", "stmt": stmt}, {"type": "close"}],
        timeout=timeout,
    )
    self._check_pipeline_results(results)
    # Return the execute result if it has column data
    for r in results:
        if r.get("type") == "ok":
            resp = r.get("response", {})
            if resp.get("type") == "execute":
                return resp.get("result", {})
    return {}
```

### 1.5 New method: `_execute_remote_transaction()`

Multi-statement transactional write (for bulk operations):

```python
def _execute_remote_transaction(
    self,
    statements: list[tuple[str, tuple]],
    timeout: int = 30,
) -> None:
    """Execute multiple DML statements in a single remote transaction.

    All statements are sent in one HTTP pipeline request with BEGIN/COMMIT.

    Args:
        statements: List of (sql, params) tuples.
        timeout: HTTP timeout in seconds.
    """
    pipeline = [{"type": "execute", "stmt": {"sql": "BEGIN", "args": []}}]
    for sql, params in statements:
        pipeline.append({
            "type": "execute",
            "stmt": {"sql": sql, "args": [_format_param(p) for p in params]},
        })
    pipeline.append({"type": "execute", "stmt": {"sql": "COMMIT", "args": []}})
    pipeline.append({"type": "close"})

    results = self._execute_remote_pipeline(pipeline, timeout=timeout)
    self._check_pipeline_results(results)
```

### 1.6 New method: `_sync_replica()`

Post-write pull. Behavior depends on context:

```python
def _sync_replica(self, fatal: bool = False) -> None:
    """Pull remote changes to local embedded replica.

    Since no DML writes go to the replica in the new model, conn.sync()
    is effectively pull-only (nothing to push).

    Args:
        fatal: If True, raise SyncError on failure. If False, log warning only.
                Use fatal=True before any operation that reads locally then writes remotely.
    """
    if not self.is_turso_enabled or self._connection is None:
        return
    try:
        self._connection.sync()  # type: ignore
        self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
    except Exception as e:
        if fatal:
            raise SyncError(
                f"Replica sync failed before read-then-write operation: {e}. "
                "Aborting to prevent stale reads. Run 'db pull' to recover.",
                cause=e,
            )
        logger.warning(f"Replica sync after write failed (non-fatal): {e}")
```

---

## Phase 2: Refactor Write Methods

All write methods in `DatabaseClient` must switch from `self.transaction()` (local write) to `_execute_remote()` or `_execute_remote_transaction()` (remote write), followed by `_sync_replica()`.

### 2.1 Pattern for Turso-enabled writes

```python
def insert_song(self, song: Song) -> None:
    if self.is_turso_enabled:
        self._execute_remote(
            """INSERT OR REPLACE INTO songs (
                id, title, title_pinyin, composer, lyricist,
                album_name, album_series, musical_key, lyrics_raw,
                lyrics_lines, sections, source_url, table_row_number,
                scraped_at, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                song.id, song.title, song.title_pinyin, song.composer, song.lyricist,
                song.album_name, song.album_series, song.musical_key, song.lyrics_raw,
                song.lyrics_lines, song.sections, song.source_url, song.table_row_number,
                song.scraped_at,
                song.created_at or datetime.now().isoformat(),
                song.updated_at or datetime.now().isoformat(),
            ),
        )
        self._sync_replica(fatal=False)
    else:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO songs (...)  VALUES (?, ...)""",
                (...),
            )
```

**All write methods follow this pattern**:
- `insert_song()`
- `insert_recording()`
- `update_recording_status()`
- `update_recording_analysis()`
- `update_recording_lrc()`
- `update_recording_download()`
- `update_recording_visibility()`
- `soft_delete_song()` / `soft_delete_recording()`
- `restore_song()` / `restore_recording()`
- `update_sync_metadata()` (keep local — sync_metadata is admin-only, not replicated to app)

### 2.2 `update_recording_status()` — dynamic SET clause

This method builds a dynamic SET clause from non-None args. In the remote path, use the same dynamic approach but build the SQL string for the HTTP API:

```python
def update_recording_status(self, hash_prefix: str, **kwargs) -> None:
    # Filter to only provided (non-None) kwargs
    updates = {k: v for k, v in kwargs.items() if v is not None}
    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = tuple(updates.values()) + (hash_prefix,)
    sql = f"UPDATE recordings SET {set_clause} WHERE hash_prefix = ?"

    if self.is_turso_enabled:
        self._execute_remote(sql, params)
        self._sync_replica(fatal=False)
    else:
        with self.transaction() as conn:
            conn.cursor().execute(sql, params)
```

### 2.3 Bulk insert — scrape flow

The scraper calls `insert_song()` per song in a loop. For Turso, batch these into a single transaction to reduce HTTP round trips:

Add a new method `bulk_insert_songs()` and use it from the scrape command:

```python
def bulk_insert_songs(self, songs: list[Song]) -> None:
    """Insert multiple songs in a single remote transaction."""
    if not songs:
        return
    sql = """INSERT OR REPLACE INTO songs (
        id, title, title_pinyin, composer, lyricist, album_name, album_series,
        musical_key, lyrics_raw, lyrics_lines, sections, source_url,
        table_row_number, scraped_at, created_at, updated_at, deleted_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)"""

    if self.is_turso_enabled:
        statements = [
            (sql, (
                s.id, s.title, s.title_pinyin, s.composer, s.lyricist,
                s.album_name, s.album_series, s.musical_key, s.lyrics_raw,
                s.lyrics_lines, s.sections, s.source_url, s.table_row_number,
                s.scraped_at,
                s.created_at or datetime.now().isoformat(),
                s.updated_at or datetime.now().isoformat(),
            ))
            for s in songs
        ]
        self._execute_remote_transaction(statements)
        self._sync_replica(fatal=False)
    else:
        with self.transaction() as conn:
            conn.cursor().executemany(sql, [...])
```

---

## Phase 3: Refactor Schema Migration — Remote DDL

### 3.1 Add `apply_column_migrations_remote()` to `schema.py`

**CRITICAL**: Make a test HTTP call to verify PRAGMA response format before implementing.

This mirrors `apply_column_migrations()` but sends DDL to Turso via a DatabaseClient instance. Lives in `schema.py` to stay co-located with `COLUMN_MIGRATIONS`.

```python
def apply_column_migrations_remote(client: "DatabaseClient") -> None:
    """Apply COLUMN_MIGRATIONS to the Turso remote via HTTP API.

    Queries remote schema via PRAGMA table_info before issuing ALTER TABLE,
    matching the behavior of the local apply_column_migrations(). Suppresses
    'duplicate column name' errors for safety.

    Args:
        client: DatabaseClient with Turso enabled (has http_pipeline_url).
    """
    # Query remote schema for each table
    tables_needed = {table for table, _, _ in COLUMN_MIGRATIONS}
    existing_columns: dict[str, set[str]] = {}

    for table in tables_needed:
        result = client._execute_remote(f"PRAGMA table_info({table})")
        rows = result.get("rows", [])
        # Each row: [cid, name, type, notnull, dflt_value, pk]
        # NOTE: Verify actual format via test HTTP call - may be objects not arrays
        existing_columns[table] = {row[1] for row in rows if row}  # index 1 = column name

    for table, column, col_type in COLUMN_MIGRATIONS:
        if column not in existing_columns.get(table, set()):
            try:
                client._execute_remote(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except SyncError as e:
                # Suppress "duplicate column" — race condition safety net
                if "duplicate column" in str(e).lower():
                    pass
                else:
                    raise
```

**Note**: `_execute_remote()` for PRAGMA returns the result set; the column name is at index 1 of each row in `result["rows"]`. **Verify the exact HTTP API response structure by making a test call during implementation** (it may use column objects instead of positional arrays).

### 3.2 Refactor `client.sync()` — admin local sync (greatly simplified)

The admin's `sync()` method becomes:

```python
def sync(self) -> None:
    """Pull remote changes to local embedded replica.

    In the remote-write model, this is pull-only: the replica has no local
    DML writes to push, so conn.sync() fetches remote changes without conflict.

    Raises:
        SyncError: If sync fails or schema is invalid after pull.
    """
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")

    # Pull from remote
    try:
        conn = self.connection
        conn.sync()  # type: ignore
    except Exception as e:
        raise SyncError(f"Sync failed: {e}", cause=e)

    # Validate schema matches expected column counts
    cursor = self.connection.cursor()
    self._validate_schema(cursor)

    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
```

**Removed**: pre-sync `apply_column_migrations`, post-sync `apply_column_migrations`, second `conn.sync()` to push columns. None of these are needed when DDL goes to remote directly.

**Kept**: `_validate_schema()` — still useful to detect drift.

### 3.3 Refactor `initialize_schema()` — remote DDL for Turso

```python
def initialize_schema(self) -> None:
    """Initialize database schema. For Turso, sends DDL to remote via HTTP.
    For sqlite3, applies locally.
    """
    if self.is_turso_enabled:
        # Send all DDL to Turso remote
        statements = [(stmt, ()) for stmt in ALL_SCHEMA_STATEMENTS]
        # Use pipeline but suppress "table already exists" errors per statement
        for stmt in ALL_SCHEMA_STATEMENTS:
            try:
                self._execute_remote(stmt)
            except SyncError as e:
                if "already exists" in str(e).lower():
                    pass  # Idempotent
                else:
                    raise
        apply_column_migrations_remote(self)
        # Data migration: set visibility_status for existing LRC-completed recordings
        try:
            self._execute_remote(
                "UPDATE recordings SET visibility_status = 'published' "
                "WHERE lrc_status = 'completed' AND visibility_status IS NULL"
            )
        except SyncError:
            pass  # Column may not exist yet on remote
        self._sync_replica(fatal=False)
    else:
        # Local sqlite3: unchanged
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(CREATE_SONGS_TABLE)
            cursor.execute(CREATE_RECORDINGS_TABLE)
            cursor.execute(CREATE_SYNC_METADATA_TABLE)
            apply_column_migrations(cursor)
            # ... data migration, indexes, triggers ...
```

---

## Phase 4: Stale-Read Protection for Read-Then-Write Flows

### 4.1 `reconcile` flow in `commands/audio.py`

At the start of `--reconcile` processing (before the read phase at line ~1874), add:

```python
# Force sync before reading — reconcile writes to remote based on local reads
if db_client.is_turso_enabled:
    try:
        db_client._sync_replica(fatal=True)
    except SyncError as e:
        console.print(f"[red]Sync failed before reconcile: {e}[/red]")
        console.print("Aborting reconcile to prevent stale reads.")
        raise typer.Exit(1)
```

### 4.2 `catalog scrape` with `soft_delete_missing`

In the scrape command, if `soft_delete_missing=True`, force sync before computing `existing_ids`:

```python
if soft_delete_missing and db_client.is_turso_enabled:
    db_client._sync_replica(fatal=True)
existing_ids = {s.id for s in db_client.list_songs(include_deleted=True)}
```

### 4.3 General principle

Any future code that follows the pattern "read locally → decide → write to remote" must call `db_client._sync_replica(fatal=True)` before the read. Document this in a code comment near `_sync_replica()`.

---

## Phase 5: Refactor `turso-init` in `commands/infra.py`

The `turso-init` command currently uses libsql embedded replica to create schema (local write → push). Refactor to use `DatabaseClient` with the HTTP write path:

```python
@app.command("turso-init")
def turso_init(...):
    # ... config loading and validation unchanged ...

    # Use DatabaseClient instead of raw libsql.connect
    from stream_of_worship.admin.db.client import DatabaseClient

    client = DatabaseClient(
        db_path=config.db_path,
        turso_url=effective_url,
        auth_token=turso_token,
    )

    console.print("[yellow]Creating schema on Turso via HTTP...[/yellow]")
    client.initialize_schema()  # Now sends DDL to remote via HTTP
    console.print("[green]Schema created successfully![/green]")

    if seed:
        # Sync to confirm remote state before seeding
        client._sync_replica(fatal=True)

        cursor = client.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM songs")
        remote_song_count = cursor.fetchone()[0]

        # ... force/confirm logic unchanged ...

        # Seed via HTTP transactions (not local write + push)
        local_conn = sqlite3.connect(config.db_path)
        local_conn.row_factory = sqlite3.Row
        local_cursor = local_conn.cursor()

        local_cursor.execute("SELECT * FROM songs")
        songs_rows = local_cursor.fetchall()
        # Build bulk transaction via _execute_remote_transaction()
        if songs_rows:
            columns = ", ".join(songs_rows[0].keys())
            placeholders = ", ".join("?" * len(songs_rows[0]))
            sql = f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})"
            statements = [(sql, tuple(row)) for row in songs_rows]
            client._execute_remote_transaction(statements)

        # Similarly for recordings, sync_metadata
        client._sync_replica(fatal=False)
        local_conn.close()

    console.print("[bold green]Turso initialization completed![/bold green]")
```

**Key change**: Removes the libsql embedded replica as the write path for init. No more `conn.commit()` + `conn.sync()` to push schema — DDL goes directly to Turso via HTTP.

---

## Phase 6: App-Side Recovery (Read-Only Client)

The app's `ReadOnlyClient` already only reads. Add auto-recovery for WAL/metadata errors in `sync()`:

```python
def sync(self) -> None:
    """Pull catalog from Turso. Auto-recovers from WAL/metadata corruption."""
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")
    try:
        self.connection.sync()  # type: ignore
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ("walconflict", "wal", "metadata", "malformed", "corrupt")):
            # Auto-recovery: delete local DB + sidecars, re-pull from Turso
            self._recover_replica()
            try:
                self.connection.sync()  # type: ignore
            except Exception as e2:
                raise SyncError(f"Sync failed even after recovery: {e2}", cause=e2)
        else:
            raise SyncError(f"Sync failed: {e}", cause=e)

def _recover_replica(self) -> None:
    """Delete local DB and sidecar files to force clean pull from Turso."""
    self.close()  # Close connection before deleting
    for suffix in ("", "-wal", "-shm", "-info"):
        path = self.db_path.with_suffix(self.db_path.suffix + suffix) if suffix else self.db_path
        # Construct sidecar paths correctly
        p = Path(str(self.db_path) + suffix) if suffix else self.db_path
        if p.exists():
            p.unlink()
    # Re-open connection (lazy creation on next access)
```

**Note**: Sidecar paths are `<db>.db-wal`, `<db>.db-shm`, `<db>.db-info` (the suffix appended to the full path, not the stem). Use `Path(str(self.db_path) + "-wal")` etc.

---

## Phase 7: CLI Command Updates (`commands/db.py`)

**Replace `db sync` with `db pull`**:

```python
@app.command("pull")
def pull_db(
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
    force: bool = typer.Option(False, "--force", "-f", help="Force pull even if configuration appears invalid"),
) -> None:
    """Pull remote changes from Turso to local replica.
    
    Synchronizes the local SQLite database with Turso cloud using
    embedded replicas. This is a one-way pull: remote → local.
    
    To recover from corruption:
      1. Delete local DB: rm <db_path> <db_path>-wal <db_path>-shm <db_path>-info
      2. Re-run: sow-admin db pull
    """
    # ... similar to old db sync but simplified ...
    # Calls client._sync_replica() or client.sync() which is now pull-only
```

**No `db publish`, no `db update`, no deprecated `db sync` alias** — keeps CLI surface minimal and unambiguous.

---

## Testing

### Unit tests

1. **`test_execute_remote_pipeline()`**: Mock `requests.post`. Verify correct Authorization header, JSON payload, and error propagation.

2. **`test_format_param()`**: Cover all Python types (None, bool, int, float, str, bytes, other).

3. **`test_execute_remote_ddl_idempotent()`**: Mock HTTP response with "duplicate column name" error. Verify `apply_column_migrations_remote()` continues without raising.

4. **`test_sync_replica_fatal()`**: Mock `conn.sync()` raising. Verify `_sync_replica(fatal=True)` raises `SyncError`, `fatal=False` only logs.

5. **`test_reconcile_syncs_before_read()`**: Verify reconcile path calls `_sync_replica(fatal=True)` before `list_recordings()`.

### Integration tests (require Turso test DB)

1. Insert song via `_execute_remote()` → verify on Turso via HTTP read → verify on local after `_sync_replica()`.
2. `apply_column_migrations_remote()` on DB that already has all columns → no error.
3. `initialize_schema()` on empty Turso DB → verify schema exists → run again (idempotent) → no error.

### Manual verification

```bash
# 1. Run with Turso configured, insert a song
SOW_TURSO_TOKEN=... uv run --extra admin sow-admin catalog scrape --limit 5

# 2. Verify no WalConflict in logs

# 3. Pull and verify local replica has data
uv run --extra admin sow-admin db pull

# 4. Verify app sees the data
uv run --extra app sow-app run  # Check catalog loads

# 5. Run reconcile and verify it syncs before reading
SOW_TURSO_TOKEN=... uv run --extra admin sow-admin audio status --reconcile
```

---

## Open Questions (Resolved)

1. **PRAGMA table_info via HTTP API**: ✅ **Will make test HTTP call during implementation** to verify exact response format before coding `apply_column_migrations_remote()`.

2. **`update_sync_metadata()` — keep local or go remote?**: ✅ **Keep local-only** — sync_metadata is admin-internal and not replicated to app.

3. **`requests` import in `client.py`**: ✅ **Already available** in `admin` extra (confirmed in `pyproject.toml`).

4. **`db publish` command**: ✅ **Dropped** — confusing since writes already go to remote.

5. **`db update` command**: ✅ **Dropped** — rarely needed, manual recovery documented instead.

6. **`db sync` deprecated alias**: ✅ **Dropped** — no alias, just `db pull`.

7. **Recovery logic location**: ✅ **Keep in SyncService** — `_sync_replica()` is thin wrapper, recovery stays in service layer.

---

## Summary of Changes from v1

| Aspect | v1 Spec | v2 Spec (this doc) |
|--------|---------|-------------------|
| CLI commands | `db pull`, `db publish`, `db update`, `db sync` (alias) | `db pull` only |
| `db publish` | No-op + pull | **Dropped** |
| `db update` | Force re-pull | **Dropped** — manual recovery docs only |
| `db sync` | Deprecated alias | **Dropped** — no alias |
| Recovery | In `_sync_replica()` or separate | **Keep in SyncService** |
| PRAGMA verification | Documented concern | **Will test HTTP call first** |
