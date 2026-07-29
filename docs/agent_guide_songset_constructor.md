# Agent Guide: Songset Constructor — Generating and Evaluating Diverse Songsets

This guide explains how to use the songset constructor to generate diverse Chinese worship songsets and evaluate the quality of the results.

## Quick Start

The production path is the `sow-admin songset construct` command in the admin CLI:

```bash
# Prerequisite: populate theme_anchors table (one-time)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin theme-anchors sync

# Deterministic mode (no LLM required)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --no-cache

# Agentic mode (LLM planning + optional judge)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --llm --llm-judge --yes
```

> **Deprecated:** The POC script `lab/poc-scripts/construct_songset_agent.py` is
> retained for reference but is no longer the primary path. Use
> `sow-admin songset construct` for all production work. The admin CLI
> subpackage at `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/`
> is the source of truth.

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
| `--only-evaluate-pool-enrichment` / `--full-run` | `--full-run` | — | Run only through `enrich_pool` and write a distribution report (no LLM required) |
| `--output-dir` | auto-timestamped | path | Override output directory |

## Two Operating Modes

The songset constructor is a LangGraph state machine that runs in two modes:

| Mode | Flag | Behavior |
|------|------|----------|
| **Deterministic** | `--no-llm` | Pure beam search + scoring, no LLM needed |
| **Agentic** | `--llm` (default) | LLM plans/refines a draft, validated against hard constraints |

Both modes share the same pipeline stages (1–4, 7–11). Only stages 5–6 (`llm_plan`, `validate_score`, `llm_refine`) are exclusive to agentic mode.

## 5-Phase Worship Arc

Songs are classified into 5 phases based on **fused theme scores** (title 35% + lyrics 25% + song embedding 25% + line embedding 15%):

| Phase | Theme |
|-------|-------|
| 1 | 讚美 (Praise) |
| 2 | 感恩 (Thanksgiving) |
| 3 | 敬拜/祈禱/信心/聖靈 (Worship) |
| 4 | 奉獻/認罪/十字架 (Response) |
| 5 | 差遣/跟隨/復興 (Commission) |

**Seasonal bias** (`--season`) boosts relevant themes. For example:
- `christmas` → 讚美/感恩
- `lent` → 認罪/十字架

## 8 Hard Constraints (H0–H8)

| Code | Rule | Default |
|------|------|---------|
| H0 | Correct song count | — |
| H1 | One phase-1 opener, worship/response middle, phase 4/5 closer | — |
| H2 | Opener tempo >= 90 BPM | 90 |
| H3 | Closer tempo <= 90 BPM (80 intimate) | 90/80 |
| H4 | Adjacent BPM delta <= 35 (25 without crossfade/gap) | 35/25 |
| H5 | Circle-of-fifths distance <= 2 | 2 |
| H6 | No duplicate song IDs | — |
| H7 | Phase drops by at most 1 between adjacent songs | 1 |
| H8 | Low key-confidence songs (<0.6) can't be transposed | 0.6 |

## Pipeline Architecture

The constructor runs as a LangGraph state machine with these stages:

```
load_catalog → enrich_pool → build_transition_matrix → beam_seed_candidates
                    │                                               ↓
                    │                                     ┌─────────┴──────────┐
                    │                                   --no-llm              LLM mode
                    │                                     │                      │
                    │                               finalize_rank          llm_plan → validate_score
                    │                                     │                 ↓               ↓
                    │                                Accepted         Refine (loop ≤3)
                    │                                     ↓               ↓               ↓
                    │                            finalize_rank ←────────┘
                    │                                     ↓
                    │                            ┌──────────┴──────────┐
                    │                       --llm-judge         default
                    │                            │                   │
                    │                       llm_judge                │
                    │                            │                   │
                    └────────────────────────────┴───────────────────┘
                                                               ↓
                                                    interactive_review (optional)
                                                               ↓
                                                        write_artifacts

--only-evaluate-pool-enrichment:
load_catalog → enrich_pool → write_enrichment_report → END
```

### Stage Details

