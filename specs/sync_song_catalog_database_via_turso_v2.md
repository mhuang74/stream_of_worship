# Turso DB Sync for Catalog Metadata — V2

> Supersedes `sync_song_catalog_database_via_turso.md`. V2 adds song-ID stability, soft-delete tombstones, songset export/import, and pre-sync snapshots — all of which V1 left as accepted risk or out-of-scope.

## Context

Today the metadata database (`sow.db`) is a local SQLite file at `~/.config/sow-admin/db/sow.db`, shared on a single machine between the admin CLI (`sow-admin`, sole writer of `songs`/`recordings`) and the user TUI (`sow-app`, read-only over `songs`/`recordings`, read-write over `songsets`/`songset_items`). To distribute the catalog to non-admin machines, we will publish `songs`/`recordings` to a Turso master DB; user installs maintain a libSQL **embedded replica** that they can read fully offline, refreshed on demand.

V1 of this plan did the replica plumbing but left an integrity hole: `songs.id` is computed from the scrape-time row index (`pinyin(title) + "_" + row_num`, see `src/stream_of_worship/admin/services/scraper.py:288-314`), so a sop.org row insert/delete/reorder cascades new IDs to every song downstream of the change. After the cross-DB FK is dropped, this would silently break user songsets on every meaningful re-scrape. V2 fixes this at the source, adds tombstones for the hard-delete case, and gives users a real recovery path (export/import + auto-snapshot).

## Decisions (confirmed with user)

1. **Song ID is content-derived and stable.** `songs.id = <pinyin_slug>_<8-hex-hash>` where the hash is `sha256(NFKC(title) + "|" + NFKC(composer) + "|" + NFKC(lyricist))[:8]`. Re-scrapes preserve IDs unless one of those three fields actually changes. The slug keeps IDs human-readable for `sow-admin audio view-lrc <song_id>` and TUI-log copy/paste.
2. **Songsets reference recordings as the canonical anchor.** `songset_items.recording_hash_prefix` (SHA-256 of audio bytes, already stable — `admin/services/hasher.py:11-45`) is the source of truth. `songset_items.song_id` is kept as a denormalized display hint for sort/group operations, but is allowed to be stale; display walks `recording_hash_prefix → recordings.song_id → songs` for canonical metadata. Per the UX rule, items can only be added to a songset once a recording exists.
3. **Catalog deletes are soft.** Add `deleted_at` to `songs` and `recordings`. The admin `delete_recording` path (`admin/db/client.py:787-806`) and any future `delete_song` mark the row deleted instead of `DELETE FROM`. The user UI shows orphan items as "Removed: <title>" using the still-present row data.
4. **Songsets are user-exportable and auto-snapshotted.** New `sow-app songsets export <id> | export-all | import <file>` CLI subcommands serialize to JSON. Each `sow-app sync` first copies `songsets.db` to a rotated `.bak-<timestamp>` file (last N kept).
5. **Storage split** (unchanged from V1): `sow.db` becomes the Turso embedded replica (admin-writable in cloud, read-only locally). User-writable songsets move to `~/.config/sow-app/db/songsets.db`. SQL FK constraints from `songset_items` into `songs`/`recordings` are dropped; integrity is validated in app code on writes.
6. **Credential model** (unchanged): two shared tokens. Admin keeps a RW token in `SOW_TURSO_TOKEN`. RO token distributed with the user app.
7. **Sync triggers** (unchanged): user app syncs at startup (background) and via manual TUI keybind + `sow-app sync` CLI. No periodic timer.
8. **Turso free-plan capacity is sufficient.** Verified by analysis: ~0.5% of free quota at worst-case projected usage (300 users × 5 syncs/day × ~100 rows/sync). No code change required.
9. **Greenfield Turso provisioning** (unchanged): plan includes `turso db create`, schema bootstrap, initial seed.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Admin's machine (one user, RW)                                      │
│  ┌──────────────┐    libsql.connect(sync_url, RW token)              │
│  │  sow-admin   │────────────────────────────────────────┐           │
│  └──────────────┘                                        │           │
│  Local file: ~/.config/sow-admin/db/sow.db (embedded replica)        │
└────────────────────────────────────────┬─────────────────┼───────────┘
                                         │ conn.sync()     │
                                         ▼                 ▼
                               ┌──────────────────────────────────┐
                               │  Turso master DB                 │
                               │  libsql://sow-catalog.turso.io   │
                               │  Tables: songs (with deleted_at),│
                               │   recordings (with deleted_at),  │
                               │   sync_metadata                  │
                               └──────────────────┬───────────────┘
                                                  │ conn.sync() (RO token)
                                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  User's machine                                                      │
