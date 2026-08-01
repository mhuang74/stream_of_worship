# `sow-admin songset create` Implementation Plan v1

## Problem

The Admin CLI currently offers two ways to produce a songset:

1. `sow-admin songset list` — read-only inspection of existing songsets.
2. `sow-admin songset construct` — algorithmic generation via beam search / LLM
   planning against the songset-constructor graph (requires the `constructor`
   extra, the `theme_anchors` table, and a multi-step proposal-review workflow).

There is no way for an operator to rapidly hand-pick an ordered songset from a
known list of song IDs or titles and persist it to the database. Today, the only
way to do this is through the Next.js webapp UI (`/songsets` editor → browse →
add songs one at a time).

This spec adds a `sow-admin songset create` command that accepts an explicit,
ordered list of song IDs / titles, validates each entry, enforces the same
songset-size limits as the webapp, and saves the songset under a given user
context.

## Scope

### In scope

- New `create` subcommand on the existing `songset` Typer group.
- Accept a variadic positional list of song tokens (each is either a song ID or
  a song title).
- Resolve each token to a `(song, active recording)` pair with interactive
  disambiguation for ambiguous title matches.
- Enforce the 5-song / 25-minute songset size limits at create time, mirroring
  the webapp.
- Auto-generate a name from the resolved titles (chained with underscores) when
  `--name` is omitted.
- Persist atomically via the existing `SongsetClient.create_songset_with_items`.

### Out of scope (explicitly deferred)

- **Render-job trigger**: The original request suggested an option to kick off
  rendering after songset creation. Per clarification, this is **descoped** from
  v1. The webapp is currently the only component that writes to `render_jobs`
  and dispatches to the SQS render queue (see
  `delivery/webapp/src/lib/render/job-manager.ts:129` `createRenderJob` and
  `delivery/webapp/src/lib/render/dispatcher.ts:25` `dispatchToRenderWorker`).
  Replicating that logic in `sow-admin` would duplicate validation, the
  `uq_render_jobs_active_per_songset_user` unique constraint handling, and SQS
  auth. Defer until a shared render-dispatch helper exists. After `create` the
  operator triggers rendering through the webapp UI or a future
  `sow-admin render enqueue` command.
- Transition parameter tuning (`gap_beats`, `crossfade_enabled`,
  `key_shift_semitones`, `tempo_ratio`). Persisted with the same defaults as
  `SongsetClient.add_item` (`gap_beats=2.0`, `crossfade_enabled=False`,
  `key_shift_semitones=0`, `tempo_ratio=1.0`). A future `--tune` flag may gate
  per-item overrides.
- Bulk songset import from CSV/JSON.

## Decisions

| Decision | Choice |
|----------|--------|
| Render-job trigger | **Descoped from v1** (per clarification). See "Out of scope". |
| Where validation lives | **Create-time + (future) render-time.** Both song-count and total-duration limits enforced at `create`. Mirrors webapp which enforces at `addSongsetItem` and `POST /api/render-jobs`. |
| Ambiguous title match resolution | **Interactive picker** — list all matches with song_id / album / composer, prompt user to pick by number. In `--yes` (non-interactive) mode, error and ask for `song_id`. |
| Python constants location | New module `ops/admin-cli/src/stream_of_worship/admin/constants.py` mirroring `delivery/webapp/src/lib/constants.ts`. Note duplication in spec; refactor to single source later. |
| Persistence path | Reuse existing `SongsetClient.create_songset_with_items` (atomic single-transaction insert). |
| Pre-existing `add_item` vs `create_songset_with_items` | Use `create_songset_with_items`. It validates recording_hash_prefix existence in a single round-trip and rolls back on any failure — no partial songsets. |

## Key Changes

### 1. New shared constants module

**File (new):** `ops/admin-cli/src/stream_of_worship/admin/constants.py`

```python
"""Shared constants mirroring the webapp's constants.ts.

NOTE: these duplicate `delivery/webapp/src/lib/constants.ts`. When a shared
config source lands, prefer that and remove this module.
"""

SONGSET_MAX_SONGS = 5
SONGSET_MAX_DURATION_SECONDS = 1500  # 25 minutes
```

### 2. Song-resolution helper

**File (new):** `ops/admin-cli/src/stream_of_worship/admin/commands/songset_create.py`

