# Songset Construct v3 Implementation Summary

**Spec:** `specs/songset_construct_command_v3.md`
**PR:** [#136](https://github.com/mhuang74/stream_of_worship/pull/136)
**Branch:** `bulk_songset_creation_0725`
**Date:** 2026-07-28

## What Changed From v2

| v2 (POC) | v3 (Production) |
|---|---|
| `POOL_QUERY` fetches `se.embedding::text` (~16.5 KB/row) | `POOL_QUERY` computes cosine similarity in SQL via `theme_anchors` table; returns 12 scalar scores (~200 bytes/row as JSON) |
| `LINE_EMBEDDING_QUERY` fetches all line `embedding::text` (~64.5 MB/run) | `LINE_THEME_QUERY` computes `MAX(cosine)` per theme per song in SQL; returns 12 scores/song as JSON (~200 bytes/song) |
| Pool query uses `SONG_COLUMNS_FOR_JOIN` (24 cols) + `RECORDING_COLUMNS_FOR_JOIN` (34 cols) | Constructor-specific column projection: 9 song + 6 recording columns (only fields the constructor uses) |
| No pool caching | Local cache layer in `cache.py`; subsequent runs with same `(pool_limit, album_series)` skip DB entirely |
| `SongCandidate` carries `song_embedding: list[float]` + `line_embeddings: list[list[float]]` | `SongCandidate` carries `song_theme_scores_raw: dict[str,float]` + `line_theme_scores_raw: dict[str,float]` (pre-normalization cosine scores from SQL) |
| `enrich_pool` calls `classify_embedding_themes` (numpy cosine in Python) | `enrich_pool` calls `normalise_cosine_scores` directly on pre-computed SQL scores; `classify_embedding_themes` and `load_theme_anchors` no longer called |
| CLI: `[-p, --pool]` only | CLI adds `--no-cache`, `--cache-dir`, `--cache-ttl` |
| No `theme_anchors` table | New `theme_anchors` table in schema + `sow-admin theme-anchors sync` command |
| Per-run transfer: ~68.8 MB | Per-run transfer: ~340 KB (cache miss) / ~0 KB (cache hit) |

## Bandwidth Projections

| Scenario | v2 (per run) | v3 (cache miss) | v3 (cache hit) |
|---|---:|---:|---:|
| Pool query (200 songs) | 4.3 MB | ~300 KB | 0 KB |
| Line embedding query (200 songs × 20 lines) | 64.5 MB | ~42 KB | 0 KB |
| **Total per run** | **68.8 MB** | **~342 KB** | **0 KB** |
| 80 runs | 5.4 GB | 27 MB | 27 MB (first run only) |
| 100 runs | 6.7 GB | 34 MB | 34 MB (first run only) |

## New Files Created (44 files, ~4300 lines)

### Subpackage: `songset_constructor/`

```
ops/admin-cli/src/stream_of_worship/admin/songset_constructor/
├── __init__.py
├── config.py              # RunConfig with use_cache, llm_enabled, relaxed fields
├── models.py              # SongCandidate (in-DB scores), TransitionCandidate, SongsetProposal, etc.
├── db.py                  # POOL_QUERY + LINE_THEME_QUERY with pgvector <=>
├── cache.py               # SHA-256 keyed pool cache (24h TTL)
├── runner.py              # Graph entry point (cache → fetch → build → stream)
├── persist.py             # Atomic create_songset_with_items
├── diagnose.py            # assemble_report_sections for diagnose_report.md
├── report_writer.py       # Write diagnose_report.md
├── data/
│   └── theme_anchors.json # 12 × 1536-dim anchor vectors
├── graph/
│   ├── __init__.py
│   ├── builder.py         # StateGraph definition (10 nodes)
│   ├── state.py           # ConstructorState TypedDict
│   ├── nodes.py           # Graph node implementations (v3 — no load_theme_anchors)
│   ├── checkpointer.py    # Always InMemorySaver
│   └── llm.py             # ChatOpenAI builder with structured output
├── rules/
│   ├── __init__.py
│   ├── beam.py            # Deterministic beam search with fallback tiers
│   ├── diagnostics.py     # Rule-drop diagnostics and role eligibility
│   ├── embeddings.py      # load_theme_anchors only (parse_pgvector_text removed)
│   ├── fitness.py         # Scoring: f_theme, f_tempo, f_harmony, f_diversity
│   ├── hard_constraints.py # H0-H8 validation
│   ├── harmony.py         # Key distance (CFD), pitch class, transposition
│   ├── phases.py          # Theme fusion, seasonal bias, phase inference
│   ├── proposals.py       # Draft/Proposal converters, rank_proposals with diversity
│   ├── themes.py          # Title/lyrics classifiers, normalise_cosine_scores (public)
│   └── transitions.py     # Transition recommendations (pivot/vamp/modulation)
└── artifacts/
    ├── __init__.py
    ├── writer.py           # Markdown reports, CSV, JSON artifacts
    ├── enrichment_report.py  # Pool enrichment distribution report
    └── trace.py            # Trace event helpers
```

### Command Files

- `ops/admin-cli/src/stream_of_worship/admin/commands/theme_anchors.py` — `sow-admin theme-anchors sync`
- `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` — extended with `construct` subcommand (~180 lines)

### Modified Files

- `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` — added `CREATE_THEME_ANCHORS_TABLE` to `ALL_SCHEMA_STATEMENTS`
- `ops/admin-cli/src/stream_of_worship/admin/main.py` — registered `theme-anchors` Typer app
- `ops/admin-cli/pyproject.toml` — added `constructor` extra
- `AGENTS.md` — updated with constructor commands

### Infrastructure

- `delivery/webapp/drizzle/0018_theme_anchors.sql` — Drizzle migration

### Tests (38 new, all pass)

- `test_cache.py` — hit/miss/TTL/invalidation (7 tests)
- `test_db_queries.py` — SQL shape, `_candidate_from_row` parsing, cosine normalization (7 tests)
- `test_diagnose.py` — report sections (2 tests)
- `test_relax_parser.py` — relax token parsing (9 tests)
- `test_runner.py` — RunConfig validation (10 tests)
- `test_theme_anchors_sync.py` — anchor loading/structure (3 tests)

## CLI Surface

### `sow-admin songset construct`

```
--user <email>                [required]
--count, -n                   2-5 (default 3)
--proposals, -k               1-20 (default 3)
--pool, -p                    >=4 (default 200)
--album-series                repeatable filter
--include-cpw / --no-include-cpw   (default False)
--intimate / --no-intimate         (default False)
--hymnal-mode / --no-hymnal-mode   (default False)
--season                      advent|christmas|lent|easter|pentecost
--llm / --no-llm              (default --no-llm)
--llm-judge / --no-llm-judge  (default --no-llm-judge)
--llm-model NAME
--relax                       h2:90,h3:80,h4,h5:3
--constraints-file PATH       YAML/JSON override
--report                      write diagnose_report.md
--report-dir PATH             default ./output/songset_constructor/<ts>/
--dry-run                     skip DB writes
--yes                         auto-save without prompting
--no-cache                    bypass pool cache
--cache-dir PATH              default ~/.cache/sow/songset_constructor/
--cache-ttl HOURS             default 24
-c, --config PATH             AdminConfig path
```

### `sow-admin theme-anchors sync`

```
--force                       re-insert even if 12 rows exist
-c, --config PATH             AdminConfig path
```

## Key Implementation Decisions

1. **In-DB scoring**: Song-level scores via `json_object_agg(theme, 1 - (se.embedding <=> ta.embedding))` from `song_embedding`; line-level via `MAX(1 - (sle.embedding <=> ta.embedding))` grouped by `(song_id, theme)`.
2. **No raw vectors fetched**: The `::text` cast pattern is permanently eliminated. No `parse_pgvector_text` or Python-side cosine.
3. **Pool cache is pre-enrichment**: Enrichment depends on `config.season` (seasonal bias), so caching the raw pool allows re-enrichment with different seasons.
4. **`llm_enabled` defaults False**: Constructor runs deterministically by default. LLM deps are in a separate `constructor` extra.
5. **Always `InMemorySaver`**: No Sqlite-based checkpointing for interactive sessions (removed from v3).
6. **Theme anchor validation**: Startup check ensures `theme_anchors` has 12 rows before proceeding.
7. **Lazy import guard**: Clear error message if `constructor` extra is not installed.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `theme_anchors` not populated | Startup validation; clear error message with sync command |
| pgvector `CROSS JOIN` performance | 12-row table → trivial; HNSW index on `embedding` column |
| NULL embedding → NULL scores | `_candidate_from_row` handles NULL → empty dict |
| Stale pool cache | 24h TTL; `--no-cache` bypass |
| POC code drift | Lab code frozen; subpackage is production source of truth |
| Floating-point diff between pgvector vs numpy | <1e-6; min-max normalization robust to tiny perturbations |
