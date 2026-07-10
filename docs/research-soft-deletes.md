# Soft-Delete Behavior Across Catalog and Songsets

## Overview

Catalog data uses soft deletes for long-lived source entities. User songsets do
not. The current split is:

- `songs` and `recordings` are soft-deleted by setting `deleted_at`.
- `songsets` and `songset_items` have no `deleted_at`; deletes are hard deletes.
- Soft-deleting a catalog row does not clean up existing `songset_items`
  references.

This matters because songsets store `song_id` and `recording_hash_prefix` as
plain values, while render and playback paths later resolve those values back to
catalog rows and R2 objects.

## Entity Matrix

| Entity | Delete model | Storage | Primary delete paths | Restore path |
| --- | --- | --- | --- | --- |
| `songs` | Soft delete | `songs.deleted_at` | `sow-admin catalog delete`; full scraper missing-song pass | `sow-admin catalog restore`; scraper upsert |
| `recordings` | Soft delete plus asset deletion in CLI paths | `recordings.deleted_at` | `sow-admin audio delete`; `sow-admin audio delete --stdin`; `sow-admin audio download --force` | recording upsert; `DatabaseClient.restore_recording()` |
| `songsets` | Hard delete | no `deleted_at` | webapp `deleteSongset()`; TUI `SongsetClient.delete_songset()` | none |
| `songset_items` | Hard delete through parent cascade or item delete | no `deleted_at` | `songsets` cascade; item delete paths | none |
| `render_jobs` | Hard delete through `songsets` cascade | no `deleted_at` | `songsets` cascade | none |
| `songset_share` | Hard delete through `songsets` cascade | no `deleted_at` | `songsets` cascade | none |

## How Soft Deletes Are Stored

The admin catalog schema defines nullable `timestamptz` soft-delete columns:

- `songs.deleted_at` in `src/stream_of_worship/admin/db/schema.py`
- `recordings.deleted_at` in `src/stream_of_worship/admin/db/schema.py`

The webapp Drizzle schema mirrors those columns as `deletedAt` in
`webapp/src/db/schema.ts`.

The admin DB client in `src/stream_of_worship/admin/db/client.py`
consistently treats `NULL` as active:

- `DatabaseClient.get_song(..., include_deleted=False)` adds
  `songs.deleted_at IS NULL`.
- `DatabaseClient.get_recording_by_hash(..., include_deleted=False)` and
  recording list methods add `recordings.deleted_at IS NULL`.
- `DatabaseClient.delete_recording()` runs
  `UPDATE recordings SET deleted_at = NOW() WHERE hash_prefix = %s`.
- `DatabaseClient.soft_delete_song()` runs
  `UPDATE songs SET deleted_at = NOW() WHERE id = %s`.

## Trigger Paths

### Recording Soft Deletes

`sow-admin audio delete` is implemented in
`src/stream_of_worship/admin/commands/audio.py`. It deletes external R2 assets
first, then soft-deletes the database row. The shared helper is
`_delete_recording_and_files()`; it deletes:

- `recording.r2_audio_url`
- `recording.r2_stems_url`
- `recording.r2_lrc_url`

After those object deletions, it calls `DatabaseClient.delete_recording()`.

The same helper is used by:

- `sow-admin audio delete <song_id>`
- `sow-admin audio delete --stdin`
- `sow-admin audio download --force`, which deletes the existing active
  recording before downloading/importing the replacement.

### Song Soft Deletes

`sow-admin catalog delete <song_id>` is implemented in
`src/stream_of_worship/admin/commands/catalog.py`. It soft-deletes the song
through `DatabaseClient.soft_delete_song()` and then calls
`DatabaseClient.hold_recordings_for_song()`.

Catalog deletion is intentionally less destructive than recording deletion:

- It preserves R2 audio, stems, and LRC objects.
- It sets active recordings for the song to `visibility_status = 'hold'`.
- It counts existing `songset_items` references but does not remove them.

Full scraper runs can also soft-delete songs. In
`src/stream_of_worship/admin/services/scraper.py`,
`CatalogScraper.scrape_all_songs()` tracks existing song IDs and IDs seen in the
latest scrape. For full, non-limited runs with `soft_delete_missing=True`, it
calls `DatabaseClient.soft_delete_song()` for songs missing from the latest
scrape.

## Restore Paths

Song restore paths are exposed:

- `sow-admin catalog restore <song_id>` in
  `src/stream_of_worship/admin/commands/catalog.py` calls
  `DatabaseClient.restore_song()` and clears `songs.deleted_at`.
- Scraper upserts also resurrect songs. `DatabaseClient.insert_song()` and
  `DatabaseClient.insert_songs_bulk()` set `deleted_at = NULL` on conflict.

Recording restore is partly exposed through data flow, but not through a current
Admin CLI command:

- Recording upserts clear `recordings.deleted_at` on conflict in
  `DatabaseClient.insert_recording()` in
  `src/stream_of_worship/admin/db/client.py`.
- `DatabaseClient.restore_recording(hash_prefix)` exists and clears
  `recordings.deleted_at` in `src/stream_of_worship/admin/db/client.py`.
- No `sow-admin audio restore` command currently exposes
  `DatabaseClient.restore_recording()`.

Restoring a song does not automatically republish held recordings. The catalog
restore command prints suggested follow-ups to set recording visibility back to
`review` or `published`.

## How Deleted Rows Are Hidden or Exposed

Admin CLI:

- `sow-admin catalog list --deleted` uses `DatabaseClient.list_deleted_songs()`.
- The admin DB client has `DatabaseClient.list_deleted_recordings()`, but there
  is no matching audio CLI flag or command currently exposing deleted
  recordings.

