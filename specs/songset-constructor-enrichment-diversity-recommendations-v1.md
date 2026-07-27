# Spec: Enrichment Diversity Recommendations Implementation

> Detailed implementation plan for the five recommendations from
> `reports/enrichment_eval_comparison.md` (Part 3 evaluation), targeting the
> theme classification and phase distribution diversity problems identified in
> the post-Traditional-conversion enrichment eval.
>
> Reference (not to be edited):
> - `specs/songset-constructor-traditional-chinese-and-enrich-pool-eval-v1.md`
> - `reports/enrichment_eval_comparison.md`
> - `reports/enrichment_eval_post_traditional.md`

| | |
|---|---|
| **Date** | 2026-07-26 |
| **Status** | Plan — pending implementation |
| **Component** | `lab/poc-scripts/poc/songset_constructor/` |
| **Read-only** | Postgres reads only; no writes to `songsets` / `songset_items` |
| **Output** | Updated theme classification + regenerated anchors + comparison report |

---

## Overview

Five coordinated changes to the songset constructor POC's enrichment pipeline,
derived from the post-Traditional-conversion evaluation:

1. **Rec A — Expand THEME_VOCAB (HIGH):** Data-driven expansion of keyword
   vocabulary for underrepresented themes (認罪, 差遣, 復興) by analyzing the
   actual catalog lyrics for high-frequency unmatched words.
2. **Rec C — Agreement-Based Fusion Shutoff (MEDIUM-HIGH):** When title and
   lyrics keyword classifiers agree on the same top theme, reduce the embedding
   contribution to prevent unchanged embedding anchors from overriding the
   keyword signal (directly targets 敬拜 dominance).
3. **Rec B — Multi-Phase Tagging (MEDIUM):** Add a `secondary_phases` field to
   `SongCandidate` and `ProposalItem` so borderline songs can appear in multiple
   phase slots, giving the beam search access to candidates for
   underrepresented phases.
4. **Rec E — Regenerate Embedding Anchors (MEDIUM):** Rewrite the short
   bag-of-words anchor texts in `regen_theme_anchors.py` as rich Traditional
   Chinese sentences incorporating the expanded vocabulary, then regenerate
   `theme_anchors.json`.
5. **Rec D — Worship Fallback (LOW):** Deferred — zero-theme songs are already
   0/438, so a worship fallback would have minimal impact on this catalog.

---

## Recommendation Validation

The five recommendations in `reports/enrichment_eval_comparison.md` are
technically sound and correctly diagnose the issues. Validation summary:

| Rec | Priority in Report | Validated Priority | Notes |
|---|---|---|---|
| A: Expand THEME_VOCAB | HIGH | **HIGH** ✓ | Correctly identified. Vocab is sparse (5-7 terms/theme). Data-driven expansion is the right approach. |
| C: Dynamic Fusion Weights | MEDIUM | **MEDIUM-HIGH** ↑ | Undervalued in report. This directly targets the root cause (敬拜 dominance from embedding anchors). Agreement-based shutoff is simple and effective. Should be done early. |
| B: Multi-Phase Tagging | MEDIUM | **MEDIUM** ✓ | Correctly identified. Requires structural changes to `SongCandidate` and ~27 downstream `.phase` reads. Medium complexity confirmed. |
| D: Worship Fallback | LOW | **LOW** ✓ | Correctly deprioritized. Zero-theme songs already 0/438. |
| E: Regenerate Anchor Texts | MEDIUM | **MEDIUM** ✓ | Correctly identified. Anchors are short bag-of-words. Richer sentences will improve embedding classification quality. |

### Gap in the Report

`THEME_VOCAB` has only 5-7 terms per theme and each theme uses approximately
the same number of keywords. Since `_matches()` (`themes.py:30`) counts unique
term hits and `classify_lyrics_themes()` (`themes.py:44`) normalizes by the
**sum of all hits across all themes**, a theme with more keywords naturally
gets more matches, inflating its share. 敬拜's 7 terms (including the very
common `榮耀`) structurally bias toward it. The data-driven expansion should
aim for **balanced keyword counts across themes** and avoid adding many more
terms to already-dominant themes.

### User Decisions (from clarification interview)

| Decision Point | Choice |
|---|---|
| THEME_VOCAB expansion method | **Data-driven** (analyze catalog lyrics) |
| Dynamic fusion strategy | **Agreement-based shutoff** |
| Phase mapping changes | **Keep `THEME_TO_PHASE` as-is** |
| Embedding endpoint available | **Yes** (`SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`) |
| Multi-phase tagging scope | **Full multi-phase field** |
| Success criteria | **Relative improvement only** |

---

## Current State (Reference)

### Theme Classification Pipeline

```
load_catalog → enrich_pool → beam_search → validate → score → serialize
                    │
                    ├── classify_title_themes()      [keyword regex, weight 0.35]
                    ├── classify_lyrics_themes()     [keyword regex, weight 0.25]
                    ├── classify_embedding_themes()  [cosine sim, weight 0.25/0.15]
                    ├── fuse_themes()               [weighted average of active sources]
                    ├── apply_seasonal_bias()        [liturgical season boost]
                    └── infer_phase()               [dominant theme → phase 1-5]
```

