# PROMPT: Create OpenCode SKILL for Songset Construction

| | |
|---|---|
| **Date** | 2026-07-31 |
| **Status** | Prompt — pending implementation |
| **Purpose** | Create an OpenCode SKILL that implements a songset construction POC using the OpenCode agent as the orchestration harness (replacing the LangGraph state machine). |
| **Skill location** | `lab/skills/songset-constructor/` (source-controlled), symlinked to `.agents/skills/songset-constructor/` |
| **Reference doc** | `docs/agent_guide_songset_constructor.md` |

---

## 1. Overview

The existing songset constructor uses a LangGraph state machine with 11 pipeline stages (load_catalog → enrich_pool → build_transition_matrix → beam_seed_candidates → llm_plan → validate_score → llm_refine → finalize_rank → llm_judge → interactive_review → write_artifacts). The LangGraph harness coordinates stage routing, state passing, and the LLM plan/refine/judge loop.

This SKILL replaces the LangGraph harness with the OpenCode agent itself. The agent LLM becomes the planner, validator, refiner, and judge — coordinating the entire workflow through reasoning. Tool scripts handle the data-heavy, deterministic steps (DB access, theme classification, transition computation, fitness scoring, constraint validation, artifact writing). The agent calls these scripts as needed and uses their structured JSON output to inform its planning decisions.

**Goal:** Compare effectiveness of an agentic LLM harness (OpenCode) vs a fixed state-machine harness (LangGraph) for the songset construction task.

**Operating mode:** Agentic only. No `--no-llm` / `--llm` toggle — the agent IS the LLM.

**Output:** A single `proposal_report.md` artifact (no `proposals.json` or `candidate_pool.csv`). The agent writes a summary to the user after completion.

**Persistence:** Artifacts only. No DB writes to `songsets` or `songset_items` tables.

---

## 2. SKILL Directory Structure

```
lab/skills/songset-constructor/
├── SKILL.md                     # Skill definition + agent instructions
└── scripts/
    ├── preflight.sh             # Pre-flight environment checks (Bash)
    ├── fetch_pool.py             # Catalog pool retrieval from PostgreSQL (Python)
    ├── enrich_pool.py            # Theme fusion + phase inference (Python)
    ├── build_transitions.py      # Pairwise transition matrix (Python)
    ├── score_songset.py          # Fitness scoring + H0-H8 validation (Python)
    ├── get_lyrics.py             # LRC lyrics retrieval from R2/DB (Python)
    ├── semantic_search.py        # pgvector semantic / keyword search (Python)
    └── write_report.py           # Final proposal_report.md generation (Python)
```

Symlink to activate:

```bash
mkdir -p .agents/skills
ln -s ../../lab/skills/songset-constructor .agents/skills/songset-constructor
```

---

## 3. SKILL.md Specification

### 3.1 Frontmatter

```yaml
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
```

### 3.2 Body Instructions

The SKILL.md body should contain the following instructions for the agent:

---

#### Workflow

You are a worship songset constructor agent. Your job is to plan, validate, and rank multi-song worship sets that follow a 5-phase worship arc with smooth transitions between songs. Follow this workflow:

**Step 1 — Pre-flight Checks**

Run `scripts/preflight.sh` to verify:
- Database connectivity (catalog must be reachable)
- `theme_anchors` table populated (must have exactly 12 rows)
- R2 credentials available (for LRC lyrics access)

If any check fails, report the issue to the user and stop.

**Step 2 — Fetch Catalog Pool**

Run `scripts/fetch_pool.py` with appropriate flags:
- `--pool-limit 500` for the full catalog (default)
- `--album-series "敬拜讚美 (1)"` to filter by album series (repeatable)
- `--no-cache` to bypass the pool cache

This returns a JSON array of raw SongCandidate objects (pre-enrichment). Each song has: `song_id`, `title`, `title_pinyin`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `lyrics_raw`, `song_theme_scores_raw`, `line_theme_scores_raw`, `recording_hash_prefix`, etc.

**Step 3 — Enrich Pool**

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

**Step 4 — Build Transition Matrix**

Pipe the enriched pool into `scripts/build_transitions.py`:
```bash
scripts/enrich_pool.py ... | scripts/build_transitions.py
```

This computes pairwise transition recommendations for all song pairs where circle-of-fifths distance (CFD) ≤ 6. Each transition includes: `cfd`, `bpm_delta`, `key_compat` (0-1), `suggested_key_shift` (semitones), `transition_technique` (pivot/direct/relative/transposition/vamp/direct_modulation), `crossfade_enabled`, `crossfade_duration_seconds`, `gap_beats`. Also computes `fan_out` (how many valid transitions each song has) and marks `is_dead_end` songs.

