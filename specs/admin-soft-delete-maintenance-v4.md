# Admin Soft-Delete Maintenance Plan v4

## Summary

Add a `sow-admin maintenance` Typer group for safe catalog cleanup, songset repair,
render-failure diagnosis, and R2 waste cleanup.

Primary safety rules:

- `sow-admin audio delete` soft-deletes only. It sets `recordings.deleted_at` and never
  deletes R2 objects or hard-deletes rows.
- `sow-admin audio download --force` imports the replacement first, then soft-deletes
  the old active recording only after the new recording is safely persisted. It then
  updates any `songset_items` referencing the old hash to point to the new hash, all
  within a single DB transaction with the soft-delete.
- Soft-deleted DB-backed recording assets remain recoverable until `purge-soft-deletes`.
- `purge-r2-waste` deletes orphan recording-prefix objects only: R2 prefixes with no DB
  recording row.
- Destructive maintenance commands are dry-run by default and require explicit targets:
  IDs/prefixes or `--all`.
- Destructive non-interactive apply requires `--confirm`.
- Maintenance refuses to mutate songsets with queued or running render jobs unless a
  future explicit override is added.
- Destructive apply operations use database transactions with row locking to prevent
  race conditions between validation checks and mutations.

## Key Changes

### Existing Audio Commands

- Change `sow-admin audio delete` and `audio delete --stdin` to call only
  `DatabaseClient.delete_recording(hash_prefix)`.
- Remove R2 client construction from `audio delete`.
- Update delete help and confirmation text to say assets are preserved and can be
  reviewed by maintenance commands.
- Change `audio download --force` ordering:
  1. Resolve old active recording.
  2. Download, upload, and insert the replacement recording first.
  3. After successful insert, update any `songset_items` referencing the old hash
     to point to the new hash, then soft-delete the old recording, all within a
     single DB transaction.
  4. If replacement fails at any step, leave the old recording active and do not
     mutate `songset_items`.
  5. If the songset_items update or old recording soft-delete fails, the entire
     transaction rolls back, leaving the old recording active and songset_items
     unchanged.
- If a song has multiple active recordings, song-id based commands refuse ambiguity and
  require a hash-prefix targeted command where available.

### Maintenance Commands

Add `sow-admin maintenance list-soft-deletes`:

- Options: `--entity all|songs|recordings`, `--format table|json|ids`, `--limit`,
  `--with-r2`.
- `--format ids` returns a combined list with entity type labels when used with
  `--entity all`.
- Show reference counts and, with `--with-r2`, object count and bytes for each recording
  prefix.

Add `sow-admin maintenance purge-soft-deletes`:

- Options: `--entity all|songs|recordings`, repeatable `--song-id`, repeatable
  `--hash-prefix`, `--all`, `--confirm`, `--format table|json`, `--limit`.
- Require at least one selector: `--song-id`, `--hash-prefix`, or `--all`.
- `--limit` caps the number of items processed per run. Without it, all matching items
  are processed.
- Dry-run prints a manifest and blocked reasons.
- Recording purge applies only to soft-deleted rows and refuses rows still referenced by
  `songset_items`.
- Applied recording purge deletes all R2 objects under the exact `<hash_prefix>/`, then
  hard-deletes the soft-deleted recording row. The reference check, R2 deletion, and DB
  hard-delete are wrapped in a single database transaction with row locking.
- If R2 deletion succeeds but DB hard-delete fails, the command is idempotent: re-running
  the same purge command will retry the DB hard-delete (R2 delete is safely idempotent).
- Document that a dangling soft-deleted row after R2 deletion is recoverable by re-running
  the purge command.
- Song purge applies only to soft-deleted songs with zero recordings of any status (active
  or deleted) and zero songset references.

Add `sow-admin maintenance restore-soft-deletes`:

- Options: `--entity all|songs|recordings`, repeatable `--song-id`, repeatable
  `--hash-prefix`, `--all`, `--confirm`, `--format table|json`.
- Require at least one selector: `--song-id`, `--hash-prefix`, or `--all`.
- Dry-run shows what would be restored.
- Recording restore sets `deleted_at = NULL` on the recording row.
- Song restore sets `deleted_at = NULL` on the song row. Does not automatically restore
  the song's recordings; admin should restore recordings separately if needed.
