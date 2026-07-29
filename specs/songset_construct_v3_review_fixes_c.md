# Songset Construct v3 — Implementation Review and Fix Plan (Round C)

**Date:** 2026-07-28
**Spec:** `specs/songset_construct_command_v3.md`
**Prior Reviews:** `specs/songset_construct_v3_review_fixes.md` (Round A, resolved), `specs/songset_construct_v3_review_fixes_b.md` (Round B, resolved)
**Scope:** Fresh post-Round-B re-review of the implementation against the v3 spec, external docs (PostgreSQL 18, psycopg 3), and cross-component conventions.

## Overview

Round A and Round B issues are confirmed fixed: `write_artifacts` removed from the graph, atomic `create_songset_with_items` exists, `runner` uses `invoke()`, diversity matrix is in `diagnose.py`, runner/cache tests exist, `load_catalog` node removed, `include_cpw`/`hymnal_mode` guards removed, partial-failure exit-1 implemented, `--no-*` flag pairs added, `theme_anchors` HNSW index is in `ALL_SCHEMA_STATEMENTS`.

This round found **3 CRITICAL, 2 HIGH, 4 MEDIUM, 5 LOW** remaining issues. The two most important findings are runtime-breaking data-contract bugs that unit tests currently *mask* because the tests simulate driver behavior that differs from real psycopg3:

1. psycopg3 auto-parses `json`/`jsonb` result columns into Python objects; `db.py` then calls `json.loads()` on an already-parsed `dict` → `TypeError`.
2. PostgreSQL's `json_object_agg` includes NULL values as JSON nulls (it does NOT return SQL NULL), so songs without a `song_embedding` row produce `{"讚美": null, ...}` → pydantic `dict[str, float]` validation failure.

Either bug alone crashes `sow-admin songset construct` against a real database on the first pool query.

External references used to verify behavior:
- psycopg 3 docs, "JSON adaptation": "By default Psycopg uses the standard library `json.dumps` and `json.loads` functions to serialize and de-serialize Python objects to JSON" (no `set_json_loads` override exists anywhere in this repo — verified by grep).
- PostgreSQL 18 docs, Table 9.62: `json_object_agg` — "Collects all the key/value pairs into a JSON object. … **Values can be null, but keys cannot.**" (i.e., NULL values become JSON `null` entries, not skipped, and the aggregate never collapses to SQL NULL unless zero rows are aggregated.)

---

## CRITICAL Issues

### 1. psycopg3 auto-parses `json` columns; `json.loads()` on the parsed `dict` crashes every real run

**Files:** `songset_constructor/db.py:58-59` (`_candidate_from_row`), `db.py:98-99` (`fetch_line_theme_scores`)

```python
raw_scores = row[15]
song_theme_scores_raw = json.loads(raw_scores) if raw_scores else {}
```

`POOL_QUERY`'s final column is `json_object_agg(...)` (type `json`, OID 114) and `LINE_THEME_QUERY` returns the same. Under psycopg 3 (`psycopg[binary]>=3.2.0` in `ops/admin-cli/pyproject.toml`), `json`/`jsonb` values are loaded with `json.loads` **by the driver** and arrive as Python `dict` (or `None` for SQL NULL). `json.loads(<dict>)` raises:

```
TypeError: the JSON object must be str, bytes or bytearray, not dict
```

**Masking factor:** `tests/songset_construct/test_db_queries.py` feeds raw JSON *strings* into `row[15]` (e.g., lines 48 and 148: `scores = json.dumps({...})`), encoding the wrong driver contract. All tests pass while production crashes — on **every** row with an embedding, not just edge cases.

**Impact:** `fetch_catalog_pool` → `_candidate_from_row` raises on the first fetched row with a `song_embedding`; `fetch_line_theme_scores` raises on the first row of line-scores. The `construct` command cannot complete a single real DB run. All acceptance criteria involving a live DB fail.

**Fix:**
1. Handle both driver-parsed and string forms defensively:

```python
def _parse_json_scores(raw) -> dict[str, float]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items() if v is not None}
```

Use it in `_candidate_from_row` (row[15]) and in `fetch_line_theme_scores` (per-row `scores_json`).
2. Update `test_db_queries.py` to pass **dicts** at `row[15]` (psycopg-faithful) and keep one `str` case to lock in the dual-mode handling; add a `None` case (already present) and a null-values case.

