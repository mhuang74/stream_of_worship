# Admin Songset List v1

## Summary

Add a new `sow-admin songset` Typer subgroup with a single `list` subcommand. With no
flags, `sow-admin songset list` prints every songset across all users, one row per
`songset_item`, joined with `songs.title`/`songs.musical_key`, `recordings.duration_seconds`/
`recordings.tempo_bpm`. The optional `--user <user_email>` flag filters to songsets owned
by a single user (resolved by `UserClient.get_user_by_email`). Orphaned items (no matching
`songs` row) are still shown but render dashes for all song/recording columns.

This spec covers **only** the `list` subcommand. `create`, `show`, `delete`, etc. are
deferred to a future spec.

## Key Changes

### 1. Extend `SongsetClient` with admin-level read methods

File: `ops/admin-cli/src/stream_of_worship/db/app/songset_client.py`

Add three new methods. These deliberately bypass `self.user_id` because admin operations
are not user-scoped (the `--user` filter, when supplied, is resolved by the command into
a `user_id` and passed explicitly).

- `list_all_songsets(limit: Optional[int] = None) -> list[Songset]`
  - `SELECT id, user_id, name, description, created_at, updated_at FROM songsets
     ORDER BY updated_at DESC [LIMIT %s]`
  - Returns plain `Songset` rows (no items yet — those come from the join method below).

- `list_songsets_for_user_id(self, user_id: int, limit: Optional[int] = None) -> list[Songset]`
  - `SELECT id, user_id, name, description, created_at, updated_at FROM songsets
     WHERE user_id = %s ORDER BY updated_at DESC [LIMIT %s]`
  - The command resolves email → user_id via `UserClient.get_user_by_email` before
    calling this, so the client stays free of email-lookup concerns and the command
    can produce a friendly "User not found" error.

- `list_songset_items_with_song_recording(self, songset_ids: list[str]) -> dict[str, list[SongsetItem]]`
  - Batch JOIN query across multiple songsets in a single round-trip. Columns returned
    (in schema order matching `SongsetItem.from_row(detailed=True, len(row) >= 16)`):
    `songset_items.id, songset_items.songset_id, songset_items.song_id,
    songset_items.recording_hash_prefix, songset_items.position,
    songset_items.gap_beats, songset_items.crossfade_enabled,
    songset_items.crossfade_duration_seconds, songset_items.key_shift_semitones,
    songset_items.tempo_ratio, songset_items.created_at,
    songs.title, songs.musical_key, recordings.duration_seconds,
    recordings.tempo_bpm, recordings.musical_key, NULL::REAL AS loudness_db`
    (loudness_db is set to NULL because admin only needs Key/BPM; the model expects
    row[16] to be `loudness_db`.)
  - LEFT JOIN `songs` on `songs.id = songset_items.song_id` (and `songs.deleted_at IS NULL`).
  - LEFT JOIN a `recording_pick` CTE that picks the most recently imported active
    recording per `song_id` (`DISTINCT ON (song_id) ... WHERE deleted_at IS NULL
    ORDER BY song_id, imported_at DESC`). This matches the webapp convention of
    "most recent active recording wins" when `recording_hash_prefix` is null.
  - `WHERE songset_id = ANY(%s) ORDER BY songset_id, position ASC`.
  - Group rows into `dict[songset_id -> list[SongsetItem]]`. Missing songset IDs get
    an empty list entry so the command can render "(no songs)" rows uniformly.

Draft SQL (to live in `stream_of_worship.db.app.schema.SONGSET_ITEMS_FULL_QUERY_WITH_JOINS`):

```sql
WITH recording_pick AS (
   SELECT DISTINCT ON (song_id) song_id, duration_seconds, tempo_bpm,
          musical_key, NULL::REAL AS loudness_db
   FROM recordings
   WHERE deleted_at IS NULL
   ORDER BY song_id, imported_at DESC
)
SELECT
   si.id, si.songset_id, si.song_id, si.recording_hash_prefix, si.position,
   si.gap_beats, si.crossfade_enabled, si.crossfade_duration_seconds,
   si.key_shift_semitones, si.tempo_ratio, si.created_at,
   s.title, s.musical_key, rp.duration_seconds, rp.tempo_bpm,
   rp.musical_key, rp.loudness_db
FROM songset_items si
LEFT JOIN songs s ON s.id = si.song_id AND s.deleted_at IS NULL
LEFT JOIN recording_pick rp ON rp.song_id = si.song_id
WHERE si.songset_id = ANY(%s)
ORDER BY si.songset_id, si.position;
```

(If `songset_items.recording_hash_prefix` is non-null, the command can post-filter /
override the picked recording — out of scope for v1 since recording-key precedence is
already handled by `SongsetItem.display_key`.)

### 2. New Typer group `commands/songset.py`

File: `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` (new)

Pattern follows `commands/catalog.py` (shared `get_db_client` helper, rich Table,
`AdminConfig.load`, optional `--config`).

```python
@app.command("list")
def list_songsets(
    user: Optional[str] = typer.Option(
        None, "--user", "-u",
        help="Filter songsets to one user, resolved by email",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Cap the number of songsets returned",
    ),
    format: str = typer.Option(
        "table", "--format", "-f", help="Output format (table|ids)",
    ),
    config_path: Path = typer.Option(None, "--config", "-c", "Path to config file"),
) -> None: ...
```

Behavior:

1. Load `AdminConfig`. Build `ConnectionProvider` + a `SongsetClient(connection_provider,
   user_id=0)` (user_id is irrelevant for the methods we use).