- Refuse to restore recordings whose song is still soft-deleted (would create an active
  recording pointing to a deleted song).

Add `sow-admin maintenance repair-songsets`:

- Options: `--songset-id`, `--hash-prefix`, `--all`, `--confirm`, `--format table|json`.
- `--all` scans and repairs only songsets that have at least one stale item; healthy
  songsets are skipped.
- Find songset items whose recording hash points to a missing or soft-deleted recording.
- Refuse to apply changes for songsets with queued or running render jobs. Query the
  `render_jobs` table directly (admin CLI already queries cross-schema tables like
  songsets and songset_items).
- The queued/running job check and the `songset_items` update are wrapped in a database
  transaction with row locking to prevent race conditions.
- Choose active replacement recordings for the same song by:
  1. `visibility_status = 'published'`
  2. `lrc_status = 'completed'`
  3. `analysis_status = 'completed'`
  4. R2 audio exists
  5. Newest `imported_at`
  6. Hash prefix as final deterministic tie-breaker
- Dry-run reports item ID, songset ID, song ID/title, old hash, replacement hash, and
  reason.
- Apply updates only `songset_items.recording_hash_prefix`.
- Do not mutate historical `render_jobs` or clear `songsets.last_failed_render_job_id`.

Add `sow-admin maintenance diagnose-render-failures`:

- Options: `--job-id`, `--since-days`, `--limit`, `--format table|json`.
- Inspect failed render jobs and current songset state.
- Label findings as current-state diagnosis, not definitive historical root cause.
- Validate missing hash, missing row, soft-deleted row, missing R2 audio, and
  repairability.

Add `sow-admin maintenance list-r2-waste`:

- Options: `--format table|json`, `--limit`.
- Scan the full bucket and filter client-side for top-level prefixes matching the
  recording hash-prefix format.
- Use the admin config file blacklist to exclude known non-recording artifact prefixes
  (e.g., render outputs, thumbnails, temp files) from the scan.
- List only orphan recording prefixes: prefixes with no DB recording row, active or
  deleted.
- Include object count, total bytes, last modified summary, and whether any songset item
  still references the prefix.

Add `sow-admin maintenance purge-r2-waste`:

- Options: repeatable `--prefix`, `--all`, `--confirm`, `--format table|json`.
- Require `--prefix` or `--all`.
- Accept only validated full recording hash prefixes.
- Refuse prefixes with any DB recording row.
- Refuse prefixes still referenced by songset items.
- Apply by deleting all objects under exact `<prefix>/`.
- Never hard-delete or mutate DB rows.

## Implementation Details

Extend `DatabaseClient` with focused helpers:

- Count active recordings by song and list active recordings by song with deterministic
  ordering.
- Hard-delete soft-deleted recordings guarded by `deleted_at IS NOT NULL`.
- Hard-delete soft-deleted songs guarded by `deleted_at IS NOT NULL`.
- Count songset references by `recording_hash_prefix` and by `song_id`.
- Find stale songset item recording references.
- Find failed render jobs by ID, age, and limit.
- Find queued/running render jobs for affected songsets (queries `render_jobs` table).
- Find replacement recording candidates with DB-side pruning before R2 checks.
- Update a songset item recording hash in a transaction.
- Update multiple `songset_items` recording hashes by old hash in a batch operation.
- Restore soft-deleted recordings and songs (set `deleted_at = NULL`).
- Check if a recording's song is soft-deleted (for restore guard).

Extend `R2Client` with:

- Paginated `list_prefix(prefix: str)` with page size of 100 objects.
- Batched `delete_prefix(prefix: str)` with batch size of 100 objects.
- Hash-prefix-only top-level scanner for recording prefixes.
- Strict prefix validation that always operates on `<hash_prefix>/`, never arbitrary
  partial strings.
- Missing-prefix deletion as idempotent success.

Schema/index changes:

- Add `idx_songset_items_recording_hash_prefix` on
  `songset_items(recording_hash_prefix)` in the Python app schema.
- Add the same index to the Webapp Drizzle schema and migration.
- Do not add a foreign key from `songset_items.recording_hash_prefix` to
  `recordings.hash_prefix`; loose references remain intentional.

