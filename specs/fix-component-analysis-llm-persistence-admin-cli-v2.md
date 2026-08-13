# Plan v2: Fix admin-cli LLM theme/posture persistence to song_components table

> Revised from `specs/fix-component-analysis-llm-persistence-admin-cli.md`
> after a production-readiness review against current code. The original v1
> spec is left unedited; this v2 supersedes it.

## Production review summary

The v1 spec (`fix-component-analysis-llm-persistence-admin-cli.md`) was
reviewed against current code. **Three critical / high-severity issues
found**, all of which would have caused silent data-loss or no-op recovery
on release.

### 🔴 Critical 1 — `schema_version` check is stale; rejects the very caches that carry LLM fields

- v1 spec claims `COMPONENT_SCHEMA_VERSION` "was already `1` before v5 added
  LLM fields" (lines 104–108). **Incorrect.** Current value is **`2`**
  (`ops/analysis-service/src/sow_analysis/storage/cache.py:15`), and the
  worker writes/rejects against `2`
  (`ops/analysis-service/src/sow_analysis/storage/r2.py:505`).
- admin-cli's `get_cached_component_result` still checks
  `payload.get("schema_version") != 1`
  (`ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:718`).
  So **every v2 cache written by the worker is silently rejected** as if
  it didn't exist.
- Consequences:
  - **Bug 2 as diagnosed is not currently reachable in prod** — the
    cache-hit early-return (`audio.py:2027-2037`) never fires, so no stale
    NULL-theme rows get persisted via that path today. The real
    persistence-loss bugs are Bug 1 (`--no-wait`) and the wait/poll path
    silently returning `None` on poll failure.
  - The new `sync-components` command would **always report "No cached
    components.json found in R2"**, making the **Recovery section a
    no-op** even though R2 has the LLM-bearing v2 caches. Worse: the
    operator sees a green-ish "synced" message, thinks data was recovered,
    but the `song_components` table is unchanged → silent data-gap.

### 🔴 Critical 2 — R2 credentials sourced from env vars, not config; failure is silent

- `get_cached_component_result` reads `SOW_R2_BUCKET` /
  `SOW_R2_ENDPOINT_URL` / `SOW_R2_ACCESS_KEY_ID` /
  `SOW_R2_SECRET_ACCESS_KEY` directly from env (`analysis.py:698-701`) and
  returns `None` if any are missing (`analysis.py:703-704`).
- Every other admin-cli command sources these from `config.r2_bucket` /
  `config.r2_endpoint_url` and the admin `R2Client` (which raises
  `ValueError` loudly on missing creds — `services/r2.py:86-93`). So in
  any environment where the config has R2 settings but those exact env
  vars aren't exported (very common in operator/CI shells),
  `sync-components` silently no-ops.
- Note: the admin `R2Client.download_component_result` method **already
  exists** (`r2.py:567`) and already uses config creds — it just doesn't
  itself validate `schema_version`. So the fix is small and idiomatic.

### 🟠 High 3 — `upsert_song_components` clobber (DELETE-then-INSERT) can silently drop rows

- `db/client.py:1995-2046`: `DELETE FROM song_components WHERE song_id,
  content_hash` then `INSERT`. If a re-segmented `sync-components` run
  returns fewer components than currently stored, the extras are silently
  dropped.
- v1 spec never mentions this. For a "recovery" command this is a real
  data-loss vector, especially in batch `--stdin` mode.

### Minor

- Change 2 puts
  `from ..services.analysis import _cached_components_have_llm_fields`
  **inside** the function body. Should be a top-level import.
- Bug 1 / Change 3 warning message hardcodes
  `sync-components {song_id}` while one of the affected call sites is
  batch (`--stdin --no-wait`). The warning is per-iteration so technically
  fine, but the message should point to `--stdin` for the batch case.
- Tests proposed in v1 don't cover the schema_version=2 contract nor the
  new `--dry-run` safeguard.

---

## Revised plan (v2)

Decisions captured from clarification:

- schema_version → fix to `!= 2` (compare to current constant).
- R2 creds → refactor to config + admin `R2Client`.
- Bug 2 / Change 2 → keep as defense-in-depth (validator still useful for
  any lingering pre-v5 caches once #1 unblocks the path).
- upsert clobber → add `--dry-run` + row-count warning.

### New prerequisites (Change 0 + Change 1)

#### Change 0 — Fix `get_cached_component_result`: config-driven creds + current schema_version

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
**Location:** Replace body of `get_cached_component_result` (currently
`analysis.py:678-724`).

Stop reading R2 creds directly from env. Delegate to the admin
`R2Client.download_component_result` (already exists at `services/r2.py:567`,
config-driven, raises `ValueError` on missing creds) and add the
schema_version guard using a shared constant.

```python
# ops/admin-cli/src/stream_of_worship/admin/services/analysis.py
# Module-level constant (new); kept in sync with analysis-service's
# sow_analysis.storage.cache.COMPONENT_SCHEMA_VERSION.
COMPONENT_SCHEMA_VERSION = 2
```

```python
def get_cached_component_result(
    self,
    hash_prefix: str,
    r2_client: Optional["R2Client"] = None,
) -> Optional[dict]:
    """Return parsed {hash_prefix}/components.json from R2, or None.

    Args:
        hash_prefix: 12-character content hash prefix.
        r2_client: Optional pre-constructed admin R2Client. If None, the
            caller MUST construct one from config (recommended pattern);
            this method will NOT fall back to env vars.

    Returns:
        Parsed components.json dict whose ``schema_version`` equals
        COMPONENT_SCHEMA_VERSION, or None if:
          - no object exists at {hash_prefix}/components.json (404/NoSuchKey),
          - the payload is corrupt JSON,
          - schema_version is missing or stale (pre-v2 caches).
    """
    if r2_client is None:
        raise ValueError(
            "get_cached_component_result requires an admin R2Client "
            "(constructed from config). The old env-var path was removed."
        )
    try:
        payload = r2_client.download_component_result(hash_prefix)
    except ClientError:
        return None
    except json.JSONDecodeError:
        return None
    if payload is None:
        return None
    if payload.get("schema_version") != COMPONENT_SCHEMA_VERSION:
        return None
    return payload
```

**Migration impact:** Callers that today invoke
`get_cached_component_result(hash_prefix)` without `r2_client` will fail
loudly. That's intentional — the existing callers are exactly the broken
paths we're fixing. There is one internal caller (the cache-hit block in
`_submit_component_analysis_job`) and it is updated by Change 2 to pass a
config-built `R2Client`. Net effect: previously-silent-None returns become
explicit errors before any DB write, eliminating the silent no-op class.

#### Change 1 — Add `_cached_components_have_llm_fields` helper (defense-in-depth)

(Identical to v1's Change 1, kept as defense-in-depth: once Change 0
unblocks the cache-hit path, this guard prevents persisting any lingering
pre-v2 cache that somehow passed the schema check. Helper lives in
`services/analysis.py` next to `get_cached_component_result`.)

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

### Change 2 — Cache-hit path: validate schema + LLM fields, pass config R2Client

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Replace lines 2022-2039 (`_submit_component_analysis_job`).

```python
# Check R2 for cached components.json (unless force).
if not force:
    try:
        r2_client = R2Client(
            config.r2_bucket, config.r2_endpoint_url, config.r2_region
        )
        cached = client.get_cached_component_result(
            recording.hash_prefix, r2_client=r2_client
        )
    except ValueError as e:
        # Misconfigured R2 creds — surface loudly, do NOT fall through
        # silently to a job submission that nobody asked to gate on cache.
        console.print(f"[red]R2 cache check skipped: {e}[/red]")
        cached = None
    except Exception:  # noqa: S110 - network error: fall through to submit.
        cached = None

    if cached is not None:
        cached_components = cached.get("components", [])
        cache_valid = _cached_components_have_llm_fields(
            cached_components,
            classify_theme=classify_theme,
            classify_vocal_posture=classify_vocal_posture,
            all_components=all_components,
        )
        if cache_valid:
            console.print(
                f"[green]Cached component result found in R2 "
                f"(schema_version={cached.get('schema_version')})[/green]"
            )
            components = _parse_component_results(
                cached_components, song_id, recording.content_hash
            )
            if components:  # guard against empty = no-op upsert wipe
                db_client.upsert_song_components(
                    song_id, recording.content_hash, components
                )
            return components
        console.print(
            "[yellow]Cached components.json lacks requested LLM fields; "
            "submitting new job to compute them.[/yellow]"
        )
```

Notes:

- `from ..services.analysis import _cached_components_have_llm_fields` is
  a **module-level** import near the other `services.analysis` imports
  (fixes v1's inline-import smell).
- Misconfigured R2 creds now produce a visible red message instead of
  silent `None` fall-through. Operators notice.

### Change 3 — `--no-wait` warning (refined for batch)

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2070-2073.

Add an explicit warning with the correct recovery command form (single vs
`--stdin`):

```python
console.print(f"[green]Component analysis submitted (job: {job.job_id})[/green]")

if not wait:
    recovery_hint = (
        f"  sow-admin audio sync-components {song_id}"
        if song_id
        else "  sow-admin audio sync-components --stdin < song_ids.txt"
    )
    console.print(
        "[yellow]Job submitted in fire-and-forget mode. "
        "song_components DB table will NOT be updated until you run:[/yellow]\n"
        + recovery_hint
    )
    return None
```

### Change 4 — `sync-components` command with `--dry-run` and row-count safeguard

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Same single / `--stdin` modes as v1, with two additions:

1. **`--dry-run` flag** — fetch R2 cache, parse, compare against existing
   `get_song_components(song_id)` row count, **write nothing**, print
   planned action + delta. Exit 0.
2. **Row-count shrink guard** — when not `--dry-run`, if
   `len(new) < len(existing)`, refuse to write unless `--yes` is also
   passed. This protects against the DELETE-then-INSERT clobber
   (`db/client.py:1995-2046`) dropping component rows from a
   re-segmentation that the operator may not have intended.

```python
@app.command("sync-components")
def sync_components(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to sync components for"),
    stdin: bool = typer.Option(False, "--stdin", help="Batch: read song IDs from stdin"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing to DB"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm destructive sync when new row count < existing"
    ),
    format_: str = typer.Option("table", "--format", help="Output format (table|json)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Sync component results from R2 cache to the song_components DB table.

    Fetches {hash_prefix}/components.json from R2 (must be schema_version=2)
    and upserts the component rows (including LLM theme/posture) into the DB.

    Safety:
      - --dry-run: report delta vs existing rows, write nothing.
      - If new row count < existing row count, the sync is refused unless
        --yes is passed (the upsert is DELETE-then-INSERT and would drop
        the extra rows).

    Exit codes:
      0 — synced (or dry-run reported)
      1 — error: no recording, no R2 cache, schema_version stale,
          or shrink refused (use --yes to override).
    """
    # ... argument/credential validation identical to v1 ...

    # Single or batch loop calls _sync_components_from_r2(...) below.
```

Helper (replaces v1's `_sync_components_from_r2`):

```python
def _sync_components_from_r2(
    recording: Recording,
    song_id: str,
    config: AdminConfig,
    db_client: DatabaseClient,
    console: Console,
    dry_run: bool = False,
    yes: bool = False,
) -> Optional[list[SongComponent]]:
    """Fetch components.json from R2 and upsert into song_components.

    Returns None if no R2 cache; [] if cache exists but parses to no
    components. Respects dry_run / yes for the clobber safeguard.
    """
    r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    analysis_client = AnalysisClient(config.analysis_url, timeout=300)

    cached = analysis_client.get_cached_component_result(
        recording.hash_prefix, r2_client=r2_client
    )
    if cached is None:
        console.print(
            f"[red]No schema_version=2 components.json in R2 for "
            f"{recording.hash_prefix}.[/red]\n"
            f"Run 'sow-admin audio components {song_id} --compute-all-fields'."
        )
        return None

    cached_components = cached.get("components", [])
    has_llm = _cached_components_have_llm_fields(
        cached_components, classify_theme=True, classify_vocal_posture=True
    )
    components = _parse_component_results(
        cached_components, song_id, recording.content_hash
    )

    existing = db_client.get_song_components(song_id)
    console.print(
        f"[green]R2 cache found[/green] "
        f"({len(cached_components)} components, schema_version="
        f"{cached.get('schema_version')}, LLM={'present' if has_llm else 'absent'}); "
        f"DB now has {len(existing)} rows."
    )

    if dry_run:
        if components and len(components) < len(existing):
            console.print(
                f"[yellow]DRY-RUN: would shrink {song_id} "
                f"{len(existing)} -> {len(components)} rows; "
                f"supply --yes when running for real.[/yellow]"
            )
        else:
            console.print(f"[cyan]DRY-RUN: would upsert {len(components)} rows.[/cyan]")
        return components

    if components and len(components) < len(existing) and not yes:
        console.print(
            f"[red]Refusing to shrink {song_id} "
            f"{len(existing)} -> {len(components)} rows. "
            f"Pass --yes to override (upsert is DELETE+INSERT).[/red]"
        )
        return None

    if components:  # never call upsert with [] - would wipe existing rows
        db_client.upsert_song_components(song_id, recording.content_hash, components)
    console.print(f"[green]Synced {len(components)} component(s) R2 -> DB.[/green]")
    return components
```

### Change 5 — Tests (expanded)

- `tests/admin/test_component_cache_validation.py` — v1's 4 helper tests.
- `tests/admin/test_sync_components.py` — adds:
  - schema_version=2 cache is accepted; stale `1` / missing is rejected.
  - `--dry-run` writes nothing; reports delta.
  - Shrink refused without `--yes`; succeeds with `--yes`.
  - Empty components list does not trigger upsert (no wipe).
  - Missing R2 creds raises `ValueError` loudly (not silent None).
- Regression test: `get_cached_component_result(hash_prefix)` without
  `r2_client` raises `ValueError` (locks the API change against
  accidental env-var revert).

Run:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  pytest tests/admin/test_component_cache_validation.py tests/admin/test_sync_components.py -v
```

### Verification (manual, updated)

1. **Bug repro (now expected to be different):**
   `sow-admin audio components <song> --no-wait` then `audio status <job>`
   then `audio show <song>` → theme/posture still `-` (Bug 1 confirmed).
2. **Change 0 (schema fix):** on a song whose R2 cache exists,
   `sow-admin audio components <song>` without `--force` should now print
   "Cached component result found in R2 (schema_version=2)" and return
   populated theme/posture — previously it submitted a new job every time.
3. **Change 2 (LLM guard):** manually place a v2 cache missing `theme`
   on R2 → command should print "lacks requested LLM fields" and submit
   a new job.
4. **Change 4 dry-run:** `sow-admin audio sync-components <song> --dry-run`
   → "DRY-RUN: would upsert N rows", DB unchanged.
5. **Change 4 shrink guard:** after a re-segmented run with fewer
   components, `sync-components <song>` without `--yes` → refuses. With
   `--yes` → upserts.
6. **Change 4 batch:** `printf 'a\nb\n' | sow-admin audio sync-components --stdin`.

### Recovery (revised — now actually works)

For the 25 NULL theme/posture rows: the v2 caches already exist in R2
(written by the worker at `queue.py:1047-1065`). After Change 0 unblocks
`get_cached_component_result`:

```bash
# Inspect first (writes nothing):
sow-admin audio sync-components <song> --dry-run

# Recover:
sow-admin audio sync-components <song>
# Or batch:
sow-admin audio sync-components --stdin < song_ids.txt
```

If `--dry-run` reports "No schema_version=2 components.json in R2" for a
song, that song's cache is pre-v2 (or never had LLM run); use:

```bash
sow-admin audio components <song> --compute-all-fields --force
```

### Critical files (updated)

- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
  - `get_cached_component_result` — **rewritten** (Change 0):
    config-driven R2Client, `schema_version != 2` guard.
  - New module constant `COMPONENT_SCHEMA_VERSION = 2`.
  - New `_cached_components_have_llm_fields` (Change 1).
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
  - Cache-hit block (`audio.py:2022-2039`) — Change 2.
  - `--no-wait` block (`audio.py:2070-2073`) — Change 3.
  - New `sync-components` + `_sync_components_from_r2` with
    `--dry-run`/`--yes` — Change 4.
  - Module-level import of `_cached_components_have_llm_fields`.
- `ops/admin-cli/src/stream_of_worship/admin/services/r2.py` — **no
  change** (`download_component_result` already exists and is
  config-driven; just doesn't validate schema, which is now the caller's
  job).
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py:1972-2046` —
  **no change** (DELETE-then-INSERT is unchanged; the `--yes` safeguard in
  Change 4 wraps the call site).
- `ops/admin-cli/tests/admin/test_component_cache_validation.py` (new) —
  Change 1.
- `ops/admin-cli/tests/admin/test_sync_components.py` (new) — Change 4 +
  Change 0 regression.
- `ops/analysis-service/src/sow_analysis/workers/classifier.py:162-182` —
  **no change** (`has_cached_llm_fields`, the model for Change 1).

### Rollout order

1. Change 0 + Change 1 (analysis.py) — must land together; unblocks
   everything else.
2. Change 2 + Change 3 (audio.py cache-hit + `--no-wait` warning).
3. Change 4 + tests.
4. Run Recovery batch against the 25 affected songs.

### Related specs

- `specs/fix-component-analysis-llm-persistence-admin-cli.md` — original
  v1 (superseded by this v2; left unedited per request).
- `specs/fix-component-analysis-llm-cache-reuse.md` — worker-side cache
  reuse (already shipped).
