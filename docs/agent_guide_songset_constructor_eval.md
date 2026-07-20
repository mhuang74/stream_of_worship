# Agent Guide: Songset Constructor — Generating and Evaluating Diverse Songsets

This guide explains how to use the songset constructor POC to generate diverse Chinese worship songsets and evaluate the quality of the results.

## Quick Start

```bash
# Deterministic mode (no LLM required)
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm

# Agentic mode (LLM planning + optional judge)
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --llm-judge
```

## Prerequisites

- Environment variables `SOW_LLM_API_KEY`, `SOW_LLM_MODEL`, `SOW_LLM_BASE_URL` must be set for agentic mode (`--llm-judge`). The CLI auto-loads `/opt/sow/.env` or accepts `--env-file`.
- `--no-llm` mode requires no LLM credentials and runs fully deterministic.
- The catalog database must be reachable (read-only `SELECT` queries via `ReadOnlyClient`).

## CLI Options

| Option | Default | Range | Purpose |
|--------|---------|-------|---------|
| `--songs` | 3 | 2–5 | Songs per songset |
| `--top-k` | 3 | 1–20 | Number of ranked proposals to output |
| `--pool-limit` | 200 | ≥4 | Max songs to load from catalog (use 500 for full catalog) |
| `--no-llm` / `--llm` | `--llm` | — | Toggle deterministic vs agentic mode |
| `--llm-judge` / `--no-llm-judge` | `--no-llm-judge` | — | Enable LLM re-ranking of finalists |
| `--intimate` / `--no-intimate` | `--no-intimate` | — | Lower closer tempo ceiling from 90 to 80 BPM |
| `--season` | None | advent, christmas, lent, easter, pentecost | Seasonal theme bias |
| `--album-series` | None | repeatable | Filter catalog by album series (e.g., `--album-series "敬拜讚美 (1)"`) |
| `--relax-h1` / `--no-relax-h1` | `--relax-h1` | — | Relax phase-1 opener requirement (allow phase 2 openers) |
| `--auto-relax` / `--no-auto-relax` | `--auto-relax` | — | Auto-relax H2/H3/H4/H5 if no proposals found |
| `--relax-h3-bpm` | None | ≥0 | Override closer tempo ceiling |
| `--relax-h2-bpm` | None | ≥0 | Override opener tempo floor |
| `--relax-h4` / `--no-relax-h4` | `--no-relax-h4` | — | Widen tempo jump limit from 35 to 40 BPM |
| `--relax-h5` / `--no-relax-h5` | `--no-relax-h5` | — | Widen circle-of-fifths distance from 2 to 3 |
| `--interactive-review` / `--no-interactive-review` | `--no-interactive-review` | — | Pause for human approve/reject of top proposal |
| `--output-dir` | auto-timestamped | path | Override output directory |

## Pipeline Architecture

The constructor runs as a LangGraph state machine with these stages:

```
load_catalog → enrich_pool → build_transition_matrix → beam_seed_candidates
                                                          ↓
                                            ┌─────────────┴──────────────┐
                                            │                            │
                                      --no-llm                      LLM mode
                                            │                            │
                                    finalize_rank              llm_plan → validate_score
                                            │                    ↓               ↓
                                            │              Accepted         Refine (loop ≤3)
                                            │                    ↓               ↓
