# Agent Guide: Songset Constructor — Generating and Evaluating Diverse Songsets

The canonical path for songset construction is the **songset-constructor skill** at
`lab/skills/songset-constructor/` (symlinked at `.agents/skills/songset-constructor`).
Its `SKILL.md` is the operational 12-step workflow; the calling agent IS the LLM
planner — agentic only, no `SOW_LLM_API_KEY` needed for the skill path. This guide is
the conceptual + evaluation companion: it documents the component-metadata model
introduced by `specs/songset-constructor-component-metadata-integration-v2.md` and
ADR-0006 (`docs/adr/0006-component-theme-primary-for-phase-inference.md`), and how to
evaluate results under the current constraint and scoring semantics.

## Paths at a glance

| Path | Status | Component metadata? |
|------|--------|---------------------|
| Skill scripts (`lab/skills/songset-constructor/scripts/`) | **Canonical** | Yes — boundary keys/BPM, component themes, posture, energy |
| `sow-admin songset construct` (LangGraph) | Deprecated | No — song-level only |
| `lab/poc-scripts/construct_songset_agent.py` | Deprecated (reference only) | No |

Why is the LangGraph path song-level only? The shared rules package
(`ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/`) gained
component awareness, but the graph's loader/nodes never populate component fields, so
every song behaves component-less (four-way weights, song-level H2/H3/H8). This
divergence is intentional per spec v2 ("skill-scripts-only scope").

## Quick Start (skill path)

**Prerequisite (one-time):** populate the `theme_anchors` table:

```bash
uv run --project ops/admin-cli --extra admin sow-admin theme-anchors sync
```

This seeds 12 anchor rows from
`ops/admin-cli/src/stream_of_worship/admin/songset_constructor/data/theme_anchors.json`.

**Pre-flight:**

```bash
bash lab/skills/songset-constructor/scripts/preflight.sh
```

Checks: DB URL (`SOW_DATABASE_URL` or `~/.config/stream-of-worship-admin/config.toml`),
DB reachable + `theme_anchors` = 12 rows, R2 credentials, pool cache. DB-unreachable is
a WARN (not FAIL) when a valid pool cache exists — proceed from cache and note the
staleness in the run summary. No cache + no DB = the run cannot proceed.

> **CRITICAL invocation note:** bare `python` is NOT on PATH. Every script MUST run
> via:
>
> ```bash
> uv run --project ops/admin-cli --extra admin --extra constructor python \
>   lab/skills/songset-constructor/scripts/<script>.py [OPTIONS]
> ```
>
> Omitting `uv run` silently leaves a 0-byte output file (the shell redirect creates
> the file before `python` fails), which breaks downstream parsing with
> `json.JSONDecodeError`.

