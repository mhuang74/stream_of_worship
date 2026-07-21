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
                                            │            finalize_rank ←────────┘
                                            │                    ↓
                                            │         ┌──────────┴──────────┐
                                            │    --llm-judge         default
                                            │         │                   │
                                            │    llm_judge                │
                                            │         │                   │
                                            └─────────┴───────────────────┘
                                                      ↓
                                              write_artifacts
```

### Stage Details

1. **load_catalog** — Fetches songs from PostgreSQL via read-only `SELECT`. Loads songs with published/review recordings that have LRC lyrics. Pool size is bounded by `--pool-limit`.

2. **enrich_pool** — Classifies each song's themes (from title, lyrics, embeddings), infers worship phase (1=call, 2=adoration, 3=praise, 4=cross/response, 5=commitment), and applies seasonal bias. Drops songs lacking both tempo and key metadata.

3. **build_transition_matrix** — Computes pairwise transition recommendations (BPM delta, circle-of-fifths distance, suggested key shift, crossfade/gap settings) for all song pairs where CFD ≤ 6. Also computes fan-out (how many valid transitions each song has) and marks dead-end songs.

4. **beam_seed_candidates** — Runs diverse beam search (see below). Produces ranked candidate sequences following the phase template.

5. **llm_plan** (LLM mode only) — LLM drafts a songset from the pool using structured output. Hallucinated hash prefixes are repaired via fuzzy matching.

6. **validate_score** — Validates the LLM draft against hard constraints H0–H8. If it fails, routes to `llm_refine` (up to 3 iterations).

7. **finalize_rank** — Deduplicates proposals by song sequence, then applies greedy diverse selection with a middle-song diversity penalty (see below).

8. **llm_judge** (optional) — LLM re-ranks finalists and adds judge reasons/scores without changing deterministic order.

9. **write_artifacts** — Writes 5 output files (see below).

## How Diverse Beam Search Works

The beam search in `rules/beam.py` uses a **two-level round-robin diverse selection** to maximize song variety across proposals:

### Phase Templates

| Songs | Template | Arc |
|-------|----------|-----|
| 2 | (1, 4) | Call → Response |
| 3 | (1, 3, 5) | Call → Praise → Commitment |
| 4 | (1, 3, 4, 5) | Call → Praise → Cross → Commitment |
| 5 | (1, 2, 3, 4, 5) | Full worship arc |

### Beam Expansion

At each position in the template, the beam expands all valid candidates. Validity is checked against:
- **Phase match**: opener must be phase 1/2, closer must be phase 4/5
- **Tempo floor/ceiling**: opener ≥ 90 BPM (configurable), closer ≤ 90 BPM (80 intimate)
- **H4 tempo jump**: adjacent BPM delta ≤ 35 (25 without crossfade, 40 if relaxed)
- **H5 circle-of-fifths**: CFD ≤ 2 (3 if relaxed) unless key shift is applied
- **H7 phase arc**: phase may drop by at most 1 between adjacent songs
- **Dead-end filtering**: non-closer positions skip songs with zero fan-out

### Diverse Selection (Round-Robin)

At each phase after the opener, sequences are grouped by:
1. **Opener** (first song) — ensures different openers survive
2. **Middle-song signature** (positions 1..-1) — ensures different middle combinations survive

Within each opener group, middle-song groups are ranked by quality (phase score + tempo delta). A round-robin selection alternates between openers, and within each opener alternates between middle signatures, so no single opener or middle combination dominates the beam.

At position 1 (opener), ALL valid openers are kept (up to beam width) to maximize starting-song diversity.

### Beam Width

Beam width is scaled to `max(top_k * 5, 40)`. For `--top-k 20`, the beam width is 100, allowing many diverse sequences to survive pruning.

## How Diversity Penalty Works

The `rank_proposals` function in `rules/proposals.py` uses a **greedy diverse selection with middle-song penalty**:

1. Deduplicate proposals by song sequence hash
2. Sort by score (descending)
3. Greedily select proposals one at a time:
   - For each candidate, compute `score_with_diversity_penalty(proposal, config, matrix, used_middle_songs)`
   - The penalty reduces total score by `0.15 * (overlap_count / middle_count)` where overlap is the number of middle songs already used in higher-ranked proposals
   - Pick the proposal with the highest penalized score
   - Add its middle songs to the `used_middle_songs` set
4. Repeat until `top_k` proposals are selected

This spreads middle-slot variety across the final top-k, preventing all proposals from reusing the same 2–3 middle songs.

## Hard Constraints (H0–H8)

| Rule | Description | Relaxable |
|------|-------------|-----------|
| H0 | Cardinality: proposal must have exactly the requested song count | No |
| H1 | Phase coverage: one phase-1 opener, at least one phase 3/4, ends on phase 4/5 | Yes (`--relax-h1`) |
| H2 | Opening tempo ≥ 90 BPM | Yes (`--relax-h2-bpm`) |
| H3 | Closing tempo ≤ 90 BPM (80 intimate) | Yes (`--relax-h3-bpm`) |
| H4 | Adjacent BPM delta ≤ 35 (25 without crossfade, 40 if relaxed) | Yes (`--relax-h4`) |
| H5 | Circle-of-fifths distance ≤ 2 (3 if relaxed) unless key shift applied | Yes (`--relax-h5`) |
| H6 | No duplicate song IDs | No |
| H7 | Phase may drop by at most 1 between adjacent songs | No |
| H8 | Songs with key confidence < 0.6 cannot be transposed | No |

When `--auto-relax` is enabled (default), the search automatically relaxes H4/H5, then H2/H3, then H1 if no proposals are found. Relaxed proposals carry warning labels (e.g., `relaxed_H4_H5`).

## Fitness Scoring

Each proposal is scored on four components:

| Component | Weight | What It Measures |
|-----------|-------:|-----------------|
| `f_theme` | 0.40 | How well song phases match the template arc |
| `f_tempo` | 0.30 | Tempo smoothness (low BPM delta between adjacent songs) + arc bonus (opener BPM ≥ closer BPM) |
| `f_harmony` | 0.20 | Average key compatibility across adjacent transitions |
| `f_diversity` | 0.10 | Unique songs (0.7 weight) + unique themes (0.3 weight) within the set |

Total score = `0.40 * theme + 0.30 * tempo + 0.20 * harmony + 0.10 * diversity`, clamped to [0, 1].

## Output Artifacts

Each run writes 5 files to the output directory (default: `lab/poc-scripts/output/songset_constructor/<timestamp>/`):

| File | Description |
|------|-------------|
| `proposals.json` | Machine-readable proposals with full metadata (songs, scores, transitions, config) |
| `proposal_report.md` | Human-readable markdown table of all ranked proposals |
| `candidate_pool.csv` | Full enriched pool with phase, BPM, key, themes per song |
| `graph_trace.jsonl` | LangGraph execution trace (one JSON object per node event) |
| `songset_review.md` | Auto-generated review summary with key findings, run config, and per-proposal details |

## How to Evaluate Results

### 1. Check Proposal Count

The run log prints `candidates=N` after `beam_seed_candidates` and `proposals=N` after `finalize_rank`. If `proposals=0`, check the no-results summary printed by the CLI — it explains which stage blocked output.

### 2. Read the Proposal Report

Open `proposal_report.md`. For each proposal, check:

- **Phase arc**: Does the phase sequence follow the template (e.g., 1→3→4→5 for 4 songs)?
- **BPM arc**: Does the tempo generally decrease from opener to closer? Large jumps indicate weak transitions.
- **Key compatibility**: Are adjacent keys close on the circle of fifths? Large key shifts (e.g., C major to F# major) reduce harmony score.
- **Transition settings**: `shift 0, gap 2 beats` means a simple gap transition. `shift -2, gap 4 beats` means a 2-semitone transpose with a longer gap. Crossfade transitions allow larger BPM deltas.
- **Warnings**: `relaxed_H4_H5` means the strict constraints were too tight and had to be relaxed. This is acceptable but indicates the catalog lacks perfectly compatible transitions.

### 3. Assess Diversity

Count unique songs per slot across all proposals:

```bash
uv run --project lab/poc-scripts --extra songset_constructor python -c "
import json
from pathlib import Path