Splitting into its own module keeps `commands/songset.py` from growing much
larger (already 584 lines, mostly the `construct` command). The `songset` Typer
`app` is imported and re-registered.

Resolution flow for each user-supplied token:

```python
def resolve_song_token(
    token: str,
    read_client: ReadOnlyClient,
    console: Console,
    *,
    non_interactive: bool,
) -> tuple[Song, Recording]:
    """Resolve a song ID or title token to (song, active_recording).

    Resolution order:
      1. Exact `song_id` match (non-deleted) via `read_client.get_song(token)`.
      2. If no exact ID match, treat `token` as a title and call
         `read_client.search_songs(token, field="title", limit=20)`.
      3. If exactly one match: use it.
      4. If multiple matches and `non_interactive` is False: render a
         `rich.table.Table` of candidates (row#, song_id, title, album, key,
         bpm), prompt the user to pick a number via `typer.prompt`.
      5. If multiple matches and `non_interactive` is True: raise typer.Exit(1)
         with guidance to supply the song_id directly.
      6. If zero matches: raise typer.Exit(1) with the failing token.

    After a song is selected, fetch its active recording:
      - `read_client.get_recording_by_song_id(song.id, include_deleted=False)`.
      - If `None`: raise typer.Exit(1). The songset_items schema requires a
        `recording_hash_prefix` (validated by `create_songset_with_items`), so
        a song with no active recording cannot be added.

    Returns:
        (song, recording) tuple.
    """
```

### 3. New `create` subcommand

**File (new):** `ops/admin-cli/src/stream_of_worship/admin/commands/songset_create.py`

```python
@app.command("create")
def create_songset(
    songs: list[str] = typer.Argument(
        ...,
        help="Ordered song IDs and/or titles (resolved in order)",
        min_args=1,
    ),
    user: str = typer.Option(
        ...,
        "--user",
        help="Email of the user to own the songset",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Songset name. If omitted, chained from resolved song titles "
             "(song1_song2_song3).",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        "-d",
        help="Optional songset description",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Non-interactive: auto-confirm, refuse to disambiguate titles",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve + validate but skip DB writes",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Create a songset from an explicit, ordered list of songs.

    Each positional argument is a song ID (exact match) or a title (fuzzy
    match via title + title_pinyin). Ambiguous title matches prompt for an
    interactive pick unless --yes is set. The songset is persisted under the
    user identified by --user (resolved by email).

    Enforces SONGSET_MAX_SONGS=5 and SONGSET_MAX_DURATION_SECONDS=1500
    (25 min total recording duration) at create time, mirroring the webapp.

    Examples:
      sow-admin songset create --user alice@example.com \\
          song_0123 "信實偉大" "song_0089" "恩典之路"

      sow-admin songset create -u bob@example.com -n "Sunday_Set_1" \\
          song_0123 song_0044 song_0089 --yes
    """
```

#### Execution flow

1. **Load config** — `AdminConfig.load(config_path)`, fail with `typer.Exit(1)`
   on `FileNotFoundError` (same pattern as `songset list` at
   `commands/songset.py:98-101`).
2. **Resolve user** — `UserClient.get_user_by_email(user)`. Fail with
   `typer.Exit(1)` if `None` (same as `construct` at
   `commands/songset.py:458-463`).
3. **Business-rule validation (cheap, pre-DB)** —
   `if len(songs) > SONGSET_MAX_SONGS: raise typer.Exit(1)` with a clear
   message. This avoids wasted DB round-trips for an obviously oversize list.
4. **Resolve every token** — For each `token` in `songs` (in order), call
   `resolve_song_token(token, read_client, console, non_interactive=yes)`.
   Collect `resolved: list[tuple[Song, Recording]]`.
5. **Duration check** — Sum `recording.duration_seconds` across `resolved`.
   If `> SONGSET_MAX_DURATION_SECONDS`, fail with the same message the webapp
   returns (`Songset exceeds maximum duration of 25 minutes`). Use the
   recording guard against `None` (treat missing duration as `0`).
6. **Auto-name** — If `--name` is None, build it from `resolved[i].song.title`
   joined by `_`. Sanitize: replace spaces with empty string, strip non-
   printable chars. If any title is empty after sanitize, fall back to the
   song_id at that position.