**Note:** No other repo code disables psycopg3 JSON adaptation (grep for `set_json_loads`/`JsonLoader`/`register_default_json` found nothing), so this is uniform across all connections created by `ConnectionProvider`.

---

### 2. `json_object_agg` does NOT return NULL for missing embeddings — it returns 12 JSON `null` values

**File:** `songset_constructor/db.py` `POOL_QUERY` (lines 20-37); **Spec:** `songset_construct_command_v3.md` lines 361, 790

The spec assumes (line 361): *"When `song_embedding` is NULL (no embedding), the subquery returns NULL."* This is incorrect. The correlated subquery

```sql
(SELECT json_object_agg(ta.theme, 1 - (se.embedding <=> ta.embedding))
 FROM theme_anchors ta) AS song_theme_scores_raw
```

always aggregates over 12 `theme_anchors` rows. When `se.embedding IS NULL`, each value expression is NULL, and per PostgreSQL docs `json_object_agg` **includes** them:

```json
{"讚美": null, "感恩": null, "敬拜": null, ... , "跟隨": null}
```

**Impact chain (after Issue 1 is fixed):** `SongCandidate.song_theme_scores_raw: dict[str, float]` receives `None` values → pydantic v2 raises `ValidationError` ("Input should be a valid number") → crash for **every song lacking a `song_embedding` row** (a normal condition for unprocessed catalog entries, hence the `LEFT JOIN`).

