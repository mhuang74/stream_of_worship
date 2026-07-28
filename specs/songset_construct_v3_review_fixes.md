# Songset Construct v3 — Implementation Review and Fix Plan

**Date:** 2026-07-28
**Spec:** `specs/songset_construct_command_v3.md`
**Impl Summary:** `reports/songset_construct_v3_impl_summary.md`
**PR:** [#136](https://github.com/mhuang74/stream_of_worship/pull/136)
**Branch:** `bulk_songset_creation_0725`

## Overview

This document identifies discrepancies between the v3 spec and the implementation, and provides a prioritized plan to fix them. Issues are categorized by severity.

---

## CRITICAL Issues

### 1. Graph unconditionally writes 5 artifact files (spec violation)

**Spec:** Step 7 says *"No other artifacts are written"* — only `diagnose_report.md` via `--report`.

**Implementation:** `graph/builder.py:44,86` always includes the `write_artifacts` node, which calls `artifacts/writer.py:322-343` and writes:

| File | Format | Description |
|---|---|---|
| `proposals.json` | JSON | All proposals + config |
| `proposal_report.md` | Markdown | Per-proposal details |
| `candidate_pool.csv` | CSV | Pool candidates with metadata |
| `graph_trace.jsonl` | JSONL | Trace event log |
| `songset_review.md` | Markdown | Fallback or LLM review |

These are written on **every** `construct` run — even `--dry-run` — to a hardcoded `Path("output") / "songset_constructor" / <timestamp>/` directory.

**Fix:**
- Remove `write_artifacts` from `graph/builder.py` (both the `add_node` call and the terminal edge)
- Replace terminal edges: `finalize_rank` → `END`, `optional_review` (approve) → `END`, `llm_judge` → `END`
- Remove `write_artifacts` import from `graph/nodes.py`
- Keep `write_enrichment_report` node — it is only reachable via `route_after_enrich` when `only_evaluate_pool_enrichment` is True (currently forced False by construct command, so dead code that is harmless)

**Files:** `graph/builder.py`, `graph/nodes.py`

---

### 2. `persist.py` is not truly atomic (spec violation)

**Spec (Step 9):** *"Atomic save per proposal."* The spec mentions adding a `create_songset_with_items` method or using raw SQL transaction.

**Implementation:** `persist.py:10-29` calls `client.create_songset()` then `client.add_item()` in a loop with **no transaction boundary**. If `add_item` fails partway through (e.g., `MissingReferenceError` on the 3rd of 5 items), the songset row is already committed with partial items.

```python
# persist.py lines 16-28 — no transaction wrapping
songset = client.create_songset(name=name, description=description)
for item in items:
    client.add_item(songset_id=songset.id, ...)
return songset.id
```

**Fix:**
- Add a context manager method on `SongsetClient` or use raw `connection_provider.get_connection()` as a context manager
- Wrap `create_songset` + `add_item` loop in `BEGIN` / `COMMIT` / `ROLLBACK`
- On `MissingReferenceError`, rollback and skip to next proposal

```python
def create_songset_with_items(client, conn, name, description, items):
    with conn:
        songset = client.create_songset(name=name, description=description)
        for item in items:
            client.add_item(songset_id=songset.id, ...)
    return songset.id
```

**Files:** `persist.py`, possibly `db/app/songset_client.py`

---

### 3. Hardcoded `Path("output")` paths (AGENTS.md violation)

**AGENTS.md states:** *"Do not hardcode paths (e.g., avoid `Path("output")`, use configured paths)."*

**Violations:**

| File | Line | Code |
|---|---|---|
| `graph/nodes.py` | 106 | `output_dir = Path(config.output_dir) if config.output_dir else Path("output") / "songset_constructor"` |
| `artifacts/writer.py` | 329 | `output_dir = Path(config.output_dir) if config.output_dir else Path("output") / "songset_constructor" / datetime.now(UTC).strftime(...)` |
| `commands/songset.py` | 500 | `report_dir or Path("output") / "songset_constructor" / datetime.now(UTC).strftime(...)` |

- `nodes.py` and `writer.py` hardcodes: Resolved by Issue 1 (removing `write_artifacts` node eliminates both paths)
- `commands/songset.py` default: The spec explicitly allows `./output/songset_constructor/<UTC stamp>/` as the default report dir, so this is spec-compliant. However, it violates AGENTS.md convention.

**Fix for commands/songset.py:** Replace `Path("output")` with a configured base from `AdminConfig` or define a constant in `config.py`:
```python
DEFAULT_REPORT_OUTPUT = Path.cwd() / "output" / "songset_constructor"
```

**Files:** `commands/songset.py`, optionally `songset_constructor/config.py`

---

## HIGH Issues

### 4. Fragile state extraction in `runner.py`

**File:** `runner.py:38-39`

```python
events = list(graph.stream(initial_state, stream_mode="debug"))
final_state = events[-1][1] if len(events) == 1 else events[-1]
```

With `stream_mode="debug"`, LangGraph yields tuples of `(chunk, mode)`. The conditional is unreliable:
- If 1 event: `events[-1][1]` selects the mode string `"debug"` (no `.get()` method)
- If N events: `events[-1]` selects a tuple (no `.get()` method)

**Impact:** `final_state.get("final_proposals")` at line 42 would raise `AttributeError` if the type is wrong.

**Fix:** Use `graph.invoke()` instead:

```python
result = graph.invoke(initial_state)
```

Or if streaming is needed for progress reporting:

```python
final_state = None
for chunk in graph.stream(initial_state):
    final_state = chunk if isinstance(chunk, dict) else final_state
if final_state is None:
    final_state = graph.invoke(initial_state)
```

**Files:** `runner.py`

---

### 5. Diagnose report missing diversity matrix section

**Spec (Step 7):** Report should include *"7. Diversity matrix."*

**Implementation:** `diagnose.py::assemble_report_sections()` produces:
1. Pool Enrichment Metrics
2. Pool Overview
3. Phase Distribution & Role-Eligibility
4. Rule-Drop Diagnostics
5. Proposals (or No Results — fallback)

Missing: Diversity matrix (#7 in spec list). The diversity functions (`_diversity_metrics`, `_song_overlap_matrix`, `_song_frequency_table`, `_theme_coverage_lines`) exist in `artifacts/writer.py` but are only used by the removed `write_artifacts` graph node.

**Fix:**
- Extract the diversity-matrix logic from `artifacts/writer.py` into `diagnose.py` (or import them)
- Add a `"## Diversity Matrix"` section to `assemble_report_sections()` that produces the overlap matrix + frequency table + theme coverage + bottlenecks

**Files:** `diagnose.py`, `artifacts/writer.py` (for import extraction)

---

### 6. `runner.run()` function is untested

**File:** `tests/songset_construct/test_runner.py` — only tests `RunConfig` validation (10 tests), not `runner.run()`.

The fragile state extraction (Issue 4) was never exercised by any test.

**Fix:** Add these test cases to `test_runner.py`:
- **Cache hit:** Mock `cache.try_load_pool` to return a pool list; mock `build_graph.stream` to yield known events; verify `run()` extracts final state correctly
- **Cache miss:** Mock `cache.try_load_pool` to return None; mock `fetch_catalog_pool` to return a pool; verify `cache.save_pool` was called
- **State extraction:** Mock `build_graph.stream` to yield 1 event, N events; verify `final_proposals` / `pool` / `trace` / `enrichment_metrics` in result dict

**Files:** `tests/songset_construct/test_runner.py`

---

## MEDIUM Issues

### 7. Cache status message missing age

**Spec (Step 5):** `[dim]Pool loaded from cache (age: Nh)[/dim]`

**Implementation:** `runner.py:18` prints `"[dim]Pool loaded from cache[/dim]"` — the age is omitted.

**Fix:** Pass `_cache_path(...)` info or use `path.stat().st_mtime` to compute and include age:

```python
age_h = (time.time() - _cache_path(...).stat().st_mtime) / 3600
Console().print(f"[dim]Pool loaded from cache (age: {age_h:.0f}h)[/dim]")
```

**Files:** `runner.py`

---

### 8. Missing bandwidth validation integration test

**Spec:** A test that runs `fetch_catalog_pool` against a test database with pgvector and asserts:
- No `embedding::text` appears in any executed query
- `song_theme_scores_raw` JSON has exactly 12 keys per row (when embedding exists)
- `line_theme_scores_raw` JSON has exactly 12 keys per song (or is empty)
- Total bytes transferred < 1 MB for pool_limit=200

**Implementation:** `test_db_queries.py` only does SQL string assertions (`"embedding::text" not in POOL_QUERY`) and unit tests on `_candidate_from_row` parsing. No integration test runs against a real or mock database cursor.

**Fix:** Add a `test_bandwidth_validation` test:
- Create a fake cursor that records all executed SQL queries + parameters
- Mock `ReadOnlyClient.connection.cursor` to return the fake cursor
- Call `fetch_catalog_pool(config, client=mock_read_client)`
- Assert no recorded query contains `embedding::text`
- Assert `song_theme_scores_raw` has 12 keys when populated
- Estimate total bytes from recorded result set dimensions

**Files:** `tests/songset_construct/test_db_queries.py`

---

## LOW Issues

### 9. Duplicate imports in `commands/songset.py`

**Lines 6-12:** `Path` and `Optional` are imported twice:

```python
from pathlib import Path       # line 6
from typing import Optional    # line 7
import json                    # line 9
from datetime import UTC, datetime  # line 10
from pathlib import Path       # line 11 — DUPLICATE
from typing import Optional     # line 12 — DUPLICATE
```

**Fix:** Remove the duplicate imports at lines 11-12.

**Files:** `commands/songset.py`

---

### 10. Unused `numpy` import in `commands/theme_anchors.py`

**Line 12:** `import numpy as np` — neither `numpy` nor `np` is used in the file. The sync command builds vector strings manually (`str(v)` concatenation with commas).

**Fix:** Remove the `import numpy as np` line.

**Files:** `commands/theme_anchors.py`

---

### 11. Inline `from rich.console import Console` in `persist.py`

**Lines 39, 70, 73, 76:** Console and Progress are imported inside function bodies instead of at the module top level:

```python
# persist.py:39 — inside persist_proposals()
from rich.progress import BarColumn, Progress, TextColumn

# persist.py:70 — inside persist_proposals() loop
from rich.console import Console
```

Since `rich` is always available (in the `admin` extra), these can be top-level imports.

**Fix:** Move `from rich.console import Console` and `from rich.progress import ...` to the top of `persist.py`.

**Files:** `persist.py`

---

### 12. Spec `_candidate_from_row` pseudocode has wrong indices

**Spec (lines 506-532):** The pseudocode says "15 columns" (indices 0-14) and uses:
- `raw_scores = row[14]` — but row[14] is `r.loudness_db`
- `musical_key=row[10] or row[7]` — but row[10] is `r.tempo_bpm`
- `musical_mode=row[11]` — but row[11] is `r.musical_key AS r_musical_key`

**Actual tuple layout (16 columns, indices 0-15):**

| Index | Column | Index | Column |
|-------:|--------|-------:|--------|
| 0 | `s.id` | 8 | `s.lyrics_raw` |
| 1 | `s.title` | 9 | `r.hash_prefix` |
| 2 | `s.title_pinyin` | 10 | `r.tempo_bpm` |
| 3 | `s.composer` | 11 | `r_musical_key` |
| 4 | `s.lyricist` | 12 | `r.musical_mode` |
| 5 | `s.album_name` | 13 | `r.key_confidence` |
| 6 | `s.album_series` | 14 | `r.loudness_db` |
| 7 | `s.musical_key` | **15** | **`song_theme_scores_raw`** |

**The implementation is CORRECT** (`_candidate_from_row` at `db.py:57-77` uses `row[15]`, `row[11] or row[7]`). Only the spec document needs fixing.

**Fix:** Update the spec's `_candidate_from_row` code block to:
- Change `row[14]` to `row[15]`
- Change `musical_key=row[10]` to `musical_key=row[11]`
- Change `musical_mode=row[11]` to `musical_mode=row[12]`
- Update the tuple layout comment from 15 to 16 columns
- Update `key_confidence=row[12]` to `key_confidence=row[13]`
- Update `loudness_db=row[13]` to `loudness_db=row[14]`

**Files:** `specs/songset_construct_command_v3.md`

---

### 13. `route_after_judge` dead code

**`graph/nodes.py:428-429`**: The routing function checks `interactive_review`, but the `construct` command forces `interactive_review = False` at `commands/songset.py:459`. The `optional_review` node uses `langgraph.types.interrupt` which will never be triggered.

This is dead code but causes no harm — it's valid to have graph nodes that are never reached. Can be left as-is for future use.

**Fix:** No change needed unless the graph is simplified (which happens naturally if `write_artifacts` is removed — `llm_judge` can edge directly to `END`).

---

## Implementation Order

| Order | Issue | Description | Effort | Files |
|------:|-------|-------------|--------|-------|
| 1 | #1 | Remove `write_artifacts` node from graph | S | `builder.py`, `nodes.py` |
| 2 | #4 | Fix fragile state extraction in `runner.py` | S | `runner.py` |
| 3 | #2 | Make `persist.py` atomic | M | `persist.py`, `songset_client.py` |
| 4 | #3 | Fix hardcoded `Path("output")` (partly via #1) | S | `commands/songset.py`, `config.py` |
| 5 | #7 | Add cache age to status message | XS | `runner.py` |
| 6 | #9 | Remove duplicate imports | XS | `commands/songset.py` |
| 7 | #10 | Remove unused numpy import | XS | `commands/theme_anchors.py` |
| 8 | #11 | Move rich imports to top level | XS | `persist.py` |
| 9 | #12 | Fix spec `_candidate_from_row` indices | XS | `specs/songset_construct_command_v3.md` |
| 10 | #5 | Add diversity matrix to diagnose report | M | `diagnose.py`, `artifacts/writer.py` |
| 11 | #6 | Add `runner.run()` tests | M | `test_runner.py` |
| 12 | #8 | Add bandwidth validation test | M | `test_db_queries.py` |

**Legend:** XS = <1hr, S = 1-2hr, M = 2-4hr

## Verification After Fixes

After all fixes, run:

```bash
# Lint
uv run --project ops/admin-cli --extra admin --extra constructor --extra test ruff check src/stream_of_worship/admin/songset_constructor/ src/stream_of_worship/admin/commands/songset.py src/stream_of_worship/admin/commands/theme_anchors.py

# Tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra constructor --extra test pytest tests/songset_construct/ -v

# Dry-run (requires DB with theme_anchors populated)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct --user test@example.com --count 3 --proposals 3 --dry-run --report

# Verify no unintended files written
# Prior to fix: proposals.json, proposal_report.md, candidate_pool.csv, graph_trace.jsonl, songset_review.md
# After fix: only diagnose_report.md in --report-dir
```
