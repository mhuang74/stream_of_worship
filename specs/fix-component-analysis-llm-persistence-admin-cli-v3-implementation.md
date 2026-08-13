# Implementation Plan v3: Fix admin-cli LLM theme/posture persistence to song_components table

> Builds on `specs/fix-component-analysis-llm-persistence-admin-cli-v2.md`.
> v2's diagnosis was fully verified against the codebase (14/14 claims TRUE).
> This v3 captures final implementation decisions and is the actionable spec.
>
> - v1 (`fix-component-analysis-llm-persistence-admin-cli.md`) — superseded; left unedited.
> - v2 (`fix-component-analysis-llm-persistence-admin-cli-v2.md`) — left unedited per request.

## Verification verdict on v2

All claims in v2 were verified against the codebase on 2026-08-13:

| Claim | Status | Evidence |
|---|---|---|
| `COMPONENT_SCHEMA_VERSION = 2` worker-side | TRUE | `ops/analysis-service/src/sow_analysis/storage/cache.py:15` |
| Worker rejects stale schema against constant | TRUE | `ops/analysis-service/src/sow_analysis/storage/r2.py:505` |
| admin-cli checks `schema_version != 1` | TRUE | `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:718` |
| Cache-hit early-return at audio.py:2022-2039 | TRUE | `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2022-2039` |
| admin-cli reads `SOW_R2_*` from env directly | TRUE | `analysis.py:698-701` |
| Missing env vars → silent None | TRUE | `analysis.py:703-704` |
| Admin `R2Client` raises `ValueError` on missing creds | TRUE | `ops/admin-cli/src/stream_of_worship/admin/services/r2.py:86-93` |
| Admin `R2Client.download_component_result` exists, no schema check | TRUE | `r2.py:567-590` |
| Other admin-cli commands source R2 creds from config | TRUE | `audio.py:2017` |
| `upsert_song_components` is DELETE-then-INSERT | TRUE | `db/client.py:1995-2046` |
| `--no-wait` early return + 3 affected call sites | TRUE | `audio.py:2072-2073`, `946-959`, `2294-2309`, `2372-2382` |
| Worker-side `has_cached_llm_fields` exists (model for mirror) | TRUE | `ops/analysis-service/src/sow_analysis/workers/classifier.py:162-182` |
| Worker re-persists R2 cache with LLM fields | TRUE | `ops/analysis-service/src/sow_analysis/workers/queue.py:1047-1065` |
| v1 spec's "schema_version was already 1 before v5" claim | **FALSE** | Commit `4aa42d1e` bumped `1 → 2` with v5 |

Conclusion: all three v2 issues (Critical 1, Critical 2, High 3) are **valid** and merit **critical/high priority**. Proceed to implement v3.

## Locked decisions (from clarification)

1. **`COMPONENT_SCHEMA_VERSION` source of truth** — Duplicate the constant in admin-cli with a comment directing future maintainers to keep it in sync with `sow_analysis.storage.cache.COMPONENT_SCHEMA_VERSION`. Do NOT import from analysis-service (would drag in heavy ML deps or force a shared constants package; out of scope).
2. **`r2_client=None` semantics** — Always raise `ValueError` loudly. Force all callers to update; surfaces misconfiguration during development and prevents accidental silent-None regression.
3. **Shrink guard in `--stdin` batch** — `--yes` is required globally (single AND batch). Batch operators are expected to first run `--dry-run` to review the delta, then re-run with `--yes` if shrinkage is intentional.
4. **Pre-v2 cache recovery** — Do NOT auto-submit a compute job from `sync-components`. Print a clear error directing the operator to `sow-admin audio components <song> --compute-all-fields --force`. Keep `sync-components` strictly read-from-R2 + write-to-DB (no compute side-effects).

## Implementation changes

### Change 0 — Rewrite `get_cached_component_result`: config-driven creds + schema_version=2

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
**Location:** Replace body of `get_cached_component_result` (currently lines 678-724).

Add module-level constant near the top of `analysis.py`:

```python
# Keep in sync with analysis-service's
# sow_analysis.storage.cache.COMPONENT_SCHEMA_VERSION.
# Bump together when the worker bumps its version.
COMPONENT_SCHEMA_VERSION = 2
```

Rewrite the function:

```python
def get_cached_component_result(
    self,
    hash_prefix: str,
    r2_client: Optional["R2Client"] = None,
) -> Optional[dict]:
    """Return parsed {hash_prefix}/components.json from R2, or None.

    Args:
        hash_prefix: 12-character content hash prefix.
        r2_client: Pre-constructed admin R2Client. Required; raises
            ValueError if None. The previous env-var fallback was removed
            to surface misconfiguration loudly.

    Returns:
        Parsed components.json dict whose ``schema_version`` equals
        COMPONENT_SCHEMA_VERSION, or None if:
          - no object exists at {hash_prefix}/components.json (404/NoSuchKey),
          - the payload is corrupt JSON,
          - schema_version is missing or doesn't match COMPONENT_SCHEMA_VERSION.

    Raises:
        ValueError: If r2_client is None.
    """
    if r2_client is None:
        raise ValueError(
            "get_cached_component_result requires an admin R2Client "
            "(constructed from config). The env-var path was removed."
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

Imports needed at top of `analysis.py` (verify / add):
- `from botocore.exceptions import ClientError` (or already imported transitively via `r2.py`)
- `from .r2 import R2Client` (type hint only — use `TYPE_CHECKING` guard to avoid circularity if needed)

**Migration impact:** The cache-hit block in `_submit_component_analysis_job` (`audio.py:2022-2039`) currently calls `get_cached_component_result(recording.hash_prefix)` without `r2_client`. With this change, that call raises `ValueError` instead of returning `None`. Change 2 fixes that caller by constructing the client from config.

### Change 1 — Add `_cached_components_have_llm_fields` helper

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
**Location:** Module-level, near `get_cached_component_result`.

```python
def _cached_components_have_llm_fields(
    components: list[dict],
    classify_theme: bool,
    classify_vocal_posture: bool,
    all_components: bool = False,
) -> bool:
    """Check whether cached component dicts carry the requested LLM fields.

    Mirrors analysis-service's has_cached_llm_fields() (classifier.py:162-182)
    but operates on raw dicts from R2 components.json rather than
    ComponentInstance objects.

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

### Change 2 — Cache-hit path: pass config-built R2Client + LLM guard

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Replace lines 2022-2039 (`_submit_component_analysis_job`).

Top-level import (near other `services.analysis` imports):

```python
from ..services.analysis import (
    _cached_components_have_llm_fields,
    get_cached_component_result,  # if not already imported
)
from ..services.r2 import R2Client  # if not already imported
```

Cache-hit block:

```python
# Check R2 for cached components.json (unless force).
if not force:
    cached = None
    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
        cached = client.get_cached_component_result(
            recording.hash_prefix, r2_client=r2_client
        )
    except ValueError as e:
        # Misconfigured R2 creds — surface loudly. Do NOT fall through to a
        # job submission that nobody asked to gate on cache.
        console.print(f"[red]R2 cache check skipped: {e}[/red]")
    except Exception:  # noqa: S110 - network error: fall through to submit.
        pass

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

### Change 3 — `--no-wait` warning with correct recovery hint

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
**Location:** Lines 2070-2073.

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

### Change 4 — `sync-components` command with `--dry-run` + shrink guard

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Add `sync-components` command (single + `--stdin` batch modes) and helper.

```python
@app.command("sync-components")
def sync_components(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to sync components for"),
    stdin: bool = typer.Option(False, "--stdin", help="Batch: read song IDs from stdin"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing to DB"
    ),
    yes: bool = typer.Option(
        False, "--yes",
        help="Confirm destructive sync when new row count < existing (applies globally, including --stdin batch)",
    ),
    format_: str = typer.Option("table", "--format", help="Output format (table|json)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Sync component results from R2 cache to the song_components DB table.

    Fetches {hash_prefix}/components.json from R2 (must be schema_version=2)
    and upserts the component rows (including LLM theme/posture) into the DB.

    This command is read-from-R2 + write-to-DB only. It does NOT submit any
    analysis-service jobs. If no schema_version=2 cache exists for a song,
    run `sow-admin audio components <song> --compute-all-fields --force` first.

    Safety:
      - --dry-run: report delta vs existing rows, write nothing.
      - If new row count < existing row count, the sync is refused unless
        --yes is passed. (Upsert is DELETE-then-INSERT; shrinkage would drop
        rows.)  In --stdin batch mode, --yes applies globally — review with
        --dry-run first.

    Exit codes:
      0 — synced (or dry-run reported)
      1 — error: no recording, no R2 cache, schema_version stale,
          or shrink refused (use --yes to override).
    """
    # ... argument validation identical to v1 spec (mutual exclusion of
    # song_id/--stdin, config load, format validation) ...

    # Single or batch loop calls _sync_components_from_r2(...) below,
    # passing dry_run and yes through.
