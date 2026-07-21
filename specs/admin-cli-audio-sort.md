# Spec: `sow-admin audio list` — sort by last update date

## Goal

Add a new `--sort updated` option to `sow-admin audio list` that orders recordings by
`updated_at DESC` (most recently changed rows appear first). When this sort is active,
the table output shows an additional **Updated** column with the timestamp.

## Why

The `recordings` table already has an `updated_at` column with a `BEFORE UPDATE` trigger
that auto-refreshes it on any row change. Users need a quick way to see which recordings
they modified most recently (e.g. after bulk visibility or LRC updates).

## Changes (3 files)

### 1. `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

**Location:** `list_recordings_with_songs()` method, `order_map` dict (around line 847).

**Change:** Add one entry to `order_map`:

```python
"updated": "r.updated_at DESC NULLS LAST",
```

**Also:** Ensure the SQL `SELECT` clause in `list_recordings_with_songs` includes
`r.updated_at`. If it is not already selected, add it to the SELECT list.

### 2. `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

**Location:** `list_command()` function.

**Changes:**

a. **Help string** (around line 1216): update `--sort` help from
   `(album|series|title|imported)` to `(album|series|title|imported|updated)`.

b. **Validation set** (around line 1254): add `"updated"` to `valid_sorts`:
   ```python
   valid_sorts = {"album", "series", "title", "imported", "updated"}
   ```

c. **Table output** (around lines 1304–1312): when `sort == "updated"`, add an
   **Updated** column to the table header and row rows. The timestamp should be
   formatted as `YYYY-MM-DD HH:MM` (or similar short format). The column appears
   *only* when `--sort updated` is used — not for other sort modes.

   Implementation approach:
   - In the table header construction, check if `sort == "updated"` and append
     `"Updated"` to the header list.
   - In the row rendering loop, append `rec.updated_at.strftime("%Y-%m-%d %H:%M")`
     (or `str(rec.updated_at)[:16]` if it's a string) to the row values.
   - The enriched row object from `db_client.list_recordings_with_songs()` must
     expose `updated_at` — guaranteed by the SELECT clause change in step 1.

d. **No client-side re-sort branch needed** in the post-processing block
   (lines 1277–1287). The `updated` sort is handled entirely by the DB, same
   pattern as `imported`.

### 3. `ops/admin-cli/tests/admin/test_audio_commands.py`

**Location:** `TestAudioListCommand` class.

**Changes:**

a. **Integration test** — `@pytest.mark.integration` decorated:

   ```
   TestAudioListSortUpdated
   ```

   Steps:
   1. Use `make_test_provider` fixture (testcontainers Postgres) to get a DB connection.
   2. Insert song A and song B via `client.insert_song()`.
   3. Insert recording A via `client.insert_recording()` (gets current `updated_at`).
   4. Insert recording B via `client.insert_recording()` (gets current `updated_at`).
   5. Use direct SQL (via `make_test_provider`'s connection cursor) to UPDATE
      recording A's `updated_at` to a future timestamp (e.g. `NOW() + INTERVAL '1 day'`),
      making it newer than B.
   6. Run `runner.invoke(app, ["audio", "list", "--sort", "updated", "--config", config_path])`.
   7. Assert `result.exit_code == 0`.
   8. Assert recording A's title appears before recording B's title in `result.output`.
   9. Assert the "Updated" column header is present in `result.output`.
   10. Assert the updated_at timestamps appear in the output.

b. **Validation test** — non-integration test:

   ```
   TestAudioListSortUpdatedValidation
   ```

   Steps:
   1. Write minimal config (no DB URL).
   2. Run `audio list --sort updated` → assert non-zero exit code (fails at DB, not at validation).
   3. Run `audio list --sort bogus` → assert non-zero exit code with error mentioning
      "invalid choice" or similar validation message.
   4. This confirms `updated` passes CLI validation while invalid values are rejected.

## Out of scope

- `--format ids` output remains unchanged (no timestamp shown, matches existing `imported` behavior).
- No DB migration needed — `updated_at` column and trigger already exist.
- No changes to legacy apps, lab apps, or other CLI commands.
- No changes to the table output for non-`updated` sort modes.

## Verification

After implementation, run:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  pytest -v ops/admin-cli/tests/admin/test_audio_commands.py::TestAudioListCommand
```

Also verify the CLI help text:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin sow-admin audio list --help
```

Expected to show `updated` in the `--sort` options.