**Example pool-prep chain** (deterministic baseline; planning/scoring is the agent's
job — see SKILL.md Steps 5–9):

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/fetch_pool.py --pool-limit 500 --prefer-fresh \
| uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/enrich_pool.py --season christmas \
| uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/build_transitions.py \
> /tmp/transitions_pool.json
```

For the full operational workflow (Steps 0–12: leader-range interview, planning,
scoring, refinement, ranking, report, summary, optional persistence), read
`lab/skills/songset-constructor/SKILL.md` first — it is the canonical procedure.

## Skill script reference

| Script | Purpose | Flags |
|--------|---------|-------|
| `preflight.sh` | Env checks (DB, theme_anchors=12, R2, cache) | run via `bash .../preflight.sh` — no `uv run` prefix on the script itself |
| `fetch_pool.py` | Fetch raw catalog pool (cached by default) | `--pool-limit` (int, default 500), `--album-series` (repeatable), `--use-cache`/`--no-cache`, `--prefer-fresh` (DB first, cache only on DB error), `--allow-stale` (default true), `--cache-dir` (default `~/.cache/sow/songset_constructor`) |
| `enrich_pool.py` | Themes → phase, seasonal bias, energy percentiles, leader range | `--input`, `--season {advent,christmas,lent,easter,pentecost}`, `--leader-range '{"comfortable_pcs": [...], "label": "..."}'` |
| `build_transitions.py` | Pairwise transition matrix + fan-out/dead-end | `--input`; emits wrapper `{"transitions": [...], "pool": [...]}` — NOT directly pipeable into `score_songset.py`; merge per SKILL.md Step 6 |
| `score_songset.py` | Score + validate a draft | `--input`; stdin `{"items", "pool", "transitions", "config"}`; `config` keys are filtered against `RunConfig` fields (unknown keys silently dropped) |
| `write_report.py` | Write `proposal_report.md` | `--output-dir` (default `output/songset_constructor/<timestamp>/`); stdin `{"proposals", "pool", "config", "transitions", "summary"}` |
| `semantic_search.py` | Semantic/keyword song search | `--query` (required), `--limit` (default 20), `--field title\|lyrics\|composer\|all`, `--mode semantic\|keyword\|auto`, `--album-series` (repeatable) |
| `get_lyrics.py` | Fetch LRC/raw lyrics for one recording | `--hash-prefix` (12-char hex) or `--song-id`; `--source lrc\|raw\|auto` (default auto) |
| `voice_ranges.py` | Voice type → comfortable tonic PCs | library/helper: `normal male` → PCs 0–5 (A2–E4); free-form parser (`"A2 to G4"`) |

Discover valid album-series values with:

```bash
uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series
```

## Component metadata

This branch's core change: per-section analysis data in `song_components` now flows
into the pool and drives phase inference, transitions, and scoring.

### What it is

Per-section analysis rows in `song_components` carry BPM, key, confidence, energy dB,
a 12-value theme distribution, and vocal posture. About 70% of the pool has components
(312/444, measured 2026-09-11 per spec v2): `has_components = true`. Component-less
songs keep their song-level values and are **never dropped**
(`has_components = false`).

### Boundary fields on each enriched song

`entry_bpm` / `entry_key` / `entry_key_confidence` / `entry_energy_level_db` and the
`exit_*` equivalents — the opener-boundary (role `entry`) and closer-boundary (role
`exit`) values of the song's recording. Selection is deterministic: lowest
`occurrence_index`, then lowest `id`. `entry_mode`/`exit_mode` are copied from the
recording-level `musical_mode` because component rows store bare note names only.

### Component theme distribution

`component_theme_scores` is a dense 12-key dict. Chorus rows vote at weight 1.0,
non-chorus rows at 0.5, and each vote is scaled by its `theme_confidence`; the result
is normalized to sum 1.0.

### Phase inference is component-primary (ADR-0006)

When `component_theme_scores` is non-empty, phase comes from that distribution and
`theme_source = "component"`. The 4-source text/embedding fusion (see below) is the
**fallback** for component-less songs (`theme_source = "fusion"`). Seasonal bias is
applied after either path. There is no confidence floor on the component path.

### Posture

`component_posture` ∈ {`To God`, `About God`, `To Congregation`} plus
`component_posture_confidence`. The **effective posture** used downstream resolves
`component_posture → recording_posture → None`.

### Energy percentiles

`entry_energy_pct` / `exit_energy_pct` ∈ [0, 1], computed **within the fetched pool
only** (ties-averaged midpoint percentile) from the boundary `*_energy_level_db`
values. There are no absolute dB thresholds anywhere. Songs without energy data carry
`None` percentiles and are skipped (not penalized) in f_energy; fewer than 2 usable
values pool-wide → all percentiles are `None`.

### Boundary-first transitions

A pair is `boundary_source = "component"` only when **both** sides have boundary keys
(`exit_key` on the from-side, `entry_key` on the to-side); CFD, `key_compat`, and
`suggested_key_shift` then compute on those boundary keys. Otherwise the whole pair
computes on song-level keys (`boundary_source = "song_level"`) — **keys never mix
provenance within one pair**.

BPM is per-side best-available inside component pairs (each side: boundary BPM if
present, else song-level `tempo_bpm`), with per-song warnings like
`missing exit bpm on <title>; used song-level bpm`; both sides missing →
`missing boundary bpm on both sides — delta unreliable`. Song-level pairs with a
missing tempo also warn (`missing bpm on <title> — delta unreliable`) instead of
silently collapsing to a delta of 0.

### Technique ladder

Verified in `rules/transitions.py` (CFD = circle-of-fifths distance):

| CFD | Technique | Crossfade | Gap |
|-----|-----------|-----------|-----|
| ≤ 1 | pivot | 0 s | 2 beats |
| ≤ 2 (mode differs) | relative | 0 s | 2 beats |
| ≤ 2 (mode same) | direct | 0 s | 2 beats |
| ≤ 2 with non-zero shift | transposition | 4 s | 4 beats |
| 3 | vamp | 6 s | 4 beats |
| else | direct_modulation | 8 s | 6 beats |

### H2/H3/H8 boundary semantics

- **H2** checks the opener's **entry** BPM (song-level fallback; both missing → fails).
- **H3** checks the closer's **exit** BPM (song-level fallback).
- **H8** gates transposition on the destination's `entry_key_confidence ≥ 0.6` when
  the incoming transition is component-sourced; song-level `key_confidence ≥ 0.6`
  otherwise. The opener is always song-level-gated; the from-side is not separately
  gated.

**Disclosed accepted regression:** under the boundary gate the transposable pool
population drops from 250 (56.3%) to ~201 (45.3%) — component key detection is
low-confidence far more often than song-level detection. The report's pool overview
tracks both counts so this stays observable run-over-run. Singing-range shifts
(`recommended_key_shift_for_range`) stay song-level-gated — they transpose the whole
song, not a boundary.

### Structural note

The transition matrix is precomputed O(n²) in `build_transitions.py`;
`score_songset.py` and the planner see only the matrix — pair compatibility cannot be
recomputed at plan time. Trust `cfd`/`bpm_delta`/`suggested_key_shift` from the matrix.

## 5-Phase Worship Arc

Theme → phase map (`rules/phases.py`):

| Theme | Phase |
|-------|-------|
| 讚美 | 1 |
| 感恩 | 2 |
| 敬拜 / 祈禱 / 信心 / 聖靈 | 3 |
| 奉獻 / 認罪 / 十字架 | 4 |
| 差遣 / 跟隨 / 復興 | 5 |

Arc labels (matching SKILL.md): 1 Call, 2 Thanksgiving, 3 Worship, 4 Response,
5 Commission.

**Special case:** dominant theme 聖靈 with tempo < 70 BPM → phase 4 (slow Spirit song
is a Response, not Worship).

**Tempo-only fallback** (fusion-path songs with zero theme hits): ≥ 100 → 1,
≥ 90 → 2, ≥ 70 → 3, < 70 → 4; unknown tempo → 3.

**`secondary_phases`:** themes scoring ≥ 0.85 × dominant contribute secondary phases,
at most 2, excluding the primary. H1 middles/closers and H7 accept primary **or**
secondary phases.

**Seasonal bias** (applied after the component or fusion path; values unchanged):

| Season | Boosts |
|--------|--------|
| advent, christmas | 讚美 ≥ 0.7, 感恩 ≥ 0.5 |
| lent | 認罪 ≥ 0.7, 十字架 ≥ 0.65 |
| easter | 復興 ≥ 0.65, 讚美 ≥ 0.65 |
| pentecost | 聖靈 ≥ 0.75 |

**Phase templates** (`rules/fitness.py`):

| Songs | Template | Arc |
|-------|----------|-----|
| 2 | (1, 4) | Call → Response |
| 3 | (1, 3, 5) | Call → Worship → Commission |
| 4 | (1, 3, 4, 5) | Call → Worship → Response → Commission |
| 5 | (1, 2, 3, 4, 5) | Full worship arc |

## Theme classification (fusion fallback) background

This fusion path now applies only to component-less songs. Each song is classified by
**four independent sources**, each returning a `dict[str, float]` over the 12 themes:

1. **Title keywords** — bilingual vocabulary (Chinese + pinyin + English) in
   `rules/themes.py:THEME_VOCAB`; scores normalized by the max hit count.
2. **Lyrics keywords** — 2-line sliding window over the lyrics, keyword hits per
   theme, normalized by total hits.
3. **Song embedding** — cosine similarity of the song-level embedding vs 1536-dim
   theme anchor vectors.
4. **Line embeddings** — best per-theme cosine over line-level embeddings.

Both embedding sources are min-max normalized so the best theme scores 1.0.

**Fusion weights** (`rules/phases.py:fuse_themes`) — reliability-ordered, with a twist
the old guide got wrong:

| Case | Title | Lyrics | Song emb | Line emb |
|------|-------|--------|----------|----------|
| Title/lyrics top themes **agree** | 0.45 | 0.35 | 0.15 | 0.05 |
| Top themes **disagree** | 0.35 | 0.25 | 0.25 | 0.15 |

When line embeddings are absent, the line weight is redistributed proportionally over
the remaining sources. Only non-empty sources contribute (per-theme renormalization).

### Traditional Chinese Matching Rationale

All theme keys, matching terms, and embedding anchor texts in the songset constructor
use **Traditional Chinese only**. This is because:

1. **Catalog lyrics are Traditional Chinese.** All SOP.org song lyrics are in
   Traditional Chinese. Simplified-only keywords (e.g., `宝血`, `传扬`, `门徒`) would
   never match the actual lyric text, resulting in missed theme classifications.
2. **Eliminates duplicate term pairs.** The previous vocab had both Simplified and
   Traditional forms of the same word (e.g., `赞美` and `讚美` in the same tuple),
   creating redundant matching with no benefit.
3. **Ensures correct matching.** With Traditional-only keywords, the title and lyrics
   classifiers match against the actual character forms present in the catalog.

The conversion was measured (historical facts of the Simplified→Traditional
conversion, documented in `reports/enrichment_eval_comparison.md`):

- **Title hits**: 76 → 106 (+30 songs now have title theme hits)
- **Lyrics hits**: 342 → 372 (+30 songs now have lyrics theme hits)
- **Zero-theme songs**: remained at 0 (all songs still get a theme via embeddings)

The embedding anchor vectors in `data/theme_anchors.json` were key-renamed from
Simplified to Traditional (vectors unchanged) because the embedding endpoint was
unavailable. A full regeneration with Traditional anchor texts should happen when
`SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` are available.

**Anchor file role in the skill path:** `songset_constructor/data/theme_anchors.json`
seeds the `theme_anchors` DB table via `theme-anchors sync`; in the skill path theme
scores arrive DB-computed (the pool query joins `theme_anchors`), not from the JSON.

## Hard Constraints H0–H9

One canonical table (verified in `rules/hard_constraints.py` + `config.py`):

| Code | Rule | Default | Relax knob (skill config key / CLI `--relax` token) |
|------|------|---------|------------------------------------------------------|
| H0 | Exactly `count` songs (2–5) | — | no |
| H1 | Exactly one phase-1 **primary** opener (primary only, not secondary); ≥ 1 phase 3/4 (primary or secondary); closer phase 4/5 (primary or secondary) | — | `relax_h1` / `h1` — relaxed keeps only the phase-4/5 closer requirement |
| H2 | Opener tempo ≥ 90 BPM, checked on **entry** BPM (song-level fallback; both missing → fails) | 90 | `relax_h2_bpm` / `h2:80` |
| H3 | Closer tempo ≤ 90 BPM (80 intimate), checked on **exit** BPM (song-level fallback) | 90/80 | `relax_h3_bpm` / `h3:100` |
| H4 | Adjacent BPM delta ≤ limit; limit = 45 when the transition carries crossfade or any gap (`gap_beats > 0`), 40 otherwise; boundary-aware delta | 45/40 | `relax_h4` → 55, or `relax_h4_bpm` / `h4` or `h4:50` |
| H5 | Circle-of-fifths distance ≤ 3 between boundary keys unless the suggested key shift is applied | 3 | `relax_h5` → 4, or `relax_h5_cfd` / `h5` or `h5:4` |
| H6 | No duplicate song IDs | — | no |
| H7 | Phase may drop by at most 1 between adjacent songs (checked across primary + secondary phases) | — | no |
| H8 | Transposition gate: key confidence ≥ 0.6 — boundary-sourced pairs gate on destination `entry_key_confidence`, others on song-level `key_confidence`; opener always song-level | 0.6 | no |
| H9 | Total duration ≤ 1500 s (25 min; `SONGSET_MAX_DURATION_SECONDS`); unknown (`None`) durations contribute 0 and don't trigger H9 | 1500 | no |

Notes:

- Matrix-backed transitions always carry `gap_beats ≥ 2`, so they use the 45 cap; only
  matrix-missing pairs fall to the 40 cap (and those fail H5 anyway).
- **CLI relax token syntax** (from `_parse_relax`, `commands/songset.py`):
  `--relax "h1,h2:80,h3:100,h4,h5:4"`.
- **Skill-side:** pass the same `RunConfig` field names in `score_songset.py`'s
  `config` dict, e.g. `{"count": 4, "intimate": false, "relax_h1": true}`.
- The LangGraph path auto-relaxes (`auto_relax` default true, order H4/H5 → H2/H3 →
  H1). The skill planner relaxes manually after 3 failed refinement iterations and
  labels relaxed proposals (e.g. `relaxed_H4_H5`).
- **Suggested relax order** (SKILL.md Step 7): H4 → H5 → H2 → H3 → H1.
- H9-violations alone → swap a long song for a shorter one (via `semantic_search`)
  rather than reduce count.
- High range penalty → apply the song's `recommended_key_shift_for_range`
  (max ±2, requires song-level `key_confidence ≥ 0.6`).
- **Draft-time guardrails:** never draft more than 5 songs (`SONGSET_MAX_SONGS` fails
  at `songset create`); accumulate running duration while drafting; `None` duration →
  don't block, warn.

## Fitness scoring

**Base weights:** f_theme 0.40 / f_tempo 0.30 / f_harmony 0.20 / f_diversity 0.10.
**f_energy** and **f_posture** join at 0.05 each **only when their signal exists
pool-wide**; an absent term's 0.05 is redistributed proportionally across the base
weights (verified `rules/fitness.py`).

| Mode | Weights |
|------|---------|
| four-way (no energy/posture pool-wide) | 0.40 / 0.30 / 0.20 / 0.10 |
| five-way (+energy only) | 0.38 / 0.285 / 0.19 / 0.095 + 0.05·f_energy |
| five-way (+posture only) | 0.38 / 0.285 / 0.19 / 0.095 + 0.05·f_posture |
| six-way (+both) | 0.36 / 0.27 / 0.18 / 0.09 + 0.05·f_energy + 0.05·f_posture |

**f_energy** (`rules/fitness.py`): ordinal on pool percentiles, never absolute dB. Arc
value = `entry_energy_pct`; the adjacency term penalizes |entry(B) − exit(A)|; the
arc-shape term penalizes sets that land louder than they opened (max(0, opener-entry −
closer-exit)). Score = 0.5·adjacency + 0.5·arc. Returns None (→ weight redistributed)
with < 2 usable adjacency values and no closer-exit value.

**f_posture** (`rules/fitness.py`): mean of `POSTURE_PHASE_FIT[posture][phase]` over
items with posture; items missing posture are skipped; None when no item has posture.
Fit matrix (verified `components.py`):

| Posture | P1 | P2 | P3 | P4 | P5 |
|---------|----|----|----|----|----|
| To God | 0.5 | 1.0 | 1.0 | 1.0 | 0.5 |
| About God | 1.0 | 0.5 | 0.5 | 0.0 | 1.0 |
| To Congregation | 1.0 | 0.0 | 0.5 | 1.0 | 1.0 |

Liturgical rationale: direct address (To God) peaks in Worship/Response; testimony
(About God) fits Call and Commission; congregational exhortation (To Congregation)
fits Call/Response/Commission but not Worship.

**Range penalty** (`score_songset.py`): only when `--leader-range` was provided. 0.05
per semitone from the closest comfortable tonic PC (circular distance on
`(tonic_pc + key_shift) % 12`), capped 0.20/song; **subtracted from total after the
weighted sum**.

**Deliberate asymmetry:** f_tempo stays song-level (`item.bpm`) while H2/H3/H4 are
boundary-aware. Whole-song tempo is the liturgical "feel"; boundary BPM only governs
adjacency and the opener/closer floor/ceiling checks. Do not "fix" this without a user
decision (spec v2 §3.2).

**Interpretation table** (from SKILL.md):

| Component | Good | Acceptable | Concern |
|-----------|------|------------|---------|
| f_theme | ≥ 0.90 | ≥ 0.80 | < 0.80 (phase mismatch) |
| f_tempo | ≥ 0.70 | ≥ 0.65 | < 0.60 (large BPM jumps) |
| f_harmony | ≥ 0.70 | ≥ 0.50 | < 0.40 (key incompatibility) |
| f_diversity | 1.00 | 1.00 | < 1.00 (duplicate songs) |
| f_energy | ≥ 0.80 (smooth descent) | ≥ 0.65 | < 0.50 (energy clashes / rising arc) |
| f_posture | ≥ 0.80 (fits the phase template) | ≥ 0.50 | < 0.40 (posture fights the phase) |
| range_penalty | 0.00 | ≤ 0.05 | > 0.10 (out-of-range songs) |
| **total** | **≥ 0.80** | **≥ 0.70** | **< 0.65** |

Note: the weight mode (four/five/six-way) differs between pools, so compare totals
within a run, not across runs.

## Output artifacts

**Skill path:** a single `proposal_report.md`, written by `write_report.py`. Contents:

- **Run configuration** — including leader-range label + comfortable PCs.
- **Pool overview** — phase distribution, theme coverage, tempo/key coverage, duration
  distribution, **component coverage %**, **theme_source distribution**,
  **transposable-population counts** (song-level vs boundary-gated — makes the H8
  regression observable run-over-run).
- **Per-proposal details** — song sequence (with `theme_source`), phase arc, BPM/key
  journey with **boundary entry/exit BPM + key columns** alongside song-level values,
  per-song + total duration vs the 1500 s cap, score breakdown with **active weight
  mode** (four/five/six-way) and f_energy/f_posture values or `(n/a)` + range_penalty,
  transition settings, singing-range status, **adjacency table** with per-pair
  provenance (`component`/`song-level`), CFD, BPM Δ, warnings; **energy arc**
  (entry/exit pct per position); **posture sequence** vs phase with fit values.
- **Component-metadata warnings** — songs with `has_components = false` used in a
  proposal ("song-level fallback: theme via fusion, no energy/posture data");
  per-adjacency boundary-BPM warnings.
- **Diversity matrix** — unique songs/themes/composers, overlap matrix, frequency
  table, bottlenecks.

**Deprecated LangGraph path** writes 5 files (verified `artifacts/writer.py`):
`proposals.json`, `proposal_report.md`, `candidate_pool.csv`, `graph_trace.jsonl`,
`songset_review.md`, plus `diagnose_report.md` when `--report`.

## How to evaluate results

### Read the report

- **Phase arc** vs the template (e.g., 1→3→4→5 for 4 songs).
- **BPM arc** generally decreasing opener → closer; large jumps indicate weak
  transitions.
- **Adjacency provenance**: `component` rows are the honest boundary math;
  `song-level` rows mean at least one side lacked boundary keys.
- **Warnings column**: boundary-BPM fallback = missing analysis data, not an algorithm
  bug; `low_key_confidence` = transposition may be H8-blocked; `relaxed_*` labels =
  strict constraints were relaxed for this proposal (acceptable, but note it).

### Score distribution

Use the interpretation table in [Fitness scoring](#fitness-scoring). Two extra rules:

- Totals are only comparable **within a run** — the weight mode (four/five/six-way)
  varies with pool coverage.
- A six-way pool with f_energy ≥ 0.80 but f_posture < 0.40 means posture is fighting
  the arc even though the set "flows" energetically.

### Diversity targets (SKILL.md Step 8)

- ≥ 50% unique openers across proposals.
- ≥ 3 unique songs per middle slot.
- ≥ 2 unique closers.
- Subtract 0.15 × (overlap_count / middle_count) from a proposal's total when its
  middle songs were already used by higher-ranked proposals.

If slot-2 variety is only 2–3 songs, that's the H4/H5 catalog limit, not a bug — widen
the pool, relax H4/H5, or use `semantic_search` for the thin slot.

### Enrichment health checks (from `enrich_pool.py` stderr summary)

- `Theme source: component=N, fusion=M` — all-fusion usually means a **stale pool
  cache predating component integration** → refetch with `--prefer-fresh` or
  `--no-cache`.
- `Pool: X loaded → Y enriched (Z dropped)` — large dropped counts mean missing
  tempo/key metadata.
- `Phase distribution: P1..P5` — you need phase-1 openers AND phase-4/5 closers AND
  phase-3 middles; a missing bucket blocks valid sets.
- `Theme inference: N from themes, M from tempo fallback` — a large tempo-fallback
  share means keyword/embedding gaps → artificial phase clusters.
- `Title hits` / `Lyrics hits` — low lyrics hits = `THEME_VOCAB` gaps.
- `Theme entropy: X bits (max 3.585)` — below 2.5 bits = low theme diversity.
- Singing-range lines (when `--leader-range` given): in/out-of-range counts.

### Transposable-population check

Compare the pool overview's song-level vs boundary-gated counts. If the gap is much
larger than ~250 → ~201, boundary key confidence regressed (data issue — report it).

## Recipes (skill path)

### Seasonal pool prep chain

See [Quick Start](#quick-start-skill-path) — the `fetch_pool → enrich_pool →
build_transitions` chain is the single deterministic baseline.

### Leader-range enrichment

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/enrich_pool.py \
  --leader-range '{"comfortable_pcs": [0, 1, 2, 3, 4, 5], "label": "normal male"}'
```