Webapp catalog paths:

- `webapp/src/lib/db/songs.ts` builds song list/detail filters with
  `songs.deletedAt IS NULL`.
- Published-recording existence checks in `webapp/src/lib/db/songs.ts` require
  `recordings.deleted_at IS NULL`.
- Semantic search joins published recordings with `r.deleted_at IS NULL` and
  filters songs with `s.deleted_at IS NULL`.
- Full-text search in `webapp/src/lib/db/search.ts` requires
  `songs.deletedAt IS NULL` and only loads non-deleted recordings.

Webapp songset paths:

- `listSongsetSummaries()` counts and sums only items whose joined recording has
  `recordings.deletedAt IS NULL`.
- `getSongsetEditorData()` fetches all item rows, but then filters out rows with
  `recordingDeletedAt` before returning editor items.
- `getRenderPageData()` also filters out deleted-recording rows when computing
  render page item data.

TUI app paths:

- `ReadOnlyClient` defaults to excluding deleted songs and recordings, but most
  lookup methods accept `include_deleted=True`.
- `CatalogService.get_songset_items_with_details()` intentionally batch-fetches
  songs and recordings with `include_deleted=True` so orphaned or removed items
  can still be displayed.

Signed URL and preview handlers:

- `webapp/src/app/api/signed-url/shared-handler.ts` allows source recording URLs
  only when `recordings.visibilityStatus = 'published'`.
- `webapp/src/app/api/transitions/preview/route.ts` also requires
  `visibilityStatus = 'published'`.
- Neither handler explicitly checks `recordings.deletedAt IS NULL`; they rely on
  visibility and the row existing.

## Songsets and Hard Deletes

The songset tables do not define `deleted_at`:

- Python app schema: `src/stream_of_worship/app/db/schema.py`
- Webapp schema: `webapp/src/db/schema.ts`

Webapp deletion is a hard delete:

- `webapp/src/app/api/songsets/[id]/route.ts` handles `DELETE`.
- `webapp/src/lib/db/songsets.ts` implements `deleteSongset()` with
  `db.delete(songsets)`.

TUI deletion is also a hard delete:

- `src/stream_of_worship/app/db/songset_client.py`
  `SongsetClient.delete_songset()` runs
  `DELETE FROM songsets WHERE id = %s AND user_id = %s`.

Postgres cascades remove dependent rows:

- `songset_items.songset_id` references `songsets.id ON DELETE CASCADE`.
- `render_jobs.songset_id` references `songsets.id ON DELETE CASCADE`.
- `songset_share.songset_id` references `songsets.id ON DELETE CASCADE`.

## Dangling References

`songset_items.song_id` and `songset_items.recording_hash_prefix` are not hard
foreign keys to catalog entities in the app schema. The Python schema explicitly
keeps `song_id` as plain text, and the webapp schema stores
`recording_hash_prefix` as text with a Drizzle relation to
`recordings.hashPrefix`, not a database-enforced FK.

As a result:

- Soft-deleting a song does not delete or rewrite `songset_items.song_id`.
- Soft-deleting a recording does not delete or rewrite
  `songset_items.recording_hash_prefix`.
- Deleting recording R2 assets does not remove songset references to that hash.
- Webapp list/detail paths often hide items whose joined recording is deleted,
  so UI counts and editor payloads can differ from raw `songset_items`.

Render worker behavior is different. In
`services/render-worker/src/sow_render_worker/pipeline.py`,
`fetch_songset_items()` reads all rows for the songset and left-joins
`recordings` and `songs`, but it does not filter on `r.deleted_at` or
`s.deleted_at`. Later,
`services/render-worker/src/sow_render_worker/audio_engine.py` calls
`asset_fetcher.download_audio(item.recording_hash_prefix)` for each item.

That means a stale `recording_hash_prefix` can reach the render worker. If
`sow-admin audio delete` already removed the R2 source audio, the worker can fail
at asset download time even though the songset row still exists.

Example observed failure mode:

1. Recording `b1979244c818` is referenced by one or more `songset_items`.
2. `sow-admin audio delete` removes its R2 audio/stems/LRC and sets
   `recordings.deleted_at`.
3. The songset still contains `recording_hash_prefix = 'b1979244c818'`.
4. The render worker fetches that item without a `deleted_at` filter.
5. `asset_fetcher.download_audio('b1979244c818')` requests the deleted R2 object
   and receives HTTP 404.

## Operational Guidance

- Use `sow-admin catalog delete` when the catalog row is bad but existing
  assets and references should be preserved for investigation.
- Use `sow-admin audio delete` when the recording asset itself is wrong and the
  R2 audio/stems/LRC should be removed.
- Before deleting a recording that may have user-facing usage, check for
  `songset_items.recording_hash_prefix` references.
- After restoring a soft-deleted song, review held recordings and explicitly set
  visibility back to `review` or `published` when appropriate.
- Treat webapp songset item counts as visible-item counts, not necessarily raw
  database row counts, because deleted-recording items may be filtered out.

## Known Gaps and Follow-ups

- There is no Admin CLI command to list deleted recordings, even though
  `DatabaseClient.list_deleted_recordings()` exists.
- There is no Admin CLI command to restore a deleted recording, even though
  `DatabaseClient.restore_recording()` exists.
- Recording soft delete does not clean up or invalidate `songset_items`
  references.
- The render worker does not filter deleted songs when fetching songset items, but it now validates and fails fast on soft-deleted or missing recordings.
- Signed URL and transition preview handlers check `visibility_status =
  'published'` but do not explicitly check `deleted_at IS NULL`.
- Webapp songset list/detail filtering can hide dangling/deleted recording items
  from users while raw rows remain present.