### Key Files

| Component | File Path |
|---|---|
| `THEME_VOCAB` + classifiers | `lab/poc-scripts/poc/songset_constructor/rules/themes.py` |
| Fusion + phase inference | `lab/poc-scripts/poc/songset_constructor/rules/phases.py` |
| Embedding helpers | `lab/poc-scripts/poc/songset_constructor/rules/embeddings.py` |
| Beam search | `lab/poc-scripts/poc/songset_constructor/rules/beam.py` |
| Hard constraints | `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py` |
| Fitness scoring | `lab/poc-scripts/poc/songset_constructor/rules/fitness.py` |
| Proposal creation | `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` |
| Diagnostics | `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` |
| Enrichment node | `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` |
| Models | `lab/poc-scripts/poc/songset_constructor/models.py` |
| Anchor regen script | `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` |
| Anchor data | `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json` |
| Report writer | `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` |
| Enrichment report | `lab/poc-scripts/poc/songset_constructor/artifacts/enrichment_report.py` |
| Tests (rules) | `lab/poc-scripts/tests/test_songset_constructor_rules.py` |
| Tests (artifacts) | `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` |
| Tests (graph) | `lab/poc-scripts/tests/test_songset_constructor_graph.py` |

### Current Fusion Weights

| Source | Weight |
|---|---|
| Title hits | 0.35 |
| Lyrics hits | 0.25 |
| Song embedding | 0.25 |
| Line embedding | 0.15 |

### Current Theme/Phase Distribution (Post-Traditional Baseline)

| Phase | Count | % | Status |
|---|---:|---:|---|
| Phase 1 (讚美) | 70 | 16.0% | ok |
| Phase 2 (感恩) | 30 | 6.8% | underrepresented |
| Phase 3 (敬拜) | 249 | 56.8% | ok (dominant) |
| Phase 4 (奉獻) | 68 | 15.5% | ok |
| Phase 5 (差遣) | 21 | 4.8% | underrepresented |

| Underrepresented Theme | Count |
|---|---:|
| 認罪 | 3 |
| 差遣 | 3 |
| 復興 | 1 |

---

## Phase 1: Data-Driven THEME_VOCAB Expansion (Rec A)

**Goal:** Analyze the catalog's actual lyrics to find high-frequency Traditional
Chinese words that currently don't match any theme, then assign them to
appropriate themes — especially underrepresented ones (認罪, 差遣, 復興).

### Step 1.1: Build a Catalog Lyrics Analyzer Script

**New file:** `lab/poc-scripts/poc/songset_constructor/analyze_vocab_gaps.py`

- Load the real SOP.org catalog (438 songs) from the database via the existing
  DB helpers in `lab/poc-scripts/poc/songset_constructor/db.py`.
- For each song, extract `lyrics_raw` and tokenize:
  - Use CJK character bigrams + unigrams (check if `jieba` is already a
    dependency; if not, use a simple n-gram approach to avoid adding a heavy
    dependency).
  - Also extract title words (titles are short and high-signal).
- Count all bigram and unigram frequencies across the entire corpus.
- For each frequent word (>5 occurrences), check if it already matches any term
  in `THEME_VOCAB` via the existing `_matches()` function in `themes.py:30`.
- Output: a JSON report to `reports/vocab_gap_analysis.json` listing:
  - Unmatched high-frequency words, sorted by frequency
  - The songs each word appears in (for manual categorization context)
  - The current theme distribution for reference

### Step 1.2: Categorize Unmatched Words into Themes

**Review checkpoint:** User confirms the keyword assignments before proceeding.

- Review the unmatched word list from Step 1.1.
- Assign each candidate word to one of the 12 themes based on theological
  meaning.
- Prioritize words for underrepresented themes (認罪, 差遣, 復興).
- **Target keyword counts per theme:**
  - Underrepresented themes (認罪, 差遣, 復興): expand to 10-12 terms
  - Mid-range themes (感恩, 奉獻, 聖靈, 十字架, 跟隨, 祈禱): maintain at 8-10 terms
  - Dominant themes (讚美, 敬拜, 信心): do not expand beyond current 6-7 terms
    (to avoid amplifying their dominance)
- Ensure **balanced keyword counts across themes** to avoid the structural bias
  where themes with more terms naturally win in `classify_lyrics_themes()`.
- Keep all terms in Traditional Chinese + English + Pinyin.
- Do not add the same term to multiple themes (avoids ambiguous classification).

### Step 1.3: Update THEME_VOCAB

**File:** `lab/poc-scripts/poc/songset_constructor/rules/themes.py:14-27`

- Add the new terms as tuples, maintaining existing formatting style.
- Keep all existing terms (additive change, no removals).
- Example expansion (illustrative — actual terms depend on Step 1.1 analysis):

