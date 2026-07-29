# Songset Construct v3 — Implementation Review and Fix Plan (Round B)

**Date:** 2026-07-28
**Spec:** `specs/songset_construct_command_v3.md` (v3)
**Prior Review:** `specs/songset_construct_v3_review_fixes.md` (Round A — all issues resolved)
**Scope:** Post-Round-A implementation re-review against spec

## Overview

This document identifies discrepancies between the v3 spec and the current implementation that remain after Round A fixes. All Round A issues (write_artifacts node, non-atomic persist, fragile runner state extraction, missing diversity matrix, missing tests) have been resolved. New issues are cataloged below.

---

## CRITICAL Issues

### 1. `load_catalog` graph node causes double pool fetch, defeating cache entirely

**Files:** `graph/nodes.py:54-57`, `graph/builder.py:33,45-46`, `runner.py:19-31`

**Spec (Step 5):** The runner performs cache lookup → DB fetch → cache save → build graph → invoke. The pool is passed as `initial_state["pool"]` so the graph operates on the pre-fetched (possibly cached) pool.

**Implementation:** The graph starts with `START → load_catalog → enrich_pool`. The `load_catalog` node (`nodes.py:54-57`) unconditionally calls `fetch_catalog_pool(config, client=state.get("_read_client"))`, overwriting the pool that was pre-fetched and cached by the runner:

```python
# runner.py — pre-fetches pool (cache or DB), saves to cache
pool = cache.try_load_pool(config) or fetch_catalog_pool(config, client=read_client)
cache.save_pool(config, pool)

# graph/nodes.py:54-57 — RE-FETCHES from DB, discarding the cached pool
def load_catalog(state: ConstructorState) -> dict:
    config = state["config"]
    pool = fetch_catalog_pool(config, client=state.get("_read_client"))
    return {"pool": pool, ...}
```

**Impact:**
- **Cache HIT path:** Runner loads from cache, but `load_catalog` re-fetches from DB — cache is completely bypassed. The "Pool loaded from cache" message prints, but the graph still hits the DB.
- **Cache MISS path:** Pool is fetched twice from DB (once by runner, once by `load_catalog`).
- The `_read_client` key passed in `initial_state` by the runner is not declared in `ConstructorState` TypedDict (`graph/state.py`), relying on Python dict's ability to hold undeclared keys.

**Fix:** Remove the `load_catalog` node entirely. The runner already provides the pool via `initial_state["pool"]`. Replace the graph edges:
```python
# builder.py — before
builder.add_node("load_catalog", load_catalog)
builder.add_edge(START, "load_catalog")
builder.add_edge("load_catalog", "enrich_pool")

# builder.py — after
builder.add_edge(START, "enrich_pool")
```
Remove the `load_catalog` function and its import from `nodes.py`. Remove `"_read_client"` from `runner.py` initial_state. The `enrich_pool` node already reads from `state.get("pool", [])`.

Alternatively, make `load_catalog` a no-op or conditional:
```python
def load_catalog(state: ConstructorState) -> dict:
    if state.get("pool"):
        return {"trace": _trace(state, "load_catalog", "exit", {"pool_size": len(state["pool"])})}
    config = state["config"]
    pool = fetch_catalog_pool(config, client=state.get("_read_client"))
    return {"pool": pool, ...}
```

---

### 2. `include_cpw` and `hymnal_mode` silently no-op when `album_series` is empty

**File:** `config.py:70-75`

**Spec:** `--include-cpw` adds "CPW" to `album_series`; `--hymnal-mode` adds "HYMN". These should work regardless of whether `--album-series` is specified.

**Implementation:** The `if self.album_series:` guard prevents the append when the list is empty — which is the default case (no `--album-series`):

```python
if self.include_cpw and "CPW" not in self.album_series:
    if self.album_series:          # ← Bug: always False when no --album-series
        self.album_series.append("CPW")
if self.hymnal_mode and "HYMN" not in self.album_series:
    if self.album_series:          # ← Bug: always False when no --album-series
        self.album_series.append("HYMN")
```

**Impact:** Running `sow-admin songset construct --include-cpw` (without `--album-series`) has zero effect. "CPW" is never added. Same for `--hymnal-mode`.

**Fix:** Remove the inner `if self.album_series:` guard:
```python
if self.include_cpw and "CPW" not in self.album_series:
    self.album_series.append("CPW")
if self.hymnal_mode and "HYMN" not in self.album_series:
    self.album_series.append("HYMN")
```

---

### 3. Partial persist failures don't exit 1

**File:** `commands/songset.py:524-531`

**Spec (Step 9):** "Exit 1 if any proposal failed; exit 0 if all succeeded."

**Implementation:** The command only exits 1 if ALL proposals fail:
```python
created = persist_proposals(run_config, proposals, songset_client)
if created:
    console.print(f"\n[green]Created {len(created)} songset(s).[/green]")
else:
    console.print("[red]Failed to create any songsets.[/red]")
    raise typer.Exit(1)
```

If 2 of 3 proposals fail, `created` has 2 entries (truthy), so the command exits 0 — violating the spec.