**Step 5 — Plan Songset(s)**

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
| H1 | One phase-1 opener, worship/response middle, phase 4/5 closer | relaxable |
| H2 | Opener tempo ≥ 90 BPM | 90 (relaxable) |
| H3 | Closer tempo ≤ 90 BPM (80 if intimate) | 90/80 (relaxable) |
| H4 | Adjacent BPM delta ≤ 35 (25 without crossfade/gap, 40 if relaxed) | 35 |
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

**Step 6 — Score and Validate**

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

**Step 7 — Refine (if needed)**

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

**Step 8 — Generate Multiple Proposals**

Repeat steps 5-7 to generate the requested number of proposals (default 3, configurable). Maximize diversity across proposals:
- Use different openers when possible (≥ 50% unique openers across proposals)
- Vary middle songs (≥ 3 unique per middle slot)
- Apply a diversity penalty: reduce score by 0.15 × (overlap_count / middle_count) for reused middle songs

**Step 9 — Rank Proposals**

Rank proposals by:
1. Total score (descending)
2. Composer diversity (descending)
3. Hash prefix sequence (lexicographic, for reproducibility)

**Step 10 — Write Report**

Run `scripts/write_report.py` with the final proposals, pool, transitions, and config:
```bash
echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...]}' | scripts/write_report.py --output-dir output/songset_constructor/<timestamp>/
```

This writes a single `proposal_report.md` containing:
- Run configuration
- Pool overview (phase distribution, theme coverage, tempo/key coverage)
- Per-proposal details: song sequence, phase arc, BPM/key journey, score breakdown, transition settings, warnings
- Diversity matrix: unique songs/themes/composers, song overlap matrix, song frequency table, theme coverage, bottlenecks

**Step 11 — Summary to User**

After writing the report, provide a concise summary to the user:
- Number of proposals generated
- Top proposal: song sequence, phase arc, total score
- Key findings: diversity assessment, any constraints that required relaxation, any concerns
- Path to the output report file

---

## 4. Tool Script Specifications

