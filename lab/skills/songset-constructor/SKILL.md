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

> **CRITICAL:** Bare `python` is NOT on PATH in this environment. All scripts
> MUST be run via `uv run`. Omitting `uv run` will silently produce empty
> output files (the shell redirect creates the file before `python` fails,
> leaving a 0-byte file that breaks downstream JSON parsing).

All Python scripts run via:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/<script>.py [OPTIONS]
```

**Extra groups required:**
- `--extra admin` — psycopg3, typer, rich, boto3, miniaudio, numpy (DB access, R2, config)
- `--extra constructor` — langgraph, pydantic, rapidfuzz, numpy (theme fusion, beam search, scoring)

Both are required for all skill scripts. The `preflight.sh` script already uses
`uv run` internally — invoke it with `bash lab/skills/songset-constructor/scripts/preflight.sh`
(no `uv run` prefix needed for the shell script itself).

**Troubleshooting empty output files:** If a downstream script fails with
`json.JSONDecodeError` on a file that should contain JSON, check that the
upstream command was invoked with `uv run` and that the file is not 0 bytes.

All scripts accept JSON via stdin or `--input <file>` and output JSON to stdout
(diagnostics to stderr).

## Workflow

### Step 1 — Pre-flight Checks

Run `scripts/preflight.sh` to verify:
- Database connectivity (catalog must be reachable)
- `theme_anchors` table populated (must have exactly 12 rows)
- R2 credentials available (for LRC lyrics access)

DB-unreachable is a WARN (not a hard fail) when a valid `pool_*.json` cache exists. The agent proceeds from cache and reports this to the user. If absolutely no cache exists and DB is unreachable, the run cannot proceed.

### Step 2 — Fetch Catalog Pool

**Discovering available album series:**
Before using `--album-series`, find valid values via the Admin CLI:
```bash
uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series
```
This prints a table of album names, album series, and song counts. Use the
exact `album_series` string (e.g., `敬拜讚美 (1)`, `HYMN`, `CPW`) as the
`--album-series` argument. The `--albums` flag shows aggregated counts;
omit it to list individual songs.

Run `scripts/fetch_pool.py` with appropriate flags:
- `--pool-limit 500` for the full catalog (default)
- `--album-series "敬拜讚美 (1)"` to filter by album series (repeatable)
- `--no-cache` to bypass the pool cache
- `--prefer-fresh` to attempt DB first and only fall back to cache on DB error

This returns a JSON array of raw SongCandidate objects (pre-enrichment). Each song has: `song_id`, `title`, `title_pinyin`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `lyrics_raw`, `song_theme_scores_raw`, `line_theme_scores_raw`, `recording_hash_prefix`, etc.

`fetch_pool.py` serves cached pool by default, even when stale, when DB read fails. Use `--no-cache` to force a fresh DB query, `--prefer-fresh` to attempt DB first and only fall back to cache on DB error. If the preflight DB check WARN-flagged cache availability, accept the stale cache and note the staleness in the run summary.

### Step 3 — Enrich Pool

Pipe the raw pool JSON into `scripts/enrich_pool.py`:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/fetch_pool.py \
    --pool-limit 500 --prefer-fresh \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py --season christmas
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
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py ... \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/build_transitions.py
```

**Output shape:** `build_transitions.py` outputs a JSON **object** (not a bare array):
```json
{
  "transitions": [...],
  "pool": [...]
}
```
The `pool` array is the enriched pool with `fan_out` and `is_dead_end` fields
updated. The `transitions` array contains TransitionCandidate objects keyed
by `(from_hash_prefix, to_hash_prefix)`.

**Important:** This wrapper object cannot be piped directly into
`score_songset.py`. See Step 6 for bridging instructions.

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
| H1 | One phase-1 *primary* opener (primary only, not secondary_phases), middle worship/response, phase 4/5 closer (primary or secondary) | relaxable (opt-in via --relax-h1) |
| H2 | Opener tempo ≥ 90 BPM | 90 (relaxable) |
| H3 | Closer tempo ≤ 90 BPM (80 if intimate) | 90/80 (relaxable) |
| H4 | Adjacent BPM delta ≤ 45 (40 without crossfade; 55 if relaxed) — gap_beats > 0 (any gap) triggers crossfade-tier cap | 45/40 |
| H5 | Circle-of-fifths distance ≤ 3 (4 if relaxed) unless key shift applied | 3 |
| H6 | No duplicate song IDs | — |
| H7 | Phase drops by at most 1 between adjacent songs | — |
| H8 | Songs with key confidence < 0.6 cannot be transposed (key_shift must be 0) | 0.6 |

**Planning guidelines:**
- Select an opener: phase 1 (or 2), tempo ≥ 90 BPM, not a dead-end song
- Select middle songs: phase matches template position, BPM delta ≤ 45 from previous (40 without crossfade), CFD ≤ 3 (or apply key shift if CFD > 3 and key confidence ≥ 0.6)
- Select a closer: phase 4 or 5, tempo ≤ 90 BPM (80 if intimate)
- Ensure phase doesn't drop by more than 1 between adjacent songs (H7)
- **Hard cap: `count ≤ 5`** (enforced by `SONGSET_MAX_SONGS`; exceeding this fails at `songset create` time, not earlier). Never draft a proposal with more than 5 songs.
- Maximize theme diversity across the set
- Consider tempo arc: opener should be faster than closer

