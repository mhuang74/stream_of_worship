# Admin Soft-Delete Maintenance Plan v2

## Summary

Add a `sow-admin maintenance` Typer group for safe catalog cleanup, songset repair,
render-failure diagnosis, and R2 waste cleanup.

Primary safety rules:

- `sow-admin audio delete` soft-deletes only. It sets `recordings.deleted_at` and never
  deletes R2 objects or hard-deletes rows.
- `sow-admin audio download --force` imports the replacement first, then soft-deletes
  the old active recording only after the new recording is safely persisted.
- Soft-deleted DB-backed recording assets remain recoverable until `purge-soft-deletes`.
- `purge-r2-waste` deletes orphan recording-prefix objects only: R2 prefixes with no DB
  recording row.
- Destructive maintenance commands are dry-run by default and require explicit targets:
  IDs/prefixes or `--all`.
- Destructive non-interactive apply requires `--apply --yes`.
- Maintenance refuses to mutate songsets with queued or running render jobs unless a
  future explicit override is added.

## Key Changes

### Existing Audio Commands

- Change `sow-admin audio delete` and `audio delete --stdin` to call only
  `DatabaseClient.delete_recording(hash_prefix)`.
- Remove R2 client construction from `audio delete`.
- Update delete help and confirmation text to say assets are preserved and can be
  reviewed by maintenance commands.
- Change `audio download --force` ordering:
  - Resolve old active recording.
  - Download, upload, and insert the replacement recording first.
  - After successful insert, soft-delete the old recording.
  - If replacement fails at any step, leave the old recording active.
- If a song has multiple active recordings, song-id based commands refuse ambiguity and
  require a hash-prefix targeted command where available.

### Maintenance Commands

Add `sow-admin maintenance list-soft-deletes`:

- Options: `--entity all|songs|recordings`, `--format table|json|ids`, `--limit`,
  `--with-r2`.
- `--format ids` is valid only with `--entity songs` or `--entity recordings`; reject it
  with `--entity all`.
- Show reference counts and, with `--with-r2`, object count and bytes for each recording
  prefix.

Add `sow-admin maintenance purge-soft-deletes`:

- Options: `--entity all|songs|recordings`, repeatable `--song-id`, repeatable
  `--hash-prefix`, `--all`, `--apply`, `--yes`, `--format table|json`.
- Require at least one selector: `--song-id`, `--hash-prefix`, or `--all`.
- Dry-run prints a manifest and blocked reasons.
- Recording purge applies only to soft-deleted rows and refuses rows still referenced by
  `songset_items`.
- Applied recording purge deletes all R2 objects under the exact `<hash_prefix>/`, then
  hard-deletes the soft-deleted recording row.
- Song purge applies only to soft-deleted songs with zero recordings, including deleted
  rows, and zero songset references.

Add `sow-admin maintenance repair-songsets`:

- Options: `--songset-id`, `--hash-prefix`, `--apply`, `--yes`, `--format table|json`.
- Find songset items whose recording hash points to a missing or soft-deleted recording.
- Refuse to apply changes for songsets with queued or running render jobs.
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
- Scan only top-level prefixes matching the recording hash-prefix format.
- List only orphan recording prefixes: prefixes with no DB recording row, active or
  deleted.
- Include object count, total bytes, last modified summary, and whether any songset item
  still references the prefix.

Add `sow-admin maintenance purge-r2-waste`:

- Options: repeatable `--prefix`, `--all`, `--apply`, `--yes`, `--format table|json`.
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
- Find queued/running render jobs for affected songsets.
- Find replacement recording candidates with DB-side pruning before R2 checks.
- Update a songset item recording hash in a transaction.

Extend `R2Client` with:

- Paginated `list_prefix(prefix: str)`.
- Batched `delete_prefix(prefix: str)`.
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

## Test Plan

Admin command tests:

- `audio delete` soft-deletes without constructing or calling `R2Client`.
- `audio delete --stdin` soft-deletes multiple recordings without R2 calls.
- `audio download --force` leaves old recording active when replacement
  download/upload/import fails.
- `audio download --force` imports replacement then soft-deletes the old recording on
  success.
- Song-id commands refuse ambiguous multiple active recordings.
- `list-soft-deletes` reports deleted songs/recordings and reference counts.
- `list-soft-deletes --entity all --format ids` fails with a clear error.
- `purge-soft-deletes` requires explicit target or `--all`.
- `purge-soft-deletes --apply` refuses referenced recordings.
- `purge-soft-deletes --apply` deletes R2 prefix and hard-deletes only eligible
  soft-deleted recording rows.
- `purge-soft-deletes --apply` hard-deletes only empty soft-deleted songs.
- `repair-songsets` dry-run and apply select deterministic best replacements.
- `repair-songsets --apply` refuses affected songsets with queued/running render jobs.
- `repair-songsets` reports no-replacement blockers.
- `diagnose-render-failures` reports current-state deleted, missing, and missing-R2
  causes.
- `list-r2-waste` reports orphan hash-like R2 prefixes only.
- `purge-r2-waste` refuses active rows, soft-deleted rows, non-hash prefixes, and
  still-referenced prefixes.

R2 tests:

- Paginated prefix listing.
- Batched prefix deletion.
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