7. **Confirmation summary table** — Render a `rich.table.Table` with columns:
   `#`, `Song ID`, `Title`, `Album`, `Key`, `BPM`, `Duration`, `Recording Hash`
   for each resolved song. Show computed name and total duration footer.
8. **Confirm** — If not `--yes`, `typer.confirm("Save songset ...?")`.
9. **Persist** — Call `SongsetClient.create_songset_with_items(
        name=name,
        description=description or "",
        items=[
            {
                "song_id": song.id,
                "recording_hash_prefix": recording.hash_prefix,
                "position": i,
                "gap_beats": 2.0,
                "crossfade_enabled": False,
                "crossfade_duration_seconds": None,
                "key_shift_semitones": 0,
                "tempo_ratio": 1.0,
            }
            for i, (song, recording) in enumerate(resolved)
        ],
   )`. Catch `MissingReferenceError` and surface a friendly `[red]` message.
10. **Dry-run** — If `--dry-run`, skip step 9, print `[yellow]Dry run: skipping DB writes.[/yellow]`.
11. **Output** — Print `[green]Created songset <id> '<name>' (<N> songs, <mm:ss>).[/green]`.

### 4. Register the new command module

**File (edit):** `ops/admin-cli/src/stream_of_worship/admin/commands/__init__.py`

Import `songset_create` so Typer registers its `@app.command("create")`. Verify
`sow-admin songset create --help` appears alongside `list` and `construct`.

If `__init__.py` follows a pattern of side-effect imports (typical for the
existing commands), add an `import stream_of_worship.admin.commands.songset_create  # noqa: F401`
line. Otherwise register the new module's `app` subapp via `add_typer` in
`main.py` (the existing `songset` subapp already uses an `app`-on-module
pattern at `commands/songset.py:22`).

**Preferred:** Keep `songset_create` as a separate module but register its
`create` command on the *same* `app` Typer instance exported from
`commands/songset.py`. This requires either:
- (a) importing `app` from `commands.songset` in `songset_create.py` and
  decorating `create_songset` with `@app.command("create")`, **or**
- (b) defining `create_songset` in `songset_create.py` with a local
  `app = typer.Typer()` and merging with `commands.songset.app.add_typer(app)`,
  **or**
- (c) simply placing the `create` command function directly in
  `commands/songset.py` next to `construct`.

Option (c) is the simplest and matches the current convention. Use (c);
place the new `create_songset` function in `commands/songset.py` after the
`construct_songset` definition. Split helper functions (`resolve_song_token`,
`_resolve_owner_emails`-style utilities) into a private
`commands/_songset_create_helpers.py` module if `commands/songset.py` exceeds
~700 lines.

### Command-structure file map

| File | Status | Purpose |
|------|--------|---------|
| `ops/admin-cli/src/stream_of_worship/admin/constants.py` | new | `SONGSET_MAX_SONGS`, `SONGSET_MAX_DURATION_SECONDS` |
| `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` | edit | Add `create` subcommand (option c) |
| `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py` | new (if needed) | `resolve_song_token`, sanitization, duration formatter |

## Validation Rules

Mirrors `delivery/webapp/src/app/api/render-jobs/route.ts:43-65` plus
`delivery/webapp/src/lib/db/songsets.ts:952-959`.

| Rule | Threshold | Where enforced | Source |
|------|-----------|----------------|--------|
| Max songs per songset | `> 5` rejected | `create_songset`, after token list parse | `constants.SONGSET_MAX_SONGS` |
| Max total duration | `> 1500` seconds rejected | `create_songset`, after recording resolution | `constants.SONGSET_MAX_DURATION_SECONDS` |
| Each song must exist & not be soft-deleted | ID lookup via `ReadOnlyClient.get_song`; title search excludes deleted via `search_songs(include_deleted=False)` | `resolve_song_token` | `read_client.py:83-105`, `171-217` |
| Each song must have ≥ 1 active recording | `get_recording_by_song_id(song_id, include_deleted=False)` is not None | `resolve_song_token` | `read_client.py:276-303` |
| `recording_hash_prefix` must exist in `recordings` table | Validated inside `create_songset_with_items` (raises `MissingReferenceError`) | `songset_client.py:712-734` | (existing) |
| Ambiguous title match | Interactive picker if multiple matches; `typer.Exit(1)` in `--yes` mode | `resolve_song_token` | (new) |
| Duplicate songs in the ordered list | Allowed but warned (print `[yellow]⚠ song X appears twice[/yellow]`). The webapp allows duplicates; mirror that behaviour. | `create_songset`, step 7 | (new) |

