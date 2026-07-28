# Spec: `sow-admin songset construct` (Revised)

## Goal

Integrate the songset-constructor POC (`lab/poc-scripts/poc/songset_constructor/**`) into the Admin CLI as a lazily-loaded subpackage (`stream_of_worship.admin.songset_constructor`), exposed via `sow-admin songset construct`. The command persists constructed songsets to a designated user, gated by `--dry-run`, `--yes`, and an optional `--report` flag.

This is a **production refactor**, not a pure relocation. The CLI surface, default behavior, and DB coupling are intentionally changed to fit the Admin CLI's ergonomics and dependency model.

## Architecture Decision: Admin-CLI Subpackage (Not a New Package)

Instead of promoting the POC to a standalone `ops/songset-constructor` package, the code is copied and adapted into:

```
ops/admin-cli/src/stream_of_worship/admin/songset_constructor/
  __init__.py
  config.py              # adapted: no __file__-based paths, no sow_lab_app imports
  models.py              # copied as-is
  db.py                  # adapted: accepts ReadOnlyClient only, no internal client builder
  graph/
    __init__.py
    builder.py           # copied; checkpointer may drop SqliteSaver path
    nodes.py
    state.py
    llm.py
    checkpointer.py      # adapted: always InMemorySaver (no interactive_review)
  rules/
    __init__.py
    beam.py
    diagnostics.py
    embeddings.py
    fitness.py
    hard_constraints.py
    harmony.py
    phases.py
    proposals.py
    themes.py
    transitions.py
  artifacts/
    __init__.py
    writer.py
    enrichment_report.py
    trace.py
  runner.py              # NEW: builds RunConfig, drives LangGraph, returns result dict
  persist.py             # NEW: SongsetProposal[] -> SongsetClient atomic save
  diagnose.py            # NEW: assembles report content from result dict
  report_writer.py       # NEW: writes diagnose_report.md to filesystem
```

**Why a subpackage?**
- **Eliminates circular dependency.** No cross-package import dance; one `pyproject.toml`, one lockfile.
- **No file move from `lab/`.** The POC in `lab/poc-scripts/poc/songset_constructor/**` remains untouched and frozen for reference. No import-rewrite busywork.
- **Lazy imports.** The `construct` subcommand only imports the subpackage at runtime. Users without the `constructor` extra get a clear error.

## Non-Goals

- Modifying the core rules/graph scoring logic — beam search, fitness, harmony, transitions remain identical.
- ML/heavy analysis (Demucs, allin1) — Admin CLI still refuses to import these.
- Video/audio rendering of constructed songsets.
- Surfaces other than Typer CLI.
- Preserving the `lab/poc-scripts/construct_songset_agent.py` entrypoint or the POC CLI surface.

## CLI Surface (Revised)

New Typer subcommand added to `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py`:

```
sow-admin songset construct \
  --user alice@example.com \
  [-n, --count 2..5]                (default 3)
  [-k, --proposals 1..20]           (default 3)
  [-p, --pool >=4]                  (default 200)
  [--album-series "敬拜讚美 (1)" ...]
  [--include-cpw / --no-include-cpw]    (default False)
  [--intimate / --no-intimate]          (default False)
  [--hymnal-mode / --no-hymnal-mode]    (default False)
  [--season advent|christmas|lent|easter|pentecost]
  [--llm / --no-llm]                   (default --no-llm)
  [--llm-judge / --no-llm-judge]       (default --no-llm-judge)
  [--llm-model NAME]
  [--relax h2:90,h3:80,h4,h5:3]        (optional; see Relax Syntax)
  [--constraints-file PATH]            (YAML/JSON override for --relax)
  [--report]                           (default False; writes diagnose_report.md)
  [--report-dir PATH]                  (default ./output/songset_constructor/<UTC stamp>/)
  [--dry-run]                          (default False; skips DB writes, still writes report if --report)
  [--yes]                              (default False; auto-saves without prompting)
  [-c, --config PATH]                  (existing; for AdminConfig)
```

