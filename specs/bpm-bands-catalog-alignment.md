# Plan: Align Songset Constructor BPM Bands with Actual Catalog Distribution

## Overview

The songset constructor POC (`lab/poc-scripts/poc/songset_constructor/`) applies
hard constraints (H2/H3/H4) and phase-inference thresholds whose BPM bands were
tuned by assumption, not by measurement. After running
`lab/poc-scripts/diagnose_closers.py` against the live catalog, the actual BPM
distribution reveals that the current defaults are catastrophically mismatched:
the strict beam search dies at position 5 (closer) and only succeeds after
auto-relaxing H3 to 160 BPM — a 70 BPM over-correction.

This plan re-aligns every BPM threshold in the rules with the catalog's actual
distribution so the strict beam pass has a meaningful chance of succeeding on
the first attempt, and the auto-relax fallbacks stay within one tempo band
rather than jumping 30-40 BPM.

## Actual Catalog Distribution (measured 2026-07-18)

Source: `uv run --project lab/poc-scripts python lab/poc-scripts/diagnose_closers.py`

- **Pool size**: 200 songs (published/review recordings with LRC)
- **Phase counts**: `{1: 39, 2: 17, 3: 114, 4: 24, 5: 6}`
- **Valid closers (H3 strict)**: 21 (phase 4/5 & tempo ≤ 90)
- **Phase 4/5 songs (any tempo)**: 30
- **Tempo ≤ 90 songs (any phase)**: 126

### Tempo histogram

| Bucket | Count | % |
|--------|------:|--:|
| `< 70` | 111 | 55.5% |
| `70–83` | 14 | 7.0% |
| `84–89` | 1 | 0.5% |
| `90–99` | 30 | 15.0% |
| `100–109` | 39 | 19.5% |
| `110–117` | 2 | 1.0% |
| `>= 118` | 3 | 1.5% |

### Beam search results

| Config | final_beams | Notes |
|--------|------------|-------|
| strict (pre-change) | 0 | Died at position 5 (closer) |
| strict (post-change) | 8 | Reaches position 5; picks 99.4 BPM songs for positions 1–4, then 68.0 BPM closer (delta ~31, within h4_limit=35) |
| relax_h3=160 (pre-change) | 8 | Completed all 5 positions |
| relax_h3=160 (post-change) | 8 | Completed all 5 positions |

### Key observations

1. **Catalog is heavily slow-skewed**: 63% of songs are below 90 BPM; 55.5%
   are below 70 BPM. Only 2.5% are at or above 110 BPM.
2. **Catalog is bimodal with a ~30 BPM gap**: 111 songs are below 70 BPM
   (slow cluster) and 69 songs are in 90–109 BPM (moderate cluster). Only
   15 songs (7.5%) fall in the 70–89 range. This gap is the root cause of
   H4 failures — the beam picks 99.4 BPM songs for positions 1–4 (moderate
   cluster), then cannot reach phase-5 closers at 66–72 BPM (slow cluster)
   because the delta (~28–33 BPM) exceeds the original h4_limit of 20.
3. **Opening floor of 110 BPM is catastrophic**: only 5 songs (2.5%) qualify
   as openers. The beam can fill position 1 but has almost no diversity,
   increasing dead-end probability downstream.
4. **Closing limit of 90 BPM is well-aligned**: 126 songs (63%) are below 90.
   The 21 valid closers (phase 4/5 + tempo ≤ 90) are sufficient in count — the
   bottleneck is H4 (tempo jump), not H3.
5. **H4 limit of 20 BPM (15 without crossfade) is too tight**: with the
   bimodal gap, the transition from position 4 (~99 BPM, moderate cluster)
   to a closer at ~68 BPM (slow cluster) produces a delta of ~31 BPM —
   exceeding the original h4_limit of 20. The strict beam dies because no
   closer is both phase 4/5, tempo ≤ 90, AND within 20 BPM of the preceding
   song. h4_limit=35 bridges this gap.
6. **Phase inference thresholds are misaligned**: `>= 118` for phase 1
   captures only 3 songs; `>= 84` for phase 3 captures only 15 songs in the
   84-99 range. The thresholds don't reflect the catalog's natural clustering
   at < 70 / 90-109 / 100+.

## Changes