## Auto-naming Algorithm

When `--name` is not supplied:

```
resolved[i].song.title  for i in range(len(resolved))
   → strip whitespace
   → replace internal spaces with "" (compact, e.g. "信實偉大" not "信 實 偉 大")
   → strip non-printable / non-Chinese / non-alphanumeric chars
   → if empty, fall back to resolved[i].song.id
   → join with "_"
```

Example: titles `["信實偉大", "恩典之路", "主我敬拜你"]` → name
`"信實偉大_恩典之路_主我敬拜你"`.

If the resulting name exceeds 255 chars, truncate to 252 chars + `"..."`.

## Examples

### Mixed IDs and titles, interactive disambiguation

```
$ sow-admin songset create --user alice@example.com \
    song_0123 "信實偉大" "song_0089" "恩典之路"

Resolving user alice@example.com ... done
Resolving 4 song tokens ...

Multiple matches for "恩典之路":
 #  Song ID      Title           Album          Key
 1  song_0044    恩典之路         敬拜讚美15     G
 2  song_0072    恩典之路 (Live)  敬拜讚美21     D
Pick [1-2]: 1

 +----+-------------+--------------+-----------+-----+-----+----------+
 | #  | Song ID     | Title        | Album     | Key | BPM | Duration |
 +----+-------------+--------------+-----------+-----+-----+----------+
 | 1  | song_0123   | 你信實何廣大   | 敬拜讚美15 | C   | 72  | 04:12    |
 | 2  | song_0045   | 信實偉大      | 敬拜讚美15 | G   | 76  | 03:48    |
 | 3  | song_0089   | 我心敬拜       | 敬拜讚美18 | D   | 80  | 04:05    |
 | 4  | song_0044   | 恩典之路      | 敬拜讚美15 | G   | 74  | 05:01    |
 +----+-------------+--------------+-----------+-----+-----+----------+
 Total duration: 17:06 / 25:00

Save songset '你信實何廣大_信實偉大_我心敬拜_恩典之路' (4 songs) for alice@example.com? [y/N]: y

✓ Created songset ss_0a1b2c3d '你信實何廣大_信實偉大_我心敬拜_恩典之路' (4 songs, 17:06)
```

### Explicit name, non-interactive

```
$ sow-admin songset create -u bob@example.com -n "Sunday_Set_1" -y \
    song_0123 song_0044 song_0089
✓ Created songset ss_0a1b2c3d 'Sunday_Set_1' (3 songs, 12:02)
```

### Oversize list (rejected early)

```
$ sow-admin songset create -u alice@example.com \
    song_001 song_002 song_003 song_004 song_005 song_006
Error: songset exceeds maximum of 5 songs (got 6). Trim the list or split into two songsets.
```

### Oversize duration (rejected after resolution)

```
Error: songset exceeds maximum duration of 25 minutes (got 26:14). Drop one song or pick shorter recordings.
```

## Testing Plan

All tests under `ops/admin-cli/tests/admin/test_songset_create.py`. Follow the
mocking conventions already used in `tests/admin/test_audio_soft_delete_maintenance.py`
(in-memory `ReadOnlyClient` / `SongsetClient` stubs).

### Unit tests for `resolve_song_token`

| Case | Expected |
|------|----------|
| Token is exact `song_id` of an active song with one active recording | returns `(song, recording)` |
| Token is `song_id` of a soft-deleted song | `typer.Exit(1)` |
| Token is a title with a single match | returns `(song, recording)` |
| Token is a title with multiple matches, interactive mode | renders table, prompts via `typer.prompt`, returns the picked song |
| Token is a title with multiple matches, `--yes` mode | `typer.Exit(1)` with guidance to use `song_id` |
| Token is a title with zero matches | `typer.Exit(1)` with the failing token |
| Song resolves but has no active recording | `typer.Exit(1)` |
| Token is a title matching via `title_pinyin` (not `title`) | resolves correctly (`search_songs` covers both columns) |