Resolve a voice type to PCs via `voice_ranges.py`. The map (verified):

| Voice | Range | Comfortable PCs |
|-------|-------|-----------------|
| normal male | A2–E4 | 0, 1, 2, 3, 4, 5 |
| low male | E2–B3 | 8, 9, 10, 11, 0, 1 |
| high male | C3–G4 | 2, 3, 4, 5, 6, 7 |
| normal female | G3–E5 | 0, 2, 3, 4, 5, 7 |
| low female | E3–B4 | 9, 10, 11, 0, 2 |
| high female | C4–G5 | 2, 4, 5, 7, 9 |

Declining the range question → default to normal male and note it in the run summary.

### Score a draft

Merge the build_transitions output with your draft items and config using a quoted
heredoc (the quoted sentinel `<<'EOF'` suppresses shell interpolation):

```bash
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

uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/score_songset.py --input /tmp/score_input.json
```

### Refine loop

Read `repair_hints` from the validation feedback; swap songs, reorder, or adjust key
shifts; re-score. At most 3 iterations — then relax in the H4 → H5 → H2 → H3 → H1
order from [Hard Constraints](#hard-constraints-h0h9).

### Fill a thin slot

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/semantic_search.py --query 感恩 --limit 20
```

Add `--album-series "敬拜讚美 (1)"` (repeatable) to restrict the search.

### Inspect a transition

When a pair has CFD > 2, inspect how each song ends/starts:

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
  lab/skills/songset-constructor/scripts/get_lyrics.py --hash-prefix <12hex>
```

Run it on both songs of the pair.

### Write the report

```bash
echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...], "summary": "..."}' \
  | uv run --project ops/admin-cli --extra admin --extra constructor python \
      lab/skills/songset-constructor/scripts/write_report.py \
      --output-dir output/songset_constructor/<timestamp>/
```

The `summary` field is a 3–5 sentence executive summary (per SKILL.md Step 9b).

### Persist (optional)

Song IDs use the format `{slug}_{8-char-hex}` (e.g. `wo_de_ye_su_4c27d159`) — extract
the `song_id` tokens from proposal items. **NEVER** pass `recording_hash_prefix`
(12-char hex; invalid as a song ID).

```bash
export SOW_DEFAULT_USER=alice@example.com

# Extract song_ids from the top proposal (assumes /tmp/top_proposal.json).
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

Notes:

- Owner: `SOW_DEFAULT_USER` env var or `--user <email>`.
- Dry-run first, then `--yes`.
- Defensive trim to ≤ 5 items by `position` — enforced limits are 5 songs
  (`SONGSET_MAX_SONGS`) / 1500 s (`SONGSET_MAX_DURATION_SECONDS`).
- Latest-active recording is auto-selected.
- Ambiguous title matches error in `--yes` mode → always use `song_id`.

## Deprecated paths

### LangGraph `sow-admin songset construct`

Invocation:

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user <email> [flags]
```

Real flags (verified typer signature):

| Flag | Default | Notes |
|------|---------|-------|
| `--count` / `-n` | 3 | 2–5 |
| `--proposals` / `-k` | 3 | 1–20 |
| `--pool` / `-p` | 200 | max pool size |
| `--album-series` | none | repeatable |
| `--include-cpw` | off | adds CPW series |
| `--intimate` | off | closer ceiling 90 → 80 |
| `--hymnal-mode` | off | adds HYMN series |
| `--season` | none | advent, christmas, lent, easter, pentecost |
| `--llm` / `--no-llm` | `--no-llm` | LLM planning mode |
| `--llm-judge` | off | requires `--llm` |
| `--llm-model` | `SOW_LLM_MODEL` | |
| `--relax` | none | token string, e.g. `"h1,h2:80,h3:100,h4,h5:4"` |
| `--constraints-file` | none | YAML/JSON relax overrides |
| `--report` (+ `--report-dir`) | off | writes `diagnose_report.md` |
| `--dry-run` | off | skip DB writes |
| `--yes` | off | auto-save without prompting |
| `--no-cache` | off | bypass pool cache |
| `--cache-dir` | default cache dir | |
| `--cache-ttl` | 24 | hours |

Caveats: song-level only (no component metadata, four-way weights, song-level
H2/H3/H8); auto-relaxes by default (`auto_relax` true, order H4/H5 → H2/H3 → H1);
persists via `persist_proposals` unless `--dry-run`; requires `theme_anchors` = 12.

Internals (deterministic mode): diverse beam search in `rules/beam.py` with beam width
`max(top_k*5, 40)` and round-robin opener/middle grouping; `rank_proposals` in
`rules/proposals.py` then applies greedy diverse selection with a 0.15
middle-overlap penalty. This is deterministic machinery — in the skill path the agent
does the planning, refinement, and ranking judgment itself.

### POC script

`lab/poc-scripts/construct_songset_agent.py` — retained for reference only.

### Read-only guarantee

Skill scripts issue bounded `SELECT`s via `ReadOnlyClient` and write **no DB rows**;
persistence happens only through explicit `songset create`. The deprecated `construct`
path persists unless `--dry-run`.

## Troubleshooting

### `json.JSONDecodeError` on a 0-byte file

Missing `uv run` prefix — see the invocation note in
[Quick Start](#quick-start-skill-path).

### No proposals generated

1. Check the report's warnings/relax labels — which stage blocked output.
2. Relax in the H4 → H5 → H2 → H3 → H1 order (see Hard Constraints).
3. `pool_size = 0` → the catalog lacks published/review recordings with LRC lyrics.
4. Phase-1 or phase-4/5 count 0 → no valid openers/closers (check the enrichment
   summary).

### Stale cache symptoms

`duration_seconds = None`, no component fields, `Theme source: fusion=` for all songs
→ refetch with `--prefer-fresh` or `--no-cache`. Old caches still validate (new fields
default `None`/`False`), so the pipeline won't reject them — detect them by symptom.

### All proposals share the opener / middle songs

Catalog constraint under H4/H5: few compatible phase-3 songs per BPM group. Widen the
pool, relax H4/H5, vary `--album-series`, or use `semantic_search`.

### H8 blocks more transpositions than before

Expected boundary gate (see Component metadata → H2/H3/H8 boundary semantics). Check
the transposable counts in the pool overview — accepted regression.

### DB unreachable

Preflight WARNs. `fetch_pool.py` serves the stale cache by default (`--allow-stale`);
`--prefer-fresh` tries DB first. No cache + no DB = cannot proceed.

### Harmony scores are low

Large key jumps between adjacent songs. `suggested_key_shift` improves compatibility
but H8 may block it — check boundary vs song-level key confidence in the report.

## Key source files

| File | Purpose |
|------|---------|
| `lab/skills/songset-constructor/SKILL.md` | Operational 12-step workflow (canonical; agent reads first) |
| `lab/skills/songset-constructor/scripts/fetch_pool.py` | Raw pool fetch + cache |
| `lab/skills/songset-constructor/scripts/enrich_pool.py` | Component-primary themes, phase, seasonal bias, energy percentiles, leader range |
| `lab/skills/songset-constructor/scripts/build_transitions.py` | Boundary-first transition matrix + fan-out/dead-end |
| `lab/skills/songset-constructor/scripts/score_songset.py` | Draft scoring + H1–H9 validation + range penalty |
| `lab/skills/songset-constructor/scripts/write_report.py` | `proposal_report.md` writer |
| `lab/skills/songset-constructor/scripts/semantic_search.py` / `get_lyrics.py` / `voice_ranges.py` / `preflight.sh` | Slot-filling search; lyric inspection; voice-range mapping; env checks |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py` | `SongCandidate` / `ProposalItem` / `TransitionCandidate` incl. component + boundary fields |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py` | Read-only pool queries (song + line themes + component rows) |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/components.py` | Component-row aggregation + `POSTURE_PHASE_FIT` |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/config.py` | `RunConfig` (H4/H5/H2/H3 limit properties, relax knobs) |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/hard_constraints.py` | H1–H9 + boundary-aware H2/H3/H8 |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/fitness.py` | Weights, f_energy, f_posture, diversity penalty |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/transitions.py` | Boundary-first `recommend_transition` + technique ladder |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/phases.py` / `rules/themes.py` | Fusion fallback, seasonal bias, phase inference / keyword vocab |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/beam.py` + `rules/proposals.py` | LangGraph beam search + ranking (deprecated path) |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/data/theme_anchors.json` | Seed vectors for `theme-anchors sync` |
| `specs/songset-constructor-component-metadata-integration-v2.md` + `docs/adr/0006-component-theme-primary-for-phase-inference.md` | Design records for this integration |
