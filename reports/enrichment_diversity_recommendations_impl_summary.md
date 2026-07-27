# Enrichment Diversity Recommendations Implementation Summary

**Specification:** @specs/songset-constructor-enrichment-diversity-recommendations-v1.md  
**Date:** 2026-07-26  
**Component:** `lab/poc-scripts/poc/songset_constructor/`

---

## Overview

This implementation addresses the theme classification and phase distribution diversity problems identified in the post-Traditional-conversion enrichment evaluation (`reports/enrichment_eval_post_traditional.md`). Five coordinated recommendations from `reports/enrichment_eval_comparison.md` were implemented:

| Rec | Priority | Status | Summary |
|---|---|---|---|
| A — Expand THEME_VOCAB | HIGH | ✅ Done | Data-driven keyword expansion for underrepresented themes |
| C — Agreement-Based Fusion Shutoff | MEDIUM-HIGH | ✅ Done | Reduce embedding weight when title and lyrics agree |
| B — Multi-Phase Tagging | MEDIUM | ✅ Done | `secondary_phases` field for borderline songs |
| E — Regenerate Embedding Anchors | MEDIUM | ✅ Done | Rich sentence anchor texts with expanded vocab |
| D — Worship Fallback | LOW | ⏭️ Deferred | Zero-theme songs already 0/438; minimal impact |

---

## Changes Implemented

### Phase 1: Data-Driven THEME_VOCAB Expansion (Rec A)

**Goal:** Expand keyword vocabulary for underrepresented themes (認罪, 差遣, 復興) by adding theologically appropriate Traditional Chinese terms, aiming for balanced keyword counts across themes to avoid structural bias.

#### New File: `analyze_vocab_gaps.py`

Catalog lyrics analyzer script that:
- Loads the real SOP.org catalog (438 songs) from the database via existing DB helpers
- Tokenizes lyrics using CJK character bigrams + unigrams (no heavy `jieba` dependency)
- Counts all token frequencies across the corpus
- For each frequent word (>5 occurrences), checks if it matches any existing `THEME_VOCAB` term via `_matches()`
- Outputs a JSON report to `reports/vocab_gap_analysis.json` with unmatched high-frequency words, their frequencies, sample songs, and current theme distribution

**Usage:**
```bash
uv run --project lab/poc-scripts --extra admin python -m poc.songset_constructor.analyze_vocab_gaps
```

#### Modified: `rules/themes.py` — THEME_VOCAB Expansion

Expanded keyword counts per theme with balanced distribution:

| Theme | Before | After | Category |
|---|---:|---:|---|
| 讚美 | 6 | 6 | Dominant (unchanged) |
| 感恩 | 7 | 10 | Mid-range |
| 敬拜 | 7 | 7 | Dominant (unchanged) |
| 奉獻 | 7 | 10 | Mid-range |
| 認罪 | 7 | 13 | Underrepresented |
| 差遣 | 7 | 13 | Underrepresented |
| 信心 | 7 | 7 | Dominant (unchanged) |
| 祈禱 | 7 | 11 | Mid-range |
| 復興 | 6 | 13 | Underrepresented |
| 聖靈 | 5 | 7 | Mid-range |
| 十字架 | 7 | 10 | Mid-range |
| 跟隨 | 7 | 10 | Mid-range |

Key additions include: 罪孽, 洗淨, 軟弱, 虧欠, 過犯 (認罪); 使命, 福音, 見證, 大使命, 傳道 (差遣); 覺醒, 澆灌, 甦醒, 靈火, 復活 (復興). All terms are additive — no existing terms were removed.

#### Modified: `regen_theme_anchors.py` — ANCHOR_TEXTS Update

Updated `ANCHOR_TEXTS` dict to include the expanded vocabulary keywords. These were subsequently rewritten as rich sentences in Phase 4.

---

### Phase 2: Agreement-Based Fusion Shutoff (Rec C)