### Unit tests for `create_songset`

| Case | Expected |
|------|----------|
| 4 valid tokens, no `--name` | persists via `create_songset_with_items` with chained-title name; output prints `"Created songset"` line |
| 4 valid tokens, `--name "Custom"` | persists with name `"Custom"` |
| 6 tokens | `typer.Exit(1)` before any DB lookup |
| 5 valid tokens whose recordings sum to 1600 s | `typer.Exit(1)` with duration-limit message; `create_songset_with_items` not called |
| `--dry-run` with valid 4 tokens | full resolution + summary table printed; `create_songset_with_items` not called |
| `MissingReferenceError` raised by `create_songset_with_items` (edge: recording soft-deleted between resolution and persist) | caught; `[red]` message; `typer.Exit(1)` |
| Duplicate song in token list | `[yellow]` warning printed, both occurrences persisted (mirrors webapp behaviour) |
| `--yes` with ambiguous title | `typer.Exit(1)` from `resolve_song_token` before persistence |

### Constants-parity test

A sanity test (in `test_songset_create.py`) asserts
`SONGSET_MAX_SONGS == 5` and `SONGSET_MAX_DURATION_SECONDS == 1500`, with a
comment pointing to `delivery/webapp/src/lib/constants.ts:1-2` and a note to
keep them in sync. A failing test surfaces drift promptly.

### Run command

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/test_songset_create.py -v
```

## File-level changes summary

### New files

1. `ops/admin-cli/src/stream_of_worship/admin/constants.py`
2. `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py`
   (only if `commands/songset.py` would otherwise exceed ~700 lines)
3. `ops/admin-cli/tests/admin/test_songset_create.py`

### Edited files

1. `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py`
   — adds `create_songset` (and any necessary helpers). Optionally imports
   `resolve_song_token` from the helper module.
2. `ops/admin-cli/src/stream_of_worship/admin/commands/__init__.py`
   — only if the new command lives in a separate module (option a or b above).
   If option (c) is used, no edit needed because the command lives in
   `commands/songset.py` which is already imported.

## Risk & Mitigation

| Risk | Mitigation |
|------|-----------|
| Recording is soft-deleted *between* the resolution (step 4) and the persist (step 9). `create_songset_with_items` re-validates and raises `MissingReferenceError`. | Catch and surface a friendly `[red]` message with the recording hash; suggest retrying. |
| Title-fuzzy match returns the wrong song silently (e.g., user meant "信實" the song vs "信實" the chord chart, etc.) | Always render the confirmation table (step 7) before persistence, even in `--yes` mode. In `--yes` mode, do not skip the table — only skip the `y/N` prompt. This makes ambiguous picks visible. |
| Constants drift between webapp and admin CLI. | Add the parity test noted above. Long-term refactor: ship constants in a small shared JSON / env-backed module consumed by both stacks. |
| Webapp maximum-duration check is computed at render-time only (`POST /api/render-jobs`), not enforced at songset edit. Adding a strict create-time check could surprise operators used to the looser webapp flow. | Document in command `--help`: `"enforced both at create-time and render-time — matches the webapp's profile."` Surface the limit prematurely rather than collapsing a render later. |
| Operator passes a song that has no recording (audio not yet imported). | Hard-fail at `resolve_song_token` with clear `No active recording for song <id> '<title>'` message. Do not silently insert a `None` recording_hash_prefix. |
| Operator passes a title that triggers full-text search against `lyrics_raw`. | Resolve only against `field="title"` (covers `title` and `title_pinyin` columns). Never use `field="all"` for `resolve_song_token` to avoid surprise lyrics matches. |

## Out-of-band follow-ups (non-blocking)

1. Add `--tune <JSON>` flag for per-item transition overrides once a stable
   JSON shape exists (currently ad-hoc via the webapp editor).
2. Fold webapp + admin constants into a shared config source (e.g.,
   serialized JSON or a Postgres-backed `system_config` table).
3. Add `sow-admin render enqueue --songset <id>` as a future sibling command
   that performs the `render_jobs` insert + SQS dispatch currently living in
   `delivery/webapp/src/lib/render/job-manager.ts`. Reuse by `create --render`
   when implemented.
4. Consider `sow-admin songset import --csv <path>` for bulk creation.