### Mutually Exclusive / Validation Rules
- `--dry-run` + `--yes`: allowed; `--yes` is simply moot. No error.
- `--no-llm` + `--llm-judge`: error at `RunConfig.validate_environment()`.
- `--report` without `--report-dir`: uses cwd-relative default.
- `--report-dir` without `--report`: allowed but has no effect.

### Removed from POC
- `--songs` → `--count` (`-n`)
- `--top-k` → `--proposals` (`-k`)
- `--pool-limit` → `--pool` (`-p`)
- `--output-dir` → `--report-dir` (only active with `--report`)
- `--diagnose-report` → `--report`
- `--interactive-review`, `--resume-thread-id`, `--only-evaluate-pool-enrichment`
- `--env-file` (admin-cli uses `AdminConfig` / shell env)
- All individual `--relax-hN*` flags → `--relax`

### Relax Syntax
`--relax` accepts a comma-separated list of `key[:value]` tokens:

| Token | Maps to |
|-------|---------|
| `h1` | `relax_h1 = True` |
| `h2:90` | `relax_h2_bpm = 90` (also implies `relax_h1` if auto-relax logic requires) |
| `h3` | `relax_h3_bpm = <default>` |
| `h3:85` | `relax_h3_bpm = 85` |
| `h4` | `relax_h4 = True` |
| `h4:40` | `relax_h4_bpm = 40` |
| `h5` | `relax_h5 = True` |
| `h5:3` | `relax_h5_cfd = 3` |

`--constraints-file` is a YAML/JSON dict with the same keys. It is merged with `--relax`; explicit flags win.

Required: `--user <email>`. Missing → error exit 2.

## Dependency Changes

### Admin CLI: new `constructor` extra

`ops/admin-cli/pyproject.toml`:

```toml
[project.optional-dependencies]
admin = [
    # ... existing deps ...
    "numpy>=1.24.0",
]
constructor = [
    "langgraph>=0.2.50",
    "langchain-core>=0.3.0",
    "langchain-openai>=0.2.0",
    "numpy>=1.26.0",
    "pydantic>=2.7.0",
    "python-dotenv>=1.2.2",
    "rapidfuzz>=3.0.0",
]
test = [
    # ... existing test deps ... (already covers pytest, pydantic)
]
```

Notes:
- `langgraph-checkpoint-sqlite` is **omitted** — `InMemorySaver` is used exclusively.
- `typer` and `rich` are already in the `admin` extra.
- `psycopg` comes via `stream-of-worship[postgres]`; the constructor subpackage does not add new DB drivers.
- No circular dependencies.

### Lazy-import guard

In `commands/songset.py`:

```python
def _import_constructor():
    try:
        from stream_of_worship.admin.songset_constructor.config import RunConfig
        from stream_of_worship.admin.songset_constructor.graph.builder import build_graph
        from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool
        # ... other imports ...
    except ImportError as exc:
        raise RuntimeError(
            "constructor extra not installed. Run: "
            "`uv sync --extra admin --extra constructor`"
        ) from exc
```

## Module Layout

```
ops/admin-cli/src/stream_of_worship/admin/
  commands/
    songset.py                 # extended with 'construct' subcommand (thin wrapper)
  songset_constructor/         # NEW subpackage
    __init__.py
    config.py                  # RunConfig with revised defaults and no __file__ paths
    models.py
    db.py                      # fetch_catalog_pool(ReadOnlyClient, RunConfig)
    graph/
      builder.py
      nodes.py
      state.py
      llm.py
      checkpointer.py          # always InMemorySaver
    rules/
      beam.py
      diagnostics.py
      embeddings.py
      fitness.py
      hard_constraints.py
      harmony.py
      phases.py
      proposals.py
      themes.py
      transitions.py
    artifacts/
      writer.py
      enrichment_report.py
      trace.py
    runner.py                  # RunConfig from CLI args -> graph.run() -> result dict
    persist.py                 # atomic save: create_songset + add_items in one tx
    diagnose.py                # assemble markdown sections from result
    report_writer.py           # write diagnose_report.md if --report

ops/admin-cli/tests/songset_construct/
  __init__.py
  test_runner.py
  test_persist.py
  test_diagnose.py
  test_relax_parser.py
```