**Fix:** Track failures explicitly:
```python
created = persist_proposals(run_config, proposals, songset_client)
if created:
    console.print(f"\n[green]Created {len(created)} songset(s).[/green]")
if len(created) < len(proposals):
    failed = len(proposals) - len(created)
    console.print(f"[red]{failed} proposal(s) failed to save.[/red]")
    raise typer.Exit(1)
```

Update `persist_proposals` to return both created IDs and failed count, or have the caller compute `failed = len(proposals) - len(created)`.

---

## MAJOR Issues

### 4. Missing `--no-*` flag pairs for `--include-cpw`, `--intimate`, `--hymnal-mode`

**File:** `commands/songset.py:310-324`

**Spec CLI surface:**
```
[--include-cpw / --no-include-cpw]    (default False)
[--intimate / --no-intimate]          (default False)
[--hymnal-mode / --no-hymnal-mode]    (default False)
```

**Implementation:** Only the positive flag is defined:
```python
include_cpw: bool = typer.Option(False, "--include-cpw", help="...")
intimate: bool = typer.Option(False, "--intimate", help="...")
hymnal_mode: bool = typer.Option(False, "--hymnal-mode", help="...")
```

Typer treats these as simple boolean flags (set True when present, default False when absent). There's no `--no-*` counterpart, so users cannot explicitly negate them (e.g., in a script that sets defaults then overrides).

**Fix:** Use the `--flag/--no-flag` pattern (already used for `--llm/--no-llm`):
```python
include_cpw: bool = typer.Option(False, "--include-cpw/--no-include-cpw", help="...")
intimate: bool = typer.Option(False, "--intimate/--no-intimate", help="...")
hymnal_mode: bool = typer.Option(False, "--hymnal-mode/--no-hymnal-mode", help="...")
```

---

### 5. Missing `stream_mode="debug"` for console progress

**File:** `runner.py:46`

**Spec (Step 5):** "Stream: `stream_mode='debug'` (for console progress only)."

**Implementation:** The runner uses `graph.invoke(initial_state)` which runs synchronously without streaming. No progress feedback is printed during graph execution.

