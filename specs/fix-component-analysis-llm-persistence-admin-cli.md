# Plan: Fix admin-cli LLM theme/posture persistence to song_components table

## Context

Investigation of COMPONENT_ANALYSIS job `job_9dc6fa08ba80` revealed that the
LLM theme/posture classification step completes successfully (logs confirm
`theme=奉獻, posture=To God` for all 17 components), yet the `song_components`
table shows NULL for `theme` and `vocal_posture` across **all 25 components**
in the database.

Query reproducing the symptom:

```sql
SELECT s.title, c.theme, c.vocal_posture
FROM   song_components c
JOIN   songs s ON s.id = c.song_id;
```

### Root cause

The analysis-service worker correctly:
1. Runs the LLM and populates `theme` / `vocal_posture` on in-memory
   `ComponentInstance`s (`queue.py:1039`, `classifier.py:459-464`).
2. Re-persists `components.json` to local cache + R2 **with** LLM fields
   (`queue.py:1047-1065`).
3. Builds `job.result.components` as `ComponentResult` Pydantic models **with**
   `theme`/`vocal_posture` (`queue.py:1086-1091`).
4. Persists `result_json` (including theme) to the `jobs` table
   (`queue.py:1119`).

**But the analysis-service never writes to the `song_components` DB table.**
There are zero references to `song_components` / `upsert_song_components`
anywhere in `ops/analysis-service/src/`. DB persistence of component rows is
exclusively admin-cli's responsibility, via `upsert_song_components`
(`ops/admin-cli/src/stream_of_worship/admin/db/client.py:1972-2046`).

Three admin-cli code paths silently drop or skip DB persistence, causing the
NULL theme/posture pattern observed in production.

## Bug 1: `--no-wait` silently drops DB persistence

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2072-2073 (`_submit_component_analysis_job`)

```python
# audio.py:2070-2073
console.print(f"[green]Component analysis submitted (job: {job.job_id})[/green]")

if not wait:
    return None
```

When `--no-wait` is passed (used by `download --components`, `components
--no-wait`, and stdin batch `--no-wait`), admin-cli submits the job and
returns immediately at line 2073. The worker runs server-side, completes the
LLM classification, and writes results to R2 + jobs table — but admin-cli
never calls `upsert_song_components`. The `song_components` table retains
whatever stale rows existed from a prior synchronous extraction
(audio-feature only, no LLM fields).

### Affected call sites

- `download --components` / `--all` — `audio.py:946-959` (`wait=False`)
- `components <song> --no-wait` — `audio.py:2374` (`wait=not no_wait`)
- `components --stdin --no-wait` — `audio.py:2294-2309` (`wait=False`)

## Bug 2: Cache-hit early-return ignores LLM flags

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2022-2037 (`_submit_component_analysis_job`)

```python
# audio.py:2022-2037
if not force:
    try:
        client = AnalysisClient(analysis_url, timeout=300)
        cached = client.get_cached_component_result(recording.hash_prefix)
        if cached is not None:
            components = _parse_component_results(
                cached.get("components", []), song_id, recording.content_hash
            )
            if components:
                db_client.upsert_song_components(song_id, recording.content_hash, components)
            return components
    except Exception:
        pass
```

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
**Location:** Lines 678-724 (`get_cached_component_result`)

```python
# analysis.py:717-720
payload = json.loads(resp["Body"].read().decode("utf-8"))
if payload.get("schema_version") != 1:
    return None
return payload
```

`get_cached_component_result` only validates `schema_version == 1`. It does
**not** check whether the cached components actually carry the LLM fields that
the caller requested via `--classify-theme` / `--classify-posture`.

The `COMPONENT_SCHEMA_VERSION` was already `1` before v5 added LLM fields (the
v5 widening only extended the component dict; see `components.py:
_serialize_components` lines 1462-1504). So a **pre-v5** `components.json`
(with all LLM fields NULL) has `schema_version == 1` and is accepted as a
valid cache hit.