## Behaviour

### Step 0 — Parse `--relax` and `--constraints-file`
If provided, merge into a `dict[str, Any]` passed to `RunConfig` construction.

### Step 1 — Resolve user
Same as `songset list`: `UserClient.get_user_by_email(email)`. Missing → `[red]User not found[/red]`, exit 1.

### Step 2 — Build AdminConfig + ReadOnlyClient
`AdminConfig.load(config_path)` → `ConnectionProvider` → `ReadOnlyClient`.

### Step 3 — Build RunConfig
Map Typer options to `RunConfig` fields:
- `llm_enabled` (from `--llm / --no-llm`, default `False`)
- `proposals` (from `--proposals`)
- `count` (from `--count`)
- `pool` (from `--pool`)
- `relax_*` (from parsed `--relax` / `--constraints-file`)
- `output_dir` (from `--report-dir` if `--report` else `None`)

Force:
- `interactive_review = False`
- `resume_thread_id = None`
- `only_evaluate_pool_enrichment = False`

### Step 4 — Run graph
Call `runner.run(config, read_client)` which:
1. Calls `fetch_catalog_pool(config, client=read_client)`.
2. Builds graph via `build_graph(config)` with `InMemorySaver`.
3. Streams with `stream_mode="debug"` (for console progress only).
4. Returns `{"final_proposals": ..., "pool": ..., "trace": ..., "enrichment_metrics": ...}`.