│  ┌──────────────┐  libsql.connect(sync_url, RO token)                │
│  │  sow-app     │  Local replica: ~/.config/sow-app/db/sow.db        │
│  │              │  Local-only:    ~/.config/sow-app/db/songsets.db   │
│  │              │   + songsets.db.bak-<timestamp> (last N)           │
│  └──────────────┘  JSON export:   ~/Documents/sow-songsets/*.json    │
└──────────────────────────────────────────────────────────────────────┘
```

R2 access (audio/stems/LRC) remains orthogonal and unchanged.

## Detailed Changes

### 1. Catalog schema — stable IDs and tombstones

**`src/stream_of_worship/admin/db/schema.py`** (modify):

- `songs.id` semantics change (column type stays `TEXT PRIMARY KEY`). New format: `<slug>_<hash8>`.
- Add `songs.deleted_at TIMESTAMP NULL` (default NULL).
- Add `recordings.deleted_at TIMESTAMP NULL`.
- Adjust `SONG_LIST_QUERY` and similar to filter `WHERE deleted_at IS NULL` for normal listing; admin tools that need to see soft-deleted rows use a separate query.
- `sync_version` bumped to `"2"` so the user-side soft compat check at `app/main.py` can warn if the user binary is too old to understand `deleted_at`.

**`src/stream_of_worship/admin/services/scraper.py`** (modify):

- Replace `_normalize_song_id(title, row_num)` (lines 288-314) with `_compute_song_id(title, composer, lyricist)`:
  ```python
  def _compute_song_id(self, title, composer, lyricist):
      norm = lambda s: unicodedata.normalize("NFKC", (s or "").strip())
      slug = re.sub(r"[^a-z0-9_]", "", "_".join(lazy_pinyin(norm(title))).lower())
      payload = f"{norm(title)}|{norm(composer)}|{norm(lyricist)}"
      digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
      song_id = f"{slug}_{digest}"
      if len(song_id) > 100:
          song_id = f"{slug[:91]}_{digest}"
      return song_id
  ```
- `table_row_number` column stays (useful debugging field), but is no longer the ID's stability anchor.
- Fix incremental-mode bug at lines 109-126: with stable IDs the existing `if song.id in existing_ids` check now correctly de-dupes across re-scrapes (previously duplicated when row numbers shifted).
- After a full-table scrape, detect songs in `existing_ids` that were NOT seen this run; mark `deleted_at = now()` on the admin DB. Subject to a `--no-soft-delete` opt-out for partial/`--limit` scrapes.

**`src/stream_of_worship/admin/db/client.py`** (modify):

- `delete_recording` (lines 787-806): change `DELETE FROM recordings` to `UPDATE recordings SET deleted_at = ?`. Drop the `songset_items.recording_hash_prefix` UPDATE block (lines 798-802) — that fixup was for the pre-split shared DB; soft-delete makes it unnecessary.
- New `soft_delete_song(song_id)` method.
- `insert_song` (lines 296-331): on `INSERT OR REPLACE`, clear `deleted_at` (resurrects a previously-deleted song that returns to the catalog).

**Migration script** — new `src/stream_of_worship/admin/commands/migrate.py` `migrate-song-ids` subcommand:

1. Build old→new ID map: `SELECT id, title, composer, lyricist FROM songs` → recompute new ID via `_compute_song_id`.
2. Update `recordings.song_id` from old → new.
3. Update `songset_items.song_id` in the admin's own `songsets.db`; document the manual equivalent for installed users.
4. Update `songs.id` in place (recordings remapped first to avoid FK violation).
5. Idempotent — recomputing is a no-op if rows are already on the new format.

### 2. Songsets schema — display hint + recording-anchored

**`src/stream_of_worship/app/db/schema.py`** (modify):

- Drop FK clauses on `songset_items.song_id` and `songset_items.recording_hash_prefix` (lines 23-24). Keep `REFERENCES songsets(id) ON DELETE CASCADE` (intra-DB).
- Keep both `song_id` and `recording_hash_prefix` columns. `recording_hash_prefix` is canonical; `song_id` is a denormalized hint that may go stale post-sync but is updated when the user re-edits the item.
- Replace `SONGSET_ITEMS_DETAIL_QUERY` (lines 76-103): becomes a simple "songset items only" query; the JOIN moves to Python (see §3).

### 3. App-side data access

**`src/stream_of_worship/app/db/read_client.py` — `ReadOnlyClient`** (modify):

- Mirror libsql branching from `admin/db/client.py:88-116`: when `turso_database_url` and `libsql` are present, use `libsql.connect(local_path, sync_url=..., auth_token=ro_token)`; otherwise fall back to plain `sqlite3.connect(...)` for offline/dev.
- New `sync()` method: calls `conn.sync()` on libsql; no-op on plain sqlite3. **Does NOT call `update_sync_metadata`** — RO token rejects writes; tracking is local-side only (see §4).
- Read methods filter `WHERE deleted_at IS NULL` by default. New `get_song_including_deleted(song_id)` and `get_recording_by_hash(hash, including_deleted=False)` overloads for orphan-display.

**`src/stream_of_worship/app/db/songset_client.py` — `SongsetClient`** (modify):

- Plain `sqlite3` against new local-only `songsets.db`. `initialize_schema` bootstraps an independent file; caller passes `config.songsets_db_path`.
- `add_item`/`update_item`: validate `recording_hash_prefix` exists in catalog (via injected `ReadOnlyClient.get_recording_by_hash`); derive `song_id` from `recordings.song_id` when not supplied. Raise typed `MissingReferenceError` on validation failure.
- New `snapshot_db(retention=5)`: copies `songsets.db` → `songsets.db.bak-<ISO8601>`, then prunes oldest beyond retention. Called from `AppSyncService.execute_sync()` before `read_client.sync()`.

**`src/stream_of_worship/app/services/catalog.py`** (modify):

- New `get_songset_with_items(songset_id)` — replaces the cross-DB JOIN with a two-step Python lookup:
  1. `songset_client.get_items_raw(songset_id)` → list of `SongsetItem` (no song/recording fields).
  2. For each item: `read_client.get_recording_by_hash(item.recording_hash_prefix, including_deleted=True)` then `read_client.get_song_including_deleted(recording.song_id)`. Recording-or-song missing → `is_orphan=True` with display title from `songs.title` (soft-deleted row) or "Unknown".
- This is the only cross-DB JOIN site in the codebase.

**`src/stream_of_worship/app/screens/songset_editor.py`, `songset_list.py`, `browse.py`, `transition_detail.py`** (modify):

- Render `is_orphan` items as "Removed: <title>" in muted style; disable export/play actions for those items.
- Surface `MissingReferenceError` on add/edit as a TUI toast.
- Fix count inconsistency: both the list-screen count and editor row count reflect non-orphan items, with an "(N removed)" tail when orphans exist.

### 4. App config and sync service

**`src/stream_of_worship/app/config.py`** (modify):

Add fields (decouple `db_path` from `AdminConfig`):
- `db_path` — default `~/.config/sow-app/db/sow.db`
- `songsets_db_path` — default `~/.config/sow-app/db/songsets.db`
- `songsets_backup_retention: int = 5`
- `songsets_export_dir` — default `~/Documents/sow-songsets/`
- `turso_database_url`
- `turso_readonly_token` — from `[turso] readonly_token` TOML or `SOW_TURSO_READONLY_TOKEN` env
- `sync_on_startup: bool = True`

`AppConfig.load_from_file` writes a starter TOML with placeholder Turso values when the file is missing.

**New `src/stream_of_worship/app/services/sync.py` — `AppSyncService`**:

Trimmed analogue of `admin/services/sync.py:69-251`:
- `validate_config()`, `get_sync_status()`, `execute_sync()`.
- `execute_sync()` order:
  1. `songset_client.snapshot_db(retention=config.songsets_backup_retention)`.
  2. `read_client.sync()`.
  3. Update local-side last-sync timestamp at `~/.config/sow-app/last_sync.json` (never write to the replica's `sync_metadata`).
- Typed exceptions: `TursoNotConfiguredError`, `SyncNetworkError`, `SyncAuthError`. Background sync swallows + logs; manual sync surfaces.

### 5. Songset export / import

**New `src/stream_of_worship/app/services/songset_io.py`**:

- `export_songset(songset_id, path)` — writes JSON: `{songset: {...}, items: [{song_id, recording_hash_prefix, position, transition_overrides, ...}]}`. Uses `Songset.to_dict()` / `SongsetItem.to_dict()` (`app/db/models.py:48-60, 174-201`) — wire them up; they currently have no callers.
- `export_all(dir_path)` — bulk dump, one file per songset.
- `import_songset(path, on_conflict="rename"|"replace"|"skip")` — validate every `recording_hash_prefix` against `ReadOnlyClient`; items with missing references import as orphan rows with a warning.

**`src/stream_of_worship/app/main.py`** (modify):

New CLI subcommands:
- `sow-app songsets export <id> [-o file]`
- `sow-app songsets export-all [-o dir]`
- `sow-app songsets import <file> [--on-conflict ...]`
- `sow-app sync` (from V1)

### 6. App startup wiring

**`src/stream_of_worship/app/app.py`** (modify):

- Line 60: instantiate `ReadOnlyClient(config.db_path, turso_url=..., turso_token=ro_token)` and `SongsetClient(config.songsets_db_path)`. Fix the existing bug where `config.db_path` is currently passed to both clients.
- New `_sync_in_background()` task in Textual `on_mount` if `config.sync_on_startup and turso_configured`. On failure: log + non-blocking toast ("Catalog sync failed; using cached copy").
- TUI keybind: bind `S` (capital — lowercase `s` is taken by Settings at line 41) to "Sync catalog now"; toast on completion.

### 7. Admin-side bootstrap

**`src/stream_of_worship/admin/commands/db.py`** (modify) — new `turso-bootstrap` subcommand:

Preconditions: `turso.database_url` + `SOW_TURSO_TOKEN` (RW), `libsql` installed, local `sow.db` exists.

Steps (idempotent):
1. Connect to Turso via libsql with RW token + local replica path.
2. Run `DatabaseClient.initialize_schema()` — creates tables/indexes/triggers on the master.
3. If `--seed` flag passed and remote is empty, copy all rows from a local `sqlite3` connection in chunks via `INSERT OR REPLACE`.
4. Call `conn.sync()`.

Document manual prereqs (`turso db create sow-catalog`, `turso db tokens create ... --read-only`) in admin README.

Optional: `sow-admin db tokens` helper that prints the `turso db tokens create` commands.

### 8. Dependency / packaging

**`pyproject.toml`** (modify):

- Move `libsql` from the standalone `turso` extra into the `app` extra (lines 99-124) so user installs get embedded replicas by default. Keep it in `admin` extra. Drop the standalone `turso` extra (or keep as alias).

### 9. Documentation

- Update `specs/sow_admin_design.md` — note songsets-DB split, user-side sync flow, soft-delete semantics.
- This file supersedes `specs/sync_song_catalog_database_via_turso.md`.
- Update root `README.md` "Turso client" TODO at line ~991.
- Admin README: Turso provisioning runbook + `migrate-song-ids` runbook.

## Files to Modify / Create

**Modify:**
- `src/stream_of_worship/admin/db/schema.py` — `deleted_at` columns, sync_version bump
- `src/stream_of_worship/admin/db/client.py` — soft-delete in `delete_recording`, new `soft_delete_song`, `insert_song` clears `deleted_at`
- `src/stream_of_worship/admin/services/scraper.py` — new `_compute_song_id`, fix incremental dedup, soft-delete for missing songs
- `src/stream_of_worship/admin/commands/db.py` — new `turso-bootstrap` subcommand
- `src/stream_of_worship/app/db/schema.py` — drop FKs, recording-anchored comment, simplified query
- `src/stream_of_worship/app/db/read_client.py` — libsql branching, `sync()`, deleted-aware reads
- `src/stream_of_worship/app/db/songset_client.py` — recording validation, `MissingReferenceError`, `snapshot_db()`
- `src/stream_of_worship/app/services/catalog.py` — Python-side two-step JOIN replacement
- `src/stream_of_worship/app/screens/songset_editor.py`, `songset_list.py`, `browse.py`, `transition_detail.py` — orphan rendering, count consistency
- `src/stream_of_worship/app/config.py` — new fields, decouple from AdminConfig
- `src/stream_of_worship/app/app.py` — split client paths, background sync, capital `S` keybind
- `src/stream_of_worship/app/main.py` — `sync` + `songsets export|export-all|import` subcommands
- `pyproject.toml` — `libsql` in `app` extra
- `README.md`, `src/stream_of_worship/admin/README.md` — runbooks
- `specs/sow_admin_design.md` — note split, soft-delete, user-side flow

**Create:**
- `src/stream_of_worship/app/services/sync.py` — `AppSyncService` (with pre-sync snapshot)
- `src/stream_of_worship/app/services/songset_io.py` — JSON export/import
- `src/stream_of_worship/admin/commands/migrate.py` — `migrate-song-ids` one-time migration
- `specs/sync_song_catalog_database_via_turso_v2.md` — this spec
- (Optional) `tests/admin/services/test_scraper_id_stability.py`, `tests/app/db/test_read_client_libsql.py`, `tests/app/services/test_sync.py`, `tests/app/services/test_catalog_cross_db.py`, `tests/app/services/test_songset_io.py`

## Reused functions / utilities

- `DatabaseClient.connection`/`.sync()` libsql branching — `src/stream_of_worship/admin/db/client.py:88-141` — template for `ReadOnlyClient`.
- `SyncService` shape — `src/stream_of_worship/admin/services/sync.py:69-251` — template for `AppSyncService`.
- `compute_file_hash` / `get_hash_prefix` — `src/stream_of_worship/admin/services/hasher.py:11-45` — canonical `recording_hash_prefix` source.
- `lazy_pinyin` (already imported in `scraper.py`) — used by new `_compute_song_id`.
- `Songset.to_dict()` / `SongsetItem.to_dict()` — `src/stream_of_worship/app/db/models.py:48-60, 174-201` — wire to new export path; currently no callers.
- `ReadOnlyClient.get_song`, `get_recording_by_hash` — extended with `including_deleted=True` overloads.

## Verification

1. **ID stability under re-scrape.** Fixture sop.org HTML → scrape → record IDs. Insert a row at position 1 (shifts row numbers). Re-scrape. **Expect: all existing IDs unchanged. No duplicate songs.** Test: `tests/admin/services/test_scraper_id_stability.py`.
2. **Title-correction edge case.** Change a title in the fixture. Re-scrape. **Expect: new row with new ID appears; old row remains (admin manually soft-deletes or merges).**
3. **Migration idempotency.** Run `sow-admin db migrate-song-ids` twice. Second run is a no-op.
4. **Provisioning.** `turso db create sow-catalog` → tokens → `sow-admin db turso-bootstrap --seed` → `turso db shell sow-catalog "SELECT COUNT(*) FROM songs;"` matches local count.
5. **Admin write propagation.** `sow-admin catalog scrape ...` → `sow-admin db sync` → `turso db shell` shows the new row.
6. **User pull.** Clean `~/.config/sow-app/`; set env vars; `sow-app sync` → `sow.db` exists; browse screen shows catalog.
7. **Songsets isolated.** Create songset → verify rows land in `songsets.db` not `sow.db`; sync again → songsets untouched.
8. **Soft-delete propagation.** Admin: `delete_recording` → sync user replica → open affected songset → orphan item renders as "Removed: <title>"; export action disabled.
9. **Songset export/import round-trip.** `sow-app songsets export <id> -o /tmp/s.json`; delete songset; `sow-app songsets import /tmp/s.json` → identical content restored.
10. **Pre-sync snapshot.** Run `sow-app sync` → `songsets.db.bak-<timestamp>` exists. Run sync 6× → only 5 backups present (oldest pruned).
11. **Offline fallback.** Disable network → background sync fails silently → UI works against cached replica.
12. **RO token enforcement.** `INSERT` with RO token → server-side rejection.
13. **`sync_metadata` not written from RO side.** Run `sow-app sync`; verify no Turso `sync_metadata` row for the user device.
14. **Tests.** `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_sync.py tests/app/db/test_read_client_libsql.py tests/app/services/test_catalog_cross_db.py tests/app/services/test_songset_io.py tests/admin/services/test_scraper_id_stability.py -v`.

## Open items (non-blocking)

- **Aliases table for title-corrections.** V2 leaves title-correction-induced ID drift to admin manual handling. A `song_aliases(old_id, new_id)` table that auto-resolves in the read path is a follow-up if drift becomes frequent.
- **Schema versioning past `sync_version=2`.** Use `sync_metadata.sync_version` as a soft compat check at app startup (warn if user binary's expected version < replica's `sync_version`). Already in scope for V2.
- **Songsets snapshot location.** V2 keeps backups next to the live DB. A `songsets_backup_dir` config field can relocate them if desired.
- **Audit/repair CLI** (`sow-app songsets audit|repair`). Deferred; revisit if soft-delete + export/import prove insufficient.