**Context:** Round A review (Issue #4) found that streaming with `graph.stream(..., stream_mode="debug")` caused fragile state extraction and recommended `invoke()` as a fix. The implementation adopted `invoke()`. However, this means the spec's streaming requirement is not met.

**Fix:** Two options:
1. **Accept `invoke()` (simpler):** Update the spec to note that `stream_mode="debug"` is not used; `invoke()` is preferred for robustness. No code change.
2. **Implement proper streaming:** Use `graph.stream(initial_state, stream_mode="updates")` and iterate for progress, falling back to the last accumulated state. Requires careful state merging.

**Recommendation:** Option 1 (update spec) unless real-time progress is needed for long-running constructions.

---

### 6. Missing `test_persist.py`

**File:** `tests/songset_construct/test_persist.py` (does not exist)

**Spec:** The test matrix lists `test_persist.py` with scope: "Mock `SongsetClient`; verify each proposal → one atomic create-with-items call; verify `MissingReferenceError` rollback."

**Fix:** Create `test_persist.py` with:
- Test that each proposal generates exactly one `create_songset_with_items` call
- Test that `MissingReferenceError` on one proposal doesn't prevent others from being saved
- Test that `persist_proposals` returns list of created songset IDs
- Test Rich progress bar rendering (optional)

---

## MINOR Issues

### 7. `test_bandwidth_theme_scores_12_keys` uses wrong theme names

**File:** `tests/songset_construct/test_db_queries.py:141-158`

**Issue:** The test creates 12 theme score keys using names that don't match the actual `THEMES` tuple:
```python
scores = json.dumps({theme: 0.5 for theme in [
    "讚美", "感恩", "敬拜", "懺悔", "信靠", "救贖",   # ← wrong themes
    "聖靈", "委身", "爭戰", "宣教", "永恆", "降臨",   # ← wrong themes
]})
```

The actual `THEMES` are: `讚美, 感恩, 敬拜, 奉獻, 認罪, 差遣, 信心, 祈禱, 復興, 聖靈, 十字架, 跟隨`.

**Impact:** The test passes (it only checks `len() == 12`) but doesn't validate that the correct themes are used. If `normalise_cosine_scores` is called on this data, it would process wrong keys.

**Fix:** Use the actual `THEMES` from `rules.themes`:
```python
from stream_of_worship.admin.songset_constructor.rules.themes import THEMES
scores = json.dumps({theme: 0.5 for theme in THEMES})
```

---

### 8. Missing `theme_anchors` HNSW index in admin schema

**File:** `db/schema.py:197-204`

**Issue:** The Drizzle migration (`0018_theme_anchors.sql`) includes an HNSW index:
```sql
CREATE INDEX IF NOT EXISTS idx_theme_anchors_embedding_cosine
    ON theme_anchors USING hnsw (embedding vector_cosine_ops);
```

But `schema.py` `CREATE_THEME_ANCHORS_TABLE` does not include this index, and `ALL_SCHEMA_STATEMENTS` doesn't include it. When the admin CLI initializes the DB schema (via `sow-admin db init`), the `theme_anchors` table is created without the index.

**Impact:** Minimal for a 12-row table (sequential scan is fine), but inconsistent with the Drizzle migration. If the table ever grows beyond 12 anchors, queries would be slow.

**Fix:** Add the index to `CREATE_EMBEDDING_INDEXES` or as a separate entry in `ALL_SCHEMA_STATEMENTS`:
```python
"""
CREATE INDEX IF NOT EXISTS idx_theme_anchors_embedding_cosine
ON theme_anchors USING hnsw (embedding vector_cosine_ops);
""",
```

---

### 9. Missing cosine tolerance test

**Spec Risks table:** "Normalization results differ between Python `cosine()` and pgvector `<=>` — Add a tolerance test in `test_db_queries.py`."

**Implementation:** No tolerance test exists. The `normalise_cosine_scores` function operates on pre-computed SQL scores, but there's no test verifying that pgvector's `<=>` produces values within `1e-6` of Python's `numpy.dot / (norm * norm)`.

**Fix:** Add a test (can be a unit test with known vectors, or an integration test against a test DB):
```python
def test_pgvector_python_cosine_tolerance():
    """Verify normalise_cosine_scores produces equivalent results
    whether raw scores come from pgvector or Python cosine."""
    # Test with known scores that differ by <1e-6
    pgvector_scores = {"讚美": 0.850001, "感恩": 0.750000, ...}
    python_scores = {"讚美": 0.850000, "感恩": 0.750000, ...}
    result_pv = normalise_cosine_scores(pgvector_scores)
    result_py = normalise_cosine_scores(python_scores)
    for theme in THEMES:
        assert abs(result_pv[theme] - result_py[theme]) < 1e-5
```

---

### 10. `_candidate_from_row` missing `r_musical_key` column comment discrepancy

**File:** `db.py:68`

The spec's `_candidate_from_row` pseudocode uses `musical_key=row[11] or row[7]` where `row[11]` is `r.musical_key AS r_musical_key`. The implementation matches this correctly. However, the spec's tuple layout comment (lines 508-512) still says "15 columns" when it should say "16 columns" (indices 0-15). This was noted in Round A (Issue #12) but the spec was never updated.

**Fix:** Update spec `songset_construct_command_v3.md` lines 506-512 to say "16 columns" and fix the tuple layout comment.

---

## Implementation Order

| Order | Issue | Severity | Description | Effort | Files |
|------:|-------|----------|-------------|--------|-------|
| 1 | #1 | CRITICAL | Remove `load_catalog` node (or make conditional) | S | `graph/builder.py`, `graph/nodes.py`, `runner.py` |
| 2 | #2 | CRITICAL | Fix `include_cpw`/`hymnal_mode` empty album_series guard | XS | `config.py` |
| 3 | #3 | CRITICAL | Fix partial persist failure exit code | XS | `commands/songset.py` |
| 4 | #4 | MAJOR | Add `--no-*` flag pairs | XS | `commands/songset.py` |
| 5 | #6 | MAJOR | Create `test_persist.py` | M | `tests/songset_construct/test_persist.py` |
| 6 | #5 | MAJOR | Decide on streaming (spec update or code change) | S | `runner.py` or spec |
| 7 | #7 | MINOR | Fix test theme names | XS | `test_db_queries.py` |
| 8 | #8 | MINOR | Add theme_anchors HNSW index to schema.py | XS | `db/schema.py` |
| 9 | #9 | MINOR | Add cosine tolerance test | S | `test_db_queries.py` |
| 10 | #10 | MINOR | Update spec tuple layout comment | XS | `songset_construct_command_v3.md` |

**Legend:** XS = <1hr, S = 1-2hr, M = 2-4hr

---

## Verification After Fixes

```bash
# Lint
uv run --project ops/admin-cli --extra admin --extra constructor --extra test ruff check \
  src/stream_of_worship/admin/songset_constructor/ \
  src/stream_of_worship/admin/commands/songset.py \
  src/stream_of_worship/admin/commands/theme_anchors.py

# Tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra constructor --extra test \
  pytest tests/songset_construct/ -v

# Cache verification (requires DB with theme_anchors populated)
# First run — should fetch from DB
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user test@example.com --count 3 --proposals 3 --dry-run
# Expected: "Pool fetched from DB (N songs)"

# Second run — should use cache (verify no DB pool query via log/trace)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user test@example.com --count 3 --proposals 3 --dry-run
# Expected: "Pool loaded from cache (age: 0h)"

# Verify include_cpw works without --album-series
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user test@example.com --count 3 --proposals 3 --dry-run --include-cpw
# Expected: album_series includes "CPW"
```

---

## Summary

The implementation is in good shape after Round A fixes. The most significant remaining issue is #1 (the `load_catalog` node bypassing the cache), which completely defeats the bandwidth optimization goal of the v3 spec. Issues #2 and #3 are correctness bugs that affect CLI behavior. The rest are spec-compliance and test coverage gaps.

---