```

Helper:

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

    Returns:
      - list[SongComponent]: the (intended or persisted) component rows.
      - None: no R2 cache, schema_version stale, or shrink refused.

    In dry_run mode, returns the would-be components list but writes nothing.
    An empty components list is returned as [] (not None) so callers can
    distinguish "no cache" from "cache exists but is empty"; the upsert is
    never invoked with an empty list (would wipe existing rows).
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
            f"Run: sow-admin audio components {song_id} --compute-all-fields --force"
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

### Change 5 — Tests

**File:** `ops/admin-cli/tests/admin/test_component_cache_validation.py` (new)

4 helper tests ported verbatim from v1 spec lines 470-506:
- `test_cached_components_have_llm_fields_all_populated`
- `test_cached_components_have_llm_fields_missing_theme`
- `test_cached_components_have_llm_fields_skips_non_essential`
- `test_cached_components_have_llm_fields_all_components_mode`

**File:** `ops/admin-cli/tests/admin/test_sync_components.py` (new)

Coverage:
- `test_get_cached_component_result_accepts_schema_v2` — mock R2Client returns payload with `schema_version=2`; assert non-None result.
- `test_get_cached_component_result_rejects_schema_v1` — payload with `schema_version=1`; assert None.
- `test_get_cached_component_result_rejects_missing_schema` — payload without `schema_version` key; assert None.
- `test_get_cached_component_result_requires_r2_client` — call without `r2_client`; assert `ValueError` raised (locks API change).
- `test_sync_components_dry_run_writes_nothing` — `--dry-run` flag: mock all deps; assert `upsert_song_components` NOT called; delta message printed.
- `test_sync_components_shrink_refused_without_yes` — new count < existing count, no `--yes`; assert exit 1, `upsert_song_components` NOT called.
- `test_sync_components_shrink_allowed_with_yes` — same setup, with `--yes`; assert upsert called.
- `test_sync_components_empty_components_no_upsert` — cache returns `{"components": []}`; assert `upsert_song_components` NOT called; result is `[]`, not None.
- `test_sync_components_missing_r2_creds_loud` — make `R2Client(...)` ctor raise `ValueError`; assert command surfaces the error (does NOT silently fall through).

Run:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  pytest tests/admin/test_component_cache_validation.py tests/admin/test_sync_components.py -v
```

## Critical files

- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
  - Module constant `COMPONENT_SCHEMA_VERSION = 2` (new, near top).
  - Rewrite `get_cached_component_result` (Change 0; currently lines 678-724).
  - Add `_cached_components_have_llm_fields` (Change 1).
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
  - Module-level import of `_cached_components_have_llm_fields`, `R2Client`.
  - Cache-hit block rewrite (Change 2; lines 2022-2039).
  - `--no-wait` warning (Change 3; lines 2070-2073).
  - New `sync-components` command and `_sync_components_from_r2` helper (Change 4).