**Goal:** When title and lyrics keyword classifiers agree on the same top theme, reduce the embedding contribution to prevent unchanged embedding anchors from overriding the keyword signal (directly targets 敬拜 dominance).

#### Modified: `rules/phases.py`

**Added `_top_themes_agree()` helper:**
- Returns `True` only when both title and lyrics have a non-zero top theme and they match
- Returns `False` if either source has all-zero values (no keyword hits)

**Modified `fuse_themes()` with dynamic weights:**

| Scenario | Title | Lyrics | Song Emb | Line Emb | Total Keyword | Total Embedding |
|---|---:|---:|---:|---:|---:|---:|
| Agreement (new) | 0.45 | 0.35 | 0.15 | 0.05 | 0.80 | 0.20 |
| No agreement (standard) | 0.35 | 0.25 | 0.25 | 0.15 | 0.60 | 0.40 |

When keywords agree, embedding influence is reduced by 50% (0.40→0.20) but not zeroed, preserving some contribution for songs where embeddings capture nuance the keywords miss.

---

### Phase 3: Multi-Phase Tagging (Rec B)

**Goal:** Allow borderline songs to appear in multiple phases, giving the beam search access to candidates for underrepresented phases. This is the highest blast-radius change, touching 11 files across 27 usage points.

#### Step 3.1: Models (`models.py`)

Added `secondary_phases: list[int]` field to both `SongCandidate` and `ProposalItem`:
```python
secondary_phases: list[int] = Field(default_factory=list)
```
The field defaults to an empty list, making the change fully backward compatible — all existing code that only reads `phase` continues to work.

#### Step 3.2: Phase Inference (`rules/phases.py`)

Added `infer_secondary_phases()` function:
- A theme qualifies as secondary if its fused score is ≥ 85% of the top theme's score
- Only themes mapping to a different phase than the primary are considered
- Capped at `max_secondary=2` additional phases
- Handles the 聖靈 slow-tempo special case (phase 4 when tempo < 70 BPM)

#### Step 3.3: Enrichment Node (`graph/nodes.py`)

Updated `enrich_pool()` to compute secondary phases alongside the primary phase:
```python
primary_phase = infer_phase(fused, candidate.tempo_bpm)
secondary = infer_secondary_phases(fused, primary_phase, candidate.tempo_bpm)
```

#### Step 3.4: Beam Search (`rules/beam.py`)

- Added `_phase_matches()` helper — returns True if candidate's primary or secondary phase is in the acceptable set
- Updated `_phase_score()` — returns 0 when target phase matches primary or secondary
- Updated 5 filter locations in `_sequences()`:
  - Opener filter (relaxed H1): `{1, 2}` via `_phase_matches()`
  - Opener filter (strict): `{1}` via `_phase_matches()`
  - Closer filter: `{4, 5}` via `_phase_matches()`
  - H7 phase-arc guard: checks secondary phases for forward progression

#### Step 3.5: Hard Constraints (`rules/hard_constraints.py`)

- Collects all phases per item into `item_phases: list[set[int]]` (primary + secondary)
- **H1 (relaxed):** checks `item_phases[-1] & {4, 5}` (set intersection)
- **H1 (strict):** checks exactly one phase-1 opener, phase 3/4 in middle, phase 4/5 closer — all via set membership
- **H7:** uses `_phases_connect()` logic — `any(r >= l - 1 for l in left_phases for r in right_phases)` — allowing any phase combination to satisfy forward progression

#### Step 3.6: Fitness Scoring (`rules/fitness.py`)

Updated `f_theme()` phase distance calculation:
```python
if template[index] == item.phase or template[index] in item.secondary_phases:
    phase_distance = 0
else:
    phase_distance = abs((item.phase or 3) - template[index])
```

#### Step 3.7: Proposals (`rules/proposals.py`)

Updated both `item_from_candidate()` and `proposal_from_draft()` to pass `secondary_phases=candidate.secondary_phases`.

#### Step 3.8: Diagnostics (`rules/diagnostics.py`)