1. **load_catalog** — Fetches songs from PostgreSQL via read-only `SELECT`. Loads songs with published/review recordings that have LRC lyrics. Pool size is bounded by `--pool-limit`.

2. **enrich_pool** — Classifies each song's themes (from title, lyrics, embeddings), infers worship phase (1=call, 2=adoration, 3=praise, 4=cross/response, 5=commitment), and applies seasonal bias. Drops songs lacking both tempo and key metadata. See [enrich_pool Deep Dive](#enrich_pool-deep-dive) below for implementation details.

3. **build_transition_matrix** — Computes pairwise transition recommendations (BPM delta, circle-of-fifths distance, suggested key shift, crossfade/gap settings) for all song pairs where CFD ≤ 6. Also computes fan-out (how many valid transitions each song has) and marks dead-end songs.

4. **beam_seed_candidates** — Runs diverse beam search (see below). Produces ranked candidate sequences following the phase template.

5. **llm_plan** (LLM mode only) — LLM drafts a songset from the pool using structured output. Hallucinated hash prefixes are repaired via fuzzy matching.

6. **validate_score** — Validates the LLM draft against hard constraints H0–H8. If it fails, routes to `llm_refine` (up to 3 iterations).

7. **finalize_rank** — Deduplicates proposals by song sequence, then applies greedy diverse selection with a middle-song diversity penalty (see below).

8. **llm_judge** (optional) — LLM re-ranks finalists and adds judge reasons/scores without changing deterministic order.

9. **write_artifacts** — Writes 5 output files (see below).

### enrich_pool Deep Dive

The `enrich_pool` node (`poc/songset_constructor/graph/nodes.py:47-86`) enriches each raw `SongCandidate` with computed themes, inferred phase, and hymn flag. It runs four classification steps per song and fuses the results.

#### Step 1: Drop songs with no metadata

```python
for candidate in state.get("pool", []):
    if candidate.tempo_bpm is None and candidate.musical_key is None:
        dropped += 1
        continue
```

Songs missing both tempo and key metadata are dropped immediately — they cannot participate in transition scoring or phase inference.

#### Step 2: Classify themes from four sources

Each song is classified by **four independent theme classifiers**, each returning a `dict[str, float]` mapping 12 themes to scores in [0, 1]:

```python
title = classify_title_themes(candidate.title, candidate.title_pinyin)
lyrics = classify_lyrics_themes(candidate.lyrics_raw)
song_emb, line_emb = classify_embedding_themes(
    candidate.song_embedding,
    candidate.line_embeddings,
    anchors,
)
```

**Title classification** (`rules/themes.py:35-41`) — keyword matching against a bilingual vocabulary (Chinese + pinyin + English):

```python
THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "讚美": ("讚美", "讚美", "歌唱", "欢呼", "hallelujah", "praise", "zan mei"),
    "感恩": ("感恩", "感谢", "謝謝", "恩典", "grace", "thanks", "gan en"),
    "敬拜": ("敬拜", "俯伏", "尊崇", "荣耀", "worship", "adore", "jing bai"),
    # ... 9 more themes
}

def classify_title_themes(title: str | None, title_pinyin: str | None = None) -> dict[str, float]:
    text = " ".join(part for part in [title or "", title_pinyin or ""] if part)
    hits = {theme: _matches(text, terms) for theme, terms in THEME_VOCAB.items()}
    max_hits = max(hits.values(), default=0)
    if max_hits == 0:
        return {theme: 0.0 for theme in THEMES}
    return {theme: value / max_hits for theme, value in hits.items()}
```

**Lyrics classification** (`rules/themes.py:44-56`) — sliding 2-line window over lyrics, counting keyword hits per theme, then normalizing by total hits:

```python
def classify_lyrics_themes(lyrics_raw: str | None) -> dict[str, float]:
    lines = [line.strip() for line in lyrics_raw.splitlines() if line.strip()]
    windows = [" ".join(lines[i : i + 2]) for i in range(max(1, len(lines) - 1))]
    counter: Counter[str] = Counter()
    for window in windows or [lyrics_raw]:
        for theme, terms in THEME_VOCAB.items():
            counter[theme] += _matches(window, terms)
    total = sum(counter.values())
    if total == 0:
        return {theme: 0.0 for theme in THEMES}
    return {theme: counter[theme] / total for theme in THEMES}
```

**Embedding classification** (`rules/themes.py:70-79`) — cosine similarity against 1536-dimensional theme anchor vectors (from `text-embedding-3-small`), for both the song-level embedding and the best line-level embedding per theme:

```python
def classify_embedding_themes(
    song_vec: list[float] | np.ndarray | None,
    line_vecs: list[list[float]] | list[np.ndarray] | None,
    theme_anchors: dict[str, np.ndarray],
) -> tuple[dict[str, float], dict[str, float]]:
    song_scores = {theme: cosine(song_vec, anchor) for theme, anchor in theme_anchors.items()}
    line_scores: dict[str, float] = {}
    for theme, anchor in theme_anchors.items():
        line_scores[theme] = max((cosine(vec, anchor) for vec in (line_vecs or [])), default=0.0)
    return (_normalise_cosine_scores(song_scores), _normalise_cosine_scores(line_scores))
```

The cosine scores are min-max normalized (`_normalise_cosine_scores`) so the best theme scores 1.0 and the worst is shifted to 0.

#### Step 3: Fuse themes with weighted averaging

```python
fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
```

**Theme fusion** (`rules/phases.py:23-45`) — reliability-ordered weighted average: title (35%) + lyrics (25%) + song embedding (25%) + line embedding (15%). Only non-empty sources contribute, and weights are dynamically normalized:

```python
def fuse_themes(
    title: dict[str, float],
    lyrics: dict[str, float],
    song_emb: dict[str, float],
    line_emb: dict[str, float],
) -> dict[str, float]:
    weighted_sources = [
        (0.35, title),
        (0.25, lyrics),
        (0.25, song_emb),
        (0.15, line_emb),
    ]
    totals = {theme: 0.0 for theme in THEMES}
    weights = {theme: 0.0 for theme in THEMES}
    for weight, source in weighted_sources:
        if any(value > 0 for value in source.values()):
            for theme in THEMES:
                totals[theme] += weight * source.get(theme, 0.0)
                weights[theme] += weight
    return {
        theme: (totals[theme] / weights[theme] if weights[theme] else 0.0)
        for theme in THEMES
    }
```

**Seasonal bias** (`rules/phases.py:48-63`) — after fusion, certain themes are boosted for liturgical seasons:

```python
def apply_seasonal_bias(fused: dict[str, float], season: str | None) -> dict[str, float]:
    if season not in {"advent", "christmas", "lent", "easter", "pentecost"}:
        return fused
    biased = dict(fused)
    if season in {"advent", "christmas"}:
        biased["讚美"] = max(biased.get("讚美", 0.0), 0.7)
        biased["感恩"] = max(biased.get("感恩", 0.0), 0.5)
    elif season == "lent":
        biased["認罪"] = max(biased.get("認罪", 0.0), 0.7)
        biased["十字架"] = max(biased.get("十字架", 0.0), 0.65)
    elif season == "easter":
        biased["復興"] = max(biased.get("復興", 0.0), 0.65)
        biased["讚美"] = max(biased.get("讚美", 0.0), 0.65)
    elif season == "pentecost":
        biased["聖靈"] = max(biased.get("聖靈", 0.0), 0.75)
    return biased
```

#### Step 4: Infer phase from fused themes

```python
phase = infer_phase(fused, candidate.tempo_bpm)
```

**Phase inference** (`rules/phases.py:66-80`) — the dominant theme (highest fused score) maps to a phase via `THEME_TO_PHASE`. If no themes are active, tempo-based fallback is used:

```python
THEME_TO_PHASE = {
    "讚美": 1, "感恩": 2,
    "敬拜": 3, "祈禱": 3, "信心": 3, "聖靈": 3,
    "奉獻": 4, "認罪": 4, "十字架": 4,
    "差遣": 5, "跟隨": 5, "復興": 5,
}

def infer_phase(fused: dict[str, float], tempo_bpm: float | None = None) -> int:
    if fused and max(fused.values(), default=0.0) > 0:
        theme = max(fused.items(), key=lambda item: (item[1], item[0]))[0]
        if theme == "聖靈" and tempo_bpm is not None and tempo_bpm < 70:
            return 4  # slow 聖靈 → Response instead of Worship
        return THEME_TO_PHASE.get(theme, 3)
    # Fallback: tempo-only inference
    if tempo_bpm is None:
        return 3
    if tempo_bpm >= 100:
        return 1
    if tempo_bpm >= 90:
        return 2
    if tempo_bpm >= 70:
        return 3
    return 4
```

#### Step 5: Enrich the candidate

```python
enriched.append(
    candidate.model_copy(
        update={
            "themes": fused,
            "phase": infer_phase(fused, candidate.tempo_bpm),
            "is_hymn": candidate.album_series == "HYMN",
        }
    )
)
```

The enriched `SongCandidate` now carries `themes`, `phase`, and `is_hymn` fields used by downstream stages.

## Traditional Chinese Matching Rationale

All theme keys, matching terms, and embedding anchor texts in the songset constructor use **Traditional Chinese only**. This is because:

1. **Catalog lyrics are Traditional Chinese.** All SOP.org song lyrics are in Traditional Chinese. Simplified-only keywords (e.g., `宝血`, `传扬`, `门徒`) would never match the actual lyric text, resulting in missed theme classifications.
2. **Eliminates duplicate term pairs.** The previous vocab had both Simplified and Traditional forms of the same word (e.g., `赞美` and `讚美` in the same tuple), creating redundant matching with no benefit.
3. **Ensures correct matching.** With Traditional-only keywords, the title and lyrics classifiers match against the actual character forms present in the catalog.

The conversion was validated by running `--only-evaluate-pool-enrichment` before and after the conversion. Results (documented in `reports/enrichment_eval_comparison.md`):

- **Title hits**: 76 → 106 (+30 songs now have title theme hits)
- **Lyrics hits**: 342 → 372 (+30 songs now have lyrics theme hits)
- **Zero-theme songs**: remained at 0 (all songs still get a theme via embeddings)

The embedding anchor vectors in `data/theme_anchors.json` were key-renamed from Simplified to Traditional (vectors unchanged) because the embedding endpoint was unavailable. A full regeneration with Traditional anchor texts should happen when `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` are available.

## Pool Enrichment Evaluation

The `--only-evaluate-pool-enrichment` flag runs the graph only through `load_catalog` → `enrich_pool` → `write_enrichment_report`, skipping all downstream stages (transition matrix, beam search, LLM, artifact writing). It requires no LLM credentials.

### What it reports

The enrichment report (`enrichment_report.md` + console summary) includes:

- **Pool Overview**: loaded count, enriched count, dropped count with reasons
- **Phase Distribution**: count and percentage per phase (1–5), with underrepresented flags (< 15%)
- **Theme Dominance**: how many songs have each theme as their dominant (highest fused score) theme, with underrepresented flags (< 2%)
- **Phase Inference Source**: how many songs had phase inferred from themes vs tempo-only fallback
- **Theme Signal Coverage**: how many songs have title hits, lyrics hits, song embeddings, line embeddings
- **Tempo & Key Coverage**: known vs missing, BPM range, low-confidence keys
- **Album Series Distribution**: count per album series
- **Diversity Assessment**: unique theme coverage, Shannon theme entropy (max log₂(12) ≈ 3.585), Shannon phase entropy (max log₂(5) ≈ 2.322)

### How to interpret the metrics