```python
THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "讚美": ("讚美", "歌唱", "歡呼", "hallelujah", "praise", "zan mei"),  # unchanged
    # ...
    "認罪": ("認罪", "悔改", "赦免", "潔淨", "罪孽", "洗淨", "軟弱", "虧欠",
             "forgive", "repent", "ren zui"),  # +4 new terms
    # ...
    "復興": ("復興", "更新", "燃燒", "覺醒", "澆灌", "甦醒",
             "revival", "renew", "fu xing"),  # +3 new terms
    # ...
}
```

### Step 1.4: Update Anchor Texts (for Phase 4 regeneration)

**File:** `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py:16-29`

- Add the new keywords to each theme's `ANCHOR_TEXTS` entry.
- These will be regenerated as full sentences in Phase 4.

### Files Touched

| File | Change |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/rules/themes.py` | Expand `THEME_VOCAB` dict |
| `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` | Update `ANCHOR_TEXTS` dict |
| `lab/poc-scripts/poc/songset_constructor/analyze_vocab_gaps.py` | **NEW** — analysis script |
| `reports/vocab_gap_analysis.json` | **NEW** — analysis output |

### Verification

- Run `analyze_vocab_gaps.py` and confirm the unmatched word list is reasonable.
- After updating `THEME_VOCAB`, run existing tests:
  ```bash
  uv run --project lab/poc-scripts --extra test pytest tests/test_songset_constructor_rules.py -v
  ```
- Confirm `test_theme_fusion_and_phase_inference` still passes (additive change
  should not break existing behavior).

---

## Phase 2: Agreement-Based Fusion Shutoff (Rec C)

**Goal:** When title and lyrics keyword classifiers agree on the same top theme,
reduce the embedding contribution to prevent overwriting the keyword signal
(directly targets 敬拜 dominance from unchanged embedding anchors).

### Step 2.1: Add Agreement Detection Helper

**File:** `lab/poc-scripts/poc/songset_constructor/rules/phases.py`

Add a private helper function:

```python
def _top_themes_agree(title: dict[str, float], lyrics: dict[str, float]) -> bool:
    """Return True when title and lyrics share the same top non-zero theme."""
    def _top(scores: dict[str, float]) -> str | None:
        if not scores or max(scores.values(), default=0.0) <= 0:
            return None
        return max(scores.items(), key=lambda item: (item[1], item[0]))[0]
    t, l = _top(title), _top(lyrics)
    return t is not None and t == l
```

- Returns `True` only when both title and lyrics have a non-zero top theme and
  they match.
- Returns `False` if either source has all-zero values (no keyword hits).

### Step 2.2: Modify `fuse_themes()` for Dynamic Weights

**File:** `lab/poc-scripts/poc/songset_constructor/rules/phases.py:23-45`

Current implementation uses fixed weights:

```python
weighted_sources = [
    (0.35, title),
    (0.25, lyrics),
    (0.25, song_emb),
    (0.15, line_emb),
]
```

New implementation with agreement-based shutoff:

```python
def fuse_themes(
    title: dict[str, float],
    lyrics: dict[str, float],
    song_emb: dict[str, float],
    line_emb: dict[str, float],
) -> dict[str, float]:
    if _top_themes_agree(title, lyrics):
        # Title and lyrics agree — trust keywords, reduce embedding influence
        weighted_sources = [
            (0.45, title),
            (0.35, lyrics),
            (0.15, song_emb),
            (0.05, line_emb),
        ]
    else:
        # No agreement — use standard weights
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

**Rationale for reduced weights:**
- When keywords agree, title weight increases 0.35→0.45 and lyrics 0.25→0.35
  (total keyword weight 0.60→0.80).
- Song embedding drops 0.25→0.15 and line embedding 0.15→0.05 (total embedding
  weight 0.40→0.20).
- Embeddings are not zeroed (preserves some contribution for songs where
  embeddings capture nuance the keywords miss), but their influence is
  significantly reduced.

### Step 2.3: Add Tests

**File:** `lab/poc-scripts/tests/test_songset_constructor_rules.py`

Add two test functions:

**`test_fuse_themes_reduces_embedding_when_title_lyrics_agree`:**
- Construct title and lyrics score dicts where both agree on "認罪" as top theme.
- Construct embedding score dicts where "敬拜" is dominant.
- Verify: fused top theme is "認罪" (not "敬拜").
- Verify: the fused score for "認罪" is higher than it would be with standard
  weights.

**`test_fuse_themes_preserves_embedding_when_title_lyrics_disagree`:**
- Construct title/lyrics where top themes differ (title="讚美", lyrics="敬拜").
- Construct embedding where "敬拜" is dominant.
- Verify: embedding still has full 0.25/0.15 weight and can influence the
  result (fused top theme should be "敬拜" due to embedding + lyrics agreement).

### Files Touched

