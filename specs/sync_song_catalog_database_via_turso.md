# Turso DB Sync for Catalog Metadata

## Context

Today the metadata database (`sow.db`) is a local SQLite file at `~/.config/sow-admin/db/sow.db`, shared on a single machine between the admin CLI (`sow-admin`, sole writer of `songs`/`recordings`) and the user TUI (`sow-app`, read-only over `songs`/`recordings`, read-write over `songsets`/`songset_items`). To distribute the catalog to non-admin machines, we want the admin to publish `songs`/`recordings` to a master Turso database; user installs maintain a local libSQL **embedded replica** that they can read fully offline, refreshed on demand from Turso.

The libSQL/Turso scaffolding is already partially in place on the admin side: `DatabaseClient` (`src/stream_of_worship/admin/db/client.py:88-141`) transparently switches to `libsql.connect(sync_url=..., auth_token=...)` and exposes `.sync()`; `SyncService` (`src/stream_of_worship/admin/services/sync.py`) wraps validation/status/execute; `pyproject.toml` has a `turso = ["libsql>=0.1.0"]` extra; the `sync_metadata` table already records `last_sync_at`/`sync_version`/`local_device_id`. Gaps: (a) the user app's `ReadOnlyClient` and `SongsetClient` use plain `sqlite3` and have no replica awareness; (b) `songsets`/`songset_items` are user-writable but currently live in the same SQLite file as admin-writable tables and have hard FK references into them — incompatible with treating `sow.db` as a managed replica; (c) no Turso DB has been created yet, so initial provisioning + seed are part of scope.

## Decisions (confirmed with user)

1. **Storage split**: `sow.db` becomes the Turso embedded replica (admin-writable in cloud, read-only locally). User-writable songsets move to a separate local-only `~/.config/sow-app/songsets.db`. SQL FK constraints from `songset_items` into `songs`/`recordings` are dropped; referential integrity is validated in app code.
2. **Credential model**: Two shared tokens. Admin keeps a read-write token in `SOW_TURSO_TOKEN`. A read-only token is distributed with the user app (config or env). Matches today's shared-secret pattern (R2, analysis API key).
3. **Sync triggers**: User app syncs (a) at startup in the background — UI shows existing replica data immediately, refreshes when complete; (b) via a manual command (TUI keybind + `sow-app sync` CLI). No periodic timer. No on-demand-around-reads.
4. **Greenfield**: No Turso DB exists yet. Plan includes provisioning, schema bootstrap, and an initial seed from the admin's existing local `sow.db`.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Admin's machine (one user, RW)                                     │
│  ┌──────────────┐    libsql.connect(sync_url, RW token)              │
│  │  sow-admin   │────────────────────────────────────────┐            │
│  └──────────────┘                                        │            │
│  Local file: ~/.config/sow-admin/db/sow.db (embedded replica)        │
└────────────────────────────────────────┬─────────────────┼────────────┘
                                         │ conn.sync()     │
                                         ▼                 ▼
                               ┌──────────────────────────────────┐
                               │  Turso master DB                 │
                               │  libsql://sow-catalog.turso.io   │
                               │  Tables: songs, recordings,      │
                               │          sync_metadata           │
                               └──────────────────┬───────────────┘
                                                  │ conn.sync() (RO token)
                                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  User's machine (any number of users, RO over catalog)               │
│  ┌──────────────┐  libsql.connect(sync_url, RO token)                │
│  │  sow-app     │  Local replica: ~/.config/sow-app/db/sow.db        │
│  │              │     songs, recordings, sync_metadata (read-only)   │
│  │              │  Local-only: ~/.config/sow-app/db/songsets.db      │
│  │              │     songsets, songset_items (RW)                   │
│  └──────────────┘                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

R2 access (audio/stems/LRC) remains orthogonal and unchanged: both admin and user apps already authenticate to R2 with shared keys.

## Detailed Changes

### 1. Schema split

**Catalog DB (`sow.db`, replica)** — `src/stream_of_worship/admin/db/schema.py`. No structural change. Stays the source of truth for `songs`, `recordings`, `sync_metadata`. Migrations stay inline ALTERs in `DatabaseClient.initialize_schema` (executed only on the admin side; replicas pick them up automatically).

**Songsets DB (`songsets.db`, local-only)** — `src/stream_of_worship/app/db/schema.py`:
- Drop `REFERENCES songs(id)` and `REFERENCES recordings(hash_prefix)` from `songset_items` (lines 23-24). Keep `REFERENCES songsets(id) ON DELETE CASCADE` (intra-DB).
- Add a small note in the schema file explaining cross-DB references are validated in app code.

### 2. App config — `src/stream_of_worship/app/config.py`

Add fields:
- `db_path` — path to local replica (default `~/.config/sow-app/db/sow.db`). **Decouple from `AdminConfig.db_path`** (which today is inherited at line 180-183). User app must not assume admin is on the same machine.
- `songsets_db_path` — local-only (default `~/.config/sow-app/db/songsets.db`).
- `turso_database_url` — `libsql://...` (required for sync; if absent, app runs against whatever is already on disk).
- `turso_readonly_token` — RO token. Read from `[turso] readonly_token` in TOML or env var `SOW_TURSO_READONLY_TOKEN`. Never the RW token.
- `sync_on_startup: bool = True`.