### Step 5 — Print summary
Always print a Rich table of proposals (rank, score, sequence, BPM/key arcs, warnings). If `proposals == []`, print the deterministic no-results summary (no LLM call — because `--llm` is opt-in, we don't need `_llm_no_results_summary`).

### Step 6 — Report (if `--report`)
Write `<report_dir>/diagnose_report.md` with:
1. Header + timestamp + RunConfig dump.
2. Pool enrichment metrics (fenced block).
3. Pool overview table.
4. Phase distribution & role-eligibility counts.
5. Rule-drop diagnostics bullets.
6. Per-proposal sections (summary + details + score components).
7. Diversity matrix.
8. Condensed graph trace bullets.
9. No-results fallback if applicable.

No other artifacts are written.

### Step 7 — Save flow

#### `--dry-run`
Print `[yellow]Dry run: skipping DB writes.[/yellow]` and exit 0. (Report from Step 6 still written if `--report`.)

#### Default (no `--yes`, no `--dry-run`)
Prompt: `Save N songset(s) to user <email> (y/N)?` via `typer.confirm(default=False)`.
- `N`/Ctrl-C → exit 0 cleanly.
- `y` → proceed to Step 8.

#### `--yes`
Skip prompt; proceed to Step 8.

### Step 8 — Persist songsets (`persist.py`)
**Atomic save per proposal.** For each `SongsetProposal` in `final_proposals`:

1. Build a list of `SongsetItem`-data dicts (in `position` order).
2. Call a new atomic helper on `SongsetClient` (or a raw SQL transaction):
   ```
   create_songset_with_items(
       name="Constructed rank {rank}/{proposals} ({count}-song)",
       description=first_200_chars(rationale) or fallback,
       items=[{song_id, recording_hash_prefix, position, gap_beats, ...}],
   )
   ```
3. If a `MissingReferenceError` occurs (stale recording), **rollback the entire songset** (do not leave an empty songset), print `[red]` message, and continue to the next proposal.
4. Wrap the whole loop in a Rich progress bar. Print `Created songset <id> (rank N)` on success.
5. Exit 1 at end if any proposal failed; exit 0 if all succeeded.

> **Note on atomicity:** `SongsetClient` currently commits `create_songset` and `add_item` in separate transactions. To avoid empty songsets on partial failure, either:
> - Add `SongsetClient.create_songset_with_items()` (recommended), or
> - Implement the save in `persist.py` using `connection_provider.get_connection().transaction()` directly.

## Refactors to POC Code

### `config.py`
- Remove `default_output_dir()` factory that uses `Path(__file__)`.
- Remove `load_runtime_env()` and `DEFAULT_ENV_FILE` (`/opt/sow/.env`).
- Remove `env_file` field from `RunConfig`.
- Rename `no_llm` → `llm_enabled` (default `False`).
- `output_dir: Path | None = None`.

### `db.py`
- Remove `get_connection_url()` and `build_read_client()`.
- Remove `from sow_lab_app.config import AppConfig`.
- `fetch_catalog_pool(config, *, client: ReadOnlyClient)` — `client` is **required**.

### `graph/checkpointer.py`
- Remove `SqliteSaver` path and `_stable_checkpoint_dir()`.
- Always return `InMemorySaver()` (non-interactive is the only supported mode).

## Tests

### New subpackage tests: `ops/admin-cli/tests/songset_construct/`
- `test_runner.py` — mock `build_graph` and `fetch_catalog_pool`; verify RunConfig maps correctly from Typer options; verify `--user` missing exits 2; verify `--no-llm --llm-judge` errors.
- `test_persist.py` — mock `SongsetClient`; verify each proposal becomes one atomic create-with-items call; verify `MissingReferenceError` rollback logic.
- `test_diagnose.py` — synthesize proposals + pool + trace; assert report sections.
- `test_relax_parser.py` — valid/invalid `--relax` strings and `--constraints-file` merging.

Run with:
```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra constructor --extra test pytest tests/songset_construct/ -v
```

## Documentation Updates

- `AGENTS.md` — remove `ops/songset-constructor` component. Note `constructor` extra under Admin CLI.
- `ops/admin-cli/README.md` — add `sow-admin songset construct` example with `--user`, `--count`, `--proposals`, `--llm`, `--report`.
- `docs/agent_guide_songset_constructor.md` — update Quick Start to use `sow-admin songset construct` as the production path; deprecate `construct_songset_agent.py`.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| `SongsetClient` does not support atomic create+items | Add `create_songset_with_items` or use raw SQL transaction in `persist.py`. |
| LangGraph debug streaming is slow / noisy | Acceptable for CLI; no file writes. Keep `stream_mode="debug"` for user feedback. |
| Stale recording_hash_prefix between construct and save | Validate all recordings inside the atomic transaction before inserting any rows. |
| `--user` not-yet-existing | Early `UserClient.get_user_by_email` check before running graph. |
| Heavy LLM deps under default install | Kept in `constructor` extra only; lazy-import guard in command. |
| `Path(__file__)` default output dir in POC | Removed; `output_dir` is `None` unless `--report-dir` is provided. |
| POC code drift between `lab/` and admin-cli subpackage | Lab code is frozen (not deleted). Subpackage is the production source of truth. |

## Acceptance

- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/` exists with all modules listed above.
- `uv sync --project ops/admin-cli --extra admin --extra constructor` succeeds and resolves without circular deps.
- `sow-admin songset construct --user me@x --count 3 --proposals 3 --dry-run` prints proposals, writes no DB rows, writes no files.
- Same with `--report` writes `./output/songset_constructor/<ts>/diagnose_report.md`, no other artifacts.
- Same with `--llm` and `--report` runs graph with LLM nodes and writes report.
- Same without `--dry-run` prompts `y/N`; selecting `N` leaves DB clean.
- Same with `--yes` persists N songsets atomically under `me@x`.
- Running without `constructor` extra fails with clear `RuntimeError` and install hint.