| File | Change |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/rules/phases.py` | Add `_top_themes_agree()`, modify `fuse_themes()` |
| `lab/poc-scripts/tests/test_songset_constructor_rules.py` | Add 2 new tests |

### Verification

```bash
uv run --project lab/poc-scripts --extra test pytest tests/test_songset_constructor_rules.py -v -k "fuse_themes"
```

---

## Phase 3: Multi-Phase Tagging (Rec B)

**Goal:** Allow borderline songs to appear in multiple phases, giving the beam
search access to candidates for underrepresented phases.

**Blast radius:** 27 `.phase` reads across 7 files. The "full multi-phase field"
approach requires adding a secondary phases list and updating all consumers.

### Step 3.1: Add `secondary_phases` Field to Models

**File:** `lab/poc-scripts/poc/songset_constructor/models.py`

Add to `SongCandidate` (after line 26):

```python
class SongCandidate(BaseModel):
    # ... existing fields ...
    phase: int = 0
    secondary_phases: list[int] = Field(default_factory=list)  # NEW
    fan_out: int = 0
    # ...
```

Add to `ProposalItem` (after line 64):

```python
class ProposalItem(DraftItem):
    song_id: str
    title: str
    phase: int
    secondary_phases: list[int] = Field(default_factory=list)  # NEW
    themes: list[str] = Field(default_factory=list)
    # ...
```

The primary `phase` field remains as-is (backward compatible). The
`secondary_phases` list is empty by default, so all existing code that only
reads `phase` continues to work.

### Step 3.2: Compute Secondary Phases During Enrichment

**File:** `lab/poc-scripts/poc/songset_constructor/rules/phases.py`

Add a new function:

```python
def infer_secondary_phases(
    fused: dict[str, float],
    primary_phase: int,
    tempo_bpm: float | None = None,
    threshold: float = 0.85,
    max_secondary: int = 2,
) -> list[int]:
    """Return additional phases for borderline songs.

    A theme qualifies as secondary if its fused score is >= threshold * max_score
    AND its phase differs from the primary phase.
    """
    if not fused or max(fused.values(), default=0.0) <= 0:
        return []
    max_score = max(fused.values())
    if max_score <= 0:
        return []
    ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    secondary: list[int] = []
    seen_phases = {primary_phase}
    for theme, score in ranked:
        if score < threshold * max_score:
            break
        phase = THEME_TO_PHASE.get(theme, 3)
        if theme == "聖靈" and tempo_bpm is not None and tempo_bpm < 70:
            phase = 4
        if phase not in seen_phases:
            secondary.append(phase)
            seen_phases.add(phase)
        if len(secondary) >= max_secondary:
            break
    return secondary
```

**Threshold rationale:** `0.85` means a secondary theme must score at ≥85% of
the top theme's fused score. This is selective enough to avoid diluting every
song across many phases, but permissive enough to capture genuinely borderline
songs (e.g., a song with 0.45 敬拜 and 0.42 信心 would qualify 信心 as secondary).

### Step 3.3: Update Enrichment Node

**File:** `lab/poc-scripts/poc/songset_constructor/graph/nodes.py:66-71`

Current:

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

New:

```python
primary_phase = infer_phase(fused, candidate.tempo_bpm)
secondary = infer_secondary_phases(fused, primary_phase, candidate.tempo_bpm)
enriched.append(
    candidate.model_copy(
        update={
            "themes": fused,
            "phase": primary_phase,
            "secondary_phases": secondary,
            "is_hymn": candidate.album_series == "HYMN",
        }
    )
)
```

### Step 3.4: Update Beam Search

**File:** `lab/poc-scripts/poc/songset_constructor/rules/beam.py`

Add a helper function:

```python
def _phase_matches(candidate: SongCandidate, acceptable: set[int]) -> bool:
    """Return True if candidate's primary or secondary phase is in acceptable."""
    return candidate.phase in acceptable or any(p in acceptable for p in candidate.secondary_phases)
```

Update the following locations:

**Line 59 (`_candidate_sort_key`):** No change needed (sorts by primary phase
for deterministic ordering; secondary phases don't affect sort order).

**Line 65-66 (`_phase_score`):**

```python
def _phase_score(candidate: SongCandidate, target_phase: int) -> float:
    if candidate.phase == target_phase or target_phase in candidate.secondary_phases:
        return 0.0
    return abs((candidate.phase or 3) - target_phase)
```

**Line 148 (opener filter, relaxed H1):**

```python
if not _phase_matches(candidate, {1, 2}):
```

**Line 150 (opener filter, strict):**

```python
elif not _phase_matches(candidate, {1}):
```

**Line 155 (closer filter):**

```python
if not _phase_matches(candidate, {4, 5}):
```

**Line 159 (H7 phase-arc guard):**

```python
prev_phase = beam[-1].phase
if candidate.phase < prev_phase - 1 and not any(
    p >= prev_phase - 1 for p in candidate.secondary_phases
):
```

### Step 3.5: Update Hard Constraints

**File:** `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py`

**Line 35:** Expand to collect all phases per item:

```python
item_phases: list[set[int]] = []
for item in proposal.items:
    phases = {item.phase}
    phases.update(item.secondary_phases)
    item_phases.append(phases)