### 1. `poc/songset_constructor/config.py` — default BPM bands

| Property | Old | New | Rationale |
|----------|-----|-----|-----------|
| `opening_floor` (default) | 110 | **90** | 74 songs ≥ 90 BPM (37%); only 5 songs ≥ 110 |
| `closing_limit` (non-intimate) | 90 | 90 | 126 songs < 90; already well-aligned |
| `closing_limit` (intimate) | 80 | 80 | unchanged |
| `h4_limit` (default) | 20 | **35** | Catalog is bimodal with ~30 BPM gap between slow cluster (< 70, 111 songs) and moderate cluster (90–109, 69 songs); 20 too tight to bridge the gap |
| `h4_limit` (relaxed) | 25 | **40** | Allows cross-band transitions (e.g., 100→65) with crossfade; max observed delta ~33 BPM |

The non-crossfade H4 sub-limit (currently `min(15, h4_limit)`) becomes
`min(25, h4_limit)` — proportional to the new default.

### 2. `poc/songset_constructor/rules/phases.py` — phase inference thresholds

| Threshold | Old | New | Rationale |
|-----------|-----|-----|-----------|
| Phase 1 (fast) | `>= 118` | **`>= 100`** | 44 songs ≥ 100; only 3 songs ≥ 118 |
| Phase 2 (upbeat) | `>= 100` | **`>= 90`** | 30 songs in 90-99 band |
| Phase 3 (moderate) | `>= 84` | **`>= 70`** | 14 songs in 70-83 band |
| Phase 4 (slow) | `< 84` | **`< 70`** | 111 songs < 70; natural slow cluster |
| 圣灵 theme cut | `< 82` | **`< 70`** | Align with phase 4 floor |

### 3. `poc/songset_constructor/rules/beam.py` — auto-relax fallback values

| Property | Old | New | Rationale |
|----------|-----|-----|-----------|
| `relax_h2_bpm` (auto-relax) | 80 | 80 | unchanged — already one band below new opening_floor=90 |
| `relax_h3_bpm` (non-intimate, auto-relax) | 120 | **100** | 120 allows fast songs as closers; 100 captures 90-99 band |
| `relax_h3_bpm` (intimate, auto-relax) | 100 | **90** | proportional shift |

The non-crossfade H4 sub-limit in `_sequences` also changes from
`min(15, h4_limit)` to `min(25, h4_limit)`, matching `hard_constraints.py`.

### 4. `poc/songset_constructor/rules/hard_constraints.py` — rule descriptions

Update `RULE_DESCRIPTIONS` for H2, H3, and H4 to reflect the new default
values (110→90 for H2; 20→35 for H4). H3 text unchanged (90 BPM still the
default for non-intimate).

### 5. `lab/poc-scripts/diagnose_closers.py` — tempo bucket alignment

Re-align the diagnostic tempo buckets to match the new phase thresholds:

| Old buckets | New buckets |
|-------------|-------------|
| `<70, 70-83, 84-89, 90-99, 100-109, 110-117, >=118` | `<70, 70-89, 90-99, 100-109, 110-119, >=120` |

The new buckets align with the phase inference boundaries (70, 90, 100, 120)
so the diagnostic output is directly comparable to the rule thresholds.

### 6. Tests — `tests/test_songset_constructor_rules.py`

Most tests use explicit `phase=` and `themes=` fields, so `infer_phase`'s
tempo thresholds don't apply. The tests that need updating are those that
assert specific H2/H3/H4 rejection counts or rely on the old 110/90/20
defaults:

- `test_diagnostics_counts_hard_rule_rejections`: BPMs 100/95/80/100. Under
  new defaults (opening_floor=90, closing_limit=90), the opener at 100 now
  passes H2 and the closer at 100 now fails H3 (100 > 90). Update expected
  rejection set.
- `test_beam_filters_opener_by_h2_floor`: BPMs 95/115. Under new
  opening_floor=90, the 95 BPM opener now passes strict. Update the test
  to use a lower BPM (e.g., 85) for the "slow opener" that should be filtered.
- `test_beam_filters_closer_by_h3_ceiling`: BPMs 88/100. Under new
  closing_limit=90, the 100 BPM closer still fails strict. Test still valid.
