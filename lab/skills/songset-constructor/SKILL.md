---
name: songset-constructor
description: >-
  Agentic worship songset constructor. Plans multi-song worship sets following a
  5-phase arc (Call → Thanksgiving → Worship → Response → Commission), selecting
  songs from the catalog pool with smooth tempo and key transitions. Use when the
  user asks to construct, generate, or create worship songsets, song sequences, or
  worship set lists.
license: MIT
compatibility: opencode
metadata:
  domain: worship-music
  mode: agentic
  output: proposal_report.md
---

# Songset Constructor

## Overview

You are a worship songset constructor agent. Your job is to plan, validate, and rank multi-song worship sets that follow a 5-phase worship arc with smooth transitions between songs. You are the LLM planner, validator, refiner, and judge — coordinating the entire workflow through reasoning. Tool scripts handle the data-heavy, deterministic steps (DB access, theme classification, transition computation, fitness scoring, constraint validation, artifact writing). You call these scripts as needed and use their structured JSON output to inform your planning decisions.

**Operating mode:** Agentic only. You ARE the LLM — no separate LLM API key needed.

**Output:** A single `proposal_report.md` artifact. No DB writes.

## Script Invocation

All Python scripts run via:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/<script>.py [OPTIONS]
```

All scripts accept JSON via stdin or `--input <file>` and output JSON to stdout (diagnostics to stderr).

## Workflow

### Step 1 — Pre-flight Checks

Run `scripts/preflight.sh` to verify:
- Database connectivity (catalog must be reachable)
- `theme_anchors` table populated (must have exactly 12 rows)
- R2 credentials available (for LRC lyrics access)

If any check fails, report the issue to the user and stop.

### Step 2 — Fetch Catalog Pool

Run `scripts/fetch_pool.py` with appropriate flags:
- `--pool-limit 500` for the full catalog (default)
- `--album-series "敬拜讚美 (1)"` to filter by album series (repeatable)
- `--no-cache` to bypass the pool cache

This returns a JSON array of raw SongCandidate objects (pre-enrichment). Each song has: `song_id`, `title`, `title_pinyin`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `lyrics_raw`, `song_theme_scores_raw`, `line_theme_scores_raw`, `recording_hash_prefix`, etc.

### Step 3 — Enrich Pool

Pipe the raw pool JSON into `scripts/enrich_pool.py`:
```bash
scripts/fetch_pool.py ... | scripts/enrich_pool.py --season christmas
```

This applies theme fusion (title 35% + lyrics 25% + song embedding 25% + line embedding 15%), seasonal bias, and phase inference. Songs missing both tempo and key are dropped. The enriched pool has `themes` (dict[str,float]), `phase` (1-5), `secondary_phases` (list[int]), and `is_hymn` populated.

Review the enrichment summary printed to stderr:
- **Phase distribution**: Are there enough phase-1 openers and phase 4/5 closers?
- **Theme coverage**: Are any themes severely underrepresented?
- **Dropped count**: How many songs were dropped for missing metadata?

If the pool has 0 valid openers (phase 1/2 with BPM ≥ 90) or 0 valid closers (phase 4/5 with BPM ≤ 90), report this to the user and stop.

### Step 4 — Build Transition Matrix

Pipe the enriched pool into `scripts/build_transitions.py`:
```bash
scripts/enrich_pool.py ... | scripts/build_transitions.py
```

This computes pairwise transition recommendations for all song pairs where circle-of-fifths distance (CFD) ≤ 6. Each transition includes: `cfd`, `bpm_delta`, `key_compat` (0-1), `suggested_key_shift` (semitones), `transition_technique` (pivot/direct/relative/transposition/vamp/direct_modulation), `crossfade_enabled`, `crossfade_duration_seconds`, `gap_beats`. Also computes `fan_out` (how many valid transitions each song has) and marks `is_dead_end` songs.

### Step 5 — Plan Songset(s)

You are the LLM planner. Using the enriched pool and transition matrix, plan a songset that follows the 5-phase worship arc template:

| Songs | Template | Arc |
|-------|----------|-----|
| 2 | (1, 4) | Call → Response |
| 3 | (1, 3, 5) | Call → Worship → Commitment |
| 4 | (1, 3, 4, 5) | Call → Worship → Cross → Commitment |
| 5 | (1, 2, 3, 4, 5) | Full worship arc |

**Hard Constraints (H0-H8) — all must pass:**

| Code | Rule | Default |
|------|------|---------|
| H0 | Correct song count (must match requested count) | — |
| H1 | One phase-1 opener, worship/response middle, phase 4/5 closer | relaxable (opt-in via --relax-h1) |
| H2 | Opener tempo ≥ 90 BPM | 90 (relaxable) |
| H3 | Closer tempo ≤ 90 BPM (80 if intimate) | 90/80 (relaxable) |
| H4 | Adjacent BPM delta ≤ 35 (25 without crossfade or gap; 40 if relaxed) | 35 |
| H5 | Circle-of-fifths distance ≤ 2 (3 if relaxed) unless key shift applied | 2 |
| H6 | No duplicate song IDs | — |
| H7 | Phase drops by at most 1 between adjacent songs | — |
| H8 | Songs with key confidence < 0.6 cannot be transposed (key_shift must be 0) | 0.6 |

**Planning guidelines:**
- Select an opener: phase 1 (or 2), tempo ≥ 90 BPM, not a dead-end song
- Select middle songs: phase matches template position, BPM delta ≤ 35 from previous, CFD ≤ 2 (or apply key shift if CFD > 2 and key confidence ≥ 0.6)
- Select a closer: phase 4 or 5, tempo ≤ 90 BPM (80 if intimate)
- Ensure phase doesn't drop by more than 1 between adjacent songs (H7)
- Maximize theme diversity across the set
- Consider tempo arc: opener should be faster than closer

**Optional tools during planning:**
- Use `scripts/get_lyrics.py --hash-prefix <hash>` to inspect how a song starts and ends — this helps you plan smoother transitions (e.g., if a song ends quietly, the next can start softly)
- Use `scripts/semantic_search.py --query "感恩" --limit 20` to find songs matching a specific theme you need to fill a template slot

**When to use optional tools:**
- If pool diversity for a given template slot is low (fewer than 3 candidates), run `semantic_search.py` with a theme query to find more candidates.
- If a transition has CFD > 2, run `get_lyrics.py` on both songs to inspect lyrical endings/startings for natural transition points.

### Step 6 — Score and Validate

Submit your draft songset to `scripts/score_songset.py`:
```bash
echo '{"items": [...], "pool": [...], "transitions": [...], "config": {"count": 4, "intimate": false, ...}}' | scripts/score_songset.py
```

The script returns:
- `score`: ScoreBreakdown with `f_theme` (0.40 weight), `f_tempo` (0.30), `f_harmony` (0.20), `f_diversity` (0.10), and `total` (0-1)
- `validation`: ValidationFeedback with `passed` (bool), `violated` (list of H-codes), `errors` (list of messages), `repair_hints` (list of suggestions)
- `proposal`: Full SongsetProposal with items, scores, and transition settings

**Score interpretation:**

| Component | Good | Acceptable | Concern |
|-----------|------|------------|---------|
| f_theme | ≥ 0.90 | ≥ 0.80 | < 0.80 (phase mismatch) |
| f_tempo | ≥ 0.70 | ≥ 0.65 | < 0.60 (large BPM jumps) |
| f_harmony | ≥ 0.70 | ≥ 0.50 | < 0.40 (key incompatibility) |
| f_diversity | 1.00 | 1.00 | < 1.00 (duplicate songs) |
| **total** | **≥ 0.80** | **≥ 0.70** | **< 0.65** |

### Step 7 — Refine (if needed)

If validation fails or total score < 0.70:
- Read the `repair_hints` from the validation feedback
- Swap songs, adjust key shifts, or reorder to fix violations
- Re-score with `scripts/score_songset.py`
- Repeat up to 3 iterations

If constraints are too strict (no valid proposals after 3 iterations), consider relaxation:
- Relax H4 (BPM delta 35 → 40)
- Relax H5 (CFD 2 → 3)
- Relax H2 (opener BPM floor 90 → 80)
- Relax H3 (closer BPM ceiling 90 → 100)
- Relax H1 (drop strict phase-1 opener requirement)

Relaxed proposals should carry warning labels (e.g., "relaxed_H4_H5").

### Step 8 — Generate Multiple Proposals

Repeat steps 5-7 to generate the requested number of proposals (default 3, configurable). Maximize diversity across proposals:
- Use different openers when possible (≥ 50% unique openers across proposals)
- Vary middle songs (≥ 3 unique per middle slot)
- Apply a diversity penalty: reduce score by 0.15 × (overlap_count / middle_count) for reused middle songs

### Step 9 — Rank Proposals

Rank proposals by:
1. Total score (descending)
2. Composer diversity (descending)
3. Hash prefix sequence (lexicographic, for reproducibility)

### Step 9b — Generate Agent Summary

After ranking, write a 3-5 sentence executive summary covering:
- Number of proposals generated
- Top proposal score and song sequence
- Key findings: diversity assessment, any constraints that required relaxation, any concerns

Pass this as the `summary` field in the JSON payload to `write_report.py`.

### Step 10 — Write Report

Run `scripts/write_report.py` with the final proposals, pool, transitions, and config:
```bash
echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...]}' | scripts/write_report.py --output-dir output/songset_constructor/<timestamp>/
```

This writes a single `proposal_report.md` containing:
- Run configuration
- Pool overview (phase distribution, theme coverage, tempo/key coverage)
- Per-proposal details: song sequence, phase arc, BPM/key journey, score breakdown, transition settings, warnings
- Diversity matrix: unique songs/themes/composers, song overlap matrix, song frequency table, theme coverage, bottlenecks

### Step 11 — Summary to User

After writing the report, provide a concise summary to the user:
- Number of proposals generated
- Top proposal: song sequence, phase arc, total score
- Key findings: diversity assessment, any constraints that required relaxation, any concerns
- Path to the output report file