```

**H1 (lines 54-59):** Update to check multi-phase membership:

```python
# Relaxed H1: closer must be phase 4 or 5
if not (item_phases[-1] & {4, 5}):
    h1_failed = True
# Strict H1: exactly one phase-1 opener, middle has phase 3 or 4, closer is 4 or 5
if sum(1 for phases in item_phases if 1 in phases) != 1:
    h1_failed = True
if not any(phases & {3, 4} for phases in item_phases[1:-1]):
    h1_failed = True
if not (item_phases[-1] & {4, 5}):
    h1_failed = True
```

**H7 (line 86):** Update to check if any phase combination connects:

```python
def _phases_connect(left_phases: set[int], right_phases: set[int]) -> bool:
    """Return True if right has a phase >= some left phase - 1."""
    return any(r >= l - 1 for l in left_phases for r in right_phases)

# In H7 check:
if not _phases_connect(item_phases[i], item_phases[i + 1]):
    # H7 violation
```

### Step 3.6: Update Fitness Scoring

**File:** `lab/poc-scripts/poc/songset_constructor/rules/fitness.py:28`

Current:

```python
abs((item.phase or 3) - template[index])
```

New:

```python
if template[index] == item.phase or template[index] in item.secondary_phases:
    phase_distance = 0.0
else:
    phase_distance = abs((item.phase or 3) - template[index])
```

### Step 3.7: Update Proposal Creation

**File:** `lab/poc-scripts/poc/songset_constructor/rules/proposals.py`

**Line 27 (`item_from_candidate`):**

```python
phase=candidate.phase,
secondary_phases=candidate.secondary_phases,
```

**Line 64 (`proposal_from_draft`):**

```python
phase=candidate.phase,
secondary_phases=candidate.secondary_phases,
```

### Step 3.8: Update Diagnostics

**File:** `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py`

**Lines 50, 55, 59-61:** Update counts to include secondary phases:

```python
# Valid opener count (phase 1 primary or secondary)
valid_openers = sum(
    1 for c in pool if c.phase == 1 or 1 in c.secondary_phases
)
# Valid closer count (phase 4 or 5, primary or secondary)
valid_closers = sum(
    1 for c in pool if c.phase in {4, 5} or any(p in {4, 5} for p in c.secondary_phases)
)
# Phase-1 count
phase_1_count = sum(1 for c in pool if c.phase == 1 or 1 in c.secondary_phases)
# Phase-3-or-4 count
phase_3_4_count = sum(
    1 for c in pool if c.phase in {3, 4} or any(p in {3, 4} for p in c.secondary_phases)
)
# Phase-4-or-5 count
phase_4_5_count = sum(
    1 for c in pool if c.phase in {4, 5} or any(p in {4, 5} for p in c.secondary_phases)
)
```

### Step 3.9: Update Serialization/Reporting

**File:** `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py`

**Line 86 (arc narrative):** No change needed (uses primary phase for the arc
narrative, which is the dominant theme's phase).

**Line 131 (structured data for LLM):** Include secondary phases:

```python
phase_str = f"phase {item.phase}"
if item.secondary_phases:
    phase_str += f" (also: {', '.join(str(p) for p in sorted(item.secondary_phases))})"
f"  {item.position}. {item.title} | {phase_str} |"
```

**Line 237 (diversity metrics):** Include secondary phases:

```python
unique_phases = set()
for p in proposals:
    for item in p.items:
        unique_phases.add(item.phase)
        unique_phases.update(item.secondary_phases)
```

**Line 494 (Markdown table):** Include secondary phases in the phase column:

```python
phase_display = str(item.phase)
if item.secondary_phases:
    phase_display += f" (+{','.join(str(p) for p in sorted(item.secondary_phases)})"
f"| {item.position} | {item.title} | {phase_display} |"
```

**Line 553 (CSV pool writer):** Add a `secondary_phases` column:

```python
headers = [..., "phase", "secondary_phases", ...]
writer.writerow({
    ...,
    "phase": candidate.phase,
    "secondary_phases": ";".join(str(p) for p in sorted(candidate.secondary_phases)),
    ...
})
```

**Line 712, 740 (key findings / candidate pool summary):** Include secondary
phase counts:

```python
phase_counts = Counter(candidate.phase for candidate in pool)
secondary_phase_counts: Counter[int] = Counter()
for c in pool:
    for p in c.secondary_phases:
        secondary_phase_counts[p] += 1
# Report both primary and secondary counts
```

**Line 871 (fallback review report table):** Include secondary phases:

```python
phase_str = str(item.phase)
if item.secondary_phases:
    phase_str += f" (+{','.join(str(p) for p in sorted(item.secondary_phases)})"