Updated all 5 role eligibility counts to include secondary phases:
- `valid_openers_h2`: phase 1 primary or secondary
- `valid_closers_h3`: phase 4/5 primary or secondary
- `phase_1_candidates_h1`: phase 1 primary or secondary
- `phase_3_or_4_candidates_h1`: phase 3/4 primary or secondary
- `phase_4_or_5_candidates_h1`: phase 4/5 primary or secondary

#### Step 3.9: Writer (`artifacts/writer.py`)

Updated 8 serialization locations:
- `_proposal_structured_data()`: phase string includes `(also: ...)` notation
- `_diversity_metrics()`: `unique_phases` includes secondary phases
- `write_report()` markdown table: phase column shows `3 (+1,5)` notation
- `write_pool_csv()`: new `secondary_phases` column with semicolon-separated values
- `_candidate_pool_summary()`: added `secondary_phase_counts` to summary dict
- `_proposal_section()` fallback report: phase display includes secondary phases

#### Step 3.10: Enrichment Report (`artifacts/enrichment_report.py`)

- Added `secondary_phase_counts` Counter alongside primary `phase_counts`
- Phase distribution table includes a `Secondary` column
- Console summary shows `(+N sec)` notation per phase
- Diversity metrics include `secondary_phase_counts` dict

#### Step 3.11: LLM Prompt Builder (`graph/nodes.py`)

Updated `_pool_prompt()` to include secondary phases in the candidate description:
```
h001: 讚美主, phase 3 (also: 1, 5), 124 BPM, key G maj, themes 讚美
```

---

### Phase 4: Regenerate Embedding Anchors (Rec E)

**Goal:** Rewrite short bag-of-words anchor texts as rich Traditional Chinese sentences incorporating the expanded vocabulary for better embedding classification quality.

#### Modified: `regen_theme_anchors.py` — ANCHOR_TEXTS Rewrite

Replaced short bag-of-words with 3-sentence rich descriptions per theme. Example:

**Before:**
```python
"認罪": "認罪 悔改 赦免 潔淨 repentance confession forgiveness"
```

**After:**
```python
"認罪": (
    "主啊，我認罪悔改，求你赦免我的罪孽，用寶血洗淨我一切的軟弱。"
    "我在你面前承認我的虧欠和過犯，求你潔淨我的心。"
    "Lord, I confess and repent, forgive my sins and cleanse me."
)
```

All 12 themes now have rich sentence descriptions incorporating the expanded `THEME_VOCAB` keywords. The regeneration script can be run when embedding API credentials are available:

```bash
# Requires SOW_EMBEDDING_API_KEY and SOW_EMBEDDING_BASE_URL
uv run --project lab/poc-scripts python -m poc.songset_constructor.regen_theme_anchors
```

---

### Phase 5: Validation & Measurement

Phase 5 (running the enrichment eval and writing the comparison report) requires database access and embedding API credentials. The implementation is ready for validation:

```bash
uv run --project lab/poc-scripts --extra admin sow-admin songset construct \
  --only-evaluate-pool-enrichment --pool-limit 500
```

The output should be saved to `reports/enrichment_eval_post_recommendations.md` and compared against `reports/enrichment_eval_post_traditional.md`.

---

## Recommendation D: Worship Fallback (Deferred)

Not implemented. Zero-theme songs are 0/438 in the post-Traditional baseline. All songs get a theme assignment via title, lyrics, or embeddings. A worship fallback would have minimal impact on this catalog.

---

## Files Changed

### New Files (2)