- `test_relax_h3_unblocks_when_only_high_bpm_closer_matches_preceding`:
  closer at 105 BPM. Under new closing_limit=90, still fails strict. Test
  still valid.
- `test_relax_h3_raises_ceiling_allows_loud_closer`: closers at 95/105.
  Under new closing_limit=90, both fail strict. Test still valid.
- `test_beam_rejects_h4_violating_middle_pair`: BPMs 131/95/92/82/78.
  Max delta = 36 (131→95). Under new h4_limit=35, this exceeds strict (even
  with crossfade, 36 > 35) and passes relaxed (36 ≤ 40). Update the opener
  BPM from 124 to 131 to create a delta > 35.
- `test_beam_h4_honors_crossfade_branch`: delta 18 with crossfade. Under
  new h4_limit=35, 18 < 35 so it passes. Test still valid.
- `test_compute_fan_out_uses_config_limits`: BPMs 160/120/80/40/0. All
  pairwise deltas ≥ 40. Under new h4_limit=35, all deltas > 35 → all
  dead-ends. Under relaxed h4_limit=40, adjacent deltas = 40 ≤ 40 → not
  dead-ends. Update BPMs to ensure all gaps exceed 35.

## Expected Impact

- **Strict beam pass succeeds**: with opening_floor=90 (74 eligible openers)
  and h4_limit=35 (bridges the bimodal gap), the beam reaches position 5
  and finds a valid closer without needing auto-relax. Verified via
  `diagnose_closers.py`: strict `final_beams=8` (was 0).
- **Auto-relax is a safety net, not the default path**: the auto-relax values
  (h2=80, h3=100) are one tempo band below/above the strict defaults, so
  fallback proposals remain musically sensible.
- **Phase inference matches catalog clustering**: the < 70 / 70-89 / 90-99 /
  100+ thresholds reflect the catalog's natural BPM clusters, so songs
  without theme classification get sensible phase assignments.

## Verification

```bash
# 1. Unit tests
uv run --project lab/poc-scripts --extra test pytest tests/test_songset_constructor_rules.py -v

# 2. Lint
uvx ruff check lab/poc-scripts/poc/songset_constructor --config lab/poc-scripts/pyproject.toml
uvx black --check --line-length 100 lab/poc-scripts/poc/songset_constructor

# 3. Re-run diagnostic to verify strict beam now succeeds
uv run --project lab/poc-scripts python lab/poc-scripts/diagnose_closers.py
# Expect: strict final_beams > 0 (was 0)
```

## Files Changed

| File | Change |
|------|--------|
| `lab/poc-scripts/poc/songset_constructor/config.py` | opening_floor 110→90; h4_limit 20→35, relaxed 25→40 |
| `lab/poc-scripts/poc/songset_constructor/rules/phases.py` | Phase thresholds: 118→100, 100→90, 84→70; 圣灵<82→<70 |
| `lab/poc-scripts/poc/songset_constructor/rules/beam.py` | Auto-relax h3_bpm: 120→100 (non-intimate), 100→90 (intimate); non-crossfade sub-limit 15→25 |
| `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py` | H2/H4 rule descriptions; non-crossfade sub-limit 15→25 |
| `lab/poc-scripts/diagnose_closers.py` | Tempo bucket realignment |
| `lab/poc-scripts/tests/test_songset_constructor_rules.py` | Update affected test BPMs and expected rejection counts |
| `lab/poc-scripts/tests/test_songset_constructor_config.py` | h4_limit assertions updated to 35/40 |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Opening floor 90 allows moderate-tempo openers that lack "strong opener" energy | The phase 1/2 requirement (H1) still filters by theme; tempo is secondary. A 90 BPM praise song is a valid opener in worship music. |
| H4 limit 35 BPM allows musically jarring transitions | The non-crossfade sub-limit (25) still applies to direct cuts; 35 only applies with crossfade/gap. The fitness function's `f_tempo` penalizes large deltas. |
| Phase 4 at < 70 BPM puts 111 songs in "slow reflection" | Theme-based phase assignment is primary; tempo is the fallback. Most of the 111 slow songs have themes that assign them to phase 3 (worship), not phase 4. |
| Relaxed H4 at 40 BPM is very wide | Only reached in the final auto-relax tier; the fitness function still penalizes large deltas, so proposals with 40 BPM jumps rank lower. |