- **Phase 3 (敬拜) dominance > 40%**: indicates the 敬拜 theme is over-represented. Consider expanding THEME_VOCAB for underrepresented themes or adjusting fusion weights.
- **Zero-theme songs > 10%**: indicates keyword gaps. Songs with no theme hits fall to tempo-only phase inference, creating artificial phase clusters.
- **Theme entropy < 2.5 bits**: indicates low theme diversity. The ideal is close to max (3.585).
- **Title hits < 25%**: many songs have titles that don't contain any theme keywords. This is expected for songs with metaphorical or poetic titles.
- **Lyrics hits < 70%**: indicates THEME_VOCAB keyword gaps. Expanding the vocabulary with more Traditional Chinese worship terms will improve coverage.

### Recipe

```bash
set -a && source /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --pool-limit 500 --only-evaluate-pool-enrichment
```

The report is written to `lab/poc-scripts/output/songset_constructor/<timestamp>/enrichment_report.md` and a summary is printed to console.

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

> The recipes below use the production `sow-admin songset construct` command.
> The POC script equivalents (`lab/poc-scripts/construct_songset_agent.py`)
> are deprecated but still functional.

### Generate 20 diverse 4-song sets from the full catalog

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --no-cache
```

### Generate 10 LLM-judged 5-song sets

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 5 --pool 500 --proposals 10 --llm --llm-judge --yes
```

### Generate intimate worship sets (slow closers)

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --intimate --no-cache
```

### Generate Christmas-season sets

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --season christmas --no-cache
```

### Generate sets from a specific album series

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --no-cache \
  --album-series "敬拜讚美 (1)" --album-series "敬拜讚美 (2)"
```

### Debug: strict-only mode (no auto-relax)

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 4 --pool 500 --proposals 20 --no-llm --no-cache \
  --relax h1
```

If this produces 0 proposals, the catalog lacks songs that satisfy all strict H1–H5 constraints simultaneously. Re-enable auto-relax (default) or manually relax specific constraints (e.g., `--relax h4,h5`).

### Evaluate pool enrichment distribution

```bash
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --pool 500 --dry-run --no-cache --report
```

Runs `load_catalog` → `enrich_pool` → graph. No LLM required. Use `--report` to write a diagnose report. Use this to diagnose theme classification gaps and phase imbalance before running full construction.

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

The production source of truth is the admin CLI subpackage at
`ops/admin-cli/src/stream_of_worship/admin/songset_constructor/`.

| File | Purpose |
|------|---------|
| `commands/songset.py` | CLI command (`sow-admin songset construct`) |
| `songset_constructor/config.py` | RunConfig dataclass, tempo/CFD limits |
| `songset_constructor/graph/builder.py` | LangGraph state machine definition |
| `songset_constructor/graph/nodes.py` | Graph node implementations |
| `songset_constructor/rules/beam.py` | Diverse beam search with round-robin selection |
| `songset_constructor/rules/fitness.py` | Scoring functions + diversity penalty |
| `songset_constructor/rules/proposals.py` | Proposal ranking with greedy diverse selection |
| `songset_constructor/rules/hard_constraints.py` | H0–H8 validation |
| `songset_constructor/rules/transitions.py` | Pairwise transition recommendation |
| `songset_constructor/rules/phases.py` | Theme fusion, seasonal bias, phase inference |
| `songset_constructor/rules/themes.py` | Title/lyrics/embedding theme classification |
| `songset_constructor/rules/embeddings.py` | Cosine similarity + anchor loading |
| `songset_constructor/db.py` | Read-only catalog pool query (in-DB pgvector scoring) |
| `songset_constructor/persist.py` | Atomic songset persistence |
| `songset_constructor/cache.py` | Pool cache (atomic write, corruption-tolerant) |
| `songset_constructor/data/theme_anchors.json` | 1536-dim theme anchor vectors (text-embedding-3-small) |

> **Deprecated:** The POC files under `lab/poc-scripts/poc/songset_constructor/`
> and `lab/poc-scripts/construct_songset_agent.py` are retained for reference
> but are no longer the primary path.

## Read-Only Guarantee

The POC uses `ReadOnlyClient` and only issues bounded `SELECT` queries. It does not import `SongsetClient`, does not write `songsets` or `songset_items`, and does not run schema migrations.