All Python scripts run via:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/<script>.py [OPTIONS]
```

They import from the existing `stream_of_worship.admin.songset_constructor` package and `stream_of_worship.db` modules. All scripts accept JSON via stdin or `--input` file path and output JSON to stdout (diagnostics to stderr).

### 4.1 `preflight.sh` (Bash)

**Purpose:** Verify environment readiness before starting construction.

**Checks:**
1. Database env var present (check for `SOW_DATABASE_URL` or equivalent DSN env var)
2. Database reachable (run a `SELECT 1` via a quick Python one-liner using `ConnectionProvider`)
3. `theme_anchors` table populated (query `SELECT COUNT(*) FROM theme_anchors` — must be 12)
4. R2 credentials present (`SOW_R2_ACCESS_KEY_ID` and `SOW_R2_SECRET_ACCESS_KEY` env vars set)
5. Pool cache status (report if cache file exists at `~/.cache/sow/songset_constructor/` and its age)

**Output:** Structured text to stdout with `OK` / `FAIL` per check:
```
[OK]   Database URL configured
[OK]   Database reachable
[FAIL] theme_anchors table: 0 rows (expected 12). Run: sow-admin theme-anchors sync
[OK]   R2 credentials configured
[INFO] Pool cache: 450 songs cached (age: 2h)
```

**Exit code:** 0 if all critical checks pass, 1 if any FAIL.

**Implementation notes:**
- Use `uv run --project ops/admin-cli --extra admin python -c "..."` for DB checks
- The DB DSN may come from `SOW_DATABASE_URL` or the admin CLI's own config loading — check how `ops/admin-cli/src/stream_of_worship/admin/main.py` resolves the database URL
- Reference: `ops/admin-cli/src/stream_of_worship/db/connection.py` for `ConnectionProvider`
- Reference: `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py:check_theme_anchors()` for the anchors count query

---

### 4.2 `fetch_pool.py` (Python)

**Purpose:** Retrieve the song catalog pool from PostgreSQL with in-DB pgvector theme scoring.

**Inputs (CLI args):**
- `--pool-limit` (int, default 500): Maximum songs to load
- `--album-series` (repeatable str): Filter by album series (e.g., `--album-series "敬拜讚美 (1)"`)
- `--use-cache` / `--no-cache` (bool, default cache): Toggle pool cache
- `--cache-dir` (path, default `~/.cache/sow/songset_constructor/`): Cache directory

**Output (stdout JSON):** Array of raw SongCandidate objects (pre-enrichment). Each object has:
```json
{
  "song_id": "abc123",
  "title": "讚美主",
  "title_pinyin": "zan mei zhu",
  "composer": "...",
  "lyricist": "...",
  "album_name": "...",
  "album_series": "PW",
  "recording_hash_prefix": "a1b2c3d4e5f6",
  "tempo_bpm": 120.0,
  "musical_key": "C",
  "musical_mode": "maj",
  "key_confidence": 0.85,
  "loudness_db": -12.5,
  "lyrics_raw": "...",
  "song_theme_scores_raw": {"讚美": 0.92, "感恩": 0.31, ...},
  "line_theme_scores_raw": {"讚美": 0.88, ...},
  "is_hymn": false
}
```

**Implementation:**
- Wraps `fetch_catalog_pool(config, client=read_client)` from `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py`
- Uses `ReadOnlyClient` from `ops/admin-cli/src/stream_of_worship/db/app/read_client.py`
- Uses `ConnectionProvider` from `ops/admin-cli/src/stream_of_worship/db/connection.py`
- Pool cache uses `cache.try_load_pool(config)` / `cache.save_pool(config, pool)` from `songset_constructor/cache.py`
- Constructs a `RunConfig` with the given pool_limit, album_series, and cache settings
- The DB query (`POOL_QUERY` in `db.py`) joins `songs` JOIN `recordings` LEFT JOIN `song_embedding`, filtering for `visibility_status IN ('published', 'review')` and LRC availability
- Theme scores are computed in-DB via pgvector cosine distance: `1 - (se.embedding <=> ta.embedding)` against `theme_anchors`
- Line theme scores fetched via separate `LINE_THEME_QUERY` using `song_line_embedding` table

**Stderr:** Prints pool size, cache hit/miss, and cache file path.

---

### 4.3 `enrich_pool.py` (Python)

**Purpose:** Enrich raw pool with fused themes, inferred phase, and secondary phases. Drops songs missing both tempo and key metadata.

**Inputs:**
- JSON array of raw SongCandidate objects via stdin or `--input <file>`
- `--season` (str, optional): One of `advent`, `christmas`, `lent`, `easter`, `pentecost`

**Output (stdout JSON):** Array of enriched SongCandidate objects with these additional populated fields:
```json
{
  ...,
  "themes": {"讚美": 0.85, "感恩": 0.42, ...},  // fused theme scores
  "phase": 1,                                    // primary phase (1-5)
  "secondary_phases": [3],                        // additional phases
  "is_hymn": false
}
```

**Implementation:**
- For each candidate:
  1. Drop if `tempo_bpm is None AND musical_key is None`
  2. Classify title themes: `classify_title_themes(title, title_pinyin)` — keyword matching against `THEME_VOCAB`
  3. Classify lyrics themes: `classify_lyrics_themes(lyrics_raw)` — sliding 2-line window keyword matching
  4. Normalize embedding scores: `normalise_cosine_scores(song_theme_scores_raw)` and `normalise_cosine_scores(line_theme_scores_raw)` — min-max normalize to [0,1]
  5. Fuse themes: `fuse_themes(title, lyrics, song_emb, line_emb)` — weighted average. If title and lyrics top themes agree: title 0.45, lyrics 0.35, song_emb 0.15, line_emb 0.05. Otherwise: title 0.35, lyrics 0.25, song_emb 0.25, line_emb 0.15
  6. Apply seasonal bias: `apply_seasonal_bias(fused, season)` — boosts relevant themes for liturgical seasons
  7. Infer phase: `infer_phase(fused, tempo_bpm)` — dominant theme → phase via `THEME_TO_PHASE`. Special: 聖靈 with tempo < 70 → phase 4. Fallback: tempo-only (≥100→1, ≥90→2, ≥70→3, else→4)
  8. Infer secondary phases: `infer_secondary_phases(fused, primary_phase, tempo_bpm)` — themes within 85% of max score map to additional phases (max 2)

**Reference source files:**
- `songset_constructor/rules/themes.py` — `THEMES` (12 themes), `THEME_VOCAB`, `classify_title_themes`, `classify_lyrics_themes`, `normalise_cosine_scores`
- `songset_constructor/rules/phases.py` — `THEME_TO_PHASE`, `fuse_themes`, `apply_seasonal_bias`, `infer_phase`, `infer_secondary_phases`
- `songset_constructor/graph/nodes.py:enrich_pool()` — reference implementation

**12 Themes (Traditional Chinese):** `讚美`, `感恩`, `敬拜`, `奉獻`, `認罪`, `差遣`, `信心`, `祈禱`, `復興`, `聖靈`, `十字架`, `跟隨`

**Theme → Phase mapping:**
- Phase 1 (Call/Praise): 讚美
- Phase 2 (Thanksgiving): 感恩
- Phase 3 (Worship): 敬拜, 祈禱, 信心, 聖靈
- Phase 4 (Response): 奉獻, 認罪, 十字架
- Phase 5 (Commission): 差遣, 跟隨, 復興

**Seasonal bias:**
- advent/christmas: 讚美 ≥ 0.7, 感恩 ≥ 0.5
- lent: 認罪 ≥ 0.7, 十字架 ≥ 0.65
- easter: 復興 ≥ 0.65, 讚美 ≥ 0.65
- pentecost: 聖靈 ≥ 0.75

**Stderr (enrichment summary):**
```
Pool: 500 loaded → 487 enriched (13 dropped)
Phase distribution: P1=45, P2=38, P3=210, P4=92, P5=102
Theme inference: 460 from themes, 27 from tempo fallback
Title hits: 106/487, Lyrics hits: 372/487
Theme entropy: 3.21 bits (max 3.585)
```

---

### 4.4 `build_transitions.py` (Python)

**Purpose:** Compute pairwise transition recommendations for all valid song pairs (CFD ≤ 6) and compute fan-out/dead-end status.

**Input:** JSON array of enriched SongCandidate objects via stdin or `--input`.

**Output (stdout JSON):**
```json
{
  "transitions": [
    {
      "from_hash_prefix": "a1b2c3d4e5f6",
      "to_hash_prefix": "b2c3d4e5f6a1",
      "cfd": 1,
      "bpm_delta": 5.0,
      "key_compat": 0.92,
      "suggested_key_shift": 0,
      "transition_technique": "pivot",
      "crossfade_enabled": false,
      "crossfade_duration_seconds": 0.0,
      "gap_beats": 2.0,
      "warnings": []
    },
    ...
  ],
  "pool": [...  // updated SongCandidate objects with fan_out and is_dead_end populated]
}
```

**Implementation:**
- For each pair (left, right) where `left.recording_hash_prefix != right.recording_hash_prefix`:
  - Compute `recommend_transition(left, right)` from `rules/transitions.py`
  - Include in matrix if `transition.cfd <= 6`
- Transition technique selection (based on CFD):
  - CFD ≤ 1: `pivot` (gap 2 beats, no crossfade)
  - CFD ≤ 2: `relative` (different modes) or `direct` (same mode) (gap 2 beats, no crossfade)
  - shifted_distance ≤ 2 and shift ≠ 0: `transposition` (gap 4 beats, crossfade 4s)
  - CFD = 3: `vamp` (gap 4 beats, crossfade 6s)
  - else: `direct_modulation` (gap 6 beats, crossfade 8s)
- Key compatibility scores: CFD 0→1.0, 1→0.92, 2→0.78, 3→0.55, 4→0.32, 5+→0.15
- After building matrix, compute fan-out: `compute_fan_out(pool, matrix, config)` from `rules/beam.py`
  - `fan_out` = count of valid outbound transitions (BPM delta ≤ h4_limit AND CFD ≤ h5_limit OR key shift available)
  - `is_dead_end` = fan_out == 0

**Reference source files:**
- `songset_constructor/rules/transitions.py` — `recommend_transition()`
- `songset_constructor/rules/harmony.py` — `cfd()`, `key_compatibility_score()`, `suggest_key_shift()`, `normalize_key()`
- `songset_constructor/rules/beam.py` — `compute_fan_out()`
- `songset_constructor/graph/nodes.py:build_transition_matrix()` — reference implementation

**Harmony details:**
- `normalize_key(raw)` parses key strings like "C#", "Bb", "F#m" into (note, mode) tuples
- `cfd(from_key, from_mode, to_key, to_mode)` computes circle-of-fifths distance using relative major pitch classes
- `suggest_key_shift(from_key, from_mode, to_key, to_mode)` tries shifts of -2 to +2 semitones to find minimum CFD

**Stderr:** Transition count, fan-out distribution, dead-end count.

---

### 4.5 `score_songset.py` (Python)

**Purpose:** Score a proposed songset against fitness functions and validate hard constraints H0-H8.

**Input (stdin JSON):**
```json
{
  "items": [
    {"position": 1, "recording_hash_prefix": "a1b2c3d4e5f6", "key_shift_semitones": 0, "crossfade_enabled": false, "crossfade_duration_seconds": 0.0, "gap_beats": 2.0, "tempo_ratio": 1.0},
    {"position": 2, "recording_hash_prefix": "b2c3d4e5f6a1", ...},
    ...
  ],
  "pool": [...],           // enriched SongCandidate objects
  "transitions": [...],    // TransitionCandidate objects from build_transitions
  "config": {
    "count": 4,
    "intimate": false,
    "relax_h1": true,
    "relax_h4": false,
    "relax_h5": false,
    "relax_h2_bpm": null,
    "relax_h3_bpm": null,
    "relax_h4_bpm": null,
    "relax_h5_cfd": null,
    "season": null
  }
}
```

**Output (stdout JSON):**
```json
{
  "score": {
    "f_theme": 0.95,
    "f_tempo": 0.78,
    "f_harmony": 0.85,
    "f_diversity": 1.0,
    "total": 0.88
  },
  "validation": {
    "passed": true,
    "violated": [],
    "errors": [],
    "repair_hints": []
  },
  "proposal": {
    "rank": 0,
    "items": [...],  // ProposalItem objects with song_id, title, phase, bpm, key, themes, transition settings
    "score": {...},
    "rationale": "",
    "hard_constraint_warnings": [],
    "llm_origin": true
  }
}
```

**Implementation:**
- Reconstruct `SongCandidate` objects from pool JSON
- Reconstruct `TransitionCandidate` matrix from transitions JSON (keyed by `(from_hash, to_hash)`)
- Build `RunConfig` from config JSON
- Convert `items` to `SongsetDraft` → `proposal_from_draft()` → `SongsetProposal`
- Score: `score(proposal, config, matrix)` from `rules/fitness.py`
  - `f_theme` (0.40): phase match against template. Distance = sum of |item.phase - template_phase| for each position. Score = 1 - distance / (4 × template_length)
  - `f_tempo` (0.30): smoothness = 1 - min(1, sum(BPM deltas) / (25 × num_deltas)). Arc bonus = 1.0 if opener BPM ≥ closer BPM else 0.75. Score = 0.75 × smoothness + 0.25 × arc_bonus
  - `f_harmony` (0.20): average `key_compat` across adjacent transitions
  - `f_diversity` (0.10): 0.7 × (unique_songs / total) + 0.3 × min(1, unique_themes / max(2, total))
  - Total = 0.40 × theme + 0.30 × tempo + 0.20 × harmony + 0.10 × diversity, clamped [0, 1]
- Validate: `validate(proposal, config, matrix, relax_h1=..., relax_h4=..., relax_h5=...)` from `rules/hard_constraints.py`
  - Checks H0-H8 (see constraint table in SKILL.md body above)
  - Returns `ValidationFeedback` with `passed`, `violated` (H-codes), `errors` (messages), `repair_hints`

**Reference source files:**
- `songset_constructor/rules/fitness.py` — `score()`, `f_theme()`, `f_tempo()`, `f_harmony()`, `f_diversity()`
- `songset_constructor/rules/hard_constraints.py` — `validate()`, `RULE_DESCRIPTIONS`
- `songset_constructor/rules/proposals.py` — `proposal_from_draft()`, `item_from_candidate()`
- `songset_constructor/models.py` — `SongsetDraft`, `DraftItem`, `SongsetProposal`, `ScoreBreakdown`, `ValidationFeedback`

**Phase templates (for f_theme):**
- 2 songs: (1, 4)
- 3 songs: (1, 3, 5)
- 4 songs: (1, 3, 4, 5)
- 5 songs: (1, 2, 3, 4, 5)

---

### 4.6 `get_lyrics.py` (Python)

**Purpose:** Retrieve LRC lyrics for a specific recording, enabling the agent to analyze how a song starts and ends for transition planning.

**Inputs (CLI args):**
- `--hash-prefix` (str, 12-char hex): Recording hash prefix
- `--song-id` (str): Song ID (alternative lookup)
- `--source` (str, optional): `lrc` (R2, default) or `raw` (DB lyrics_raw) or `auto` (try LRC first, fall back to raw)

**Output (stdout):** LRC or raw lyrics text (UTF-8). If neither available, exit with error message to stderr.

**Implementation:**
- Primary: `R2Client.download_lrc_content(hash_prefix)` — downloads `{hash_prefix}/lyrics.lrc` from R2 bucket
  - Requires `SOW_R2_ACCESS_KEY_ID` and `SOW_R2_SECRET_ACCESS_KEY` env vars
  - R2Client at `ops/admin-cli/src/stream_of_worship/admin/services/r2.py`
  - R2 bucket/endpoint config from env vars or admin CLI config
- Fallback: `ReadOnlyClient.get_song(song_id).lyrics_raw` — raw lyrics from `songs.lyrics_raw` column
  - If `--song-id` not provided but `--hash-prefix` is, look up song via `ReadOnlyClient.get_recording_by_hash(hash_prefix)` → `song_id` → `get_song(song_id)`
- LRC format includes timestamps like `[00:15.20]歌詞內容` — the agent can parse these to understand song structure and timing

**Use case:** The agent calls this when planning transitions — e.g., if a song ends with a slow instrumental outro, the next song can start with a similar mood. If a song ends abruptly, a gap transition may be needed.

---

### 4.7 `semantic_search.py` (Python)

**Purpose:** Search for songs by theme, lyrics content, or natural language query using pgvector semantic similarity or keyword ILIKE search.

**Inputs (CLI args):**
- `--query` (str, required): Search text (e.g., "感恩", "cross", "holy spirit")
- `--limit` (int, default 20): Maximum results
- `--field` (str, optional): `title`, `lyrics`, `composer`, `all` (for keyword search). If omitted, uses semantic search.
- `--mode` (str, default `auto`): `semantic` (pgvector), `keyword` (ILIKE), `auto` (try semantic first, fall back to keyword)

**Output (stdout JSON):** Array of matching songs:
```json
[
  {
    "song_id": "abc123",
    "title": "感恩的心",
    "title_pinyin": "gan en de xin",
    "recording_hash_prefix": "a1b2c3d4e5f6",
    "tempo_bpm": 85.0,
    "musical_key": "G",
    "musical_mode": "maj",
    "album_name": "...",
    "album_series": "PW",
    "score": 0.92,
    "match_type": "semantic"
  },
  ...
]
```

**Implementation:**
- **Semantic search:** If embedding API is available (`SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`), generate embedding for the query text using the same model as the catalog (`text-embedding-3-small`, 1536-dim). Then query `song_embedding` table via pgvector cosine distance: `SELECT ... ORDER BY embedding <=> query_embedding LIMIT N`. If embedding API unavailable, match query against `THEME_VOCAB` keywords to identify relevant themes, then query songs with high theme scores.
- **Keyword search:** Delegate to `ReadOnlyClient.search_songs(query, field, limit)` — uses SQL `ILIKE` on title, title_pinyin, lyrics_raw, composer, or lyricist columns.
- **Auto mode:** Try semantic first. If no results or embedding API unavailable, fall back to keyword search.

**Reference:**
- `ReadOnlyClient.search_songs()` at `ops/admin-cli/src/stream_of_worship/db/app/read_client.py`
- pgvector cosine distance operator: `<=>` (used in `POOL_QUERY` in `db.py`)
- `song_embedding` and `song_line_embedding` tables with HNSW cosine indexes

---

### 4.8 `write_report.py` (Python)

**Purpose:** Write the final `proposal_report.md` artifact from structured proposal data and diagnostics.

**Input (stdin JSON):**
```json
{
  "proposals": [...],     // list of SongsetProposal objects (with items, scores, warnings)
  "pool": [...],          // enriched SongCandidate objects
  "config": {...},        // RunConfig as dict
  "transitions": [...],   // TransitionCandidate objects
  "summary": "..."        // optional agent-authored summary text
}
```

**CLI args:**
- `--output-dir` (path, default `output/songset_constructor/<timestamp>/`): Output directory

**Output:** Writes `proposal_report.md` to the output directory. Prints the file path to stdout.

**Report structure (modeled after existing `artifacts/writer.py`):**

```markdown
# Songset Proposals

## Run Summary
- Run ID: songset-20260731T120000Z-4s-top3
- Generated: 2026-07-31T12:00:00Z
- Requested song count: 4
- Top-k: 3
- Pool size: 487
- Flags: intimate=false, season=christmas, ...

## Pool Overview
- Total candidates: 487
- Phase distribution: P1=45, P2=38, P3=210, P4=92, P5=102
- Tempo coverage: 460 known BPM, 27 missing
- Theme entropy: 3.21 bits (max 3.585)

## Rank 1 - Score 0.8842

> **Brief Summary**
> Songs: 1. 讚美主  →  2. 感恩的心  →  3. 十字架  →  4. 差遣
> Arc: Phase 1 → 3 → 4 → 5 (call → worship → response → commitment)
> Journey: C maj → G maj → D min → A min  |  120 → 95 → 85 → 78 BPM arc
> Score: f_theme 0.950, f_tempo 0.780, f_harmony 0.850, f_diversity 1.000
> Rationale: [agent-authored or deterministic]

### Details

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 讚美主 | 1 | 120 | C maj | 讚美, 歌唱 | — |
| 2 | 感恩的心 | 3 | 95 | G maj | 感恩, 敬拜 | shift 0, gap 2 beats |
| 3 | 十字架 | 4 | 85 | D min | 十字架, 寶血 | shift 0, gap 2 beats |
| 4 | 差遣 | 5 | 78 | A min | 差遣, 跟隨 | shift 0, gap 2 beats |

Score: theme 0.950, tempo 0.780, harmony 0.850, diversity 1.000.
Warnings: none

## Rank 2 - Score 0.8201
...

## Diversity Summary

Across 3 proposals (12 song slots total):

| Metric | Value |
|---|---|
| Unique songs | 10 / 12 (83%) |
| Unique themes | 8 / 12 |
| Unique composers | 7 |
| Unique phases | 5 / 5 |
| Middle-song reuse | 1 (across 6 middle slots) |

### Song Overlap Matrix
...

### Song Frequency
...

### Theme Coverage
Present (8): 讚美, 感恩, 敬拜, 奉獻, 十字架, 差遣, 跟隨, 信心
Missing (4): 認罪, 祈禱, 復興, 聖靈

### Bottlenecks
- Most-reused song: "感恩的心" appears in 2/3 songsets.
- Phase gap: Phase 2 (thanksgiving) absent from all top-k songsets.

## Agent Summary
[agent-authored summary text if provided]
```

**Reference source files:**
- `songset_constructor/artifacts/writer.py` — `write_report()`, `write_artifacts()`, `_diversity_summary()`, `_song_overlap_matrix()`, `_song_frequency_table()`, `_theme_coverage_lines()`, `_bottleneck_lines()`, `brief_summary_block()`, `_deterministic_arc_narrative()`
- `songset_constructor/artifacts/enrichment_report.py` — pool overview metrics, Shannon entropy
- `songset_constructor/diagnose.py` — `assemble_report_sections()`

---

## 5. Existing Source Code Reference

All paths relative to project root. The implementer should read these files to understand the existing logic that the tool scripts wrap.

### Core package: `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/`

| File | Purpose |
|------|---------|
| `models.py` | Pydantic schemas: `SongCandidate`, `TransitionCandidate`, `DraftItem`, `SongsetDraft`, `ProposalItem`, `SongsetProposal`, `ScoreBreakdown`, `ValidationFeedback`, `JudgeRanking` |
| `config.py` | `RunConfig` dataclass with `count`, `proposals`, `pool`, `intimate`, `season`, `relax_h1`, `auto_relax`, `relax_h4`, `relax_h5`, `relax_h2_bpm`, `relax_h3_bpm`, `relax_h4_bpm`, `relax_h5_cfd`, `opening_floor` (default 90), `closing_limit` (90 or 80 intimate), `h4_limit` (35 or 40 relaxed), `h5_limit` (2 or 3 relaxed) |
| `db.py` | `fetch_catalog_pool()`, `fetch_line_theme_scores()`, `check_theme_anchors()`, `POOL_QUERY`, `LINE_THEME_QUERY` |
| `cache.py` | `try_load_pool()`, `save_pool()`, `cache_path()` — JSON cache at `~/.cache/sow/songset_constructor/pool_{hash}.json` |
| `rules/themes.py` | `THEMES` (12 themes), `THEME_VOCAB`, `classify_title_themes()`, `classify_lyrics_themes()`, `normalise_cosine_scores()` |
| `rules/phases.py` | `THEME_TO_PHASE`, `fuse_themes()`, `apply_seasonal_bias()`, `infer_phase()`, `infer_secondary_phases()`, `top_themes()` |
| `rules/harmony.py` | `NOTE_TO_PC`, `FIFTH_ORDER`, `normalize_key()`, `pitch_class()`, `transpose_note()`, `relative_major_pc()`, `fifth_distance_on_circle()`, `cfd()`, `key_compatibility_score()`, `suggest_key_shift()` |
| `rules/transitions.py` | `recommend_transition()` — technique selection based on CFD |
| `rules/fitness.py` | `TEMPLATE_PHASES_2/3/4/5`, `f_theme()`, `f_tempo()`, `f_harmony()`, `f_diversity()`, `score()`, `score_with_diversity_penalty()`, `middle_song_ids()` |
| `rules/hard_constraints.py` | `RULE_DESCRIPTIONS`, `validate()` — H0-H8 validation |
| `rules/beam.py` | `_TEMPLATES`, `compute_fan_out()`, `search()` — diverse beam search with round-robin selection, auto-relaxation fallback |
| `rules/proposals.py` | `item_from_candidate()`, `draft_from_candidates()`, `proposal_from_draft()`, `proposal_hash_sequence()`, `composer_diversity()`, `rank_proposals()` — greedy diverse selection with middle-song penalty |
| `rules/diagnostics.py` | `enrichment_drop_diagnostics()`, `role_eligibility_counts()`, `beam_diagnostics()`, `diagnostic_lines()` |
| `graph/nodes.py` | LangGraph node implementations: `enrich_pool()`, `build_transition_matrix()`, `beam_seed_candidates()`, `llm_plan()`, `validate_score()`, `llm_refine()`, `finalize_rank_node()`, `llm_judge()`, `optional_review()` |
| `graph/state.py` | `ConstructorState` TypedDict |
| `graph/builder.py` | `build_graph()` — LangGraph state machine definition |
| `runner.py` | `run(config, read_client)` — entry point that loads pool, builds graph, invokes |
| `artifacts/writer.py` | `write_artifacts()`, `write_report()`, `write_pool_csv()`, `build_review_report()`, `generate_brief_summaries()`, `_diversity_metrics()`, `_song_overlap_matrix()`, `_song_frequency_table()`, `_theme_coverage_lines()`, `_bottleneck_lines()`, `_diversity_summary()`, `brief_summary_block()` |
| `artifacts/enrichment_report.py` | `build_enrichment_report()` — pool distribution metrics, Shannon entropy, phase/theme coverage |
| `artifacts/trace.py` | `event()` — graph trace event helper |
| `diagnose.py` | `assemble_report_sections()` — diagnostic report sections |
| `report_writer.py` | `write_report()` — diagnose report writer |
| `data/theme_anchors.json` | 12 × 1536-dim theme anchor vectors (text-embedding-3-small) |

### Shared DB infrastructure: `ops/admin-cli/src/stream_of_worship/db/`

| File | Purpose |
|------|---------|
| `connection.py` | `ConnectionProvider` — manages psycopg connection with auto-reconnect. `check_database_connection()` helper. |
| `app/read_client.py` | `ReadOnlyClient` — `get_song()`, `search_songs()`, `get_recording_by_hash()`, `get_recording_by_song_id()`, `list_songs()`, `list_albums()`, `list_keys()`, batch methods |

### R2 storage: `ops/admin-cli/src/stream_of_worship/admin/services/r2.py`

| Method | Purpose |
|--------|---------|
| `R2Client.download_lrc_content(hash_prefix)` | Download `{hash_prefix}/lyrics.lrc` from R2 as UTF-8 string |
| `R2Client.__init__(bucket, endpoint_url, region)` | Requires `SOW_R2_ACCESS_KEY_ID` and `SOW_R2_SECRET_ACCESS_KEY` env vars |

### Admin CLI command: `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py`

The existing `sow-admin songset construct` CLI command. The tool scripts reuse the same underlying functions but bypass the LangGraph harness.

### pyproject.toml: `ops/admin-cli/pyproject.toml`

The `constructor` extra includes: `langgraph`, `langchain-core`, `langchain-openai`, `numpy`, `pydantic`, `python-dotenv`, `rapidfuzz`. The `admin` extra includes: `psycopg[binary]`, `typer`, `rich`, `boto3`, `numpy`, etc.

Run scripts with: `uv run --project ops/admin-cli --extra admin --extra constructor python <script.py>`

---

## 6. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `SOW_DATABASE_URL` | Yes | PostgreSQL DSN for catalog access |
| `SOW_R2_ACCESS_KEY_ID` | Yes (for lyrics) | Cloudflare R2 access key |
| `SOW_R2_SECRET_ACCESS_KEY` | Yes (for lyrics) | Cloudflare R2 secret key |
| `SOW_R2_BUCKET` | Yes (for lyrics) | R2 bucket name |
| `SOW_R2_ENDPOINT_URL` | Yes (for lyrics) | R2 endpoint URL |
| `SOW_EMBEDDING_API_KEY` | Optional | For semantic search query embedding generation |
| `SOW_EMBEDDING_BASE_URL` | Optional | Embedding API base URL |
| `SOW_LLM_API_KEY` | Not needed | The OpenCode agent IS the LLM — no separate LLM API key needed |
| `SOW_LLM_MODEL` | Not needed | Same as above |
| `SOW_LLM_BASE_URL` | Not needed | Same as above |

Note: The DB DSN env var name may differ — check how `ops/admin-cli/src/stream_of_worship/admin/main.py` resolves the database URL. It may use `DATABASE_URL` or a config file. The implementer should verify the exact env var name.

---

## 7. Symlink Instructions

```bash
# From project root
mkdir -p .agents/skills
ln -s ../../lab/skills/songset-constructor .agents/skills/songset-constructor

# Verify
ls -la .agents/skills/songset-constructor/SKILL.md
```

OpenCode discovers skills from `.agents/skills/<name>/SKILL.md` (project-local, walked up from CWD to git root). The symlink ensures the skill source is version-controlled in `lab/skills/` while being discoverable by OpenCode.

---

## 8. Verification Checklist

- [ ] `SKILL.md` has valid frontmatter (`name: songset-constructor`, `description` ≤ 1024 chars)
- [ ] `name` matches directory name (`songset-constructor`)
- [ ] `preflight.sh` correctly identifies missing env vars, DB issues, and unpopulated `theme_anchors`
- [ ] `fetch_pool.py` returns valid JSON array of SongCandidate objects from the catalog
- [ ] `fetch_pool.py` cache works (second run with `--use-cache` loads from cache, reports age)
- [ ] `enrich_pool.py` correctly assigns phases based on fused theme scores
- [ ] `enrich_pool.py` drops songs missing both tempo and key
- [ ] `enrich_pool.py` seasonal bias works (e.g., `--season christmas` boosts 讚美/感恩)
- [ ] `build_transitions.py` returns transitions with correct CFD, BPM delta, and technique
- [ ] `build_transitions.py` computes fan_out and marks dead-end songs
- [ ] `score_songset.py` returns correct ScoreBreakdown (f_theme, f_tempo, f_harmony, f_diversity, total)
- [ ] `score_songset.py` flags H2 violation when opener tempo < 90 BPM
- [ ] `score_songset.py` flags H3 violation when closer tempo > 90 BPM
- [ ] `score_songset.py` flags H6 violation for duplicate songs
- [ ] `score_songset.py` flags H8 violation when low-confidence key song is transposed
- [ ] `score_songset.py` returns repair_hints that are actionable
- [ ] `get_lyrics.py` returns LRC content from R2 when available
- [ ] `get_lyrics.py` falls back to raw lyrics from DB when R2 fails
- [ ] `semantic_search.py` returns relevant songs for theme queries
- [ ] `semantic_search.py` keyword search works for title/lyrics/composer
- [ ] `write_report.py` generates formatted `proposal_report.md` with all sections
- [ ] `write_report.py` includes diversity matrix (overlap, frequency, theme coverage)
- [ ] `write_report.py` includes per-proposal score breakdowns and transition settings
- [ ] Symlink `.agents/skills/songset-constructor` → `lab/skills/songset-constructor` works
- [ ] OpenCode discovers the skill (appears in available skills list)
- [ ] End-to-end: agent can construct a 4-song worship set using the skill