`AppConfig.load_from_file` writes a starter config with placeholder Turso values when the file is missing.

### 3. User app DB clients

**`src/stream_of_worship/app/db/read_client.py` — `ReadOnlyClient`**
- Mirror the libsql branching from `admin/db/client.py:88-116`: when `turso_database_url` and `libsql` are present, use `libsql.connect(local_path, sync_url=..., auth_token=ro_token)`; otherwise fall back to plain `sqlite3.connect(...)` (useful for offline/dev; and required when `libsql` extra not installed).
- Add a `sync()` method that calls `conn.sync()` on libsql connections; no-op on plain sqlite3.
- Existing read methods unchanged.

**`src/stream_of_worship/app/db/songset_client.py` — `SongsetClient`**
- No libsql change — songsets stay plain `sqlite3` against the new local-only `songsets.db`.
- `initialize_schema` now bootstraps an independent file. Caller (`app.py:60`) passes `config.songsets_db_path`.

**Cross-DB join replacement** — `src/stream_of_worship/app/db/songset_client.py` and `app/services/catalog.py`:
- The current `SONGSET_ITEMS_DETAIL_QUERY` (`app/db/schema.py:76-103`) JOINs `songs` and `recordings`. After the split, that JOIN no longer works (different DB files).
- Replace with a two-step lookup in `CatalogService` (or a thin façade): fetch songset items from `SongsetClient`, then resolve `song_id` → song / `recording_hash_prefix` → recording via the existing `ReadOnlyClient` methods (`get_song`, `get_recording_by_hash`). Build a typed result list in Python.
- This is contained: `SONGSET_ITEMS_DETAIL_QUERY` is the only cross-DB JOIN in the codebase (per Phase 1 exploration).

**Referential validation on writes** — `app/db/songset_client.py`:
- Before `add_item`/`update_item`: pass a `ReadOnlyClient` and verify `get_song(song_id)` / `get_recording_by_hash(hash_prefix)` exist; raise a typed `MissingReferenceError` if not. Surfaced at the screen layer (`app/screens/songset_editor.py`).

### 4. User app sync service — new `src/stream_of_worship/app/services/sync.py`

A trimmed analogue of `admin/services/sync.py`:
- `class AppSyncService` with `validate_config()`, `get_sync_status()`, `execute_sync()` — same shape, but operates against `ReadOnlyClient` and uses the **read-only** token.
- `execute_sync()` calls `read_client.sync()` and updates a local-side last-sync timestamp (kept in a tiny key/value file under `~/.config/sow-app/`, since the replica's `sync_metadata` is server-authoritative — we shouldn't write to it from a read replica).
- Errors: typed exceptions for "Turso not configured", "network error", "auth error". Caller decides whether to surface or swallow (background sync should swallow + log; manual sync should surface).

### 5. App startup wiring — `src/stream_of_worship/app/app.py` and `src/stream_of_worship/app/main.py`

- `app.py:60`: instantiate `ReadOnlyClient(config.db_path, turso_url=..., turso_token=...)` and `SongsetClient(config.songsets_db_path)`.
- New `_sync_in_background()` task kicked off in the Textual `on_mount` if `config.sync_on_startup and turso_configured`. Uses `asyncio.create_task` (Textual workers); on failure, log + post a non-blocking notification ("Catalog sync failed; using cached copy").
- `main.py`: a new `sow-app sync` subcommand that runs `AppSyncService.execute_sync()` synchronously and prints a result, for users who want to refresh without launching the TUI.
- TUI keybind: bind `s` (or another free key) to a "Sync catalog now" action that calls `AppSyncService.execute_sync()` in a worker; toast on completion.

### 6. Admin-side bootstrap — new commands

**`sow-admin db turso-bootstrap`** (new `commands/db.py` subcommand):
- Preconditions: `turso.database_url` set in admin config, `SOW_TURSO_TOKEN` set (RW token), `libsql` installed, local `sow.db` exists with content.
- Steps (idempotent):
  1. Connect to Turso via libsql with RW token and local replica path. (libsql will auto-create remote tables from a fresh `initialize_schema()` call.)
  2. Run `DatabaseClient.initialize_schema()` — creates tables/indexes/triggers on the master.
  3. If `--seed` flag is passed and remote is empty, copy all rows from a local-only `sqlite3` connection on the same file into the libsql connection. Use chunked `INSERT OR REPLACE`.
  4. Call `conn.sync()` to push.
- Document the manual prereqs (`turso db create sow-catalog`, `turso db tokens create ... --read-only`, etc.) in the admin README.

**`sow-admin db tokens` (helper)** — optional convenience that prints the `turso db tokens create` commands the admin needs to run.

### 7. Dependency / packaging — `pyproject.toml`