**Optional tools during planning:**
- Use `scripts/get_lyrics.py --hash-prefix <hash>` to inspect how a song starts and ends — this helps you plan smoother transitions (e.g., if a song ends quietly, the next can start softly)
- Use `scripts/semantic_search.py --query "感恩" --limit 20` to find songs matching a specific theme you need to fill a template slot
  - Add `--album-series "敬拜讚美 (1)"` (repeatable) to restrict search to specific album series, mirroring `fetch_pool.py`'s filter

**When to use optional tools:**
- If pool diversity for a given template slot is low (fewer than 3 candidates), run `semantic_search.py` with a theme query to find more candidates.
- If a transition has CFD > 2, run `get_lyrics.py` on both songs to inspect lyrical endings/startings for natural transition points.

### Step 6 — Score and Validate

**Bridging from `build_transitions.py` output to `score_songset.py` input:**

`build_transitions.py` outputs `{"transitions": [...], "pool": [...]}`.
`score_songset.py` expects `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`.

You must merge the transitions+pool from build_transitions with your draft
items and config. Example using a quoted heredoc (preferred over multi-line
`python -c "..."` strings because the quoted sentinel `<<'EOF'` suppresses
shell interpolation):

```bash
# Save build_transitions output to a file
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py ... \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/build_transitions.py \
    > /tmp/transitions_pool.json

# Build the score_songset input by merging with your draft items.
uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
import json

tp = json.load(open('/tmp/transitions_pool.json'))
payload = {
    'items': [
        {'position': 1, 'recording_hash_prefix': 'a1b2c3d4e5f6', 'key_shift_semitones': 0},
        # ... more draft items in position order ...
    ],
    'pool': tp['pool'],
    'transitions': tp['transitions'],
    'config': {'count': 4, 'intimate': False, 'relax_h1': True},
}
with open('/tmp/score_input.json', 'w') as f:
    json.dump(payload, f, ensure_ascii=False)
EOF

# Score the draft
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/score_songset.py \
    --input /tmp/score_input.json
```

Submit your draft songset to `scripts/score_songset.py`:
```bash
echo '{"items": [...], "pool": [...], "transitions": [...], "config": {"count": 4, "intimate": false}}' \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/score_songset.py
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
- Relax H4 (BPM delta 45 → 55)
- Relax H5 (CFD 3 → 4)
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
echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...], "summary": "..."}' \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/write_report.py \
        --output-dir output/songset_constructor/<timestamp>/
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
- If preflight WARN-flagged DB unreachability, report the DB↔cache source used (cached-stale-fresh indicator) in the summary

### Step 12 — Persist Songset to DB (Optional)

If the user wants to persist the top-ranked proposal as a songset in the
database, use the Admin CLI `songset create` command.

**Song ID format:** The `song_id` field in SongCandidate objects follows the
format `{slug}_{8-char-hex}` (e.g., `wo_de_ye_su_4c27d159`). This is the
correct token to pass to `songset create`. Do NOT pass
`recording_hash_prefix` (a 12-char hex like `a1b2c3d4e5f6`) — it is not a
valid song ID.

> **Note:** The `songset create` docstring example previously used `song_0123`
> as a placeholder. Real song IDs are slug-based (e.g., `wo_de_ye_su_4c27d159`),
> not numeric `song_XXXX` IDs.

**Defensive trim:** Step 5 already constrains `count ≤ 5` at plan time. As a
belt-and-suspenders check, verify the top proposal has ≤ 5 items before
calling `songset create`; if it does not, keep items by `position` ascending
and truncate to 5.

**Extract song_ids from the top proposal:**
The top proposal's `items` list contains `song_id` fields in position order.
Extract them and pass to `songset create`:

```bash
# Set the user email (or pass --user each time)
export SOW_DEFAULT_USER=alice@example.com

# Extract song_ids from the top proposal (assumes /tmp/top_proposal.json).
# Quoted heredoc suppresses shell interpolation inside the Python source.
SONG_IDS=$(uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
import json
p = json.load(open('/tmp/top_proposal.json'))
print(' '.join(item['song_id'] for item in sorted(p['items'], key=lambda x: x['position'])))
EOF
)

# Defensive trim: keep first 5 if oversize slipped through
SONG_IDS=$(echo "$SONG_IDS" | awk '{for(i=1;i<=5 && i<=NF;i++) printf "%s%s", $i, (i<5 && i<NF ? OFS : ORS)}')

# Dry-run first to validate resolution
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    $SONG_IDS --dry-run --yes

# Persist for real
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    $SONG_IDS --name "Sunday_Worship_Set_1" --yes
```

**Flags:**
- `--user <email>` / `-u` — Owner email (or set `SOW_DEFAULT_USER` env var)
- `--name <name>` / `-n` — Custom songset name (auto-generated from titles if omitted)
- `--yes` / `-y` — Skip confirmation prompt (required for non-interactive agent runs)
- `--dry-run` — Resolve + validate but skip DB writes (recommended first)
- `--description <text>` / `-d` — Optional description

**Constraints enforced by songset create:**
- Max 5 songs (`SONGSET_MAX_SONGS`)
- Max 25 minutes total recording duration (`SONGSET_MAX_DURATION_SECONDS=1500`)
- Latest active recording selected automatically (latest-active-wins rule)

**Avoiding ambiguous title matches:** If you pass a title instead of a song_id
and multiple songs match, the command errors in `--yes` mode. Always use the
`song_id` field from SongCandidate for deterministic resolution.