# Update path to your run's output directory
data = json.loads(Path('lab/poc-scripts/output/songset_constructor/<TIMESTAMP>/proposals.json').read_text())
proposals = data['proposals']

for slot in range(len(proposals[0]['items'])):
    songs = {p['items'][slot]['title'] for p in proposals}
    print(f'Slot {slot + 1}: {len(songs)} unique songs')
"
```

**Healthy diversity indicators:**
- Openers: ≥ 50% of top_k should be unique (e.g., ≥ 10 unique openers for top_k=20)
- Middle slots: ≥ 3 unique songs per slot
- Closers: ≥ 2 unique songs

**Limited diversity indicators:**
- Slot 2 (first middle) often has only 2–3 unique songs because H4/H5 transition constraints limit compatible phase-3 songs per BPM group. This is a catalog constraint, not an algorithm bug.
- If all proposals share the same opener, the beam search is converging. Increase `--top-k` or try different `--album-series` filters.

### 4. Check Score Distribution

Read the `Score:` line under each proposal. Typical ranges:

| Component | Good | Acceptable | Concern |
|-----------|------|------------|---------|
| theme | ≥ 0.90 | ≥ 0.80 | < 0.80 (phase mismatch) |
| tempo | ≥ 0.70 | ≥ 0.65 | < 0.60 (large BPM jumps) |
| harmony | ≥ 0.70 | ≥ 0.50 | < 0.40 (key incompatibility) |
| diversity | 1.00 | 1.00 | < 1.00 (duplicate songs) |
| **total** | **≥ 0.80** | **≥ 0.70** | **< 0.65** |

### 5. Review the Songset Review

Open `songset_review.md` for an auto-generated summary including:
- Phase flow distribution in the pool (how many songs per phase)
- Tempo coverage (known vs missing BPM values)
- Relaxation/constraint warnings
- Per-proposal score breakdowns

### 6. Compare Runs

To compare diversity across different configurations, run multiple times and compare the unique-song counts per slot. Useful comparisons:

- `--no-llm` vs `--llm-judge`: LLM mode adds LLM-drafted proposals with different song selections
- `--intimate` vs default: Intimate mode selects slower closers (≤ 80 BPM)
- `--season advent` vs default: Seasonal bias adjusts theme weights
- Different `--album-series` filters: Narrows the pool to specific albums

## Recipes

### Generate 20 diverse 4-song sets from the full catalog

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm
```