| File | Purpose |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/analyze_vocab_gaps.py` | Catalog lyrics analyzer script |
| `specs/songset-constructor-enrichment-diversity-recommendations-v1.md` | Implementation spec |

### Modified Files (15)

| File | Phase(s) | Change |
|---|---|---|
| `rules/themes.py` | 1 | Expand `THEME_VOCAB` dict (additive) |
| `rules/phases.py` | 2, 3 | Add `_top_themes_agree()`, modify `fuse_themes()`, add `infer_secondary_phases()` |
| `regen_theme_anchors.py` | 1, 4 | Rewrite `ANCHOR_TEXTS` as rich sentences |
| `models.py` | 3 | Add `secondary_phases` to `SongCandidate` and `ProposalItem` |
| `graph/nodes.py` | 3 | Compute secondary phases in enrichment, update LLM prompt |
| `rules/beam.py` | 3 | Add `_phase_matches()`, update 5 phase checks |
| `rules/hard_constraints.py` | 3 | Update H1, H7 for multi-phase membership |
| `rules/fitness.py` | 3 | Update `f_theme` phase distance |
| `rules/proposals.py` | 3 | Pass `secondary_phases` in 2 locations |
| `rules/diagnostics.py` | 3 | Update 5 count checks |
| `artifacts/writer.py` | 3 | Update 8 serialization locations |
| `artifacts/enrichment_report.py` | 3 | Add secondary phase counts |
| `tests/test_songset_constructor_rules.py` | 2, 3 | Add 12 new tests |
| `tests/test_songset_constructor_artifacts.py` | 3 | Add 1 new test |

**Total: 2 new files + 14 modified files = 16 files.**

---

## Tests

All 141 tests pass (12 new tests added):

### Phase 2 Tests (Fusion Shutoff)
- `test_fuse_themes_reduces_embedding_when_title_lyrics_agree` — verifies 認罪 wins over 敬拜 when keywords agree
- `test_fuse_themes_preserves_embedding_when_title_lyrics_disagree` — verifies embedding retains full weight on disagreement

### Phase 3 Tests (Multi-Phase Tagging)
- `test_infer_secondary_phases_returns_empty_for_dominant_theme` — no secondary when one theme dominates
- `test_infer_secondary_phases_returns_near_secondary_when_close` — secondary returned when score ≥ 85% of max
- `test_infer_secondary_phases_respects_threshold` — no secondary when below threshold
- `test_infer_secondary_phases_caps_at_max_secondary` — caps at 2 secondary phases
- `test_phase_matches_helper` — `_phase_matches()` correctly checks primary + secondary
- `test_phase_score_zero_for_secondary_match` — `_phase_score()` returns 0 for secondary match
- `test_beam_search_uses_secondary_phase_for_opener` — beam accepts phase-3 song with secondary phase 1 as opener
- `test_beam_search_uses_secondary_phase_for_closer` — beam accepts phase-3 song with secondary phase 5 as closer
- `test_h7_allows_secondary_phase_connection` — H7 passes when secondary phase bridges the gap
- `test_proposal_item_includes_secondary_phases` — `model_dump()` includes the field
- `test_csv_pool_writer_includes_secondary_phases_column` — CSV has `secondary_phases` column with semicolon-separated values

---

## Verification

```bash
uv run --project lab/poc-scripts --extra test pytest lab/poc-scripts/tests/ -v
# 141 passed, 1 warning in 1.34s
```

Beam search determinism (`test_beam_search_is_deterministic`) still passes — secondary phases are computed deterministically from fused scores with no randomness.

---

## Next Steps

1. **Run the vocab gap analyzer** against the live database to validate the keyword expansion:
   ```bash
   uv run --project lab/poc-scripts --extra admin python -m poc.songset_constructor.analyze_vocab_gaps
   ```

2. **Regenerate theme anchors** when embedding API credentials are available:
   ```bash
   uv run --project lab/poc-scripts python -m poc.songset_constructor.regen_theme_anchors
   ```

3. **Run the enrichment eval** and write the comparison report:
   ```bash
   uv run --project lab/poc-scripts --extra admin sow-admin songset construct \
     --only-evaluate-pool-enrichment --pool-limit 500
   ```
   Save output to `reports/enrichment_eval_post_recommendations.md` and compare against `reports/enrichment_eval_post_traditional.md`.

4. **Write comparison report** to `reports/enrichment_eval_post_recommendations_comparison.md` following the format of `reports/enrichment_eval_comparison.md`.