2. If `--user` provided:
   - Use `UserClient(connection_provider).get_user_by_email(user)` to resolve.
   - If `None`: print `[red]User not found: <email>[/red]` and `typer.Exit(1)`.
   - Else call `songset_client.list_songsets_for_user_id(user.id, limit=limit)`.
3. Else (no `--user`): call `songset_client.list_all_songsets(limit=limit)`.
4. If empty: print `[yellow]No songsets found.[/yellow]` and return.
5. Build a `dict[str, list[SongsetItem]]` via
   `songset_client.list_songset_items_with_song_recording([s.id for s in songsets])`.
   If a songset has zero items, the dict entry is an empty list and the songset is
   still listed (with one row showing "(no songs)" in the title column).
6. `--format ids`: print one `songset_id` per line for piping. (Per-song ids format is
   out of scope for v1.)
7. Default `table` format: one row per song.

Table columns:

| Column | Source | Fallback for orphan |
|---|---|---|
| Songset | `songset.name` (repeated per row, optionally dim/green) | name |
| Owner | `User.email` resolved per `songset.user_id` (batch via `SELECT id, email FROM "user" WHERE id = ANY(%s)`; add a small helper or use the existing list and filter.) | — |
| # | `item.position + 1` | — |
| Title | `item.song_title` or `(no songs)` when list empty | `(missing)` |
| Duration | `item.formatted_duration` | `--:--` |
| Key | `item.display_key` | `-` |
| BPM | `round(item.tempo_bpm)` if not None else `--` | `--` |
| Song ID | `item.song_id` (dim) | `item.song_id` |

For head-of-table scaffolding, print a separator row when the songset changes
(`Table.add_section()`) so songsets are visually grouped in the one-row-per-song layout.
Output style follows `catalog list` (Table title = `Songsets (N total)`).

For empty songsets: still emit one section with a single row where the position column
is `-`, Title is `(no songs)`, and other per-song columns are dashes.

### 3. Register the subgroup

File: `ops/admin-cli/src/stream_of_worship/admin/main.py`

Add at the top:
```python
from stream_of_worship.admin.commands import songset as songset_commands
```
And in the registration block:
```python
app.add_typer(songset_commands.app, name="songset", help="Songset operations")
```
Update the `main` docstring's "## Commands" section to mention `songset - Songset
operations (list)`.

### 4. UX helpers in `commands/songset.py`

- A small `_resolve_owner_emails(connection_provider, songsets) -> dict[int, str]`
  helper that does `SELECT "id", "email" FROM "user" WHERE "id" = ANY(%s)` against the
  user IDs in `songsets`. Used to render the Owner column in one round-trip.
- `get_db_client` helper imported from `commands/catalog.py` (reuse test-friendly
  oriented accessor — the existing `catalog._get_db_client` is private but already
  shared between modules informally). If preferred, export it from `commands/__init__.py`
  or replicate. **Decision: replicate a tiny local `get_connection_provider(config)`
  helper** to avoid coupling between sibling command modules.

## Non-Goals

- `create`, `show`, `delete`, `share` subcommands — deferred.
- Modifying songset items / reordering / transition params from the admin CLI.
- Showing per-recording LRC or stems status.
- Picking the "best" recording when a song has multiple non-deleted recordings — v1 picks
  the most recently imported, which is consistent with the webapp's existing convention.
- Pagination — `--limit` is provided; no cursor.

## Tests

Unit tests will live at `ops/admin-cli/tests/test_songset_list_command.py` and
`ops/admin-cli/tests/test_songset_client_admin_methods.py`.

Existing test pattern: `pytest` with mocked `SongsetClient`/`UserClient`. No new
testcontainer fixture required since we mock DB access (mirrors existing
`tests/test_catalog.py` patterns if present — search before committing).

Coverage:

- `list` with no `--user` produces a Table whose section rows match the songsets/items
  returned by the mock.
- `list --user alice@example.com` flows through email lookup → user_id → filtered list.
- `list --user unknown@x.com` exits 1 with "User not found" error.
- Orphaned item (no `songs` row) renders `(missing)`, `--:--`, `-`, `--`.
- Empty songset (zero items) is rendered, not skipped.
- `--format ids` outputs IDs only.
- `--limit` propagates to client.

## Acceptance Criteria

- `sow-admin songset list` (no flags) returns all songsets owned by all users, one row
  per item, with Key/BPM/Duration/Title resolved from songs+recordings.
- `sow-admin songset list --user alice@example.com` filters to that user; raises a
  clear error if the email is unknown.
- Orphaned songset_items render dashes rather than crashing.
- Unit tests pass: `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/ -v`
- `uv run --project ops/admin-cli --extra admin sow-admin songset list --help`
  displays the new subcommand.
- `graphify update .` after merging to keep the knowledge graph current.

## Implementation Order

1. Add the new SQL constant to `db/app/schema.py`.
2. Extend `SongsetClient` (`list_all_songsets`, `list_songsets_for_user_id`,
   `list_songset_items_with_song_recording`).
3. Create `commands/songset.py` with the `list` command.
4. Register the group in `main.py`.
5. Write tests, run `pytest`.
6. Run `uv run --project ops/admin-cli --extra admin sow-admin songset list --help`
   and a live invocation against the dev DB.
7. `graphify update .`

## Risks

- The implicit "most recent active recording" selection could surprise an admin looking
  at tone-shifted stems. Mitigation: when `recording_hash_prefix` is set on an item,
  future spec can override per-row; for v1 the simple rule is documented.
- Multi-user owners list requires a second round-trip; acceptable given small N.
- The existing `SongsetClient.list_songsets` keeps returning user-scoped results — no
  behavior change for the TUI consumers.