- `ops/admin-cli/src/stream_of_worship/admin/services/r2.py` — **no change** (`download_component_result` already exists at line 567).
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — **no change** (`upsert_song_components` at lines 1995-2046; `--yes` guard wraps the call site).
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — **no change** (worker already at version 2; admin-cli mirrors the constant).
- `ops/analysis-service/src/sow_analysis/workers/classifier.py:162-182` — **no change** (model for Change 1).
- `ops/admin-cli/tests/admin/test_component_cache_validation.py` — new (Change 5).
- `ops/admin-cli/tests/admin/test_sync_components.py` — new (Change 5).

## Rollout order

1. **Land Change 0 + Change 1 together** (`services/analysis.py`). Without Change 0, Change 2's new caller will fail loudly in prod (intentional, but the function must exist first).
2. **Land Change 2 + Change 3** (`commands/audio.py` cache-hit + `--no-wait` warning). The cache-hit path is now functional and surfaces misconfiguration.
3. **Land Change 4 + Change 5** (`sync-components` + tests). After this, the recovery workflow is fully operational.
4. **Run recovery** against the 25 production songs with NULL theme/posture:
   ```bash
   # Inspect first (writes nothing):
   sow-admin audio sync-components --stdin --dry-run < song_ids.txt
   # Recover (will refuse any shrink; review and re-run with --yes only if shrink is intentional):
   sow-admin audio sync-components --stdin < song_ids.txt
   ```
   For any song reporting "No schema_version=2 components.json in R2", recompute:
   ```bash
   sow-admin audio components <song> --compute-all-fields --force
   ```

## Manual verification checklist

1. **Bug 1 (current) repro:** `sow-admin audio components <song> --no-wait` then `audio status <job>` then `audio show <song>` → theme/posture `-`. ✅ confirms Bug 1 exists in prod today.
2. **Change 0 schema fix:** on a song whose R2 cache exists with v2 schema, run `sow-admin audio components <song>` without `--force` → prints "Cached component result found in R2 (schema_version=2)" and returns populated theme/posture. Previously it always submitted a new job.
3. **Change 2 LLM guard:** manually overwrite a v2 cache in R2 with one missing `theme` → run the same command → prints "lacks requested LLM fields" and submits a new job.
4. **Change 4 dry-run:** `sow-admin audio sync-components <song> --dry-run` → "DRY-RUN: would upsert N rows", DB unchanged (`audio show <song>` shows pre-existing state).
5. **Change 4 shrink guard:** after a re-segmentation with fewer components, `sync-components <song>` without `--yes` → refuses. With `--yes` → upserts and row count drops.
6. **Change 4 batch:** `printf 'a\nb\n' | sow-admin audio sync-components --stdin --dry-run` then without `--dry-run`.
7. **Change 0 loud failure:** unset `SOW_R2_ACCESS_KEY_ID`/`SOW_R2_SECRET_ACCESS_KEY` in shell, attempt `sow-admin audio components <song>` (config-driven R2Client construction in `audio.py:2017` will raise). Verify the red error message is visible.

## Out of scope

- Bumping worker-side `COMPONENT_SCHEMA_VERSION`. (No current need; if bumped future, the admin-cli constant must be updated in lockstep — see comment in Change 0.)
- Auto-recovery / auto-deletion of pre-v2 caches in R2. (Operators use `components --compute-all-fields --force` to overwrite stale caches; `sync-components` correctly identifies them as missing.)
- Changes to `upsert_song_components` semantics (DELETE-then-INSERT). (The `--yes` guard in Change 4 wraps the call site; changing the SQL itself is a separate refactor.)

## Related specs

- `specs/fix-component-analysis-llm-persistence-admin-cli.md` — v1 (superseded).
- `specs/fix-component-analysis-llm-persistence-admin-cli-v2.md` — v2 diagnosis (verified accurate by this plan; left unedited).
- `specs/fix-component-analysis-llm-cache-reuse.md` — worker-side cache reuse (shipped).