```

**Note:** JSON serialization of `ProposalItem` via `model_dump(mode="json")`
(line 455, 632) will automatically include `secondary_phases` since it's a
pydantic field — no explicit change needed.

### Step 3.10: Update Enrichment Report

**File:** `lab/poc-scripts/poc/songset_constructor/artifacts/enrichment_report.py:85`

Add secondary phase counting alongside primary:

```python
phase_counts: Counter[int] = Counter(candidate.phase for candidate in pool)
secondary_phase_counts: Counter[int] = Counter()
for c in pool:
    for p in c.secondary_phases:
        secondary_phase_counts[p] += 1
```

Report both in the enrichment report output.

### Step 3.11: Update LLM Prompt Builder

**File:** `lab/poc-scripts/poc/songset_constructor/graph/nodes.py:171`

Current:

```python
f"..., phase {candidate.phase},"
```

New:

```python
phase_str = f"phase {candidate.phase}"
if candidate.secondary_phases:
    phase_str += f" (also: {', '.join(str(p) for p in sorted(candidate.secondary_phases))})"
f"..., {phase_str},"
```

### Step 3.12: Add Tests

**File:** `lab/poc-scripts/tests/test_songset_constructor_rules.py`

Add test functions:

**`test_infer_secondary_phases_returns_empty_for_dominant_theme`:**
- Construct fused dict where one theme dominates (score 1.0, next is 0.3).
- Verify: `infer_secondary_phases()` returns `[]`.

**`test_infer_secondary_phases_returns_near_secondary_when_close`:**
- Construct fused dict where top theme scores 0.45 and second theme (different
  phase) scores 0.42 (>= 0.85 * 0.45 = 0.3825).
- Verify: `infer_secondary_phases()` returns the second theme's phase.

**`test_infer_secondary_phases_respects_threshold`:**
- Construct fused dict where second theme scores 0.35 (< 0.85 * 0.45).
- Verify: `infer_secondary_phases()` returns `[]`.

**`test_infer_secondary_phases_caps_at_max_secondary`:**
- Construct fused dict with 4 themes all above threshold, all different phases.
- Verify: returns at most 2 secondary phases.

**`test_beam_search_uses_secondary_phase_for_opener`:**
- Construct a pool where the only phase-1-capable candidate has primary phase 3
  but secondary phase 1.
- Verify: beam search accepts it as an opener (relaxed H1).

**`test_beam_search_uses_secondary_phase_for_closer`:**
- Construct a pool where the only phase-5-capable candidate has primary phase 3
  but secondary phase 5.
- Verify: beam search accepts it as a closer.

**`test_h7_allows_secondary_phase_connection`:**
- Construct a sequence where consecutive items' primary phases violate H7
  (e.g., 5 → 2) but the second item has secondary phase 4.
- Verify: H7 passes.

**`test_proposal_item_includes_secondary_phases`:**
- Construct a `ProposalItem` with secondary phases.
- Verify: `model_dump()` includes the field.

**File:** `lab/poc-scripts/tests/test_songset_constructor_artifacts.py`

**`test_csv_pool_writer_includes_secondary_phases_column`:**
- Write a pool with candidates that have secondary phases.
- Verify: CSV has a `secondary_phases` column with semicolon-separated values.

### Files Touched

| File | Change |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/models.py` | Add `secondary_phases` to `SongCandidate` and `ProposalItem` |
| `lab/poc-scripts/poc/songset_constructor/rules/phases.py` | Add `infer_secondary_phases()` |
| `lab/poc-scripts/poc/songset_constructor/rules/beam.py` | Add `_phase_matches()`, update 5 phase checks |
| `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py` | Update H1, H7 for multi-phase |
| `lab/poc-scripts/poc/songset_constructor/rules/fitness.py` | Update `f_theme` phase distance |
| `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` | Pass `secondary_phases` in 2 locations |
| `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` | Update 4 count checks |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | Update 8 serialization locations |
| `lab/poc-scripts/poc/songset_constructor/artifacts/enrichment_report.py` | Add secondary phase counts |
| `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` | Compute secondary phases in enrichment, update LLM prompt |
| `lab/poc-scripts/tests/test_songset_constructor_rules.py` | Add 8 new tests |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | Add 1 new test |

### Verification

```bash
uv run --project lab/poc-scripts --extra test pytest tests/ -v
```

Run incrementally after each sub-step to catch regressions early:
1. After Step 3.1 (models): run all tests — should pass (additive field).
2. After Step 3.2-3.3 (inference + enrichment): run rules tests.
3. After Step 3.4 (beam): run beam search tests.
4. After Step 3.5 (constraints): run constraint tests.
5. After Step 3.6-3.11 (fitness, proposals, diagnostics, writer, report, prompt):
   run all tests.
6. After Step 3.12 (new tests): run all tests including new ones.

---

## Phase 4: Regenerate Embedding Anchors (Rec E)

**Goal:** Regenerate `theme_anchors.json` with richer, longer Traditional Chinese
anchor texts incorporating the expanded vocabulary from Phase 1.