**Fix (SQL-side, restores the spec's intended NULL semantics):**

```sql
(
    SELECT json_object_agg(ta.theme, 1 - (se.embedding <=> ta.embedding))
    FROM theme_anchors ta
    WHERE se.embedding IS NOT NULL
) AS song_theme_scores_raw
```

With zero aggregated rows, `json_object_agg` returns SQL NULL, and the Python layer maps NULL → `{}` (existing behavior, and parity with the POC, where missing embeddings normalized to all-zero theme scores).

Alternative: `json_object_agg_strict` (skips null values) — but it requires PG ≥ 16 and yields `{}`-empty objects rather than NULL; the `WHERE` filter is simpler and portable.

Also apply the defensive `v is not None` filtering in `_parse_json_scores` (Issue 1) as a second layer.

**Spec correction required:** fix lines 361 and the Risks-table row at line 790, and the `_candidate_from_row` pseudocode (`json.loads(raw_scores) if raw_scores else {}`) at lines 513-514.

---

### 3. Lazy-import guard broken: entire `sow-admin` CLI hard-requires the `constructor` extra

**Files:** `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:15`, `songset_constructor/__init__.py:6-7`, `ops/admin-cli/pyproject.toml:25-50`, `admin/main.py:16`

`commands/songset.py` has a **module-top-level** import that defeats the lazy-loading design:

```python
# songset.py:15 — evaluated at import time, before _import_constructor() can guard it
from stream_of_worship.admin.songset_constructor.config import DEFAULT_REPORT_DIR
```

Import chain:
1. `admin/main.py:16` does `from stream_of_worship.admin.commands import songset as songset_commands` (top-level).
2. `commands/songset.py:15` imports `songset_constructor.config` → Python first executes the package `songset_constructor/__init__.py`.
3. `__init__.py` eagerly does `from ...models import SongCandidate` → `models.py:5` → `from pydantic import BaseModel, Field`.

**pydantic is NOT in the `admin` extra** (`pyproject.toml` lines 25-41) — it only appears in `constructor` (≥2.7.0) and `test` (≥2.0.0). `stream_of_worship/db` is pydantic-free (verified by grep), so `songset list` and every other admin command work fine without it today.

**Impact:** `uv run --project ops/admin-cli --extra admin sow-admin --help` (the documented lightweight path in `AGENTS.md`) crashes at startup with `ImportError: No module named 'pydantic'` — **every** `sow-admin` command is broken without the `constructor` extra, not just `construct`. This also violates the acceptance criterion: *"Running without `constructor` extra fails with clear `RuntimeError` and install hint."* (The failure happens in the wrong place, as a raw ImportError, and it disables unrelated commands.) Note the test suite cannot catch this, because the `test` extra always installs pydantic.

**Fix:**
1. Remove the line-15 import; reference `DEFAULT_REPORT_DIR` inside `construct_songset()` after `_import_constructor()`:

```python
from stream_of_worship.admin.songset_constructor.config import DEFAULT_REPORT_DIR
report_path = write_report(run_config, result, report_dir or DEFAULT_REPORT_DIR / stamp)
```

2. Optional hardening: make `songset_constructor/__init__.py` empty (docstring only) so cheap submodules (`config`, `cache`) stay importable without pydantic. Not strictly required once (1) lands — `_import_constructor()`'s try/except converts the ImportError into a friendly RuntimeError.
3. Add a regression test that statically asserts no top-level `songset_constructor` imports exist in `commands/songset.py` (parse with `ast`, fail if `Import`/`ImportFrom` at module scope references `songset_constructor`). This is the only practical way to catch it without uninstalling pydantic in CI.

---

## HIGH Issues

### 4. Constructed songsets persist 1-based positions; the rest of the system is 0-based

**Files:** `songset_constructor/rules/proposals.py:43,59-64`, `songset_constructor/persist.py:35`

`draft_from_candidates` and `proposal_from_draft` build items with `enumerate(..., start=1)` → `ProposalItem.position` ∈ 1..N. `persist.py` passes `item.position` straight into `create_songset_with_items` → rows stored with `position` 1..N.

0-based convention evidence across the system:
- `db/app/models.py:92`: "position: Position in the songset (0-indexed)"
- `SongsetClient.add_item`: appends at `MAX(position)+1`, starting at 0 (`songset_client.py:351-357`)
- Webapp API: `position: z.number().int().min(0)` (`app/api/songsets/[id]/items/route.ts:13`); webapp tests use positions 0,1,2
- `sow-admin songset list` renders `item.position + 1` (`commands/songset.py:181`)

**Impact:** `songset list` displays constructed sets as items 2..N+1; ordering still works (`ORDER BY position`, render worker unaffected), but positions violate the shared convention, and any logic that assumes `position == 0` identifies the first song (webapp chapter builders, remove/reorder arithmetic in `SongsetClient.remove_item`/`reorder_item` which shift `position > X`) behaves inconsistently for constructed sets.

**Fix (recommended): normalize at the persistence boundary** — the DB convention is a persistence concern:

```python
# persist.py
for idx, item in enumerate(proposal.items):
    items.append({..., "position": idx, ...})
```

(Then `ProposalItem.position` stays 1-based "display rank" internally, and no rule/beam/LLM code changes.) Alternative: make `proposal_from_draft` 0-based and adjust `nodes.py` `optional_review` enumeration. Update `test_persist.py` to assert persisted positions are exactly `0,1,2` for a 3-song proposal.

---

### 5. `create_songset_with_items` never validates references; `MissingReferenceError` path is dead code

**Files:** `db/app/songset_client.py:675-762`, `db/app/schema.py:25-43`, `songset_constructor/persist.py:50`

The method's docstring promises `MissingReferenceError` "If a `recording_hash_prefix` is not found", but the implementation inserts unconditionally. By design there is **no FK** on `songset_items.recording_hash_prefix` or `song_id` (schema comment: "intentionally left as plain TEXT to keep data-insertion decoupled"), so nothing catches bad references. Spec Step 9's mitigation ("If a `MissingReferenceError` occurs, rollback, continue") therefore never fires in production — the `test_persist.py` coverage of that path only works because a mock raises the error.

**Impact:** Stale or hallucinated `recording_hash_prefix` values (spec Risk table calls this out: "Stale recording_hash_prefix between construct and save") are silently persisted as orphan items; render then fails downstream.

**Fix:**
1. In `create_songset_with_items`, inside the transaction and before inserting, validate in one query:

```python
cursor.execute(
    "SELECT hash_prefix FROM recordings WHERE hash_prefix = ANY(%s) AND deleted_at IS NULL",
    (list({i["recording_hash_prefix"] for i in items}),),
)
found = {r[0] for r in cursor.fetchall()}
missing = [i["recording_hash_prefix"] for i in items if i["recording_hash_prefix"] not in found]
if missing:
    raise MissingReferenceError(
        f"Recordings not found: {missing}", "recording", ",".join(missing)
    )
```

2. In `persist.py`, catch `MissingReferenceError` specifically (rollback + print red + continue, per spec); let other exceptions propagate (or catch `psycopg.Error` separately) instead of the current bare `except Exception`, which also masks programmer errors.

---

## MEDIUM Issues

### 6. Pool cache: non-atomic writes, no corruption tolerance

**File:** `songset_constructor/cache.py:23-48`

- `save_pool` writes the target file directly; a Ctrl-C mid-write leaves a truncated JSON file.
- `try_load_pool` lets `json.JSONDecodeError` and `pydantic.ValidationError` propagate — a single corrupt cache file crashes **every** subsequent `construct` run (same key) until TTL expiry or manual deletion, instead of falling back to a DB fetch.

**Fix:**
- Write atomically: `tmp = path.with_suffix(".json.tmp"); tmp.write_text(...); os.replace(tmp, path)`.
- In `try_load_pool`, wrap read/parse/validate in `try/except (OSError, json.JSONDecodeError, pydantic.ValidationError)` → treat as cache miss (optionally `path.unlink(missing_ok=True)` to self-heal).
- Tests: corrupt file content → `try_load_pool` returns None; interrupted write (pre-seeded `.tmp` file) leaves target intact.

---

### 7. Drizzle migration drift for `theme_anchors`

**Files:** `delivery/webapp/drizzle/0018_theme_anchors.sql`, `delivery/webapp/drizzle/meta/_journal.json`, `delivery/webapp/src/db/schema.ts`

- `0018_theme_anchors.sql` is a hand-written file that is **not registered** in `drizzle/meta/_journal.json` (last entry: `0017_improve_musical_key_accuracy_v2`), so `drizzle-kit migrate` will never apply it.
- `theme_anchors` is **not declared** in `src/db/schema.ts`, so `drizzle-kit push` will detect it as an unknown DB table and propose dropping it.

**Fix (recommended):** make drizzle the source of truth —
1. Add a `themeAnchors` table to `schema.ts` using a drizzle `customType` for `vector(1536)` (and the HNSW index if expressible; otherwise note it as manually created).
2. Regenerate the migration via `npx drizzle-kit generate` (replacing the hand-written 0018 file), so journal + snapshot stay consistent.

Simpler alternative: keep the table CLI-managed, add a journal/snapshot entry only, and document in `delivery/webapp/README.md` that `theme_anchors` must not be dropped by `drizzle-kit push`. Whichever path is chosen, current half-registered state (SQL file present, journal absent, schema absent) is the worst of both.

---

### 8. `construct` crashes with a raw traceback when `theme_anchors` **table** doesn't exist

**Files:** `songset_constructor/db.py:103-107`, `commands/songset.py:440-446`

`check_theme_anchors` runs `SELECT COUNT(*) FROM theme_anchors`. On databases initialized **before** this schema addition (a realistic first-run scenario), the table is missing entirely → `psycopg.errors.UndefinedTable` traceback. The friendly message + exit-1 path only covers "table exists with ≠ 12 rows". Acceptance: *"Running without `theme-anchors sync` first exits 1 with clear error"* — currently fails for the missing-table case.

**Fix:** in the command (or in `check_theme_anchors`), catch `psycopg.errors.UndefinedTable` and print:

```
[red]theme_anchors table does not exist. Run: sow-admin db init && sow-admin theme-anchors sync[/red]
```

then `raise typer.Exit(1)`.

---

### 9. Spec §"Documentation Updates" not carried out

- `ops/admin-cli/README.md` — **no** mention of `songset construct` or `theme-anchors sync` (grep: zero hits) → spec line 780 unfulfilled.
- `docs/agent_guide_songset_constructor.md` — still presents the POC script (`python lab/poc-scripts/construct_songset_agent.py ...`) as the primary path (Quick Start lines 11, 16 and ~10 more references) → spec line 781 unfulfilled (Quick Start should lead with `sow-admin songset construct`, POC marked deprecated).
- `specs/reduce-database-network-transfer-v3.md` — Phase 2 (lines 166-238) lacks the note that the v3 construct spec supersedes the POC-level fix → spec line 782 unfulfilled.
- Root `AGENTS.md` — already updated (constructor extra + `theme-anchors sync` present) ✓.

**Fix:** apply the three pending doc edits.

---

## LOW Issues

### 10. Error paths surface as raw tracebacks

**File:** `commands/songset.py`

- `RunConfig.__post_init__` `ValueError`s (invalid `--season`, `--pool < --count`), `validate_environment()` `RuntimeError` (`--no-llm` + `--llm-judge`), `_parse_relax` `int(val)` on `h2:abc`, and unknown keys from `--constraints-file` (`TypeError: unexpected keyword argument`) all propagate as tracebacks.

**Fix:** wrap RunConfig construction + `validate_environment()` in `try/except (ValueError, RuntimeError, TypeError)` → red message + `typer.Exit(1)`; in `_parse_relax`, catch `ValueError` from `int()` and report the offending token; filter constraints-file keys against `RunConfig` fields with a clear warning for unknown keys.

### 11. `output_dir` populated even when `--report` is off

**File:** `commands/songset.py:464` — `output_dir=report_dir` unconditionally. Spec Step 4: "output_dir (from `--report-dir` **if `--report` else None**)". Harmless today (`write_enrichment_report` is unreachable since `only_evaluate_pool_enrichment=False` is forced), but deviates from spec and pollutes the RunConfig dump in reports. **Fix:** `output_dir=report_dir if report else None`.

### 12. `test_theme_anchors_sync.py` never tests the sync command

**File:** `tests/songset_construct/test_theme_anchors_sync.py` — only tests `load_theme_anchors` and the JSON file's structure; none of the spec'd scope ("sync reads JSON and upserts 12 rows; `--force` re-inserts; without `--force` skips if 12 rows exist") is covered. Also uses brittle `Path(__file__).resolve().parents[4]` (line 19). **Fix:** add `CliRunner`-based tests for `sync_theme_anchors` with a mocked `ConnectionProvider`/`AdminConfig` (skip when ≥12 matching rows; upsert otherwise; `--force` forces); replace `parents[4]` with an import of `THEME_ANCHORS_PATH`/`load_theme_anchors`.

### 13. `theme_anchors.json` not declared as package data

**File:** `ops/admin-cli/pyproject.toml` — `[tool.setuptools.packages.find]` has no `[tool.setuptools.package-data]`. A built wheel/sdist will omit `songset_constructor/data/theme_anchors.json` → `sow-admin theme-anchors sync` fails on pip-installed distributions (works only from a source checkout). **Fix:**

```toml
[tool.setuptools.package-data]
"stream_of_worship.admin.songset_constructor" = ["data/*.json"]
```

### 14. Cosmetics / hygiene

- `rules/themes.py:69`: annotation `dict[str, any]` uses the builtin `any` instead of `typing.Any` (harmless under postponed evaluation).
- `classify_embedding_themes` is per-spec "deprecated, kept for reference/testing" but carries no deprecation note in its docstring.
- `test_runner.py` uses `try: ... assert False ... except ValueError` instead of `pytest.raises` (5 occurrences).

### 15. Missing CLI-level mapping test

Spec `test_runner.py` scope includes "verify RunConfig maps from Typer options; verify `--user` missing exits 2" — no `CliRunner` test exercises `construct_songset` end-to-end with mocked internals. (Typer does exit 2 for missing required options, but the option→RunConfig mapping — incl. `use_cache=not no_cache and cache_ttl > 0` — is unverified.) Add a `CliRunner` test with `runner.run`/DB clients mocked.

---

## Spec self-corrections (document fixes, not code)

Apply to `specs/songset_construct_command_v3.md`:

1. Line 361 & Risks-table line 790: correct the claim that the subquery returns NULL when `song_embedding` is missing — `json_object_agg` emits 12 JSON nulls unless a `WHERE se.embedding IS NOT NULL` filter is added (add it to the spec's `POOL_QUERY`).
2. Lines 513-514 (`_candidate_from_row` pseudocode): `json.loads(raw_scores) if raw_scores else {}` assumes `::text` semantics; psycopg3 delivers parsed dicts — replace with a dual-mode parse helper (see Issue 1).
3. Line 552-563 (`fetch_line_theme_scores`): same `json.loads` correction.
4. Behaviour Step 5 is silent about the JSON-type contract; add one sentence: "psycopg3 parses `json`/`jsonb` results natively; no `json.loads` on fetched `json_object_agg` values."

---

## Implementation Order

| Order | Issue | Severity | Fix | Effort | Files |
|------:|-------|----------|----------------------|--------|-------|
| 1 | #3 | CRITICAL | Remove top-level `songset_constructor` import from `commands/songset.py`; lazy-import `DEFAULT_REPORT_DIR`; AST guard test | XS | `commands/songset.py`, `tests/songset_construct/` |
| 2 | #1 | CRITICAL | Dual-mode JSON parsing helper in `db.py`; psycopg-faithful tests | S | `songset_constructor/db.py`, `test_db_queries.py` |
| 3 | #2 | CRITICAL | `WHERE se.embedding IS NOT NULL` in `POOL_QUERY` subquery; spec text fix | XS | `songset_constructor/db.py`, spec |
| 4 | #4 | HIGH | 0-based positions at persist boundary; update `test_persist.py` | XS | `persist.py`, `test_persist.py` |
| 5 | #5 | HIGH | Reference validation inside `create_songset_with_items`; narrow persist exception handling | S | `db/app/songset_client.py`, `persist.py` |
| 6 | #6 | MEDIUM | Atomic cache write + corruption-tolerant load + tests | S | `cache.py`, `test_cache.py` |
| 7 | #8 | MEDIUM | UndefinedTable → friendly exit 1 | XS | `commands/songset.py` or `db.py` |
| 8 | #7 | MEDIUM | Resolve drizzle journal/schema drift for `theme_anchors` | M | `delivery/webapp/drizzle/*`, `src/db/schema.ts` |
| 9 | #9 | MEDIUM | Apply spec's doc updates (README, agent guide, bandwidth spec note) | S | docs |
| 10 | #10 | LOW | Friendly error handling for config/relax errors | XS | `commands/songset.py` |
| 11 | #11 | LOW | `output_dir` only when `--report` | XS | `commands/songset.py` |
| 12 | #12 | LOW | Real sync-command tests | S | `test_theme_anchors_sync.py` |
| 13 | #13 | LOW | `package-data` for theme_anchors.json | XS | `pyproject.toml` |
| 14 | #14 | LOW | Cosmetics (`Any`, deprecation note, `pytest.raises`) | XS | themes.py, test_runner.py |
| 15 | #15 | LOW | CliRunner mapping test for construct | S | test_runner.py |

**Legend:** XS = <1hr, S = 1-2hr, M = 2-4hr

## Verification After Fixes

```bash
# 1. Admin-only install must still run (Issue 3)
uv sync --project ops/admin-cli --extra admin --extra test
uv run --project ops/admin-cli --extra admin sow-admin --help        # must succeed
uv run --project ops/admin-cli --extra admin sow-admin songset construct --user a@b.c --dry-run
#   → clear RuntimeError: constructor extra not installed ... (issue resolved)

# 2. Full constructor install + tests
uv sync --project ops/admin-cli --extra admin --extra constructor --extra test
uv run --project ops/admin-cli --python 3.11 --extra admin --extra constructor --extra test \
  pytest tests/songset_construct/ -v

# 3. Live DB smoke (Issues 1, 2): pool must build with songs lacking embeddings
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin theme-anchors sync
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@x --count 3 --proposals 3 --dry-run --no-cache
#   → no TypeError / ValidationError; songs without song_embedding get zeroed themes

# 4. Persisted positions (Issue 4)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@x --count 3 --proposals 1 --yes
uv run --project ops/admin-cli --extra admin sow-admin songset list --user me@x
#   → constructed set items render as 1..N (DB positions 0..N-1)

# 5. Cache corruption self-heal (Issue 6): echo garbage into the pool_*.json, rerun --dry-run → falls back to DB

# 6. Missing-table path (Issue 8): point at a legacy DB without theme_anchors → friendly exit 1
```

## Acceptance (Round C additions)

- `sow-admin --help` and `sow-admin songset list` work with only `--extra admin` installed (no pydantic/langgraph).
- `sow-admin songset construct` against a live DB completes with (a) songs that have embeddings and (b) songs that do not; per-run transfer remains < 1 MB and no `embedding::text` appears in queries.
- Constructed songsets store positions 0..N-1 and `songset list` renders them 1..N.
- A proposal containing an unknown `recording_hash_prefix` fails that proposal only (rollback), others persist; exit code 1 if any failed.
- A corrupted `pool_*.json` cache file results in a DB fetch, not a crash.
- `0018_theme_anchors` is applied by `drizzle-kit migrate` and `drizzle-kit push` does not propose dropping `theme_anchors`.
- Legacy DB without `theme_anchors` table → friendly message + exit 1.
- `ops/admin-cli/README.md` and `docs/agent_guide_songset_constructor.md` document `sow-admin songset construct` + `theme-anchors sync`.
