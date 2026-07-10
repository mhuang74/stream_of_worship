# Songset Constructor POC — Implementation Analysis

> **How `lab/poc-scripts/poc/songset_constructor/` implements the Chinese worship songset research report**

| | |
|---|---|
| **Date** | 2026-07-09 |
| **Status** | Final |
| **Audience** | Engineering — software engineers maintaining or porting the songset constructor |
| **Scope** | The POC under `lab/poc-scripts/poc/songset_constructor/` and its faithful mapping to the rules in `docs/research_report_chinese_worship_songset.md` |
| **Companion document** | `docs/research_report_chinese_worship_songset.md` (rule definitions) |
| **Downstream consumer** | Engineers porting the POC into `delivery/webapp/src/lib/songset/` |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Pipeline (LangGraph)](#2-pipeline-langgraph)
3. [Worship Arc & Themes (§1)](#3-worship-arc--themes-1)
4. [Tempo Progression (§2)](#4-tempo-progression-2)
5. [Key & Harmonic Compatibility (§3)](#5-key--harmonic-compatibility-3)
6. [Cataloging & Pool Query (§4)](#6-cataloging--pool-query-4)
7. [Hard Constraints H0–H8 (§5.1)](#7-hard-constraints-h0h8-51)
8. [Fitness Function (§5.3)](#8-fitness-function-53)
9. [Sequence Templates (§5.4)](#9-sequence-templates-54)
10. [Dead-end Songs & Relaxation Ladder (§5.5)](#10-dead-end-songs--relaxation-ladder-55)
11. [DB Schema Mapping (§5.6)](#11-db-schema-mapping-56)
12. [Agentic Layer (LLM)](#12-agentic-layer-llm)
13. [Deterministic Fallback](#13-deterministic-fallback)
14. [Artifacts Written Per Run](#14-artifacts-written-per-run)
15. [Diagnostics](#15-diagnostics)
16. [Best-Practices Summary Table](#16-best-practices-summary-table)

---

## 1. Overview

The POC at `lab/poc-scripts/poc/songset_constructor/` is a **constrained-optimization engine** that selects and orders Chinese worship songs into a cohesive songset. It is a faithful, mostly deterministic, optionally-LLM-augmented implementation of the research report at `docs/research_report_chinese_worship_songset.md`.

Every hard rule (`H1`–`H8`), every fitness-component weight, every transition technique, and the dead-end / limited-pool escalation ladder described in the report has a corresponding line of code. The output `SongsetProposal` artifact is shaped exactly like the report's `songset_items` schema.

The entrypoint is a thin shim:

```python
# lab/poc-scripts/construct_songset_agent.py:4
from poc.songset_constructor.cli import app

if __name__ == "__main__":
    app()
```

The Typer CLI (`lab/poc-scripts/poc/songset_constructor/cli.py:304`) builds a `RunConfig`, compiles the LangGraph, streams it with debug events, and writes artifacts to `output/songset_constructor/<run_id>/`.

---

## 2. Pipeline (LangGraph)

The graph is built in `lab/poc-scripts/poc/songset_constructor/graph/builder.py:30`. It contains 11 nodes connected as follows:

```
START → load_catalog → enrich_pool → build_transition_matrix → beam_seed_candidates
  │
  ├─[no beam candidates OR no_llm]→ finalize_rank
  └─[LLM]→ llm_plan → validate_score ─┬─[Accepted]→ finalize_rank
                                       ├─[Refine, iter<3]→ llm_refine → validate_score
                                       └─[Rejected]→ finalize_rank
finalize_rank ─┬─[no proposals]→ END
               ├─[judge]→ llm_judge ─┬─[review]→ optional_review
               │                      └─[write]→ write_artifacts
               ├─[review]→ optional_review
               └─[write]→ write_artifacts
optional_review ─┬─[approve]→ write_artifacts
                  ├─[reject]→ END
                  └─[edit]→ validate_score
```

The state schema is a `TypedDict` at `lab/poc-scripts/poc/songset_constructor/graph/state.py:18`:

```python
class ConstructorState(TypedDict, total=False):
    config: RunConfig
    pool: list[SongCandidate]
    transition_matrix: dict[tuple[str, str], TransitionCandidate]
    beam_candidates: Annotated[list[SongsetProposal], operator.add]
    llm_drafts: Annotated[list[SongsetDraft], operator.add]
    current_draft: SongsetDraft | None
    feedback: ValidationFeedback | None
    iterations: int
    final_proposals: list[SongsetProposal]
    trace: Annotated[list[dict[str, Any]], operator.add]
    approved: bool | None
    edits: dict[str, Any] | None
    artifact_paths: dict[str, str]
    llm: Any
    judge_llm: Any
```

Note the use of `Annotated[..., operator.add]` for `beam_candidates`, `llm_drafts`, and `trace` — LangGraph reducers that append rather than overwrite, so each iteration's drafts and trace events accumulate across the run.

### Node responsibilities

| Node | File:Line | Responsibility |
|---|---|---|
| `load_catalog` | `graph/nodes.py:41` | Fetch the song pool from PostgreSQL |
| `enrich_pool` | `graph/nodes.py:47` | Classify themes, infer phase, drop incomplete songs |
| `build_transition_matrix` | `graph/nodes.py:89` | Pre-compute every pairwise `TransitionCandidate` |
| `beam_seed_candidates` | `graph/nodes.py:107` | Deterministic beam search → ranked proposals |
| `llm_plan` | `graph/nodes.py:152` | LLM proposes a draft from the pool |
| `validate_score` | `graph/nodes.py:180` | Apply H0–H8; score surviving proposals |
| `llm_refine` | `graph/nodes.py:226` | LLM repairs a failed draft (up to 3 iterations) |
| `finalize_rank_node` | `graph/nodes.py:252` | Dedupe + rank top-k proposals |
| `llm_judge` | `graph/nodes.py:264` | LLM annotates finalists without reordering |
| `optional_review` | `graph/nodes.py:295` | Human-in-the-loop interrupt (approve/edit/reject) |
| `write_artifacts` | `graph/nodes.py:338` | Emit JSON/CSV/Markdown artifacts |

### Routing functions

Three conditional edges decide the flow:

```python
# graph/nodes.py:349
def route_after_beam(state: ConstructorState) -> str:
    if state["config"].no_llm or not state.get("beam_candidates"):
        return "finalize_rank"
    return "llm_plan"

# graph/nodes.py:355
def route_validation(state: ConstructorState) -> str:
    feedback = state.get("feedback")
    if feedback and feedback.passed:
        return "Accepted"
    if int(state.get("iterations", 0) or 0) < 3:
        return "Refine"
    return "Rejected"

# graph/nodes.py:364
def route_finalize(state: ConstructorState) -> str:
    if not state.get("final_proposals"):
        return "end_no_proposals"
    if state["config"].llm_judge:
        return "judge"
    if state["config"].interactive_review:
        return "review"
    return "write"
```

---

## 3. Worship Arc & Themes (§1)

The research report's §1 prescribes a five-phase worship arc — *Praise → Thanksgiving → Worship → Response → Sending* — anchored on a 12-value Chinese theme vocabulary.

### 12-value `ThemeTag` vocabulary

Defined in `lab/poc-scripts/poc/songset_constructor/rules/themes.py:12`:

```python
THEMES = ("赞美", "感恩", "敬拜", "奉献", "认罪", "差遣",
          "信心", "祈祷", "复兴", "圣灵", "十字架", "跟随")
```

These are exactly the 12 values the report recommends in §1's "Concrete recommendations".

### Theme → phase map

`lab/poc-scripts/poc/songset_constructor/rules/phases.py:7`:

```python
THEME_TO_PHASE = {
    "赞美": 1, "感恩": 2,
    "敬拜": 3, "祈祷": 3, "信心": 3, "圣灵": 3,
    "奉献": 4, "认罪": 4, "十字架": 4,
    "差遣": 5, "跟随": 5, "复兴": 5,
}
```

This mirrors §1's mapping: `{赞美, 感恩} → Praise/Thanksgiving`, `{敬拜, 祈祷, 信心} → Worship`, `{奉献, 认罪, 十字架} → Response`, `{差遣, 跟随, 复兴} → Sending`.

### Multi-source theme classification

The report's §4.2 prescribes a reliability-ordered classifier chain. The POC fuses four signals in `enrich_pool` (`graph/nodes.py:47`):

```python
# graph/nodes.py:57
title = classify_title_themes(candidate.title, candidate.title_pinyin)
lyrics = classify_lyrics_themes(candidate.lyrics_raw)
song_emb, line_emb = classify_embedding_themes(
    candidate.song_embedding,
    candidate.line_embeddings,
    anchors,
)
fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
```

The fusion weights in `rules/phases.py:29` follow the report's reliability ordering:

```python
weighted_sources = [
    (0.35, title),      # title-keyword — fastest, most reliable heuristic
    (0.25, lyrics),     # lyrics-keyword — slower but more accurate
    (0.25, song_emb),   # song-level embedding cosine vs theme anchors
    (0.15, line_emb),   # line-level embedding max-cosine vs anchors
]
```

Each classifier returns a `dict[str, float]` over the 12 themes:

- **Title classifier** (`rules/themes.py:35`): regex match against `THEME_VOCAB` (Chinese + English + pinyin terms), normalized by max hit count.
- **Lyrics classifier** (`rules/themes.py:44`): scans 2-line sliding windows (exactly as §4.2 recommends "sample 2-line windows"), counts theme-term hits per window, normalizes.
- **Embedding classifier** (`rules/themes.py:70`): cosine similarity between the song's pgvector embedding and pre-computed theme anchor vectors (loaded from `data/theme_anchors.json` via `rules/embeddings.py:35`).

### Seasonal bias

`lab/poc-scripts/poc/songset_constructor/rules/phases.py:48` implements §4.3's church-calendar overrides:

```python
def apply_seasonal_bias(fused: dict[str, float], season: str | None) -> dict[str, float]:
    if season not in {"advent", "christmas", "lent", "easter", "pentecost"}:
        return fused
    biased = dict(fused)
    if season in {"advent", "christmas"}:
        biased["赞美"] = max(biased.get("赞美", 0.0), 0.7)
        biased["感恩"] = max(biased.get("感恩", 0.0), 0.5)
    elif season == "lent":
        biased["认罪"] = max(biased.get("认罪", 0.0), 0.7)
        biased["十字架"] = max(biased.get("十字架", 0.0), 0.65)
    elif season == "easter":
        biased["复兴"] = max(biased.get("复兴", 0.0), 0.65)
        biased["赞美"] = max(biased.get("赞美", 0.0), 0.65)
    elif season == "pentecost":
        biased["圣灵"] = max(biased.get("圣灵", 0.0), 0.75)
    return biased
```

### Phase inference

`lab/poc-scripts/poc/songset_constructor/rules/phases.py:66` is the report's exact decision function — theme-driven when themes exist, tempo-fallback otherwise:

```python
def infer_phase(fused: dict[str, float], tempo_bpm: float | None = None) -> int:
    if fused and max(fused.values(), default=0.0) > 0:
        theme = max(fused.items(), key=lambda item: (item[1], item[0]))[0]
        if theme == "圣灵" and tempo_bpm is not None and tempo_bpm < 82:
            return 4
        return THEME_TO_PHASE.get(theme, 3)
    if tempo_bpm is None:
        return 3
    if tempo_bpm >= 118:
        return 1
    if tempo_bpm >= 100:
        return 2
    if tempo_bpm >= 84:
        return 3
    return 4
```

### Hymnal mode

§1 recommends a "hymnal-mode" toggle that places a 传统圣诗 at the final position. The POC implements this in `config.py:88`:

```python
if self.hymnal_mode and "HYMN" not in self.album_series:
    if self.album_series:
        self.album_series.append("HYMN")
```

When `--hymnal-mode` is set, `HYMN` is appended to the `album_series` filter so traditional hymns enter the pool.

---

## 4. Tempo Progression (§2)

The report's §2 prescribes "start hot, end slow" with specific BPM bands and a step-down rule.

### Opening and closing tempo thresholds

`lab/poc-scripts/poc/songset_constructor/config.py:97`:

```python
@property
def closing_limit(self) -> int:
    if self.relax_h3_bpm is not None:
        return self.relax_h3_bpm
    return 80 if self.intimate else 90

@property
def opening_floor(self) -> int:
    if self.relax_h2_bpm is not None:
        return self.relax_h2_bpm
    return 110
```

These match §2's "first song BPM ≥ 110, last song BPM ≤ 90 (≤ 80 if intimate closing)".

### Step-down rule (H4)

The report's "consecutive songs should differ by no more than 15 BPM, with up to 20 BPM acceptable when an instrumental vamp or modulating bridge is inserted" is encoded in `rules/hard_constraints.py:71`:

```python
h4_limit = config.h4_limit
for left, right in zip(proposal.items, proposal.items[1:]):
    transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
    bpm_delta = transition.bpm_delta if transition else abs((right.bpm or 0) - (left.bpm or 0))
    allowed = h4_limit if (right.crossfade_duration_seconds > 0 or right.gap_beats > 4) else min(15, h4_limit)
    if bpm_delta > allowed:
        failures.append(("H4", ...))
```

The `allowed` variable implements the exception: when `crossfade_duration_seconds > 0` or `gap_beats > 4`, the limit rises to `h4_limit` (default 20, or 25 when relaxed); otherwise it stays at 15.

### Monotonic non-increasing preference

§2's "prefer monotonically non-increasing tempo" is a soft constraint encoded in the fitness function at `rules/fitness.py:39`:

```python
arc_bonus = 1.0 if bpms[0] >= bpms[-1] else 0.75
return _clamp(0.75 * smoothness + 0.25 * arc_bonus)
```

### Authoritative tempo source

§2 recommends `recordings.tempo_bpm` as authoritative. The pool query at `db.py:73` joins `recordings` and uses `recording.tempo_bpm`:

```python
tempo_bpm=recording.tempo_bpm,
musical_key=recording.musical_key or song.musical_key,
```

---

## 5. Key & Harmonic Compatibility (§3)

### Circle-of-Fifths Distance (CFD) algorithm

The report's §3.3 algorithm is implemented verbatim in `lab/poc-scripts/poc/songset_constructor/rules/harmony.py`:

```python
# harmony.py:59
def relative_major_pc(key: str | None, mode: str | None = None) -> int:
    note, normalized_mode = normalize_key(f"{key or 'C'} {mode or ''}")
    pc = pitch_class(note)
    return (pc + 3) % 12 if normalized_mode == "min" else pc

# harmony.py:65
def fifth_distance_on_circle(a_pc: int, b_pc: int) -> int:
    ai = FIFTH_INDEX[a_pc % 12]
    bi = FIFTH_INDEX[b_pc % 12]
    distance = abs(ai - bi)
    return min(distance, 12 - distance)

# harmony.py:72
def cfd(from_key, from_mode, to_key, to_mode) -> int:
    return fifth_distance_on_circle(
        relative_major_pc(from_key, from_mode),
        relative_major_pc(to_key, to_mode),
    )
```

The circle-of-fifths ordering is `FIFTH_ORDER = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]` (`harmony.py:31`), which maps pitch classes to their position on the circle (C=0, G=1, D=2, A=3, E=4, B=5, F#=6, ...).

### Key compatibility score

`lab/poc-scripts/poc/songset_constructor/rules/harmony.py:79` maps CFD → [0, 1]:

```python
def key_compatibility_score(distance: int) -> float:
    if distance <= 0:
        return 1.0
    if distance == 1:
        return 0.92
    if distance == 2:
        return 0.78
    if distance == 3:
        return 0.55
    if distance == 4:
        return 0.32
    return 0.15
```

This matches §3.1's ranked table: same key = excellent (1.0), CoF neighbor = excellent (0.92 at CFD 1), two fifths = good (0.78 at CFD 2), three fifths = moderate (0.55), tritone = poor (0.15).

### Key-shift suggestion (±2 semitone cap)

§3 recommends auto-suggesting `key_shift_semitones ∈ {-2,-1,0,+1,+2}` to bring CFD ≤ 2. Implemented at `harmony.py:93`:

```python
def suggest_key_shift(from_key, from_mode, to_key, to_mode) -> tuple[int, int]:
    current = cfd(from_key, from_mode, to_key, to_mode)
    if current <= 2:
        return (0, current)
    to_note, _ = normalize_key(to_key)
    choices = []
    for shift in (-2, -1, 0, 1, 2):
        shifted = transpose_note(to_note, shift)
        choices.append((cfd(from_key, from_mode, shifted, to_mode), abs(shift), shift))
    best_distance, _, best_shift = min(choices)
    return (best_shift, best_distance)
```

The search never exceeds ±2 semitones — the report's "singer comfort" constraint.

### Transition technique selection

§3.2's transition-technique table is materialized in `rules/transitions.py:10`:

```python
def recommend_transition(from_cand, to_cand) -> TransitionCandidate:
    distance = cfd(...)
    shift, shifted_distance = suggest_key_shift(...)
    bpm_delta = abs((to_cand.tempo_bpm or 0.0) - (from_cand.tempo_bpm or 0.0))

    if distance <= 1:
        technique = "pivot"
        crossfade_seconds = 0.0
        gap_beats = 2.0
    elif distance <= 2:
        technique = "relative" if from_cand.musical_mode != to_cand.musical_mode else "direct"
        crossfade_seconds = 0.0
        gap_beats = 2.0
    elif shifted_distance <= 2 and shift != 0:
        technique = "transposition"
        crossfade_seconds = 4.0
        gap_beats = 4.0
    elif distance == 3:
        technique = "vamp"
        crossfade_seconds = 6.0
        gap_beats = 4.0
    else:
        technique = "direct_modulation"
        crossfade_seconds = 8.0
        gap_beats = 6.0
```

Each technique maps to the report's render-layer columns: `crossfade_enabled`, `crossfade_duration_seconds`, `gap_beats`, `key_shift_semitones`.

### H5 hard gate

§3's threshold rule ("CFD ≤ 2 OR non-zero crossfade OR transposed-to-compatible") is enforced at `hard_constraints.py:80`:

```python
h5_limit = config.h5_limit
distance = transition.cfd if transition else 6
shifted_ok = (
    transition is not None
    and transition.suggested_key_shift == right.key_shift_semitones
    and transition.suggested_key_shift != 0
)
if distance > h5_limit and right.crossfade_duration_seconds <= 0 and not shifted_ok:
    failures.append(("H5", ...))
```

### H8 key-confidence floor

§3's "treat `key_confidence` < 0.6 as a soft warning; require human confirmation before transposing" is enforced as a hard rule at `hard_constraints.py:88`:

```python
for item in proposal.items:
    if item.key_confidence is not None and item.key_confidence < 0.6 and item.key_shift_semitones != 0:
        failures.append(("H8", ...))
```

Additionally, `rules/transitions.py:25` surfaces a `low_key_confidence` warning on the `TransitionCandidate` when either song in a pair has low confidence.

---

## 6. Cataloging & Pool Query (§4)

### SOP-canonical source

The pool query at `lab/poc-scripts/poc/songset_constructor/db.py:24` joins `songs` ↔ `recordings` ↔ `song_embedding`:

```python
POOL_QUERY = f"""
SELECT {SONG_COLUMNS_FOR_JOIN},
       {RECORDING_COLUMNS_FOR_JOIN},
       se.embedding::text AS song_embedding_text,
       se.model_version AS song_embedding_model
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status IN ('published', 'review')
  AND (r.lrc_status = 'completed' OR r.r2_lrc_url IS NOT NULL)
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
ORDER BY s.title
LIMIT %s
"""
```

Visibility is gated to `published`/`review`, and only songs with completed LRC (or an R2 LRC URL) are included — ensuring lyrics are available for theme classification.

### `album_series` filter (PW/CPW/DEV spine)

§4.1 identifies SOP's `album_series` codes as the primary cataloging spine. The query's `cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s)` clause means: when `album_series` is empty, no filter is applied; when non-empty, only matching series are included.

### CPW exclusion by default

§4.1 says "exclude CPW series by default from adult-set pools". The POC implements this in `config.py:82`:

```python
if self.include_cpw and "CPW" not in self.album_series:
    if self.album_series:
        self.album_series.append("CPW")
```

`--include-cpw` defaults to `False`. When enabled, it only **appends** CPW to an existing filter list (never narrows an unfiltered pool).

### Line embeddings

§4.2's "audio-section classification" tier is satisfied by joining the `song_line_embedding` table (`db.py:41`):

```python
LINE_EMBEDDING_QUERY = """
SELECT song_id, line_index, embedding::text
FROM song_line_embedding
WHERE song_id = ANY(%s)
ORDER BY song_id, line_index
"""
```

These per-line vectors feed the `classify_embedding_themes` line-level signal in the theme fusion.

---

## 7. Hard Constraints H0–H8 (§5.1)

All eight report rules plus an implicit cardinality check (H0) are implemented in `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py`. The `RULE_DESCRIPTIONS` dict (`hard_constraints.py:8`) documents each one:

```python
RULE_DESCRIPTIONS: dict[str, str] = {
    "H1": "Phase coverage: the set must include exactly one phase-1 opener, at least one phase 3/4 "
          "worship/response song, and end on a phase 4/5 closer. ...",
    "H2": "Opening tempo: the first song must be phase 1 with tempo >= 110 BPM (a strong opener). ...",
    "H3": "Closing tempo: the last song must be phase 4/5 with tempo <= 90 BPM (80 BPM in intimate "
          "mode) — a calm closer. ...",
    "H4": "Tempo jump: adjacent songs' BPM delta must stay <= 20 (15 without crossfade/gap; 25 if relaxed).",
    "H5": "Circle-of-fifths distance: adjacent keys must be within CFD 2 (3 if relaxed) unless the next "
          "song is transposed to match the suggested shift.",
    "H6": "Uniqueness: no duplicate song IDs allowed in the set.",
    "H7": "Phase arc: phase may drop by at most 1 between adjacent songs (no sharp backwards worship arc).",
    "H8": "Key confidence: songs with key confidence < 0.6 cannot be transposed (key_shift must stay 0).",
}
```

### Validation function

`lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py:25`:

```python
def validate(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    relax_h4: bool = False,
    relax_h5: bool = False,
    relax_h1: bool = False,
) -> ValidationFeedback:
    failures: list[tuple[str, str, str]] = []
    phases = [item.phase for item in proposal.items]
    bpms = [item.bpm for item in proposal.items]

    # H0: Cardinality
    if len(proposal.items) != config.songs:
        failures.append(("H0", ...))
        return ValidationFeedback(passed=False, ...)

    # H1: Phase coverage
    if relax_h1:
        h1_failed = phases[-1] not in {4, 5}
    else:
        h1_failed = (
            phases.count(1) != 1
            or not any(phase in {3, 4} for phase in phases)
            or phases[-1] not in {4, 5}
        )

    # H2: Opening tempo
    if bpms[0] is None or bpms[0] < opening_floor:
        failures.append(("H2", ...))

    # H3: Closing tempo
    if bpms[-1] is None or bpms[-1] > closing_limit:
        failures.append(("H3", ...))

    # H4 + H5: Adjacent pairs
    for left, right in zip(proposal.items, proposal.items[1:]):
        ...  # see §4 and §5 above

    # H6: No duplicates
    if len({item.song_id for item in proposal.items}) != len(proposal.items):
        failures.append(("H6", ...))

    # H7: Phase arc
    for left, right in zip(proposal.items, proposal.items[1:]):
        if right.phase < left.phase - 1:
            failures.append(("H7", ...))

    # H8: Key confidence
    for item in proposal.items:
        if item.key_confidence is not None and item.key_confidence < 0.6 and item.key_shift_semitones != 0:
            failures.append(("H8", ...))

    return ValidationFeedback(
        passed=not failures,
        violated=[code for code, _, _ in failures],
        errors=[message for _, message, _ in failures],
        repair_hints=[hint for _, _, hint in failures],
    )
```

Each failure carries a `(code, message, repair_hint)` triple. The `repair_hints` are fed back to the LLM refiner (see §12).

---

## 8. Fitness Function (§5.3)

The report's weighted sum is reproduced exactly in `lab/poc-scripts/poc/songset_constructor/rules/fitness.py:64`:

```python
def score(proposal, config, matrix) -> ScoreBreakdown:
    theme = f_theme(proposal, config.songs)
    tempo = f_tempo(proposal)
    harmony = f_harmony(proposal, matrix)
    diversity = f_diversity(proposal)
    total = 0.40 * theme + 0.30 * tempo + 0.20 * harmony + 0.10 * diversity
    return ScoreBreakdown(
        f_theme=round(theme, 4),
        f_tempo=round(tempo, 4),
        f_harmony=round(harmony, 4),
        f_diversity=round(diversity, 4),
        total=round(total, 4),
    )
```

The weights `0.40 / 0.30 / 0.20 / 0.10` match §5.3 exactly: thematic progression (40%), tempo decay (30%), key compatibility (20%), diversity (10%).

### F_theme — thematic progression

`lab/poc-scripts/poc/songset_constructor/rules/fitness.py:25`:

```python
def f_theme(proposal: SongsetProposal, songs: int) -> float:
    template = _THEME_TEMPLATES[songs]
    distances = [
        abs((item.phase or 3) - template[index]) for index, item in enumerate(proposal.items)
    ]
    return _clamp(1.0 - sum(distances) / (4.0 * len(template)))
```

This rewards proposals whose phase sequence closely matches the ideal template (see §9).

### F_tempo — tempo decay shape

`lab/poc-scripts/poc/songset_constructor/rules/fitness.py:33`:

```python
def f_tempo(proposal: SongsetProposal) -> float:
    bpms = [item.bpm for item in proposal.items if item.bpm is not None]
    if len(bpms) < 2:
        return 0.5
    deltas = [abs(bpms[index + 1] - bpms[index]) for index in range(len(bpms) - 1)]
    smoothness = 1.0 - min(1.0, sum(deltas) / (25.0 * len(deltas)))
    arc_bonus = 1.0 if bpms[0] >= bpms[-1] else 0.75
    return _clamp(0.75 * smoothness + 0.25 * arc_bonus)
```

Combines smoothness (penalizes large BPM jumps) with an arc bonus (rewards starting faster than ending).

### F_harmony — adjacent key compatibility

`lab/poc-scripts/poc/songset_constructor/rules/fitness.py:43`:

```python
def f_harmony(proposal, matrix) -> float:
    if len(proposal.items) < 2:
        return 1.0
    scores = []
    for left, right in zip(proposal.items, proposal.items[1:]):
        transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
        scores.append(transition.key_compat if transition else 0.2)
    return _clamp(sum(scores) / len(scores))
```

Averages `key_compat` (from §5's `key_compatibility_score`) over all adjacent pairs.

### F_diversity — composer & album variety

`lab/poc-scripts/poc/songset_constructor/rules/fitness.py:56`:

```python
def f_diversity(proposal: SongsetProposal) -> float:
    song_ids = {item.song_id for item in proposal.items}
    themes = {theme for item in proposal.items for theme in item.themes}
    song_part = len(song_ids) / max(1, len(proposal.items))
    theme_part = min(1.0, len(themes) / max(2, len(proposal.items)))
    return _clamp(0.7 * song_part + 0.3 * theme_part)
```

This uses song-ID uniqueness and theme variety as a proxy for the report's composer/album diversity recommendation — same intent, slightly different signal.

---

## 9. Sequence Templates (§5.4)

The report defines templates for 4, 5, and 6 songs. The POC supports 2–5 songs and defines templates in both the beam search and the fitness module.

`lab/poc-scripts/poc/songset_constructor/rules/fitness.py:8`:

```python
TEMPLATE_PHASES_5 = (1, 2, 3, 4, 5)
TEMPLATE_PHASES_4 = (1, 3, 4, 5)
TEMPLATE_PHASES_3 = (1, 3, 5)
TEMPLATE_PHASES_2 = (1, 4)

_THEME_TEMPLATES: dict[int, tuple[int, ...]] = {
    2: TEMPLATE_PHASES_2,
    3: TEMPLATE_PHASES_3,
    4: TEMPLATE_PHASES_4,
    5: TEMPLATE_PHASES_5,
}
```

The same templates are mirrored in the beam search at `rules/beam.py:20`:

```python
_TEMPLATES: dict[int, tuple[int, ...]] = {
    2: (1, 4),
    3: (1, 3, 5),
    4: (1, 3, 4, 5),
    5: (1, 2, 3, 4, 5),
}
```

The 5-song template matches §5.4's default exactly: Praise → Thanksgiving → Worship → Response → Sending. The 4-song template matches the "compact Sunday" variant (skipping Thanksgiving).

---

## 10. Dead-end Songs & Relaxation Ladder (§5.5)

### Dead-end classification

§5.5 defines a dead-end song as one whose key and tempo make it nearly impossible to follow. The POC pre-classifies each song's "transition fan-out" in `rules/beam.py:32`:

```python
def compute_fan_out(pool, matrix, config) -> list[SongCandidate]:
    updated = []
    for candidate in pool:
        fan_out = 0
        for other in pool:
            if candidate.recording_hash_prefix == other.recording_hash_prefix:
                continue
            transition = matrix.get((candidate.recording_hash_prefix, other.recording_hash_prefix))
            if (
                transition
                and transition.bpm_delta <= config.h4_limit
                and (transition.cfd <= config.h5_limit or transition.suggested_key_shift != 0)
            ):
                fan_out += 1
        updated.append(
            candidate.model_copy(update={"fan_out": fan_out, "is_dead_end": fan_out == 0})
        )
    return updated
```

A song with `fan_out == 0` is marked `is_dead_end = True`.

### Dead-end placement rule

§5.5 says "place dead-end songs only at position N (last)". The beam search enforces this at `rules/beam.py:100`:

```python
if candidate.is_dead_end and position != len(target):
    continue
```

Dead-end songs are skipped for every position except the last.

### Beam search with k=8

§5.5 recommends "beam search with k = 8 beams". The POC's beam search at `rules/beam.py:69` uses `width=8` by default:

```python
def _sequences(pool, config, matrix, width: int = 8) -> Iterable[list[SongCandidate]]:
    target = _template(config.songs)
    by_hash = {candidate.recording_hash_prefix: candidate for candidate in pool}
    beams: list[list[SongCandidate]] = [[]]
    for position, target_phase in enumerate(target, start=1):
        expanded: list[list[SongCandidate]] = []
        for beam in beams:
            used = {candidate.song_id for candidate in beam}
            for candidate in pool:
                if candidate.song_id in used:
                    continue
                if position == 1:
                    if config.relax_h1:
                        if candidate.phase not in {1, 2}:
                            continue
                    elif candidate.phase != 1:
                        continue
                    if candidate.tempo_bpm is None or candidate.tempo_bpm < config.opening_floor:
                        continue
                if position == len(target):
                    if candidate.phase not in {4, 5}:
                        continue
                    if candidate.tempo_bpm is None or candidate.tempo_bpm > config.closing_limit:
                        continue
                if beam and candidate.phase < beam[-1].phase - 1:
                    continue
                if candidate.is_dead_end and position != len(target):
                    continue
                if beam:
                    # ... H4 and H5 pruning ...
                expanded.append([*beam, by_hash[candidate.recording_hash_prefix]])
        expanded.sort(key=lambda seq: (
            sum(_phase_score(item, target[index]) for index, item in enumerate(seq)),
            sum(abs((seq[index + 1].tempo_bpm or 0) - (seq[index].tempo_bpm or 0))
                for index in range(len(seq) - 1)),
            tuple(item.recording_hash_prefix for item in seq),
        ))
        beams = expanded[: max(width, 1)]
        if not beams:
            return
    yield from beams
```

The sort key prioritizes phase-match score, then tempo smoothness, then hash-prefix determinism (for reproducibility).

### Relaxation escalation ladder

§5.5 prescribes a fallback sequence when the strict template cannot be satisfied. The POC implements this as a four-tier escalation in `rules/beam.py:177`:

```python
def search(pool, config, matrix, *, width: int = 8) -> list[SongsetProposal]:
    sorted_pool = sorted(pool, key=_candidate_sort_key)
    proposals: list[SongsetProposal] = []

    # Tier 1: Standard beam search
    for sequence in _sequences(sorted_pool, config, matrix, width=width):
        proposal = _proposal_for_sequence(sequence, config, matrix)
        if validate(proposal, config, matrix, ...).passed:
            proposals.append(proposal)

    # Tier 2: Fall back to 4-song template (§5.5: "fall back to N=4 template")
    if not proposals and config.songs == 5:
        compact_config = RunConfig(**{**config.to_dict(), "songs": 4})
        for sequence in _sequences(sorted_pool, compact_config, matrix, width=width):
            proposal = _proposal_for_sequence(
                sequence, compact_config, matrix,
                warnings=["fell_back_to_4_song_template"],
            )
            if validate(...).passed:
                proposals.append(proposal)

    # Tier 3: Relax H4 (→25 BPM) and H5 (→CFD 3)
    if not proposals:
        relaxed_config = RunConfig(**{**config.to_dict(), "relax_h4": True, "relax_h5": True})
        relaxed_pool = sorted(compute_fan_out(pool, matrix, relaxed_config), key=_candidate_sort_key)
        for sequence in _sequences(relaxed_pool, relaxed_config, matrix, width=max(width * 2, 16)):
            proposal = _proposal_for_sequence(
                sequence, relaxed_config, matrix,
                warnings=["relaxed_H4_H5"],
            )
            if validate(..., relax_h4=True, relax_h5=True).passed:
                proposals.append(proposal)

    # Tier 4: auto_relax — also relax H2 (→80 BPM) and H3 (→120 BPM, or 100 intimate)
    if config.auto_relax and not proposals:
        relaxed_config = RunConfig(**{
            **config.to_dict(),
            "relax_h3_bpm": config.relax_h3_bpm if config.relax_h3_bpm is not None
                            else (100 if config.intimate else 120),
            "relax_h2_bpm": config.relax_h2_bpm if config.relax_h2_bpm is not None else 80,
            "relax_h4": True,
            "relax_h5": True,
        })
        # ... retry with doubled beam width ...

    # Tier 5: relax_h1 — drop strict phase-1 opener requirement
    if config.auto_relax and config.relax_h1 and not proposals:
        relaxed_config = RunConfig(**{
            **config.to_dict(),
            "relax_h3_bpm": ...,
            "relax_h2_bpm": ...,
            "relax_h4": True,
            "relax_h5": True,
        })
        # ... retry with relax_h1=True ...

    return rank_proposals(proposals, pool, config.top_k)
```

Each tier attaches a warning flag (`fell_back_to_4_song_template`, `relaxed_H4_H5`, `relaxed_H2_H3`, `relaxed_H1`) to the proposal's `hard_constraint_warnings` field, surfacing the relaxation to human reviewers.

### Exhaustive fallback

When the beam search fails entirely, `rules/beam.py:297` tries every combination:

```python
def exhaustive_fallback(pool, config, matrix) -> list[SongsetProposal]:
    proposals = []
    for sequence in combinations(pool, config.songs):
        proposal = _proposal_for_sequence(list(sequence), config, matrix)
        if validate(proposal, config, matrix).passed:
            proposals.append(proposal)
    return rank_proposals(proposals, pool, config.top_k)
```

---

## 11. DB Schema Mapping (§5.6)

### Read fields

The `_candidate_from_row` function at `db.py:59` maps the report's "Read from" list 1:1:

```python
def _candidate_from_row(row: tuple) -> SongCandidate:
    song = Song.from_row(row[:SONG_COLUMN_COUNT])
    recording = Recording.from_row(row[SONG_COLUMN_COUNT : SONG_COLUMN_COUNT + RECORDING_COLUMN_COUNT])
    embedding_text = row[SONG_COLUMN_COUNT + RECORDING_COLUMN_COUNT]
    embedding = parse_pgvector_text(embedding_text)
    return SongCandidate(
        song_id=song.id,
        title=song.title,
        title_pinyin=song.title_pinyin,
        composer=song.composer,
        lyricist=song.lyricist,
        album_name=song.album_name,
        album_series=song.album_series,
        recording_hash_prefix=recording.hash_prefix,
        tempo_bpm=recording.tempo_bpm,
        musical_key=recording.musical_key or song.musical_key,
        musical_mode=recording.musical_mode,
        key_confidence=recording.key_confidence,
        loudness_db=recording.loudness_db,
        lyrics_raw=song.lyrics_raw,
        song_embedding=embedding.tolist() if embedding is not None else None,
        is_hymn=song.album_series == "HYMN",
    )
```

### Write fields

The `ProposalItem` model (`models.py:61`) extends `DraftItem` and exposes every `songset_items` column the report lists:

```python
class DraftItem(BaseModel):
    position: int
    recording_hash_prefix: str
    key_shift_semitones: int = 0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: float = 0.0
    gap_beats: float = 2.0
    tempo_ratio: float = 1.0

class ProposalItem(DraftItem):
    song_id: str
    title: str
    phase: int
    themes: list[str] = Field(default_factory=list)
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    key_confidence: float | None = None
```

The `proposals.json` artifact is shaped ready for insertion into `songset_items`.

---

## 12. Agentic Layer (LLM)

When `--no-llm` is off, the POC uses LangChain's `ChatOpenAI` (`graph/llm.py:13`):

```python
def build_chat_model(config: RunConfig):
    if config.no_llm:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=config.llm_model,
        api_key=os.environ["SOW_LLM_API_KEY"],
        base_url=os.environ.get("SOW_LLM_BASE_URL"),
        temperature=0.2,
        max_retries=2,
    )
```

Structured output is requested via `with_structured_output` (`graph/llm.py:27`):

```python
def structured(chat, schema: type[SchemaT]):
    try:
        return chat.with_structured_output(schema, method="json_schema")
    except TypeError:
        return chat.with_structured_output(schema, method="function_calling")
```

### LLM plan

`lab/poc-scripts/poc/songset_constructor/graph/nodes.py:152`:

```python
def llm_plan(state: ConstructorState) -> dict:
    config = state["config"]
    injected = state.get("llm")
    planner = injected if injected is not None else structured(build_chat_model(config), SongsetDraft)
    prompt = (
        f"Select a {config.songs}-song Chinese worship set using only these hash prefixes.\n"
        f"Return exactly {config.songs} items.\n\n{_pool_prompt(state)}"
    )
    draft = planner.invoke(prompt)
    known = {candidate.recording_hash_prefix for candidate in state.get("pool", [])}
    draft, repairs = _coerce_known_hashes(draft, known)
    return {
        "current_draft": draft,
        "llm_drafts": [draft],
        "trace": _trace(state, "llm_plan", "llm_call", {"prompt": prompt, "repairs": repairs}),
    }
```

Hallucinated `recording_hash_prefix` values are auto-repaired via `difflib.get_close_matches` in `_coerce_known_hashes` (`nodes.py:136`):

```python
def _coerce_known_hashes(draft: SongsetDraft, known: set[str]) -> tuple[SongsetDraft, list[str]]:
    repairs = []
    items = []
    for item in draft.items:
        if item.recording_hash_prefix in known:
            items.append(item)
            continue
        replacement = get_close_matches(item.recording_hash_prefix, known, n=1)
        if replacement:
            repairs.append(
                f"Replaced hallucinated hash {item.recording_hash_prefix} with {replacement[0]}."
            )
            items.append(item.model_copy(update={"recording_hash_prefix": replacement[0]}))
    return draft.model_copy(update={"items": items}), repairs
```

### LLM refine

On validation failure, `llm_refine` (`nodes.py:226`) repairs the draft using the validator's `errors` and `repair_hints`:

```python
def llm_refine(state: ConstructorState) -> dict:
    config = state["config"]
    refiner = injected if injected is not None else structured(build_chat_model(config), SongsetDraft)
    feedback = state.get("feedback")
    prompt = (
        f"Repair this {config.songs}-song draft using only known hash prefixes.\n"
        f"Errors: {feedback.errors if feedback else []}\n"
        f"Hints: {feedback.repair_hints if feedback else []}\n"
        f"Prior draft: {state.get('current_draft')}\nPool:\n{_pool_prompt(state)}"
    )
    draft = refiner.invoke(prompt)
    known = {candidate.recording_hash_prefix for candidate in state.get("pool", [])}
    draft, repairs = _coerce_known_hashes(draft, known)
    iteration = int(state.get("iterations", 0) or 0) + 1
    return {
        "current_draft": draft,
        "llm_drafts": [draft],
        "iterations": iteration,
        ...
    }
```

The `route_validation` function (`nodes.py:355`) allows up to 3 refine iterations before rejecting.

### LLM judge

`--llm-judge` invokes an LLM with `JudgeRanking` structured output to annotate the top-k finalists (`nodes.py:264`):

```python
def llm_judge(state: ConstructorState) -> dict:
    config = state["config"]
    judge = injected if injected is not None else structured(build_chat_model(config), JudgeRanking)
    prompt = "Rank these finalist songsets without changing deterministic order:\n" + "\n".join(
        f"{proposal.rank}: {[item.recording_hash_prefix for item in proposal.items]}"
        for proposal in state.get("final_proposals", [])
    )
    ranking = judge.invoke(prompt)
    reasons = {
        tuple(item.recording_hash_prefixes): (item.reason, item.score)
        for item in getattr(ranking, "rankings", [])
    }
    proposals = []
    for proposal in state.get("final_proposals", []):
        key = tuple(item.recording_hash_prefix for item in proposal.items)
        reason, judge_score = reasons.get(key, (None, None))
        proposals.append(
            proposal.model_copy(update={"judge_reason": reason, "judge_score": judge_score})
        )
    return {"final_proposals": proposals, ...}
```

The prompt explicitly says "without changing deterministic order" — the judge annotates but never reorders.

### Human-in-the-loop review

`--interactive-review` uses LangGraph's `interrupt()` (`nodes.py:295`):

```python
def optional_review(state: ConstructorState) -> dict:
    proposals = state.get("final_proposals", [])
    top = proposals[0].model_dump(mode="json") if proposals else None
    decision = interrupt({"question": "Approve top proposal?", "top": top})
    action = decision.get("action", "approve")
    if action == "edit":
        # ... seed current_draft from top proposal, apply edits ...
        return {"edits": decision, "current_draft": current, ...}
    return {"approved": action == "approve", "edits": decision, ...}
```

The CLI handles the interrupt at `cli.py:370`:

```python
while "__interrupt__" in result:
    interrupt_obj = result["__interrupt__"][0]
    payload = interrupt_obj["value"] if isinstance(interrupt_obj, dict) else interrupt_obj.value
    console.print(payload)
    action = typer.prompt("Review action (approve/reject)", default="approve")
    from langgraph.types import Command
    result = _run_graph_with_traces(graph, Command(resume={"action": action}), graph_config)
```

The `--resume-thread-id` flag restarts a paused review session via the checkpointer (`graph/checkpointer.py`).

---

## 13. Deterministic Fallback

The LLM is never the only path to a result. `route_after_beam` (`nodes.py:349`) short-circuits to `finalize_rank` when (a) `--no-llm` is set or (b) the beam produced zero candidates:

```python
def route_after_beam(state: ConstructorState) -> str:
    if state["config"].no_llm or not state.get("beam_candidates"):
        return "finalize_rank"
    return "llm_plan"
```

The rule engine + beam search alone can produce ranked proposals end-to-end without any LLM call.

---

## 14. Artifacts Written Per Run

The `write_artifacts` node (`nodes.py:338`) delegates to `artifacts/writer.py:22`, which emits five files per run into `output/songset_constructor/<run_id>/`:

| File | Format | Contents |
|---|---|---|
| `proposals.json` | JSON | Final proposals with config snapshot and score breakdowns |
| `proposal_report.md` | Markdown | Human-readable table per proposal with transitions and warnings |
| `candidate_pool.csv` | CSV | Full enriched pool with phase/themes/fan_out/is_dead_end |
| `graph_trace.jsonl` | JSONL | Every node event (prompts, validator failures, repair iterations) |
| `songset_review.md` | Markdown | LLM-generated or fallback review with mandated 6-section template |

The `graph_trace.jsonl` is populated from the `trace` state field, which uses `Annotated[list, operator.add]` so events accumulate across iterations.

### Review report structure

The LLM-generated review (`writer.py:219`) follows a mandated 6-section template:

```python
def _review_prompt(payload: dict[str, Any]) -> str:
    return (
        "Write Markdown only for a human reviewer. Use exactly this section structure:\n"
        "# Songset Constructor Review\n"
        "## Key Findings\n"
        "## Run Summary\n"
        "## What Was Done\n"
        "## How Filters Were Applied\n"
        "## Proposal N for each ranked proposal\n\n"
        "Guardrails:\n"
        "- Use only facts in the payload.\n"
        "- Do not invent songs, scores, filters, validation errors, or conclusions.\n"
        "- Keep proposal tables complete and faithful.\n"
        "- Put 3-6 key-finding bullets immediately after the title.\n"
        "- Mention relaxation warnings plainly.\n"
        "- State whether proposals came from deterministic beam ranking or LLM-origin proposals.\n"
        "- Do not include raw JSON.\n\n"
        "Factual payload:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)}"
    )
```

If the LLM fails or `--no-llm` is set, a deterministic fallback (`writer.py:241`) produces the same structure from trace data.

---

## 15. Diagnostics

When no artifacts are written, the CLI produces a natural-language failure summary (`cli.py:206`). The diagnostics module (`rules/diagnostics.py`) provides three layers of insight:

### Enrichment drop diagnostics

`rules/diagnostics.py:24` identifies songs dropped for missing both tempo and key metadata:

```python
def enrichment_drop_diagnostics(pool, *, sample_limit: int = 5) -> dict:
    counts: Counter[str] = Counter()
    samples = []
    for candidate in pool:
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            counts[MISSING_TEMPO_AND_KEY_METADATA] += 1
            if len(samples) < sample_limit:
                samples.append({
                    "title": candidate.title,
                    "recording_hash_prefix": candidate.recording_hash_prefix,
                    "reason": MISSING_TEMPO_AND_KEY_METADATA,
                })
    return {"drop_reasons": dict(counts), "dropped_samples": samples}
```

### Role eligibility counts

`rules/diagnostics.py:41` counts how many songs satisfy each H-rule's role:

```python
def role_eligibility_counts(pool, config, matrix) -> dict[str, int]:
    return {
        "valid_openers_h2": sum(1 for c in pool if c.phase == 1 and (c.tempo_bpm or 0) >= opening_floor),
        "valid_closers_h3": sum(1 for c in pool if c.phase in {4, 5} and c.tempo_bpm and c.tempo_bpm <= closing_limit),
        "phase_1_candidates_h1": sum(1 for c in pool if c.phase == 1),
        "phase_3_or_4_candidates_h1": sum(1 for c in pool if c.phase in {3, 4}),
        "phase_4_or_5_candidates_h1": sum(1 for c in pool if c.phase in {4, 5}),
        "compatible_transitions_h5": sum(1 for t in matrix.values() if t.cfd <= 2),
    }
```

The CLI maps these to H-rule codes via `_ROLE_TO_RULE` (`cli.py:129`):

```python
_ROLE_TO_RULE: dict[str, str] = {
    "valid_openers_h2": "H2",
    "valid_closers_h3": "H3",
    "phase_1_candidates_h1": "H1",
    "phase_3_or_4_candidates_h1": "H1",
    "phase_4_or_5_candidates_h1": "H1",
    "compatible_transitions_h5": "H5",
}
```

### Hard-rule rejection breakdowns

`rules/diagnostics.py:99` counts which H-codes blocked the most beam sequences, at both strict and relaxed tiers:

```python
def hard_rule_rejection_counts(sequences, config, matrix, *, relax_kwargs=None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    generated = 0
    rejected = 0
    for sequence in sequences:
        generated += 1
        proposal = _proposal_for_diagnostics(sequence, config, matrix)
        feedback = validate(proposal, config, matrix, **(relax_kwargs or {}))
        if feedback.passed:
            continue
        rejected += 1
        counts.update(feedback.violated)
    return {
        "generated_sequences": generated,
        "rejected_sequences": rejected,
        "hard_rule_rejections": dict(sorted(counts.items())),
    }
```

### LLM-generated failure summary

When the LLM is available, the CLI asks it to synthesize the trace events and diagnostics into a 3-5 sentence user-facing summary (`cli.py:213`):

```python
prompt = (
    "Write a succinct, clear, but detailed user-facing summary explaining why the "
    "songset constructor produced no results. Use only the facts below. ...\n\n"
    f"Run configuration: {config.to_dict()}\n\n"
    "Trace events:\n" + "\n".join(_event_lines(result)) + "\n\n"
    "Rule-drop diagnostics:\n" f"{diagnostics}\n\n"
    "Hard rule reference:\n" f"{rule_reference}\n\n"
    f"Fallback diagnosis: {_fallback_no_results_summary(config, result)}"
)
```

---

## 16. Best-Practices Summary Table

| Report rule / best practice | Implementation anchor |
|---|---|
| 5-phase arc (Praise → Thanksgiving → Worship → Response → Sending) | `rules/beam.py:20` (`_TEMPLATES`), `rules/fitness.py:8` (`TEMPLATE_PHASES_*`) |
| 12-value Chinese theme vocabulary | `rules/themes.py:12` (`THEMES`) |
| Theme → phase mapping | `rules/phases.py:7` (`THEME_TO_PHASE`) |
| Multi-source theme classification (title 0.35, lyrics 0.25, song-emb 0.25, line-emb 0.15) | `rules/phases.py:29` (`fuse_themes`), `graph/nodes.py:57` (`enrich_pool`) |
| Seasonal bias (advent/christmas/lent/easter/pentecost) | `rules/phases.py:48` (`apply_seasonal_bias`) |
| `infer_phase()` decision function | `rules/phases.py:66` |
| Hymnal mode (传统圣诗 at final position) | `config.py:88` |
| Opening tempo ≥ 110 BPM | `config.py:103` (`opening_floor`), `hard_constraints.py:64` (H2) |
| Closing tempo ≤ 90 BPM (≤ 80 intimate) | `config.py:98` (`closing_limit`), `hard_constraints.py:67` (H3) |
| |ΔBPM| ≤ 15 default, ≤ 20 with crossfade/gap | `hard_constraints.py:71` (H4) |
| Monotonically non-increasing tempo preference | `rules/fitness.py:39` (`arc_bonus`) |
| `recordings.tempo_bpm` authoritative | `db.py:73` |
| Circle-of-Fifths Distance (CFD) algorithm | `rules/harmony.py:72` (`cfd`) |
| `key_compatibility_score()` mapping | `rules/harmony.py:79` |
| `key_shift_semitones ∈ {-2,…,+2}` minimal-shift | `rules/harmony.py:93` (`suggest_key_shift`) |
| Transition technique table (pivot/direct/relative/transposition/vamp/direct_modulation) | `rules/transitions.py:10` (`recommend_transition`) |
| CFD ≤ 2 hard gate (or crossfade or transposition) | `hard_constraints.py:80` (H5) |
| `key_confidence < 0.6` no-transpose | `hard_constraints.py:88` (H8), `rules/transitions.py:25` (warning) |
| SOP `album_series` as cataloging spine | `db.py:36` (pool query filter) |
| CPW excluded by default | `config.py:82` (`include_cpw=False`) |
| H1–H8 hard constraints | `rules/hard_constraints.py:25` (`validate`) |
| Fitness: 0.40·theme + 0.30·tempo + 0.20·harmony + 0.10·diversity | `rules/fitness.py:64` (`score`) |
| Sequence templates (2/3/4/5 songs) | `rules/beam.py:20`, `rules/fitness.py:13` |
| Beam search width k=8 | `rules/beam.py:177` (`width=8`) |
| Dead-end song → last position only | `rules/beam.py:100` |
| 5-song → 4-song fallback | `rules/beam.py:197` |
| Relax H4 to 25 BPM, H5 to CFD 3 | `config.py:109,115`, `rules/beam.py:215` |
| Auto-relax H2/H3/H4/H5/H1 escalation ladder | `rules/beam.py:235`, `rules/beam.py:261` |
| Exhaustive `combinations` fallback | `rules/beam.py:297` |
| Relaxation warnings surfaced to humans | `SongsetProposal.hard_constraint_warnings`, `proposal_report.md`, `songset_review.md` |
| Top-k proposals returned | `rules/proposals.py:86` (`rank_proposals`) |
| LLM drafts gated by hard rules (never bypass H0–H8) | `graph/nodes.py:180` (`validate_score`), `graph/nodes.py:355` (`route_validation`) |
| LLM hallucinated hash repair | `graph/nodes.py:136` (`_coerce_known_hashes`) |
| LLM refine loop (max 3 iterations) | `graph/nodes.py:226` (`llm_refine`), `graph/nodes.py:355` (`route_validation`) |
| LLM judge annotates without reordering | `graph/nodes.py:264` (`llm_judge`) |
| Human-in-the-loop interrupt (approve/edit/reject) | `graph/nodes.py:295` (`optional_review`), `cli.py:370` |
| Deterministic fallback (no LLM required) | `graph/nodes.py:349` (`route_after_beam`) |
| Artifacts: proposals.json, report.md, pool.csv, trace.jsonl, review.md | `artifacts/writer.py:22` (`write_artifacts`) |
| Diagnostics: enrichment drops, role eligibility, rejection breakdowns | `rules/diagnostics.py` |

---

*Generated 2026-07-09 as a companion to `docs/research_report_chinese_worship_songset.md`. All `file:line` references point to the POC at `lab/poc-scripts/poc/songset_constructor/`.*
