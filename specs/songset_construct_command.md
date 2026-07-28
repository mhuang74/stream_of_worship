# Spec: `sow-admin songset construct`

## Goal

Promote the songset-constructor POC (`lab/poc-scripts/poc/songset_constructor/**`) into a new production package `ops/songset-constructor` (package: `sow_songset_constructor`), then expose it via the Admin CLI as `sow-admin songset construct` — persisting constructed songsets to a designated user, gated by a single diagnose-report flag, dry-run flag, and interactive confirm / `--yes` auto-save.

## Package Promotion (lab → ops)

A new package is created at `ops/songset-constructor/` mirroring the layout of other ops packages:

```
ops/songset-constructor/
  pyproject.toml
  README.md
  src/sow_songset_constructor/
    __init__.py
    cli.py                       # moved from lab/poc-scripts/poc/songset_constructor/cli.py
    config.py                    # moved; imports updated from poc.* -> sow_songset_constructor.*
    models.py                    # moved
    db.py                         # moved
    data/
      theme_anchors.json         # moved
    graph/
      __init__.py
      builder.py                 # moved
      nodes.py                   # moved
      state.py                   # moved
      llm.py                     # moved
      checkpointer.py            # moved
    rules/
      __init__.py
      beam.py                    # moved
      diagnostics.py             # moved
      embeddings.py              # moved
      fitness.py                 # moved
      hard_constraints.py        # moved
      harmony.py                 # moved
      phases.py                  # moved
      proposals.py               # moved
      themes.py                  # moved
      transitions.py             # moved
    artifacts/
      __init__.py
      writer.py                  # moved
      enrichment_report.py       # moved
      trace.py                   # moved
  tests/
    __init__.py
    test_config.py
    test_phases.py
    test_beam.py
    test_diagnostics.py
    ... (port the relevant POC tests, if any)
```

### Promotion Rules

- **Single source of truth.** After promotion, `lab/poc-scripts/poc/songset_constructor/**` is deleted. The lab entrypoint `lab/poc-scripts/construct_songset_agent.py` becomes a thin dev wrapper that imports from `sow_songset_constructor` (kept for ad-hoc experimentation; not the production path).
- **Import rewrite.** Every `from poc.songset_constructor.*` becomes `from sow_songset_constructor.*` inside the new package. The lab dev wrapper updates its imports accordingly.
- **Data files.** `data/theme_anchors.json` ships inside the package via `[tool.setuptools.package-data]` so `importlib.resources` can locate it.
- **Dependencies.** The new package's `pyproject.toml` declares the heavy deps (langgraph, langchain-openai, numpy, pydantic, python-dotenv, rapidfuzz) as its own `[project.optional-dependencies]` groups (e.g. `llm`, `test`) — admin-cli will depend on the package itself, not redeclare its deps.
- **DB coupling.** `db.py` currently imports `from stream_of_worship.admin.db.models import Recording, Song` and `from stream_of_worship.db.app.read_client import ReadOnlyClient`. The new package declares `stream-of-worship[postgres]` (the admin-cli package) as a dependency, preserving this import path. This creates a one-way dependency: `ops/songset-constructor` → `ops/admin-cli` (db helpers only), and `ops/admin-cli` → `ops/songset-constructor` (the construct command). To avoid a circular install, admin-cli declares the songset-constructor package as an *optional* extra (`songset_constructor`), not a core dep.
- **No ML.** The new package still refuses to import PyTorch/Demucs/allin1. It is pure Python + optional langchain-openai.

## Non-Goals

- Modifying the songset-constructor rules/graph logic during the move — pure relocation + import rewrite. Behaviour must be byte-identical to the POC.
- ML/heavy analysis (Demucs, allin1) — Admin CLI still refuses to import these; songset construction is pure Python + optional LLM via langchain-openai.
- Video/audio rendering of constructed songsets.
- Surfaces other than Typer CLI (no TUI, no webapp wiring).

## User Decisions (from interview)