### Generate 10 LLM-judged 5-song sets

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 5 --pool-limit 500 --top-k 10 --llm-judge
```

### Generate intimate worship sets (slow closers)

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm --intimate
```

### Generate Christmas-season sets

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm --season christmas
```

### Generate sets from a specific album series

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm \
  --album-series "敬拜讚美 (1)" --album-series "敬拜讚美 (2)"
```

### Debug: strict-only mode (no auto-relax)

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 20 --no-llm --no-auto-relax
```

If this produces 0 proposals, the catalog lacks songs that satisfy all strict H1–H5 constraints simultaneously. Re-enable `--auto-relax` or manually relax specific constraints (e.g., `--relax-h4 --relax-h5`).

### Interactive review (human-in-the-loop)

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --songs 4 --pool-limit 500 --top-k 5 --no-llm --interactive-review
```

The CLI pauses after ranking and prompts `Review action (approve/reject)`. Use `--resume-thread-id` to resume an interrupted interactive session.

## Troubleshooting

### No proposals generated

1. Check the CLI output for the no-results summary — it explains which stage blocked output.
2. Run with `--no-auto-relax` to see if strict constraints are too tight.
3. Check pool size: if `pool_size=0`, the database query returned nothing. Verify the catalog has published/review recordings with LRC lyrics.
4. Check phase distribution in `songset_review.md`: if phase 1 or phase 4/5 count is 0, no valid openers or closers exist.

### All proposals share the same opener

This means the beam search is converging. The diverse beam search should prevent this, but if the catalog has very few valid openers (phase 1/2 with BPM ≥ 90), diversity is naturally limited. Check the phase distribution in `songset_review.md`.

### All proposals share the same middle songs

This is expected when the H4/H5 transition constraints limit compatible phase-3 songs per BPM group. The diversity penalty in `rank_proposals` spreads variety as much as possible, but it cannot create transitions that don't exist in the catalog. To increase middle-song diversity:
- Relax H4: `--relax-h4` (widens BPM delta from 35 to 40)
- Relax H5: `--relax-h5` (widens CFD from 2 to 3)
- Use a larger pool: `--pool-limit 500`

### LLM mode produces fewer proposals than expected

In LLM mode, `validate_score` replaces `beam_candidates` with the LLM draft (via `operator.add` append). The final proposal count = beam proposals + 1 LLM draft (if validation passes). If the LLM draft fails validation after 3 refinement iterations, only beam proposals survive.

### Harmony scores are low

Low harmony scores (< 0.50) indicate key incompatibility between adjacent songs. Check the `Key` column in the proposal report — large key jumps (e.g., C major to B major) reduce harmony. The transition matrix may suggest a key shift (`shift -2` etc.) to improve compatibility, but songs with low key confidence (< 0.6) cannot be transposed (H8 constraint).

## Key Source Files

| File | Purpose |
|------|---------|
| `lab/poc-scripts/construct_songset_agent.py` | CLI entrypoint |
| `poc/songset_constructor/cli.py` | Typer CLI with all options |
| `poc/songset_constructor/config.py` | RunConfig dataclass, tempo/CFD limits |
| `poc/songset_constructor/graph/builder.py` | LangGraph state machine definition |
| `poc/songset_constructor/graph/nodes.py` | Graph node implementations |
| `poc/songset_constructor/rules/beam.py` | Diverse beam search with round-robin selection |
| `poc/songset_constructor/rules/fitness.py` | Scoring functions + diversity penalty |
| `poc/songset_constructor/rules/proposals.py` | Proposal ranking with greedy diverse selection |
| `poc/songset_constructor/rules/hard_constraints.py` | H0–H8 validation |
| `poc/songset_constructor/rules/transitions.py` | Pairwise transition recommendation |
| `poc/songset_constructor/rules/phases.py` | Theme classification and phase inference |
| `poc/songset_constructor/db.py` | Read-only catalog pool query |
| `poc/songset_constructor/artifacts/writer.py` | Output file generation |

## Read-Only Guarantee

The POC uses `ReadOnlyClient` and only issues bounded `SELECT` queries. It does not import `SongsetClient`, does not write `songsets` or `songset_items`, and does not run schema migrations.