Result: running `sow-admin audio components <song> --classify-theme` (without
`--force`) silently persists stale cached components with `theme=None` /
`vocal_posture=None` and returns without ever submitting a job to the
analysis service.

The analysis-service worker already has the analogous check:
`has_cached_llm_fields()` (`classifier.py:162-182`) — but admin-cli has no
equivalent guard on its cache-hit path.

## Bug 3: No sync mechanism for completed `--no-wait` jobs

There is no admin-cli command that pulls component results from R2 back into
the `song_components` table after a `--no-wait` job completes. The only
mechanism that recovers R2-persisted components into the DB is the cache-hit
early-return path inside `_submit_component_analysis_job` (Bug 2's code path),
which only fires when re-running `sow-admin audio components <song>` (without
`--force`).

`audio status` (audio.py:3023) only handles analysis/LRC job statuses — not
component jobs. `cache` (audio.py:4045) only downloads audio/stems/lrc to
local disk. `review-components` (audio.py:4516) only reads existing DB rows.

## Changes

### Change 1: Add `_cached_components_have_llm_fields` helper to admin-cli

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
**Location:** New module-level helper function near `get_cached_component_result`
(after line 724).

Mirror the analysis-service's `has_cached_llm_fields()` check
(`classifier.py:162-182`) but operating on raw dicts (the admin-cli cache path
returns parsed JSON dicts, not `ComponentInstance` objects).

```python
def _cached_components_have_llm_fields(
    components: list[dict],
    classify_theme: bool,
    classify_vocal_posture: bool,
    all_components: bool = False,
) -> bool:
    """Check whether cached component dicts carry the requested LLM fields.

    Mirrors analysis-service's has_cached_llm_fields() but operates on raw
    dicts from R2 components.json rather than ComponentInstance objects.

    Args:
        components: List of component dicts from cached components.json.
        classify_theme: Whether theme classification was requested.
        classify_vocal_posture: Whether vocal posture classification was requested.
        all_components: If True, require ALL components to have LLM fields.
            If False, only check essential-role components (entry/exit/
            loop_target/entry_exit).

    Returns:
        True if all relevant components have the requested LLM fields populated.
    """
    essential_roles = {"entry", "exit", "loop_target", "entry_exit"}
    for comp in components:
        is_candidate = all_components or comp.get("role", "none") in essential_roles
        if not is_candidate:
            continue
        if classify_theme and not comp.get("theme"):
            return False
        if classify_vocal_posture and not comp.get("vocal_posture"):
            return False
    return True
```

### Change 2: Reject cache hit when LLM fields are missing

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2022-2037 (`_submit_component_analysis_job`)

Modify the cache-hit early-return to also validate that the cached components
have the LLM fields the caller requested. If the cache lacks them, fall through
to job submission (which will recompute and re-persist).

```python
# Check R2 for cached components.json (unless force).
if not force:
    try:
        client = AnalysisClient(analysis_url, timeout=300)
        cached = client.get_cached_component_result(recording.hash_prefix)
        if cached is not None:
            cached_components = cached.get("components", [])
            # Validate cache has the requested LLM fields; if not, fall through.
            from ..services.analysis import _cached_components_have_llm_fields

            cache_valid = _cached_components_have_llm_fields(
                cached_components,
                classify_theme=classify_theme,
                classify_vocal_posture=classify_vocal_posture,
                all_components=all_components,
            )
            if cache_valid:
                console.print(
                    f"[green]Cached component result found in R2 "
                    f"(schema_version={cached.get('schema_version', '?')})[/green]"
                )
                components = _parse_component_results(
                    cached_components, song_id, recording.content_hash
                )
                if components:
                    db_client.upsert_song_components(
                        song_id, recording.content_hash, components
                    )
                return components
            else:
                console.print(
                    f"[yellow]Cached components.json lacks requested LLM fields; "
                    f"submitting new job to compute them.[/yellow]"
                )
    except Exception:  # noqa: S110 - Fall through to job submission.
        pass  # Fall through to job submission.
```

### Change 3: Warn explicitly on `--no-wait` instead of silent success

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2070-2073 (`_submit_component_analysis_job`)

Add an explicit warning that DB won't be updated until a subsequent sync, and
print instructions for recovery using the new `sync-components` command
(Change 4).

```python
console.print(f"[green]Component analysis submitted (job: {job.job_id})[/green]")

if not wait:
    console.print(
        f"[yellow]Job submitted in fire-and-forget mode. "
        f"song_components DB table will NOT be updated until you run:[/yellow]\n"
        f"  sow-admin audio sync-components {song_id}"
    )
    return None
```

### Change 4: Add `sync-components` command

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** New `@app.command("sync-components")` definition.

Naming follows the existing verb-noun convention used by other audio
subcommands (e.g. `upload-lrc`, `edit-lrc`, `view-lrc`, `review-components`,
`align-lrc`).

The command fetches the latest `components.json` from R2 for a song and
upserts it into `song_components`. This serves two purposes:
1. Recover `--no-wait` submissions after the job completes.
2. Backfill `song_components` from R2 cache without re-running any analysis.

**Sync source: R2 cache only.** The R2 `{hash_prefix}/components.json` is
written by the analysis-service worker after every successful component
extraction AND after every successful LLM classification re-persist
(`queue.py:1047-1065`). This is the canonical, always-up-to-date source.

Support both single-song and `--stdin` batch modes (mirroring the `components`
command's existing batch mode).

```python
@app.command("sync-components")
def sync_components(
    song_id: Optional[str] = typer.Argument(
        None, help="Song ID to sync components for"
    ),
    stdin: bool = typer.Option(
        False, "--stdin",
        help="Read song IDs from stdin (one per line) for batch sync"
    ),
    format_: str = typer.Option(
        "table", "--format", help="Output format (table|json)"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Sync component results from R2 cache to the song_components DB table.

    Fetches {hash_prefix}/components.json from R2 and upserts the component
    rows (including LLM theme/posture fields) into the database. Use this to:

      - Recover components from --no-wait submissions after the job completes.
      - Backfill song_components from an existing R2 components.json without
        re-running any analysis.
      - Re-persist R2 cache contents after a DB migration or schema repair.

    Does NOT submit any analysis-service jobs. The R2 cache must already
    exist (written by a prior component analysis job).

    Batch mode: pass --stdin to read song IDs from stdin (one per line).

    Exit codes:
      0 — components synced (or already up to date)
      1 — error (no recording, no R2 cache, no components in cache)
    """
    # Validate --format option.
    _validate_choice(format_, COMPONENTS_FORMAT_VALUES, "--format")

    # In JSON mode, route all progress/error messages to stderr.
    out_console = progress_console if format_ == "json" else console

    if not song_id and not stdin:
        out_console.print(
            "[red]Error: Either provide a song_id argument or use --stdin flag[/red]"
        )
        raise typer.Exit(1)

    if song_id and stdin:
        out_console.print(
            "[red]Error: Cannot use both song_id argument and --stdin flag[/red]"
        )
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        out_console.print(
            "[red]Config file not found. Run 'sow-admin db init' first.[/red]"
        )
        raise typer.Exit(1)

    db_client = get_db_client(config)

    # Batch mode via stdin.
    if stdin:
        raw = sys.stdin.read().splitlines()
        song_ids = [
            line.strip() for line in raw if line.strip() and not line.strip().startswith("#")
        ]
        if not song_ids:
            out_console.print("[red]No song IDs read from stdin[/red]")
            raise typer.Exit(1)

        results: list[dict] = []
        for sid in song_ids:
            recording = db_client.get_recording_by_song_id(sid)
            if not recording:
                results.append({"song_id": sid, "status": "failed", "error": "No recording found"})
                continue
            try:
                components = _sync_components_from_r2(
                    recording, sid, config.analysis_url, db_client, out_console
                )
                if components is None:
                    results.append({"song_id": sid, "status": "failed", "error": "No R2 cache"})
                else:
                    results.append({
                        "song_id": sid,
                        "status": "succeeded",
                        "components": [c.to_dict() for c in components],
                    })
            except Exception as e:
                results.append({"song_id": sid, "status": "failed", "error": str(e)})

        if format_ == "json":
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            succeeded = sum(1 for r in results if r["status"] != "failed")
            failed = sum(1 for r in results if r["status"] == "failed")
            out_console.print(
                f"\n[bold]Summary:[/bold] {succeeded} synced, {failed} failed"
            )
            for r in results:
                if r["status"] == "failed":
                    out_console.print(f"  [red]{r['song_id']}: {r['error']}[/red]")

        if any(r["status"] == "failed" for r in results):
            raise typer.Exit(1)
        return

    # Single song mode.
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        out_console.print(
            f"[red]No recording found for {song_id}.[/red]"
        )
        raise typer.Exit(1)

    components = _sync_components_from_r2(
        recording, song_id, config.analysis_url, db_client, out_console
    )
    if components is None:
        raise typer.Exit(1)

    if format_ == "json":
        print(json.dumps([c.to_dict() for c in components], ensure_ascii=False, indent=2))
    else:
        _render_components_table(
            components, console, title=f"Synced Components: {song_id}"
        )


def _sync_components_from_r2(
    recording: Recording,
    song_id: str,
    analysis_url: str,
    db_client: DatabaseClient,
    console: Console,
) -> Optional[list[SongComponent]]:
    """Fetch components.json from R2 and upsert into song_components DB table.

    Used by the sync-components command. Returns None if no R2 cache exists.

    Args:
        recording: Recording instance.
        song_id: Song ID.
        analysis_url: Analysis service base URL (unused but kept for parity
            with _submit_component_analysis_job).
        db_client: Database client.
        console: Console for output.

    Returns:
        Persisted SongComponent list, or None if no R2 cache exists.
    """
    client = AnalysisClient(analysis_url, timeout=300)
    cached = client.get_cached_component_result(recording.hash_prefix)
    if cached is None:
        console.print(
            f"[red]No cached components.json found in R2 for "
            f"{recording.hash_prefix}.[/red]\n"
            f"Run 'sow-admin audio components {song_id}' first."
        )
        return None

    cached_components = cached.get("components", [])
    has_llm = _cached_components_have_llm_fields(
        cached_components,
        classify_theme=True,
        classify_vocal_posture=True,
        all_components=False,
    )
    console.print(
        f"[green]Found cached components.json in R2 "
        f"(schema_version={cached.get('schema_version', '?')}, "
        f"{len(cached_components)} components, "
        f"LLM fields={'present' if has_llm else 'absent'})[/green]"
    )

    components = _parse_component_results(
        cached_components, song_id, recording.content_hash
    )
    if not components:
        console.print("[yellow]No components found in cached components.json.[/yellow]")
        return []

    db_client.upsert_song_components(song_id, recording.content_hash, components)
    console.print(
        f"[green]Synced {len(components)} component(s) from R2 to DB.[/green]"
    )
    return components
```

## Verification

### Unit tests

**File:** `ops/admin-cli/tests/admin/test_component_cache_validation.py` (new)

```python
def test_cached_components_have_llm_fields_all_populated():
    """Returns True when all essential components have theme + posture set."""
    components = [
        {"component_type": "chorus", "role": "entry", "theme": "奉獻", "vocal_posture": "To God"},
    ]
    assert _cached_components_have_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True
    )

def test_cached_components_have_llm_fields_missing_theme():
    """Returns False when a candidate component has theme=None."""
    components = [
        {"component_type": "chorus", "role": "entry", "theme": None, "vocal_posture": "To God"},
    ]
    assert not _cached_components_have_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True
    )

def test_cached_components_have_llm_fields_skips_non_essential():
    """Non-essential components are ignored (essential-only mode)."""
    components = [
        {"component_type": "chorus", "role": "entry", "theme": "奉獻", "vocal_posture": "To God"},
        {"component_type": "verse", "role": "none", "theme": None, "vocal_posture": None},
    ]
    assert _cached_components_have_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True, all_components=False
    )

def test_cached_components_have_llm_fields_all_components_mode():
    """all_components=True requires ALL components to have fields."""
    components = [
        {"component_type": "chorus", "role": "entry", "theme": "奉獻", "vocal_posture": "To God"},
        {"component_type": "verse", "role": "none", "theme": None, "vocal_posture": None},
    ]
    assert not _cached_components_have_llm_fields(
        components, classify_theme=True, classify_vocal_posture=True, all_components=True
    )
```

**File:** `ops/admin-cli/tests/admin/test_sync_components.py` (new)

Add integration test verifying the `sync-components` command fetches from R2
and upserts into DB (mock `get_cached_component_result` and
`upsert_song_components`).

### Manual verification

1. **Reproduce the original bug:**
   ```bash
   sow-admin audio components <song> --compute-all-fields --no-wait
   # Wait for job to complete (check: sow-admin audio status <job_id>)
   sow-admin audio show <song>  # theme/posture columns still show "-"
   ```

2. **Verify Change 3 (`--no-wait` warning):**
   ```bash
   sow-admin audio components <song> --compute-all-fields --no-wait
   # Should print warning:
   #   "song_components DB table will NOT be updated until you run:
   #    sow-admin audio sync-components <song>"
   ```

3. **Verify Change 2 (cache rejection):**
   ```bash
   # If R2 components.json lacks theme (pre-v5), running:
   sow-admin audio components <song> --classify-theme
   # Should print: "Cached components.json lacks requested LLM fields;
   #                submitting new job to compute them."
   # And submit a new job instead of persisting NULLs.
   ```

4. **Verify Change 4 (`sync-components` single song):**
   ```bash
   # After a --no-wait job has completed:
   sow-admin audio sync-components <song>
   # Should fetch from R2 and upsert — theme/posture now visible in:
   sow-admin audio show <song>
   ```

5. **Verify Change 4 (`sync-components` batch):**
   ```bash
   printf 'song_a\nsong_b\nsong_c\n' | sow-admin audio sync-components --stdin
   # Should sync all three from R2 → DB.
   ```

Run tests:
```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  pytest tests/admin/test_component_cache_validation.py tests/admin/test_sync_components.py -v
```

## Recovery (immediate, no code changes needed)

For the current 25 components with NULL theme/posture, the LLM results already
exist in R2 `components.json` (written by the worker at `queue.py:1047-1065`).
Once Change 4 is implemented, recover them via:

```bash
# Single song
sow-admin audio sync-components zhu_a__wo_yao_gen_sui_mi_83163301

# All songs in batch
sow-admin audio sync-components --stdin < song_ids.txt
```

NOTE: This works **only** if R2 `components.json` was written by a worker
that ran the LLM. If the `components.json` lacks theme (pre-v5 run), use
`--force --classify-theme --classify-posture` on `sow-admin audio components`
to recompute.

## Critical files

- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
  - Lines 2022-2037: Add cache-hit LLM field validation (Change 2)
  - Lines 2070-2073: Add `--no-wait` warning (Change 3)
  - New `sync-components` command + `_sync_components_from_r2` helper
    (Change 4)
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
  - After line 724: Add `_cached_components_have_llm_fields()` helper
    (Change 1)
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py`
  - Lines 1972-2046: `upsert_song_components` — already includes theme/posture
    columns (no change needed; referenced for context)
- `ops/admin-cli/tests/admin/test_component_cache_validation.py` (new)
- `ops/admin-cli/tests/admin/test_sync_components.py` (new)
- `ops/analysis-service/src/sow_analysis/workers/classifier.py`
  - Lines 162-182: `has_cached_llm_fields()` — already exists (referenced as
    the model for the admin-cli mirror; no change needed)

## Related specs

- `specs/fix-component-analysis-llm-cache-reuse.md` — Worker-side cache reuse
  fix (already implemented). This spec addresses the admin-cli side that was
  not covered by that fix.