Admin config changes:

- Add an `r2_waste_blacklist` list to the admin config file. Each entry is a prefix
  string (e.g., `renders/`, `thumbnails/`, `temp/`). `list-r2-waste` skips any R2
  prefix that starts with a blacklisted prefix.

## Test Plan

Admin command tests:

- `audio delete` soft-deletes without constructing or calling `R2Client`.
- `audio delete --stdin` soft-deletes multiple recordings without R2 calls.
- `audio download --force` leaves old recording active when replacement
  download/upload/import fails.
- `audio download --force` imports replacement, then in a single transaction updates
  `songset_items` referencing old hash and soft-deletes the old recording.
- `audio download --force` rolls back both songset_items update and soft-delete if
  either fails.
- Song-id commands refuse ambiguous multiple active recordings.
- `list-soft-deletes` reports deleted songs/recordings and reference counts.
- `list-soft-deletes --entity all --format ids` returns combined list with entity type
  labels.
- `purge-soft-deletes` requires explicit target or `--all`.
- `purge-soft-deletes --confirm` refuses referenced recordings.
- `purge-soft-deletes --confirm` deletes R2 prefix and hard-deletes only eligible
  soft-deleted recording rows.
- `purge-soft-deletes --confirm` hard-deletes only soft-deleted songs with zero
  recordings of any status.
- `purge-soft-deletes` is idempotent: re-running on the same prefix succeeds if DB
  hard-delete was previously skipped.
- `purge-soft-deletes --limit` caps the number of items processed.
- `restore-soft-deletes` restores soft-deleted recordings and songs.
- `restore-soft-deletes` refuses to restore recordings whose song is still soft-deleted.
- `repair-songsets` dry-run and apply select deterministic best replacements.
- `repair-songsets --all` repairs only songsets with stale items.
- `repair-songsets --confirm` refuses affected songsets with queued/running render jobs.
- `repair-songsets` reports no-replacement blockers.
- `diagnose-render-failures` reports current-state deleted, missing, and missing-R2
  causes.
- `list-r2-waste` reports orphan hash-like R2 prefixes only.
- `list-r2-waste` respects the admin config blacklist.
- `purge-r2-waste` refuses active rows, soft-deleted rows, non-hash prefixes, and
  still-referenced prefixes.

R2 tests:

- Paginated prefix listing with 100-object pages.
- Batched prefix deletion with 100-object batches.
- Missing-prefix idempotency.
- Strict hash-prefix validation.
- Refusal to scan or delete non-recording namespaces.

## Assumptions

- Recording history should be preserved where the current schema allows it.
- Soft-deleted DB-backed recordings remain recoverable until `purge-soft-deletes`.
- R2 waste cleanup is intentionally narrower than "everything unreferenced by active
  recordings."
- Repair uses manifest-based dry-run/apply semantics, not per-item interactive
  confirmation.
- Active render jobs read live songset state, so maintenance mutations should avoid
  queued/running jobs by default.
- Destructive apply operations use database transactions with row locking to prevent
  race conditions.
- R2 waste scanning is a full-bucket client-side filter; performance scales with total
  bucket size.
- `audio download --force` auto-repairs `songset_items` and soft-deletes the old
  recording in a single DB transaction.
- Song purge requires zero recordings of any status (active or deleted) to prevent
  orphan recordings.
- The admin CLI queries the `render_jobs` table directly for render job checks,
  consistent with its existing cross-schema queries.
- `--confirm` is the standard flag for destructive apply operations, matching existing
  admin CLI conventions.

## Changes from v3

- `audio download --force` now wraps songset_items update and old recording soft-delete
  in a single DB transaction (was separate steps).
- Added `restore-soft-deletes` command to complete the soft-delete lifecycle.
- Song purge requires zero recordings of any status, not just zero total recordings
  (prevents orphan recordings).
- `--apply --yes` replaced with `--confirm` to match existing admin CLI conventions.
- `--format ids` now allowed with `--entity all` (returns combined list with entity
  type labels).
- Added `--limit` to `purge-soft-deletes` for safer incremental purging.
- `repair-songsets` render job check queries `render_jobs` directly (admin CLI already
  queries cross-schema tables).