### Step 4.1: Rewrite Anchor Texts

**File:** `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py:16-29`

Replace short bag-of-words with rich sentence descriptions (3-4 sentences per
theme), incorporating:
- The expanded `THEME_VOCAB` keywords from Phase 1.
- Theological context and typical worship language.
- Full sentences rather than word lists — embedding models capture semantic
  meaning better from sentences.

Example (illustrative — actual texts depend on Phase 1 expansion):

```python
ANCHOR_TEXTS = {
    "讚美": (
        "我們要讚美耶和華，用歡呼歌唱讚美他的名。"
        "哈利路亞，萬民都要來讚美主。"
        "Praise the Lord with joyful songs, hallelujah."
    ),
    "認罪": (
        "主啊，我認罪悔改，求你赦免我的罪孽，用寶血洗淨我一切的軟弱。"
        "我在你面前承認我的虧欠，求你潔淨我的心。"
        "Lord, I confess and repent, forgive my sins and cleanse me."
    ),
    "復興": (
        "求主復興你的教會，更新我們的心，澆灌你的靈。"
        "願聖靈甦醒我們，覺醒我們沉睡的靈，燃燒復興的火。"
        "Revive and renew us, pour out your Spirit, awaken our souls."
    ),
    # ... etc for all 12 themes
}
```

### Step 4.2: Run the Regeneration Script

**Prerequisites:** Environment variables must be set:
- `SOW_EMBEDDING_API_KEY`
- `SOW_EMBEDDING_BASE_URL`
- `SOW_EMBEDDING_MODEL` (optional, defaults to `text-embedding-3-small`)

**Command:**

```bash
uv run --project lab/poc-scripts python -m poc.songset_constructor.regen_theme_anchors
```

**Verification:**
- Output file `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json`
  has `"dim": 1536`.
- All 12 themes are present in the `"anchors"` dict.
- The new vectors are different from the old ones (spot-check a few values).

### Step 4.3: Commit the New Anchor File

**File:** `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json`

This is a committed artifact (single-line JSON). The regeneration script writes
it with `ensure_ascii=False` and compact separators.

### Files Touched

| File | Change |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` | Rewrite `ANCHOR_TEXTS` with rich sentences |
| `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json` | Regenerated vectors (committed artifact) |

### Verification

```bash
uv run --project lab/poc-scripts --extra test pytest tests/ -v
```

Confirm that embedding-based tests still pass (the vectors changed, but the
classification logic is the same — cosine similarity to anchors).

---

## Phase 5: Validation & Measurement

**Goal:** Re-run the enrichment eval and verify relative improvement against the
post-Traditional baseline.

### Step 5.1: Run Enrichment Eval

```bash
uv run --project lab/poc-scripts --extra admin sow-admin songset construct \
  --only-evaluate-pool-enrichment --pool-limit 500
```

Save the output report to `reports/enrichment_eval_post_recommendations.md`.

### Step 5.2: Compare Against Post-Traditional Baseline

Compare phase distribution, theme dominance, signal coverage, and diversity
metrics against `reports/enrichment_eval_post_traditional.md`.

**Relative improvement criteria (per user decision — no hard numeric targets):**

| Metric | Direction | Rationale |
|---|---|---|
| Phase 3 (敬拜) count | ↓ or stable | Rec C reduces embedding influence; Rec A expands other themes |
| Underrepresented themes (認罪, 差遣, 復興) | ↑ | Rec A adds keywords for these themes |
| Phase 2 (感恩) count | ↑ | Rec A may add keywords |
| Phase 5 (差遣) count | ↑ | Rec A + Rec B (multi-phase) |
| Phase entropy | ↑ | Improved balance |
| Theme entropy | ↑ or stable | More themes getting matches |
| Title hits | ↑ | Expanded vocab |
| Lyrics hits | ↑ | Expanded vocab |
| Secondary phase coverage | > 0 (new metric) | Rec B enables multi-phase tagging |

### Step 5.3: Write Comparison Report

**New file:** `reports/enrichment_eval_post_recommendations_comparison.md`

Same format as the existing `reports/enrichment_eval_comparison.md`:
- Pool Overview table
- Phase Distribution table (Post-Traditional vs Post-Recommendations)
- Theme Dominance table
- Signal Coverage table
- Diversity table
- Analysis section (what improved, what didn't, root cause)
- Next steps

### Step 5.4: Run Full Test Suite

```bash
uv run --project lab/poc-scripts --extra test pytest tests/ -v
```

- Verify all existing tests pass.
- Verify all new tests pass.
- Check for any regressions in beam search determinism
  (`test_beam_search_is_deterministic`).

### Files Touched

| File | Change |
|---|---|
| `reports/enrichment_eval_post_recommendations.md` | **NEW** — eval output |
| `reports/enrichment_eval_post_recommendations_comparison.md` | **NEW** — comparison report |

---

## Recommendation D: Worship Fallback (Deferred)

**Status:** Not implemented.

**Rationale:** Zero-theme songs are 0/438 in the post-Traditional baseline. All
songs get a theme assignment via title, lyrics, or embeddings. A worship
fallback (defaulting to 敬拜 when no themes match) would have minimal impact on
this catalog. It may help for future songs with unusual vocabulary, but is not
worth the implementation effort at this time.

If needed in the future, the implementation would be:
- In `infer_phase()` (`rules/phases.py:66`), add a final fallback before the
  tempo-only fallback: if no themes match, default to phase 3 (敬拜) rather than
  using tempo.
- This is already the current behavior (the tempo fallback defaults to phase 3
  when `tempo_bpm is None`), so the change would be minimal.

---

## Execution Order & Dependencies

```
Phase 1 (Vocab Expansion) ────┐
                               ├──> Phase 4 (Regenerate Anchors, needs Phase 1 keywords)