1. **Packaging** — promote `lab/poc-scripts/poc/songset_constructor/**` to a new production package `ops/songset-constructor` (import name `sow_songset_constructor`). Admin CLI depends on it via a new `songset_constructor` extra; the command lazy-imports and errors clearly when the extra is missing.
2. **Diagnose report** — emit `diagnose_report.md` only; drop `proposals.json`, `proposal_report.md`, `candidate_pool.csv`, `graph_trace.jsonl`, `songset_review.md`, `enrichment_report.md`. Flag: `--diagnose-report`.
3. **Save scope** — all top-k proposals saved as separate songsets owned by `--user`.
4. **Output dir** — `./output/songset_constructor/<timestamp>/` relative to cwd; overridable via `--output-dir`.
5. **Dry-run** — still writes `diagnose_report.md` (read-only/safe); only DB writes suppressed.

## CLI Surface

New Typer subcommand added to `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` alongside the existing `songset list`:

```
sow-admin songset construct \
  --user alice@example.com \
  [--songs 2..5]            (default 3)
  [--top-k 1..20]          (default 3)
  [--pool-limit >=4]       (default 200)
  [--album-series "敬拜讚美 (1)" ...]
  [--include-cpw / --no-include-cpw]    (default False)
  [--intimate / --no-intimate]          (default False)
  [--hymnal-mode / --no-hymnal-mode]    (default False)
  [--season advent|christmas|lent|easter|pentecost]
  [--no-llm / --llm]                   (default --llm)
  [--llm-judge / --no-llm-judge]       (default --no-llm-judge)
  [--llm-model NAME]
  [--env-file PATH]
  [--relax-h1 / --no-relax-h1]         (default --relax-h1)
  [--auto-relax / --no-auto-relax]     (default --auto-relax)
  [--relax-h2-bpm N]
  [--relax-h3-bpm N]
  [--relax-h4 / --no-relax-h4]         (default --no-relax-h4)
  [--relax-h4-bpm N]
  [--relax-h5 / --no-relax-h5]         (default --no-relax-h5)
  [--relax-h5-cfd N]
  [--output-dir PATH]                  (default ./output/songset_constructor/<UTC stamp>/)
  [--diagnose-report]                  (default False; writes diagnose_report.md)
  [--dry-run]                          (default False; skips DB writes, still writes report)
  [--yes]                              (default False; auto-saves without prompting)
  [--config PATH]                      (existing; for AdminConfig)

Mutual exclusion:
  --dry-run implies --yes is ignored (dry-run always wins).
  --no-llm forbids --llm-judge (RunConfig.validate_environment enforces).
```

Required: `--user <email>`. Missing `--user` → error exit 2.

All POC-only flags intentionally omitted: `--interactive-review`, `--resume-thread-id`, `--only-evaluate-pool-enrichment`. (Interactive review is replaced by the new `--yes` confirm prompt; resume is internal to the constructor only; pool-enrichment eval is rolled into `--diagnose-report`.)

## Module Layout

```
ops/songset-constructor/                                   (NEW package — see "Package Promotion" above)
  pyproject.toml
  src/sow_songset_constructor/...                          (moved from lab/poc-scripts/poc/songset_constructor/)
  tests/...

ops/admin-cli/src/stream_of_worship/admin/commands/songset.py   (extend with 'construct' subcommand)
ops/admin-cli/src/stream_of_worship/admin/songset_construct/
  __init__.py
  runner.py          # builds RunConfig, drives the LangGraph, returns proposals + pool + trace
  persist.py         # converts SongsetProposal[] -> SongsetClient.create_songset + add_item rows
  diagnose.py        # combines brief_summary_block, diversity matrix, diagnostics lines, no-results summary
  report_writer.py   # writes ./output/songset_constructor/<ts>/diagnose_report.md
ops/admin-cli/tests/commands/songset/test_construct.py         (new; unit tests, mocked DB)
ops/admin-cli/tests/songset_construct/
  test_runner.py
  test_persist.py
  test_diagnose.py
```

