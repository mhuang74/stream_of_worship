# `sow-admin songset create` Implementation Plan v2

## Status: supersedes v1

This is the v2 plan. It supersedes `specs/admin-cli-songset-create-v1.md`. v1 is
left intact as a historical record; do not edit it. Implementation work should
target this v2 plan.

## What changed from v1

| # | v1 behaviour | v2 behaviour | Reason |
|---|--------------|--------------|--------|
| 1 | Validation Rules table cites `delivery/webapp/src/lib/db/songsets.ts:952-959` as the duration-enforcement source in the webapp. | Updated citation: that reference only enforces the max-songs count. The webapp enforces total-duration only at render-time (`render-jobs/route.ts:60`). Admin CLI's create-time check is **stricter** than the webapp editor. | v1 had a factually wrong source for the duration rule. The webapp editor allows oversize sets; they fail at render. Admin CLI's create-time check surfaces the failure earlier. |
| 2 | Missing `recording.duration_seconds` (None) is treated as `0` and treated as passing the duration check. | Hard-fail at `resolve_song_token`: raise `typer.Exit(1)` with message `No duration_seconds on recording <hash> for song <id>. Re-run audio import or pick a different recording.` | Treating None as 0 silently lets an uncataloged recording slip under the 25-min ceiling, only to fail at render. Surfacing the failure earlier is preferable. |
| 3 | When a song has multiple active recordings, "the" active recording is fetched via `get_recording_by_song_id`. | Use a new `list_active_recordings_by_song_id(song_id)` helper ordered by `imported_at DESC`; pick `[0]`. Document this as the "latest-active-wins" rule. | Existing `get_recording_by_song_id` uses `fetchone()` without `ORDER BY`, so the choice is non-deterministic. v2 makes the rule explicit. No interactive recording picker. |
| 4 | `--format json` not discussed. | v2 still ships **table-only** output. No `--format` flag added. | Operator preference: the green confirmation line + table is sufficient. Machine-readable output for scripted callers is deferred. A future `sow-admin songset render enqueue` (out-of-scope) will own the scriptable surface. |
| 5 | No name-collision policy. (Spec didn't address.) | Auto-append `_2`, `_3`, … to the songset name until unique within the same owner. | Operator preference: don't make them think about names. The `songsets` table has NO uniqueness constraint on `(user_id, name)` (verified in `delivery/webapp/src/db/schema.ts:180-196`), so collisions are by operator convention only. Auto-suffix happens client-side after listing the owner's existing songset names (`SongsetClient.list_songsets_for_user_id`). |
| 6 | Spec was internally contradictory: help text said `--yes` "refuses to disambiguate titles"; risk row said "always print table even in `--yes` mode". | v2 keeps "always print table" for `--yes` mode. The `--yes` flag's only effect is to skip the `y/N` confirmation prompt. | Operator preference: the table catches ambiguous title picks even in scripted use. The disambiguation-interaction refusal is a side effect of `--yes`, not its primary meaning. |
| 7 | Tokens that look like `song_\d+` but fail `get_song` fall through to title search silently. | Warn-then-fallback: if token matches `^song_\d+$` and `get_song` returns None, print `[yellow]Token N 'song_0124' looks like an ID but no song exists with that ID — falling back to title search.[/yellow]` before the title lookup. | Catches typo'd IDs that happen to collide with song titles; the warning makes the fallback visible without blocking operator flow. |
| 8 | `--user` required, no env fallback. | `--user` flag value takes precedence. If omitted, fall back to `SOW_DEFAULT_USER` env var. If both absent, error. | Batch usage benefit; one env var per shell session. Mirrors the convention used in other ops tooling in this project. |
| 9 | Spec had `min_args=1` on `typer.Argument`, which is not a valid Typer Argument keyword. | Use `min=1` on `typer.Argument`, or use a manual `if not songs: raise typer.Exit(1)` guard after parsing. | Code correctness — `min_args` does not exist on Typer's `Argument`. Verify against installed Typer version before implementing. |
| 10 | Spec described persistence as "atomic" via `create_songset_with_items`. | v2 keeps using `create_songset_with_items` (it is atomic w.r.t. recording_hash_prefix existence in the `recordings` table within a single transaction). v2 also notes explicitly that it does **NOT** validate `song_id` existence in `songs` — only `recording_hash_prefix` exists in `recordings` (see `songset_client.py:712-734`). | Accuracy. There's a narrow race: a song could be hard-deleted between the `resolve_song_token` step and the persist step. Mitigation noted in Risk table. |

## Problem statement (unchanged from v1)

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
songset-size limits as the webapp render path, and saves the songset under a
given user context.

## Scope

### In scope

- New `create` subcommand on the existing `songset` Typer group (placed in
  `commands/songset.py` next to `construct`, per option (c) — see "Command
  registration" below).
- Accept a variadic positional list of song tokens (each is either a song ID or
  a song title). Use `typer.Argument(..., min=1)` to enforce ≥1 token.
- Resolve each token to a `(song, recording)` pair.
- When a song has multiple active recordings: pick the latest by `imported_at
  DESC`. No interactive recording picker.
- Enforce `SONGSET_MAX_SONGS = 5` and `SONGSET_MAX_DURATION_SECONDS = 1500` at
  create time. The duration check is **stricter than the webapp editor** (which
  only enforces at render time); document this in `--help`.
- Hard-fail on missing `recording.duration_seconds` (None); do not silently
  treat as 0.
- Auto-generate a name from resolved titles when `--name` is omitted.
- Auto-suffix the songset name with `_2`, `_3`, … if a songset with the same
  name already exists for the same owner.
- Persist atomically via existing `SongsetClient.create_songset_with_items`.
- Accept `SOW_DEFAULT_USER` env var as a fallback for `--user`.

### Out of scope (explicitly deferred, unchanged from v1)

- **Render-job trigger**: The webapp is the only component that writes to
  `render_jobs` and dispatches to the SQS render queue. Replicating that logic
  in `sow-admin` would duplicate validation, the
  `uq_render_jobs_active_per_songset_user` unique constraint handling, and SQS
  auth. Defer until a shared render-dispatch helper exists. After `create` the
  operator triggers rendering through the webapp UI or a future
  `sow-admin render enqueue` command.
- Transition parameter tuning (`gap_beats`, `crossfade_enabled`,
  `key_shift_semitones`, `tempo_ratio`). Persisted with the same defaults as
  `SongsetClient.add_item` (`gap_beats=2.0`, `crossfade_enabled=False`,
  `key_shift_semitones=0`, `tempo_ratio=1.0`).
- A `--format json` machine-readable output flag. Deferred — for scripted
  automation, parse the green `Created songset ss_...` line via the songset ID
  regex `ss_[0-9a-f]+`.
- Bulk songset import from CSV/JSON.

## Decisions

| Decision | Choice |
|----------|--------|
| Render-job trigger | **Descoped from v1.** See "Out of scope". |
| Where validation lives | **Create-time + (existing) render-time.** Both song-count and total-duration limits enforced at `create`. The webapp enforces count at `addSongsetItem` (`songsets.ts:952-959`) and duration only at `POST /api/render-jobs` (`render-jobs/route.ts:60`). Admin CLI's create-time duration check is **stricter than the webapp editor** — surface this in `--help`. |
| Ambiguous title match resolution | **Interactive picker** — list all matches with song_id / album / composer, prompt user to pick by number. In `--yes` mode, error and ask for `song_id`. |
| Multiple active recordings | **Latest-active-wins**: order by `imported_at DESC`, pick first. Document the rule explicitly in `--help`. No interactive recording picker. |
| Missing `recording.duration_seconds` | **Hard-fail** at `resolve_song_token` with `No duration_seconds on recording <hash> for song <id>. Re-run audio import or pick a different recording.` Do not silently treat as 0. |
| Token looks like `song_\d+` but ID lookup misses | **Warn, then fall back to title search.** Print yellow warning `Token N '<token>' looks like an ID but no song exists with that ID — falling back to title search.` before the title lookup. |
| Songset name collision with same owner | **Auto-suffix `_2`, `_3`, …** until unique. No DB uniqueness constraint exists on `(user_id, name)` (`schema.ts:180-196`), so the check is client-side only, after listing the owner's songset names via `SongsetClient.list_songsets_for_user_id`. |
| Python constants location | New module `ops/admin-cli/src/stream_of_worship/admin/constants.py` mirroring `delivery/webapp/src/lib/constants.ts:1-2`. Note duplication; refactor to single source later. |
| Persistence path | Reuse existing `SongsetClient.create_songset_with_items` (atomic single-transaction insert for items + songset row). |
| Atomicity scope | Note explicitly: `create_songset_with_items` validates `recording_hash_prefix` existence in `recordings` (`songset_client.py:712-734`) but does **NOT** validate `song_id` existence in `songs`. Mitigate the narrow race (song hard-deleted between resolution and persist) by surfacing `MissingReferenceError` and any FK violation as a friendly `[red]` message. |
| `--user` source | `--user` flag is optional. If absent, fall back to `SOW_DEFAULT_USER` env var. If both absent, error: `No user specified. Pass --user or set SOW_DEFAULT_USER env var.` |
| `--yes` mode semantics | `--yes` skips the `y/N` confirmation prompt only. The summary table is **always printed** (even in `--yes`) so that ambiguous picks and duplicates are visible. `--yes` does NOT silently refuse title disambiguation — instead, an ambiguous title in `--yes` mode raises `typer.Exit(1)` from `resolve_song_token`. |
| Confirmation table | Always printed, both interactive and `--yes` modes. To stderr or stdout is implementation detail; default to stdout for parity with `list` command. |
| Machine-readable output | **Deferred.** No `--format` flag on `create`. Scripts parse the final green `Created songset ss_...` line via regex `ss_[0-9a-f]+`. |

## Key changes

### 1. New shared constants module

**File (new):** `ops/admin-cli/src/stream_of_worship/admin/constants.py`

```python
"""Shared constants mirroring the webapp's constants.ts.

NOTE: these duplicate `delivery/webapp/src/lib/constants.ts:1-2`. When a shared
config source lands, prefer that and remove this module.
"""

SONGSET_MAX_SONGS = 5
SONGSET_MAX_DURATION_SECONDS = 1500  # 25 minutes
```

### 2. New read-client helper for latest active recording

**File (edit):** `ops/admin-cli/src/stream_of_worship/db/app/read_client.py`

Add a new method next to `get_recording_by_song_id` (around line 276-303):

```python
def list_active_recordings_by_song_id(
    self, song_id: str, *, include_deleted: bool = False
) -> list[Recording]:
    """List all (optionally active) recordings for a song, latest first.

    Args:
        song_id: The song ID.
        include_deleted: If False (default), exclude soft-deleted recordings.

    Returns:
        List of Recordings ordered by ``imported_at DESC``. Empty if none.
    """
    cursor = self.connection.cursor()
    deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
    cursor.execute(
        f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings "
        f"WHERE song_id = %s{deleted_clause} ORDER BY imported_at DESC",
        (song_id,),
    )
    return [Recording.from_row(tuple(r)) for r in cursor.fetchall()]
```

`resolve_song_token` will use this new method and pick index `[0]`. Do **not**
modify existing `get_recording_by_song_id` — other call sites depend on it.

### 3. Song-resolution helper

**File (new):** `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py`

Split off the helper functions from `commands/songset.py` to keep that file
under ~700 lines. Helpers exported:

- `resolve_song_token(token, read_client, console, *, non_interactive) -> tuple[Song, Recording]`
- `_sanitize_title_for_name(title: str) -> str`
- `_dedupe_songset_name(name: str, existing_names: set[str]) -> str`
- `_format_duration(seconds: float | None) -> str`

`resolve_song_token` flow:

1. If token matches `^song_\d+$`:
   - Call `read_client.get_song(token)`.
   - If found (and not soft-deleted — `get_song` already excludes deleted by
     default per `read_client.py:83-105`), proceed to step 6.
   - If None: print yellow warning `Token '<token>' looks like an ID but no song
     exists with that ID — falling back to title search.` and continue to step 2.
2. Treat `token` as a title. Call
   `read_client.search_songs(token, field="title", limit=20, include_deleted=False)`
   (search covers both `title` and `title_pinyin` columns; see
   `read_client.py:191-193`).
3. Zero matches: `typer.Exit(1)` with message `No song found for token '<token>'.`
4. One match: use it.
5. Multiple matches:
   - If `non_interactive`: `typer.Exit(1)` with guidance
     `Multiple matches for '<token>' — supply the song_id directly.`
   - Else: render `rich.table.Table` (columns: `#`, `Song ID`, `Title`,
     `Album`, `Key`, `BPM`), prompt `Pick [1-N]:` via `typer.prompt`. Validate
     the picked index is in range; re-prompt if not.
6. Verify the chosen song has at least one active recording:
   - `recordings = read_client.list_active_recordings_by_song_id(song.id, include_deleted=False)`.
   - Empty: `typer.Exit(1)` with message `No active recording for song <id> '<title>'.`
7. Pick the **latest** active recording: `recording = recordings[0]`
   (list is already ordered `imported_at DESC`).
8. Verify `recording.duration_seconds` is not None:
   - `None`: `typer.Exit(1)` with message `No duration_seconds on recording <hash_prefix> for song <song_id> '<title>'. Re-run audio import or pick a different recording.`
9. Return `(song, recording)`.

### 4. New `create` subcommand

**File (edit):** `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py`

Append after `construct_songset` (ends around line 584). Imports `resolve_song_token`,
`_sanitize_title_for_name`, `_dedupe_songset_name`, `_format_duration` from
`_songset_create_helpers`.

```python
@app.command("create")
def create_songset(
    songs: list[str] = typer.Argument(
        ...,
        help="Ordered song IDs and/or titles (resolved in order)",
        min=1,
    ),
    user: str | None = typer.Option(
        None,
        "--user",
        "-u",
        help="Email of the user to own the songset. Falls back to "
             "SOW_DEFAULT_USER env var if omitted.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Songset name. If omitted, chained from resolved song titles "
             "(song1_song2_song3). If a songset with this name already "
             "exists for the same owner, a numeric suffix (_2, _3, ...) is "
             "appended.",
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
        help="Non-interactive: skip y/N confirmation only. Ambiguous title "
             "matches still error (use song_id); the summary table is "
             "always printed.",
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
    user identified by --user (resolved by email; falls back to
    SOW_DEFAULT_USER env var if --user is omitted).

    Enforces SONGSET_MAX_SONGS=5 and SONGSET_MAX_DURATION_SECONDS=1500
    (25 min total recording duration) at create time. This is STRICTER than
    the webapp editor, which only enforces total duration at render time
    (POST /api/render-jobs). The webapp editor allows oversize sets that
    then fail at render; this command surfaces the failure earlier.

    When a song has multiple active recordings, the latest one (by imported_at)
    is selected — the "latest-active-wins" rule.

    Examples:
      sow-admin songset create --user alice@example.com \\
          song_0123 "信實偉大" "song_0089" "恩典之路"

      sow-admin songset create -u bob@example.com -n "Sunday_Set_1" \\
          song_0123 song_0044 song_0089 --yes

      # Use env var for batch:
      export SOW_DEFAULT_USER=alice@example.com
      sow-admin songset create song_0123 song_0089 -y
    """
```

#### Execution flow

1. **Resolve `--user`** — If `user` is None, read `SOW_DEFAULT_USER` from env.
   If both are None/empty, raise `typer.Exit(1)` with message `No user
   specified. Pass --user or set SOW_DEFAULT_USER env var.`
2. **Load config** — `AdminConfig.load(config_path)`. Fail with `typer.Exit(1)`
   on `FileNotFoundError` (same pattern as `songset list` at
   `commands/songset.py:98-101`).
3. **Resolve user** — `UserClient.get_user_by_email(user)`. Fail with
   `typer.Exit(1)` if `None` (same as `construct` at `commands/songset.py:458-463`).
4. **Business-rule validation (cheap, pre-DB)** —
   `if len(songs) > SONGSET_MAX_SONGS: raise typer.Exit(1)` with message
   `songset exceeds maximum of 5 songs (got N). Trim the list or split into two songsets.`
   Skips any DB round-trips for an obviously oversize list.
5. **Resolve every token** — For each `token` in `songs` (in order), call
   `resolve_song_token(token, read_client, console, non_interactive=yes)`.
   Collect `resolved: list[tuple[Song, Recording]]`.
6. **Duration check** — Sum `recording.duration_seconds` across `resolved`.
   (`duration_seconds` is guaranteed non-None after step 5's hard-fail.) If
   `> SONGSET_MAX_DURATION_SECONDS`, fail with the same message the webapp
   returns at render-time: `Songset exceeds maximum duration of 25 minutes (got M:SS). Drop one song or pick shorter recordings.`
7. **Auto-name** — If `--name` is None, build it from `resolved[i].song.title`
   via `_sanitize_title_for_name(...)` joined by `_`. If any title is empty
   after sanitize, fall back to the song_id at that position.
8. **Deduplicate name within owner** —
   - Call `songset_client.list_songsets_for_user_id(resolved_user.id)` to get
     all existing songset names for the owner.
   - If `name` is already in that set, call `_dedupe_songset_name(name,
     existing_names)` to produce `name_2`, `name_3`, etc. (first unused suffix).
9. **Truncate name** — If `len(name) > 255`, truncate to 252 chars + `"..."`.
   Note: Python slicing is per-character (codepoint), which is safe for CJK
   titles. Avoid byte-slicing.
10. **Confirmation summary table** — Render a `rich.table.Table` with columns:
    `#`, `Song ID`, `Title`, `Album`, `Key`, `BPM`, `Duration`,
    `Recording Hash Prefix` for each resolved song. Show computed name and
    total duration footer. For duplicate songs (same `song_id` appears
    multiple times in `resolved`), mark the duplicate row's `#` column with
    `[yellow]N (dup)[/yellow]` AND print a `[yellow]⚠ song X appears
    twice[/yellow]` line before the table. **Always printed**, `--yes` or
    not.
11. **Confirm** — If not `--yes`, `typer.confirm("Save songset '<name>' (N
    songs, M:SS) for <user_email>?")`.
12. **Dry-run** — If `--dry-run`, skip step 13, print
    `[yellow]Dry run: skipping DB writes.[/yellow]` and return.
13. **Persist** —
    ```python
    songset_client = SongsetClient(connection_provider, user_id=resolved_user.id)
    try:
        songset = songset_client.create_songset_with_items(
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
        )
    except MissingReferenceError as e:
        console.print(f"[red]Persistence failed: recording reference missing: {e}[/red]")
        console.print("[red]A recording may have been soft-deleted after resolution. Retry the command.[/red]")
        raise typer.Exit(1)
    except Exception as e:
        # Catch FK violations from song_id being hard-deleted between step 5
        # and step 13, surface friendlier message.
        console.print(f"[red]Persistence failed: {e}[/red]")
        raise typer.Exit(1)
    ```
14. **Output** — Print
    `[green]✓ Created songset <id> '<name>' (N songs, M:SS)[/green]`
    where `<id>` is `songset.id` (a `ss_<hex>` string). This line is the
    script-friendly success signal — scripts can extract the ID with regex
    `ss_[0-9a-f]+`.

### 5. Command registration

Place the `create_songset` function in `commands/songset.py` (option c from v1).
Helper functions go into the new `commands/_songset_create_helpers.py` module
and are imported by `commands/songset.py` at the top of the file.

`commands/__init__.py` does not need editing (it currently contains only a
docstring; commands are registered via the `songset` Typer group's side-effect
imports elsewhere in the app bootstrap).

**File-level changes summary:**

| File | Status | Purpose |
|------|--------|---------|
| `ops/admin-cli/src/stream_of_worship/admin/constants.py` | new | `SONGSET_MAX_SONGS`, `SONGSET_MAX_DURATION_SECONDS` |
| `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py` | new | `resolve_song_token`, title sanitizer, name deduper, duration formatter |
| `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` | edit | Add `create_songset` after `construct_songset`; import helpers |
| `ops/admin-cli/src/stream_of_worship/db/app/read_client.py` | edit | Add `list_active_recordings_by_song_id` method |
| `ops/admin-cli/tests/admin/test_songset_create.py` | new | Unit tests (see Testing Plan) |
| `ops/admin-cli/tests/admin/test_read_client_latest_recording.py` | new (optional) | Tests for the new `list_active_recordings_by_song_id` ordering |

## Validation rules

Mirrors `delivery/webapp/src/app/api/render-jobs/route.ts:60-62` (render-time)
and `delivery/webapp/src/lib/db/songsets.ts:952-959` (count, at-add-time).
Admin CLI enforces BOTH at create-time — which is stricter than the webapp
editor (which only enforces count, not duration, at edit time).

| Rule | Threshold | Where enforced | Source |
|------|-----------|----------------|--------|
| Max songs per songset | `> 5` rejected | `create_songset`, step 4 | `constants.SONGSET_MAX_SONGS` (mirrors `constants.ts:1`) |
| Max total duration | `> 1500` seconds rejected | `create_songset`, step 6 | `constants.SONGSET_MAX_DURATION_SECONDS` (mirrors `constants.ts:2`); webapp enforces at `render-jobs/route.ts:60` |
| Each song must exist & not be soft-deleted | ID lookup via `ReadOnlyClient.get_song`; title search excludes deleted via `search_songs(include_deleted=False)` | `resolve_song_token` | `read_client.py:83-105`, `171-217` |
| Each song must have ≥ 1 active recording | `list_active_recordings_by_song_id(song_id, include_deleted=False)` returns non-empty list | `resolve_song_token` | (new method) |
| Each active recording must have `duration_seconds` | `recording.duration_seconds is not None` | `resolve_song_token` | (new — harder than webapp) |
| `recording_hash_prefix` must exist in `recordings` table | Validated inside `create_songset_with_items` (raises `MissingReferenceError`) | `create_songset`, step 13 | `songset_client.py:712-734` |
| Song name uniqueness within owner | Soft / client-side only — auto-suffix `_2`, `_3`, … | `create_songset`, step 8 | `SongsetClient.list_songsets_for_user_id`; no DB constraint at `schema.ts:180-196` |
| Ambiguous title match | Interactive picker if multiple matches; `typer.Exit(1)` in `--yes` mode | `resolve_song_token` | (new) |
| Duplicate songs in the ordered list | Allowed but warned (yellow line before table + `(dup)` marker in `#` column). The webapp allows duplicates; mirror that behaviour. | `create_songset`, step 10 | (new) |

## Auto-naming algorithm

When `--name` is not supplied:

```
for i in range(len(resolved)):
    title = resolved[i].song.title
    title = title.strip()
    title = title.replace(" ", "")              # compact, e.g. "信實偉大" not "信 實 偉 大"
    title = "".join(c for c in title if c.isprintable())   # drop non-printable
    if not title:
        title = resolved[i].song.id            # fallback
    sanitized.append(title)
name = "_".join(sanitized)
```

Example: titles `["信實偉大", "恩典之路", "主我敬拜你"]` → name
`"信實偉大_恩典之路_主我敬拜你"`.

If the resulting name exceeds 255 chars, truncate to 252 chars + `"..."`
(per-character slicing — safe for CJK codepoints).

If `name` collides with an existing songset for the same owner, append
`_2`, `_3`, … at the end (before the `"..."` truncation). The numeric suffix
itself is not counted toward the 255-char limit (matching the webapp's
`name: text("name").notNull()` at `schema.ts:185`, which has no length constraint
at the DB level — the 255-char limit is an application choice).

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

 +----+-----------+--------------+-----------+-----+-----+----------+
 | #  | Song ID   | Title        | Album     | Key | BPM | Duration |
 +----+-----------+--------------+-----------+-----+-----+----------+
 | 1  | song_0123 | 你信實何廣大  | 敬拜讚美15 | C   | 72  | 04:12    |
 | 2  | song_0045 | 信實偉大      | 敬拜讚美15 | G   | 76  | 03:48    |
 | 3  | song_0089 | 我心敬拜       | 敬拜讚美18 | D   | 80  | 04:05    |
 | 4  | song_0044 | 恩典之路      | 敬拜讚美15 | G   | 74  | 05:01    |
 +----+-----------+--------------+-----------+-----+-----+----------+
 Total duration: 17:06 / 25:00

Save songset '你信實何廣大_信實偉大_我心敬拜_恩典之路' (4 songs, 17:06) for alice@example.com? [y/N]: y

✓ Created songset ss_0a1b2c3d '你信實何廣大_信實偉大_我心敬拜_恩典之路' (4 songs, 17:06)
```

### Explicit name, non-interactive

```
$ sow-admin songset create -u bob@example.com -n "Sunday_Set_1" -y \
    song_0123 song_0044 song_0089
(same table printed above)
✓ Created songset ss_0a1b2c3d 'Sunday_Set_1' (3 songs, 12:02)
```

### Env-var user, batch use

```
$ export SOW_DEFAULT_USER=alice@example.com
$ sow-admin songset create song_0123 song_0089 -y
(table printed)
✓ Created songset ss_1b2c3d4e '你信實何廣大_我心敬拜' (2 songs, 09:17)
```

### Name collision auto-suffixed

```
$ sow-admin songset create -u alice@example.com -n "Sunday_Set_1" -y song_0091 song_0092
(table printed; Sunday_Set_1 already exists for alice)
✓ Created songset ss_2c3d4e5f 'Sunday_Set_1_2' (2 songs, 08:43)
```

### Oversize list (rejected early, no DB round-trips)

```
$ sow-admin songset create -u alice@example.com \
    song_001 song_002 song_003 song_004 song_005 song_006
Error: songset exceeds maximum of 5 songs (got 6). Trim the list or split into two songsets.
```

### Oversize duration (rejected after resolution)

```
$ sow-admin songset create -u alice@example.com \
    song_0101 song_0102 song_0103 song_0104 song_0105
Error: songset exceeds maximum duration of 25 minutes (got 31:27). Drop one song or pick shorter recordings.
```

### Missing duration (hard fail at resolution)

```
$ sow-admin songset create -u alice@example.com "信實偉大"
Error: No duration_seconds on recording rec_abc123 for song song_0045 '信實偉大'. Re-run audio import or pick a different recording.
```

### ID-shaped token with no matching song (warn then fallback)

```
$ sow-admin songset create -u alice@example.com song_0124 "信實偉大"
⚠ Token 'song_0124' looks like an ID but no song exists with that ID — falling back to title search.
(table continues with disambiguation or single-match resolve of 'song_0124' as a title …)
```

### Duplicate song in token list

```
$ sow-admin songset create -u alice@example.com song_0123 song_0123 song_0089
⚠ song song_0123 appears twice (positions 1 and 2)

 +--------+-----------+----------+-----+
 | #      | Song ID   | Title    | ... |
 +--------+-----------+----------+-----+
 | 1      | song_0123 | 你信實... | ... |
 | 2 (dup)| song_0123 | 你信實... | ... |
 | 3      | song_0089 | 我心敬拜 | ... |
 +--------+-----------+----------+-----+
```

## Testing plan

All tests under `ops/admin-cli/tests/admin/`. Follow the mocking conventions
already used in `tests/admin/test_audio_soft_delete_maintenance.py` (in-memory
`ReadOnlyClient` / `SongsetClient` stubs).

### Unit tests for `resolve_song_token`

| Case | Expected |
|------|----------|
| Token is exact `song_id` of an active song with one active recording | returns `(song, recording)` |
| Token is `song_id` of a soft-deleted song | `typer.Exit(1)` |
| Token matches `^song_\d+$` but `get_song` returns None | yellow warning printed, then falls through to title search |
| Token is a title with a single match | returns `(song, recording)` |
| Token is a title with multiple matches, interactive mode | renders table, prompts via `typer.prompt`, returns the picked song |
| Token is a title with multiple matches, `--yes` mode | `typer.Exit(1)` with guidance to use `song_id` |
| Token is a title with zero matches | `typer.Exit(1)` with the failing token |
| Song resolves but has no active recordings | `typer.Exit(1)` with `No active recording for song <id> '<title>'.` |
| Song resolves with multiple active recordings | Uses latest (first row of `list_active_recordings_by_song_id` result, `imported_at DESC`) |
| Active recording has `duration_seconds = None` | `typer.Exit(1)` with `No duration_seconds on recording <hash_prefix> for song <song_id> '<title>'.` |
| Token is a title matching via `title_pinyin` (not `title`) | resolves correctly (`search_songs` covers both columns) |

### Unit tests for `list_active_recordings_by_song_id` (new method)

| Case | Expected |
|------|----------|
| Song has 3 active recordings with different `imported_at` timestamps | returns all 3, ordered by `imported_at DESC` |
| Song has 1 active and 2 soft-deleted recordings, `include_deleted=False` | returns 1 recording |
| Same song, `include_deleted=True` | returns 3 recordings (no `deleted_at` filter) |
| Song has 0 recordings | returns empty list |

### Unit tests for `create_songset`

| Case | Expected |
|------|----------|
| 4 valid tokens, no `--name` | persists via `create_songset_with_items` with chained-title name; output prints `✓ Created songset` line |
| 4 valid tokens, `--name "Custom"` | persists with name `"Custom"` |
| 5 valid tokens whose recordings sum to 1600 s | `typer.Exit(1)` with duration-limit message; `create_songset_with_items` not called |
| 6 tokens | `typer.Exit(1)` before any DB lookup |
| `--dry-run` with valid 4 tokens | full resolution + summary table printed; `create_songset_with_items` not called |
| `MissingReferenceError` raised by `create_songset_with_items` (edge: recording soft-deleted between resolution and persist) | caught; `[red]` message; `typer.Exit(1)` |
| Generic `Exception` (e.g., FK violation from song hard-deleted between resolution and persist) | caught; `[red]` message; `typer.Exit(1)` |
| Duplicate song in token list | `[yellow]` warning line + `(dup)` marker in table; both occurrences persisted |
| `--yes` with ambiguous title | `typer.Exit(1)` from `resolve_song_token` before persistence |
| `--yes` with unambiguous title | confirms persist without `y/N` prompt; table still printed |
| Auto-name collision with existing songset for same owner | persists with suffixed name (e.g., `"Custom_2"`); passes that suffixed name to `create_songset_with_items` |
| `--user` omitted, `SOW_DEFAULT_USER` env var set | resolves env-var user, persists normally |
| `--user` omitted, `SOW_DEFAULT_USER` env var unset | `typer.Exit(1)` with `No user specified. Pass --user or set SOW_DEFAULT_USER env var.` |
| `--user` flag explicitly set | takes precedence over `SOW_DEFAULT_USER` env var |

### Unit tests for `_dedupe_songset_name`

| Case | Expected |
|------|----------|
| `name="Foo"`, existing `{}` | returns `"Foo"` |
| `name="Foo"`, existing `{"Foo"}` | returns `"Foo_2"` |
| `name="Foo"`, existing `{"Foo", "Foo_2"}` | returns `"Foo_3"` |
| `name="Foo"`, existing `{"Foo_2"}` (Foo disappeared) | returns `"Foo"` (first unused) |
| `name="Foo"`, existing `{"Foo", "Foo_2", "Foo_3"}` | returns `"Foo_4"` |

### Constants-parity test

A sanity test (in `test_songset_create.py`) asserts
`SONGSET_MAX_SONGS == 5` and `SONGSET_MAX_DURATION_SECONDS == 1500`, with a
comment pointing to `delivery/webapp/src/lib/constants.ts:1-2` and a note to
keep them in sync. A failing test surfaces drift promptly.

### Run command

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/test_songset_create.py -v

# Also test the new read-client method:
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/test_read_client_latest_recording.py -v
```

## Cost profile

For an N-song songset (N ≤ 5), the command makes approximately
`1 (user) + N (song resolve) + N (recording lookup) + 1 (list existing names)
+ 1 (persist) = 2N + 3` DB round-trips. For a 5-song set, that's 13
round-trips. Acceptable for a CLI command; documented here for future
profiling.

## Risk & mitigation

| Risk | Mitigation |
|------|-----------|
| Recording is soft-deleted *between* resolution (step 5) and the persist (step 13). `create_songset_with_items` re-validates and raises `MissingReferenceError`. | Catch and surface a friendly `[red]` message with the recording hash; suggest retrying. |
| Song is hard-deleted between resolution and persist. `create_songset_with_items` does NOT validate `song_id` existence (only `recording_hash_prefix`). Will surface as an FK violation if FK exists, or as an orphan row otherwise. | Catch generic `Exception` in step 13, surface `[red] Persistence failed: <e>` with retry guidance. Note in `--help` that song lifecycle is the operator's responsibility within the create window. |
| Title-fuzzy match returns the wrong song silently. | Always render the confirmation table (step 10) before persistence. The table is printed in `--yes` mode too — `--yes` only skips the `y/N` prompt. |
| Constants drift between webapp and admin CLI. | Constants-parity test (asserts `SONGSET_MAX_SONGS == 5` and `SONGSET_MAX_DURATION_SECONDS == 1500`). Long-term refactor: ship constants in a small shared JSON / env-backed module consumed by both stacks. |
| Create-time duration check is **stricter than the webapp editor** (which only enforces at render-time). May surprise operators used to the looser webapp flow where you can save an oversize set and only learn at render that it fails. | Document in command `--help`: `"enforced both at create-time and render-time — stricter than the webapp editor."` Surface the limit prematurely rather than letting it collapse at render. |
| Operator passes a song that has no recording (audio not yet imported). | Hard-fail at `resolve_song_token` with clear `No active recording for song <id> '<title>'` message. Do not silently insert a `None` recording_hash_prefix. |
| Operator passes a title that triggers full-text search against `lyrics_raw`. | Resolve only against `field="title"` (covers `title` and `title_pinyin` columns). Never use `field="all"` for `resolve_song_token` to avoid surprise lyrics matches. |
| Auto-generated name collides with an existing songset for the same owner. | Auto-suffix `_2`, `_3`, … in `create_songset` step 8, before persist. No DB constraint involved (`schema.ts:180-196` has no `(user_id, name)` unique index). |
| Auto-generated name exceeds 255 chars. | Truncate to 252 chars + `"..."` (per-character slice; safe for CJK). |
| Operator passes a token `song_0124` that doesn't exist as an ID but matches a title as a substring (e.g., a song whose title contains "song_0124"). | Yellow warning `Token 'song_0124' looks like an ID but no song exists with that ID — falling back to title search.` is printed before the title lookup. The confirmation table then surfaces the actual matched song for visual verification. |
| `recording.duration_seconds` is None (audio import incomplete / ffprobe failure). | Hard-fail at `resolve_song_token` step 8 with explicit `No duration_seconds on recording <hash_prefix> for song <song_id> '<title>'. Re-run audio import or pick a different recording.` Do not silently skip the duration check. |
| Multiple active recordings exist for a song; the "latest" by `imported_at` is wrong (operator wanted a specific version). | Document "latest-active-wins" rule in `--help`. Provide `--recording-hash` override flag in a future spec (out of scope here). |
| `SOW_DEFAULT_USER` env var is set to an email that doesn't exist in the DB. | `UserClient.get_user_by_email(user)` returns None → `typer.Exit(1)` with `User not found: <email>.` Same error path as `construct_songset`. |

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
5. Add `--recording-hash <hash_prefix>` flag to override the
   latest-active-wins rule for an individual token (for songs with multiple
   active recordings).
6. Add `--format json` flag once the scriptable surface stabilises (deferred
   per operator preference).
7. Add `song_id` existence pre-check inside `create_songset_with_items` (in
   addition to the existing `recording_hash_prefix` check) to close the
   narrow race where a song is hard-deleted between resolution and persist.

## Open questions for implementation

- Should the "warn then fallback" yellow warning (for ID-shaped tokens without
  a matching song) also apply when token matches `^song_[a-z0-9_]+$` more
  broadly, or strictly `^song_\d+$`? Decision: strict `^song_\d+$` — matches
  the actual ID format used by the scraper (`Song.generate_id` yields
  `song_<numeric>` per the existing convention).
- Should the duplicate-song warning also fire when a song is duplicated but
  with a different recording (different `recording_hash_prefix`)? Yes — the
  duplicate detection is on `song_id`, not on the recording.
- Does the new `list_active_recordings_by_song_id` method belong on
  `ReadOnlyClient` (used by admin-cli) or on `SongsetClient` (which already
  has `list_songsets_for_user_id`)? Decision: `ReadOnlyClient`. It's a read
  query and belongs with the read client.

## Implementation checklist (for the implementer)

- [ ] Create `constants.py` with `SONGSET_MAX_SONGS = 5` and
      `SONGSET_MAX_DURATION_SECONDS = 1500`.
- [ ] Add `list_active_recordings_by_song_id` to `ReadOnlyClient`.
- [ ] Create `_songset_create_helpers.py` with `resolve_song_token`,
      `_sanitize_title_for_name`, `_dedupe_songset_name`, `_format_duration`.
- [ ] Add `create_songset` to `commands/songset.py` after `construct_songset`.
- [ ] Verify `typer.Argument(..., min=1)` works on the installed Typer
      version. If not, use a manual `if not songs: raise typer.Exit(1)`
      guard.
- [ ] Verify `SOW_DEFAULT_USER` env-var read uses `os.environ.get(...)` not
      `typer.Option(envvar=...)` — we want a single fallback, not a Typer
      env-var binding (which would change help-text generation).
- [ ] Test manually: `sow-admin songset create --help` shows the new command
      alongside `list` and `construct`.
- [ ] Run:
      `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_songset_create.py -v`
- [ ] Add constants-parity test asserting
      `SONGSET_MAX_SONGS == 5` and `SONGSET_MAX_DURATION_SECONDS == 1500`.
- [ ] Update `MEMORY.md` (per AGENTS.md instructions) after the implementing
      commit lands.