Phase 2 (Fusion Shutoff) ────┤
                               ├──> Phase 5 (Validation & Measurement)
Phase 3 (Multi-Phase) ───────┘
```

- **Phases 1, 2, and 3 are independent** of each other and can be developed in
  parallel.
- **Phase 4 depends on Phase 1** (needs the expanded vocab for anchor texts).
- **Phase 5 runs after all phases complete.**

### Recommended Implementation Order

1. **Phase 1** (Vocab Expansion) — lowest risk, highest expected impact on
   underrepresented themes. Review checkpoint before committing.
2. **Phase 2** (Fusion Shutoff) — medium complexity, targets 敬拜 dominance
   directly. Can be done in parallel with Phase 1.
3. **Phase 4** (Regenerate Anchors) — depends on Phase 1. Quick to execute
   once env vars are set.
4. **Phase 3** (Multi-Phase Tagging) — highest blast radius (11 files, 27
   usage points). Done last before validation so Phases 1+2 can be validated
   independently first if needed.
5. **Phase 5** (Validation & Measurement) — runs after all phases complete.

**Alternative order (if validating incrementally):**
1. Phase 1 → Phase 5 (interim) → Phase 2 → Phase 5 (interim) → Phase 4 →
   Phase 3 → Phase 5 (final)

This allows measuring the impact of each recommendation independently, but
requires more eval runs.

---

## Risk Assessment

| Phase | Risk | Mitigation |
|---|---|---|
| 1: Vocab Expansion | **LOW** — additive change, existing keywords preserved | Review checkpoint before committing; run existing tests |
| 2: Fusion Shutoff | **MEDIUM** — changes core classification behavior | Unit tests verify both agreement and disagreement paths; compare eval before/after |
| 3: Multi-Phase | **HIGH** — touches 11 files, 27 usage points | Implement incrementally: model first, then beam, then constraints, then reports. Run tests after each sub-step. |
| 4: Anchor Regen | **LOW** — simple script execution, committed artifact | Verify dimensionality and vector uniqueness; run embedding tests |
| 5: Validation | **NONE** — read-only measurement | N/A |

### Key Risks for Phase 3 (Multi-Phase Tagging)

1. **Beam search determinism:** Adding secondary phases changes which
   candidates are eligible for each position. The beam search must remain
   deterministic (`test_beam_search_is_deterministic` must still pass).
   - Mitigation: Secondary phases are computed deterministically from fused
     scores; no randomness introduced.

2. **Constraint validation correctness:** H1 and H7 checks must correctly
   handle multi-phase membership without becoming too permissive.
   - Mitigation: H1 still requires exactly one phase-1 opener (primary or
     secondary); H7 still requires forward phase progression (via any phase
     combination).

3. **Serialization backward compatibility:** Existing JSON artifacts that
   don't have `secondary_phases` must still deserialize correctly.
   - Mitigation: Pydantic field has `default_factory=list`, so missing fields
     default to empty list.

4. **Report readability:** Adding secondary phases to Markdown/CSV reports
   must not clutter the output.
   - Mitigation: Secondary phases shown as `3 (+2,5)` notation — compact and
     clear.

---

## Summary of All Files Touched

### New Files

| File | Purpose |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/analyze_vocab_gaps.py` | Catalog lyrics analyzer script |
| `reports/vocab_gap_analysis.json` | Analysis output |
| `reports/enrichment_eval_post_recommendations.md` | Post-implementation eval report |
| `reports/enrichment_eval_post_recommendations_comparison.md` | Comparison report |

### Modified Files

| File | Phase(s) |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/rules/themes.py` | 1 |
| `lab/poc-scripts/poc/songset_constructor/rules/phases.py` | 2, 3 |
| `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` | 1, 4 |
| `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json` | 4 |
| `lab/poc-scripts/poc/songset_constructor/models.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/rules/beam.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/rules/fitness.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | 3 |
| `lab/poc-scripts/poc/songset_constructor/artifacts/enrichment_report.py` | 3 |
| `lab/poc-scripts/tests/test_songset_constructor_rules.py` | 2, 3 |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | 3 |

**Total: 4 new files + 15 modified files = 19 files.**