## Behaviour

### Step 1 — Resolve user
Resolve `--user <email>` via `UserClient.get_user_by_email`. If missing → `[red]User not found: <email>[/red]`, exit 1. (Mirrors `songset list`.)

### Step 2 — Load AdminConfig + build read-only & write clients
- `AdminConfig.load(config_path)` → `ConnectionProvider`.
- `ReadOnlyClient(connection_provider)` passed into POC `fetch_catalog_pool(config, client=...)`.
- `SongsetClient(connection_provider, user_id=user.id)` constructed lazily ONLY when saving.

### Step 3 — Build RunConfig
Instantiate `sow_songset_constructor.config.RunConfig` mirroring the POC CLI's typer options (1:1 mapping table maintained inside `runner.py`). Force `interactive_review=False`, `resume_thread_id=None`, `only_evaluate_pool_enrichment=False`. The constructor's `output_dir` is set to the value resolved in Step 0 (cwd-relative default).

### Step 4 — Run the graph
Import `sow_songset_constructor.graph.builder.build_graph` lazily (so the command import doesn't hard-fail when the extra is uninstalled). Use the same streaming pattern as the original `construct_songset_agent.py:_run_graph_with_traces`. Capture `final_proposals`, `pool`, `trace`. No `__interrupt__` handling (we disabled interactive_review).

### Step 5 — Print summary
Always print a Rich table summarising every proposal (rank, score.total, song sequence, BPM arc, key arc, hard-constraint warnings). If `proposals == 0`, fall back to `_fallback_no_results_summary` (no LLM call) — same as POC `--no-llm` path.

### Step 6 — Diagnose report (if `--diagnose-report`)
Write `<output_dir>/diagnose_report.md` with this combined content:

1. `# Songset Constructor Diagnose Report` header + timestamp + RunConfig dump.
2. Pool enrichment metrics — reuse `sow_songset_constructor.artifacts.enrichment_report.render_console_summary` output as a Markdown fenced block; if `--only-evaluate-pool-enrichment` was implied by `proposals == []`, this is the only meaningful section.
3. Pool overview table (loaded / dropped / enriched counts, taken from trace).
4. Phase distribution & role-eligibility counts (reuses `rules.diagnostics.role_eligibility_counts`).
5. Rule-drop diagnostics bullets (`rules.diagnostics.diagnostic_lines`).
6. Per-proposal sections — reuse `artifacts.writer.brief_summary_block`, the details table (`write_report`'s `### Details` block), and score components line. No JSON dump, no CSV.
7. Diversity section — reuse `artifacts.writer._diversity_summary` content.
8. Graph trace condensed — one bullet per node event (original `_event_lines` helper, ported into `runner.py`).
9. No-results fallback summary if applicable.

The report replaces all 5 original artifacts. No `proposals.json`, etc., are written.

### Step 7 — Save flow

#### `--dry-run`
Print `[yellow]Dry run: --dry-run set; skipping DB writes.[/yellow]` and exit 0. (Diagnose report from Step 6 still written if requested.)

#### Default (no `--yes`, no `--dry-run`)
After printing the summary and diagnose path, prompt the user:

```
Save N songset(s) to user <email> (y/N)?
```

(Prompt via `typer.confirm(..., default=False)`.) On `N`/Ctrl-C → exit 0 without saving. On `y` → proceed to Step 8.

#### `--yes`
Skip the prompt; proceed directly to Step 8.

### Step 8 — Persist songsets (`persist.py`)
For each `SongsetProposal` in `final_proposals`:
1. `songset_client.create_songset(name=..., description=...)` where:
   - `name` = `f"Constructed rank {rank}/{top_k} ({songs}-song)"` plus optional season/series suffix.
   - `description` = first 200 chars of `proposal.rationale` if present, else `"Auto-generated by sow-admin songset construct (run_id=...)"`.
2. For each `ProposalItem` (in `position` order): `songset_client.add_item(songset_id, song_id, recording_hash_prefix, position, gap_beats, crossfade_enabled, crossfade_duration_seconds, key_shift_semitones, tempo_ratio)`. The POC `ProposalItem` extends `DraftItem`, so all of those fields are populated.
3. Wrap the whole save loop in one Rich progress display; each saved songset prints `Created songset <id> (rank N)`.

On `MissingReferenceError` (stale recording between constructor run and save): print `[red]` message, rollback that one songset, continue with the next proposal, exit 1 at end if any failed.

## Dependency Changes

### New package: `ops/songset-constructor`

`ops/songset-constructor/pyproject.toml`:
```toml
[project]
name = "sow-songset-constructor"
version = "0.1.0"
description = "Songset constructor for Stream of Worship (LangGraph state machine)"
requires-python = ">=3.11"
dependencies = [
    "stream-of-worship[postgres]",   # for ReadOnlyClient, db.models, db.schema
    "langgraph>=0.2.50",
    "langgraph-checkpoint-sqlite>=2.0.0",
    "langchain-core>=0.3.0",
    "langchain-openai>=0.2.0",
    "pydantic>=2.7.0",
    "numpy>=1.26.0",
    "python-dotenv>=1.2.2",
    "rapidfuzz>=3.0.0",
]

[project.optional-dependencies]
test = ["pytest>=7.4.0", "pytest-mock>=3.12.0"]

[tool.uv.sources]
stream-of-worship = { path = "../../ops/admin-cli" }

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
sow_songset_constructor = ["data/*.json"]
```

### Admin CLI: new extra

`ops/admin-cli/pyproject.toml`:
```toml
[project.optional-dependencies]
songset_constructor = [
    "sow-songset-constructor",   # path source below
]

[tool.uv.sources]
sow-songset-constructor = { path = "../songset-constructor" }
```

The `songset_constructor` extra is **not** part of the default `admin` extra. Users who want `sow-admin songset construct` install with `uv sync --extra admin --extra songset_constructor`.

Lazy-import guard in `runner.py`:
```python
try:
    from sow_songset_constructor.config import RunConfig
    from sow_songset_constructor.graph.builder import build_graph
    from sow_songset_constructor.db import fetch_catalog_pool
    from sow_songset_constructor.rules.diagnostics import diagnostic_lines, role_eligibility_counts
    from sow_songset_constructor.artifacts.writer import brief_summary_block, _diversity_summary
    from sow_songset_constructor.artifacts.enrichment_report import render_console_summary
except ImportError as exc:
    raise RuntimeError(
        "songset_constructor extra not installed. Run: "
        "`uv sync --extra admin --extra songset_constructor` "
        "(or `uv pip install -e '.[songset_constructor]'`)."
    ) from exc
```

### Lab POC entrypoint

`lab/poc-scripts/construct_songset_agent.py` and `lab/poc-scripts/poc/songset_constructor/**` are removed. The lab entrypoint is replaced by a thin dev wrapper at `lab/poc-scripts/construct_songset_agent.py` that imports from `sow_songset_constructor` and re-exposes the same CLI for ad-hoc experimentation:

```python
# lab/poc-scripts/construct_songset_agent.py (rewritten)
from sow_songset_constructor.cli import app, main

if __name__ == "__main__":
    main()
```

`lab/poc-scripts/pyproject.toml` adds `sow-songset-constructor` to its sources:
```toml
[tool.uv.sources]
sow-songset-constructor = { path = "../../ops/songset-constructor" }
```

## Tests

### New package tests: `ops/songset-constructor/tests/`
Port the existing POC tests (if any) and add coverage for the import-rewrite (every module imports cleanly under the new `sow_songset_constructor.*` name). Run with `cd ops/songset-constructor && uv run --extra test pytest -v`.

### Admin CLI tests: `ops/admin-cli/tests/songset_construct/`
- `test_runner.py` — mock `build_graph` + `fetch_catalog_pool`; verify RunConfig is constructed with the right alias for each Typer option; verify `--user` missing exits 2; verify `--no-llm --llm-judge` combination errors via `validate_environment`.
- `test_persist.py` — mock `SongsetClient`; verify each proposal becomes one `create_songset` + N `add_item` calls with the same `recording_hash_prefix`, `key_shift_semitones`, `crossfade_enabled`, `crossfade_duration_seconds`, `gap_beats`, `tempo_ratio`; verify `MissingReferenceError` surfaces cleanly without leaving partial songsets.
- `test_diagnose.py` — synthesise two proposals + a pool + a trace and assert the report contains the expected sections (header, pool overview, role eligibility, per-proposal blocks, diversity matrix, no-results fallback when proposals=[]).

Run with: `uv run --project ops/admin-cli --python 3.11 --extra admin --extra songset_constructor --extra test pytest tests/songset_construct/ -v`.

## Documentation Updates

- `AGENTS.md` — add `ops/songset-constructor` as a new component (between Admin CLI and Analysis Service) and add `songset construct` to the Admin CLI command list with the `--extra songset_constructor` invocation.
- `ops/songset-constructor/README.md` — package overview, install instructions, pointer to `docs/agent_guide_songset_constructor.md` for behavioural reference.
- `ops/admin-cli/README.md` — short example block for `sow-admin songset construct`.
- `docs/agent_guide_songset_constructor.md` — update the "Quick Start" section to point at `sow-admin songset construct` as the production entrypoint; keep the lab recipes for development.
- `lab/poc-scripts/README.md` (if present) — note that `construct_songset_agent.py` is now a thin wrapper around `sow_songset_constructor`.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Circular install: `ops/songset-constructor` depends on `stream-of-worship` (db helpers) while `ops/admin-cli` depends on `sow-songset-constructor` | Admin CLI declares the songset-constructor only under the optional `songset_constructor` extra, never as a core dep. The default `admin` install never pulls in the new package. |
| LangGraph sqlite checkpointer writes to a filesystem path | The package uses an in-memory checkpointer when `interactive_review=False`; confirm in `graph.builder` or pass `MemorySaver`. |
| Stale recording_hash_prefix between construct and save | `SongsetClient.add_item` already calls `validate_recording_exists` when `get_recording` is provided — wire a `CatalogService.get_recording_by_hash_prefix` lookup into the save step. |
| `--user` not-yet-existing | Early `UserClient.get_user_by_email` check before running the (slow) graph. |
| Heavy LLM deps under default `admin` extra | Keep deps under the new `songset_constructor` extra only; never import langgraph/langchain-openai at module top of `commands/songset.py`. |
| Behavioural drift during the lab → ops move | Pure relocation + import rewrite in a single commit; existing POC tests (if any) must pass unchanged against the new package before the lab code is deleted. |

## Acceptance

### Package promotion
- `ops/songset-constructor/` exists with `pyproject.toml`, `src/sow_songset_constructor/`, and `tests/`.
- `uv sync --project ops/songset-constructor --extra test` succeeds.
- `python -c "from sow_songset_constructor.graph.builder import build_graph"` succeeds.
- `lab/poc-scripts/poc/songset_constructor/**` is deleted; `lab/poc-scripts/construct_songset_agent.py` is a thin wrapper that imports from `sow_songset_constructor`.

### Admin CLI command
- `sow-admin songset construct --user me@x --no-llm --songs 3 --top-k 3 --dry-run` prints the proposed songsets, writes no DB rows, writes no diagnose_report.md (no `--diagnose-report`).
- Same with `--diagnose-report` writes `./output/songset_constructor/<ts>/diagnose_report.md`, no other artifacts.
- Same without `--dry-run` prompts `y/N`; selecting `N` leaves DB clean.
- Same with `--yes` persists 3 songsets under `me@x` with correct items and transition params; final stdout lists the created songset IDs.
- Running without the `songset_constructor` extra installed fails with a clear `RuntimeError` and the install hint.