- Move `libsql` from a separate `turso` extra into the `app` extra (currently lines 99-124) so user installs get embedded replicas by default. Keep it in `admin` extra too. Drop the standalone `turso` extra (or keep as alias).
- No change to `sow_analysis` package — `jobs.db` stays plain SQLite inside the analysis service container.

### 8. Documentation

- Update `specs/sow_admin_design.md` (existing) to note songsets-DB split and user-side sync flow.
- New `specs/turso_db_sync.md` (this spec, copied/adapted from this plan).
- Update root `README.md` "Turso client" TODO (line ~991) to point at the new user-side implementation.
- Admin README: add Turso provisioning runbook (`turso db create`, token generation, `db turso-bootstrap`).

## Files to Modify / Create

Modify:
- `src/stream_of_worship/app/db/schema.py` — drop FKs, add comment
- `src/stream_of_worship/app/db/read_client.py` — libsql branching, `sync()` method
- `src/stream_of_worship/app/db/songset_client.py` — accept ReadOnlyClient for ref validation; remove cross-DB JOIN reliance
- `src/stream_of_worship/app/services/catalog.py` — replace cross-DB JOIN with two-step lookup
- `src/stream_of_worship/app/screens/songset_editor.py`, `songset_list.py`, `browse.py`, `transition_detail.py` — surface `MissingReferenceError` cleanly
- `src/stream_of_worship/app/config.py` — new fields, decouple db_path from AdminConfig
- `src/stream_of_worship/app/app.py` — instantiate clients with new paths, kick off background sync
- `src/stream_of_worship/app/main.py` — add `sync` CLI subcommand; relax `_check_database` to handle "replica empty, sync at startup" case
- `src/stream_of_worship/admin/commands/db.py` — add `turso-bootstrap` subcommand
- `pyproject.toml` — `libsql` into `app` extra
- `README.md`, `src/stream_of_worship/admin/README.md` — runbook + status updates
- `specs/sow_admin_design.md` — note split and user-side flow

Create:
- `src/stream_of_worship/app/services/sync.py` — `AppSyncService`
- `specs/turso_db_sync.md` — the user-facing spec (this document, polished)
- (Optional) `tests/app/db/test_read_client_libsql.py`, `tests/app/services/test_sync.py`, `tests/app/services/test_catalog_cross_db.py`

Migration data action (one-time, manual, documented in spec): admin runs `sow-admin db turso-bootstrap --seed`. Existing user installs need a one-shot `sow-app migrate-songsets` (or documented manual copy) that copies any pre-split `songsets`/`songset_items` rows out of the shared `sow.db` into the new `songsets.db` — only relevant for the developer seat that has data already; users haven't been distributed the app yet.

## Reused functions / utilities

- `DatabaseClient.connection`/`.sync()` libsql branching — `src/stream_of_worship/admin/db/client.py:88-141` is the template for `ReadOnlyClient`.
- `SyncService` shape — `src/stream_of_worship/admin/services/sync.py:69-251` is the template for `AppSyncService` (validate/status/execute).
- `ReadOnlyClient.get_song`, `get_recording_by_hash` — reused for cross-DB ref resolution.
- `Songset`, `SongsetItem` dataclasses (`src/stream_of_worship/app/db/models.py`) — unchanged.

## Verification

End-to-end:
1. **Provisioning**: `turso db create sow-catalog`; create RW + RO tokens; `sow-admin db turso-bootstrap --seed` → confirm `turso db shell sow-catalog "SELECT COUNT(*) FROM songs;"` matches local count.
2. **Admin write propagation**: `sow-admin catalog scrape ...` → `sow-admin db sync` → `turso db shell` shows the new row.
3. **User pull**: on a clean machine (or wiped `~/.config/sow-app/`), set `SOW_TURSO_READONLY_TOKEN` + `turso_database_url` → `sow-app sync` → `~/.config/sow-app/db/sow.db` exists and `sow-app` browse screen shows the catalog.
4. **Songsets isolated**: create a songset in `sow-app`, verify rows land in `songsets.db` (not `sow.db`); run `sow-app sync` again, confirm songsets are untouched.
5. **Cross-DB ref validation**: manually delete a song in admin, sync user replica, attempt to add that song to a songset → expect `MissingReferenceError` surfaced as a TUI toast.
6. **Offline fallback**: disable network, launch `sow-app` → background sync fails silently, UI still works against cached replica.
7. **RO token enforcement**: try to `INSERT` from a libsql connection opened with the RO token → expect server-side rejection (Turso enforces).
8. **Tests**: `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_sync.py tests/app/db/test_read_client_libsql.py tests/app/services/test_catalog_cross_db.py -v`.

## Open items (non-blocking)

- Versioning of the catalog schema — when admin migrates schema, embedded replicas pick it up automatically, but compiled user binaries with stale code may break. Use `sync_metadata.sync_version` as a soft compat check at app startup (warn if user app's expected version < replica's `sync_version`).
- Backup policy for songsets — `songsets.db` is local-only; document a simple `sow-app songsets export` for users (out of scope for this spec).
