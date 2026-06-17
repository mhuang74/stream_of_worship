# Admin Soft-Delete Maintenance Plan

## Summary

Add a new `sow-admin maintenance` command group for cross-entity soft-delete
operations, songset repair, render-failure diagnosis, and R2 waste cleanup.
Keep `sow-admin audio delete` as a soft-delete-only command: it should only set
`recordings.deleted_at` and must not delete R2 objects or hard-delete Postgres
rows.

Defaults:

- Purge and repair commands are dry-run by default; execution requires
  `--apply`.
- Destructive non-interactive use also requires `--yes`.
- Recording purge refuses soft-deleted recordings still referenced by songsets
  until repair is run.
- Soft-deleted songs are hard-deleted only when they have no recordings and no
  songset references.
- Songset repair chooses the best active replacement recording for the same
  song.
- R2 cleanup is handled only by maintenance purge commands.
- R2 waste scanning treats any prefix not referenced by an active recording as
  purge-eligible waste.

## CLI Changes

Register a new `maintenance` Typer group from
`src/stream_of_worship/admin/main.py`.

### `sow-admin maintenance list-soft-deletes`

Options:

- `--entity all|songs|recordings`, default `all`
- `--format table|json|ids`, default `table`
- `--limit`
- `--with-r2`

Behavior:

- Lists all soft-deleted songs and recordings.
- For songs, show `song_id`, title, `deleted_at`, recording count, and songset
  reference count.
- For recordings, show hash prefix, song ID/title, `deleted_at`, songset item
  reference count, and stored R2 URL fields.
- With `--with-r2`, also show R2 object count and total bytes under the
  recording prefix.

### `sow-admin maintenance purge-soft-deletes`

Options:

- `--entity all|songs|recordings`, default `all`
- Repeatable `--song-id`
- Repeatable `--hash-prefix`
- `--all`
- `--apply`
- `--yes`
- `--format table|json`

Behavior:

- Dry-run builds and prints a deletion manifest.
- Recording purge is allowed only for rows where `recordings.deleted_at IS NOT
  NULL`.
- Recording purge refuses any recording hash still referenced by
  `songset_items.recording_hash_prefix`.
- When applied, recording purge deletes all R2 objects under `<hash_prefix>/`,
  then hard-deletes the soft-deleted recording row.
- Song purge is allowed only for rows where `songs.deleted_at IS NOT NULL`.
- Song purge hard-deletes only soft-deleted songs with zero recordings and zero
  songset references; blocked songs are reported with reasons.
- `--all` means all currently purge-eligible rows, not blocked rows.

### `sow-admin maintenance repair-songsets`

Options:

- `--songset-id`
- `--hash-prefix`
- `--apply`
- `--yes`
- `--format table|json`

Behavior:

- Finds `songset_items` whose `recording_hash_prefix` points to a soft-deleted
  recording or to no recording row.
- For each item, looks for active recordings with the same `song_id`.
- Replacement ordering:
  1. `visibility_status = 'published'`
  2. `lrc_status = 'completed'`
  3. `analysis_status = 'completed'`
  4. R2 audio exists
  5. Newest `imported_at`
- Dry-run reports each proposed item update, old hash, replacement hash, and
  reason.
- Apply updates only `songset_items.recording_hash_prefix`.
- Do not clear `songsets.last_failed_render_job_id`.
- Do not mutate historical `render_jobs`.
- Items with no replacement are reported as blocked.

### `sow-admin maintenance diagnose-render-failures`

Options:

- `--job-id`
- `--since-days`
- `--limit`
- `--format table|json`

Behavior:

- Scans failed `render_jobs`, or a single job when `--job-id` is provided.
- For each failed job, inspect the current `songset_items` for its songset.
- Validate each referenced recording:
  - missing `recording_hash_prefix`
  - missing recording row
  - soft-deleted recording row
  - active row whose R2 audio object is missing
- Use `render_jobs.error_message` as supporting context, not as the only
  classifier.
- Report repairability by checking whether `repair-songsets` can find a
  replacement candidate.

### `sow-admin maintenance list-r2-waste`

Options:

- `--format table|json`
- `--limit`

Behavior:

- Scans top-level R2 prefixes.
- Lists prefixes not referenced by any active recording.
- Include prefixes for soft-deleted recordings and orphan prefixes with no DB
  recording row.
- Show prefix, category, object count, total bytes, associated song/hash data
  when known, and whether any songset item still references the prefix.

### `sow-admin maintenance purge-r2-waste`

Options:

- Repeatable `--prefix`
- `--all`
- `--apply`
- `--yes`
- `--format table|json`

Behavior:

- Dry-run prints the R2 deletion manifest.
- Only accepts prefixes that are not referenced by active recordings.
- Refuses prefixes still referenced by songset items unless those references
  have already been repaired.
- Applies by deleting all objects under each selected `<prefix>/`.
- Does not hard-delete DB rows; DB row deletion remains part of
  `purge-soft-deletes`.

## Existing Command Changes

### `sow-admin audio delete`

Change the command to soft-delete only.

Required behavior:

- Look up the active recording by song ID as today.
- Confirm the action unless `--yes` is passed.
- Call `DatabaseClient.delete_recording(hash_prefix)`.
- Do not instantiate `R2Client`.
- Do not delete stored `r2_audio_url`, `r2_stems_url`, or `r2_lrc_url`.
- Do not delete any object under `<hash_prefix>/`.
- Update help text and confirmation output to say assets are preserved and can
  be reviewed with `sow-admin maintenance list-r2-waste`.

Batch `audio delete --stdin` follows the same soft-delete-only behavior for
each recording.

`sow-admin audio download --force` should also use the soft-delete-only helper
for the existing recording before importing the replacement. The old recording's
R2 prefix then becomes waste and can be purged separately.

## Implementation Details

### Database Helpers

Extend `DatabaseClient` with focused helpers:

- Hard-delete a soft-deleted recording by hash prefix, guarded by
  `deleted_at IS NOT NULL`.
- Hard-delete a soft-deleted song by ID, guarded by `deleted_at IS NOT NULL`.
- Count songset item references by `recording_hash_prefix`.
- Count recordings by `song_id`, including deleted rows.
- Find songset items that point to soft-deleted or missing recordings.
- Find failed render jobs by ID, age, and limit.
- Fetch songset items with joined song/recording deleted state for diagnostics.
- Find replacement recording candidates for a song.
- Update a songset item recording hash in a transaction.

Keep SQL parameterized and avoid schema-level foreign keys for
`songset_items.recording_hash_prefix`; current loose references are intentional.

### R2 Helpers

Extend `R2Client` with:

- `list_prefix(prefix: str)` returning key, size, and last modified data via
  paginated `list_objects_v2`.
- `delete_prefix(prefix: str)` using batched `delete_objects`.
- `list_top_level_prefixes()` or equivalent scanner for waste detection.
- Prefix validation that accepts recording hash-like first path segments and
  always works on `<hash_prefix>/`, not arbitrary partial strings.

Missing prefixes are treated as idempotent success for purge.

### Schema And Indexes

Add an index for efficient repair/purge lookups:

- Python app schema: `idx_songset_items_recording_hash_prefix` on
  `songset_items(recording_hash_prefix)`.
- Webapp Drizzle schema mirror for the same index.

No new tables or columns are required.

## Test Plan

Add or update admin tests:

- `audio delete` soft-deletes rows and does not construct or call `R2Client`.
- `audio delete --stdin` soft-deletes multiple rows without R2 calls.
- `audio download --force` preserves the old recording's R2 prefix and only
  soft-deletes the old DB row before importing the replacement.
- `list-soft-deletes` shows deleted songs and recordings with reference counts.
- `purge-soft-deletes` dry-run reports manifests without mutation.
- `purge-soft-deletes --apply` refuses referenced recordings.
- `purge-soft-deletes --apply` deletes unreferenced soft-deleted recording R2
  prefix and hard-deletes the row.
- `purge-soft-deletes --apply` hard-deletes only empty soft-deleted songs and
  reports blocked songs.
- `repair-songsets` dry-run and apply update stale recording hashes to the best
  active replacement.
- `repair-songsets` reports no-replacement blockers.
- `diagnose-render-failures` detects deleted rows, missing rows, and missing R2
  audio for failed render jobs.
- `list-r2-waste` reports both soft-deleted recording prefixes and orphan R2
  prefixes.
- `purge-r2-waste` refuses active or still-referenced prefixes and deletes only
  eligible unreferenced prefixes.

Add or update R2 tests:

- Paginated prefix listing.
- Batched prefix deletion.
- Missing-prefix idempotency.
- Prefix validation.

Add or update database tests:

- Hard-delete guards only affect soft-deleted rows.
- Reference counting by recording hash and song ID.
- Stale songset item detection.
- Replacement candidate ordering.
- Songset item repair transaction.

Run:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

If shared schema changes affect the webapp, also run:

```bash
pnpm --filter sow-webapp test
```

After implementation, run:

```bash
graphify update .
```
