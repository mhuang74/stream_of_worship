# Songset Constructor — Constraint Calibration & Cache-First Skill Enhancement

## Goal

Two enhancements to the songset-constructor skill:

1. **Recalibrate H1/H4/H5 constraint defaults** based on actual Stream of Praise catalog metadata (438-song pool), eliminating the situation where the agent must always opt into `--relax-h1/h4/h5` to produce natural songsets.
2. **Make the skill cache-first** for catalog access: when DB is unreachable (preflight failure), proceed from the cached pool file instead of halting, and have the agent clearly communicate this to the user.

## Background — Root Cause Analysis

### H1 — secondary_phases miscounting
`hard_constraints.py:41-43` builds `item_phases = [{item.phase} | set(item.secondary_phases) for each item]`. Strict H1 (line 63) requires:
```python
sum(1 for phases in item_phases if 1 in phases) != 1
```
Many legitimate closers like 全新的你 (phase=5, secondary_phases=[1,3]) get counted as a phase-1 song simply because 1 appears in `secondary_phases`. This is a logical bug, not an over-strict constraint — the closer shouldn't be treated as an opener.

### H4 — effective 25 BPM cap below catalog reality
`hard_constraints.py:80` uses `gap_beats > 0` for higher cap, but `beam.py:171` uses `gap_beats > 4` (different threshold). Since `transitions.py:30-31` sets `gap_beats=2.0` for all CFD ≤ 2 transitions (the *easy* ones), beam filtering applies 25 BPM cap to most adjacency. Given opener cluster at 95–117 BPM and closer cluster at 64–74 BPM, the typical opener→closer delta of 30–40 BPM exceeds 25 structurally.

### H5 — CFD=2 too tight for key diversity in the catalog
Strict CFD ≤ 2 allows C↔G (CFD=1), C↔D (CFD=2), F↔C (CFD=1). But C↔A (CFD=3) and C↔E (CFD=4) appear naturally between phase-3/4 songs and phase-1 songs (e.g., D major → A major is CFD=3, very common in SOP catalog). The strict 2 forces most adjacency through transposition, which then requires `key_confidence ≥ 0.6`.

### Cache-first gap
`fetch_pool.py:64-78` returns cached pool when fresh and falls back to DB on miss. `preflight.sh:36-109` treats DB-unreachable as a hard FAIL (`FAIL_COUNT += 1`), exiting 1 at `:158-161`. Agent has no documented procedure for proceeding from stale cache — operator must manually override.

## Design Decisions

- **H1**: count *primary* phase only for the "exactly one phase-1 opener" rule, while still allowing secondary_phases for the *middle worship/response* and *closer* checks. Make `relax_h1` opt-in retain its meaning (drop strict phase-1 requirement entirely) for scenarios with genuinely thin phase-1 pools.
- **H4**: strict default 45 BPM (covers biggest catalog opener→closer delta at 117→74 = 43 BPM non-crossfade). Non-crossfade effective cap raised from 25 to 40. Relaxed tier raises to 55. Standardize `beam.py` and `hard_constraints.py` thresholds (use `gap_beats > 0` consistently).
- **H5**: strict default 3 (covers C↔A, G↔D natural 4th-resolutions); relaxed 4 (allows C↔E family, beyond which transposition required via existing `suggest_key_shift`).
- **Cache-first**: `preflight.sh` should still alert [FAIL] when neither DB nor cache is available, but [WARN] when DB-unreachable + cache-exists. `fetch_pool.py` should serve stale cache when fresh DB read fails (wraps DB query in try/except). SKILL.md documents the procedure.

## Files to Change

### 1. `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/config.py`
In `RunConfig`:
- Add `h4_strict: int = 45` and `h4_no_crossfade: int = 40` as new fields (replacing magic numbers in hard_constraints.py and beam.py).
- Add `h5_strict: int = 3` (related to existing `h5_limit`).
- Modify `h4_limit` property:
  ```python
  @property
  def h4_limit(self) -> int:
      if self.relax_h4_bpm is not None:
          return self.relax_h4_bpm
      if self.relax_h4:
          return 55
      return self.h4_strict
  ```
- Modify `h4_no_crossfade_limit` property (new):
  ```python
  @property
  def h4_no_crossfade_limit(self) -> int:
      return min(self.h4_no_crossfade, self.h4_limit)
  ```
- Modify `h5_limit` property:
  ```python
  @property
  def h5_limit(self) -> int:
      if self.relax_h5_cfd is not None:
          return self.relax_h5_cfd
      return 4 if self.relax_h5 else self.h5_strict  # default strict=3, relaxed=4
  ```
- Update `to_dict()` to include `h4_strict`, `h4_no_crossfade`, `h5_strict` so relaxed child-config copies inherit them.

### 2. `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/hard_constraints.py`

- **H1 fix** (lines 39-66): replace with primary-only opening count:
  ```python
  # Build per-item primary+secondary for closer/middle checks, but use primary only for opener count
  primary_phases = [item.phase for item in proposal.items]
  item_phases = [{item.phase} | set(item.secondary_phases) for item in proposal.items]
  ...
  if relax_h1:
      h1_failed = not (item_phases[-1] & {4, 5})
  else:
      h1_failed = (
          sum(1 for p in primary_phases if p == 1) != 1              # primary phase-1 opener only (CHANGED)
          or not any(phases & {3, 4} for phases in item_phases[1:-1])  # middle has 3/4 via primary or secondary
          or not (item_phases[-1] & {4, 5})                             # closer has 4/5 via primary or secondary
      )
  ```
- **H4 fix** (lines 76-82): use new `h4_no_crossfade_limit` property and consistent `gap_beats > 0` threshold (matching beam.py):
  ```python
  allowed = (
      config.h4_limit
      if (right.crossfade_duration_seconds > 0 or right.gap_beats > 0)
      else config.h4_no_crossfade_limit
  )
  ```
- **H5 fix** (line 83-87): no code change — config.h5_limit now returns 3 strict / 4 relaxed by default.
- Update `RULE_DESCRIPTIONS` text for H1/H4/H5 to reflect new defaults.

### 3. `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/beam.py`

- **`_sequences` H4 filter** (lines 169-173): use `config.h4_no_crossfade_limit` and `gap_beats > 0`:
  ```python
  allowed = (
      config.h4_limit
      if transition and (transition.crossfade_duration_seconds > 0 or transition.gap_beats > 0)
      else config.h4_no_crossfade_limit
  )
  ```
- **`compute_fan_out`** (line 47-52): use `config.h4_limit` for overall transition feasibility (already does, so the higher cap = higher dead-end count → fewer dead ends).

### 4. `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/diagnostics.py`

- Update `compatible_transitions_h5` count (line 70-72) to use `config.h5_limit` instead of hardcoded `2`.
- Keep role_eligibility counts using resolved `config.closing_limit` and `config.opening_floor` (already correct).

### 5. `lab/skills/songset-constructor/scripts/preflight.sh`

- Restructure DB check (lines 57-109) so DB-unreachable becomes [WARN] (not [FAIL]) when a valid pool cache file exists:
  - After `DB_CHECK=$()` parses, if `db_status != "DB_OK"`:
    - Check `CACHE_FILES` for any `pool_*.json`; if non-empty, emit `run_check "Database reachable" "WARN" "DB unreachable — proceeding from cached pool (age: ${AGE_HOURS}h, ${SONG_COUNT} songs)"`
    - If no cache file, keep existing `run_check "Database reachable" "FAIL" ...` (still increments `FAIL_COUNT`).
- Update the summary at lines 158-161 so [WARN] checks don't prevent exit 0. Specifically: change the loop so only [FAIL] excludes from "passed" summary.
- Add a `CACHE_NOT_NEEDED=false` flag for the synthetic scenario where pool cache is the only data source.

### 6. `lab/skills/songset-constructor/scripts/fetch_pool.py`

- Reorganize main() flow so cache always wins:
  1. Always probe cache (regardless of TTL); if fresh, return cached.
  2. If stale but `--allow-stale` (new flag, default True) is on AND DB query fails: log [WARN] "Serving stale pool cache (age: ${age}h, DB unreachable)" and return cached.
  3. If no cache exists AND DB query fails: print JSON error to stderr, exit 1 with message "No pool available: DB unreachable and no cache file found."
- Wrap the existing DB-fetch block in try/except to convert DB errors into warnings (when cache exists) rather than raising.
- Add `--prefer-fresh` flag (default False) to bypass stale-but-useable fallback.

### 7. `lab/skills/songset-constructor/SKILL.md`

- **Step 1 (Pre-flight)** — replace text with: "Run `scripts/preflight.sh` to verify environment. DB-unreachable is a WARN (not a hard fail) when a valid `pool_*.json` cache exists. The agent proceeds from cache and reports this to the user. If absolutely no cache exists and DB is unreachable, the run cannot proceed."
- **Step 2 (Fetch Catalog Pool)** — add paragraph: "fetch_pool.py serves cached pool by default, even when stale, when DB read fails. Use `--no-cache` to force a fresh DB query, `--prefer-fresh` to attempt DB first and only fall back to cache on DB error. If the preflight DB check WARN-flagged cache availability, accept the stale cache and note the staleness in the run summary."
- **Hard Constraints table** — update:

  | Code | Rule | Default |
  |------|------|---------|
  | H1 | One phase-1 *primary* opener (primary only, not secondary_phases), middle worship/response, phase-4/5 closer (primary or secondary) | relaxable (opt-in via `--relax-h1`) |
  | H2 | Opener tempo ≥ 90 BPM | 90 (relaxable) |
  | H3 | Closer tempo ≤ 90 BPM (80 if intimate) | 90/80 (relaxable) |
  | H4 | Adjacent BPM delta ≤ 45 (40 without crossfade; 55 if relaxed) — gap_beats > 0 (any gap) triggers crossfade-tier cap | 45/40 |
  | H5 | Circle-of-fifths distance ≤ 3 (4 if relaxed) unless key shift applied | 3 |

- **Planning guidelines** — update bullets: "BPM delta ≤ 45 from previous (40 without crossfade)" and "CFD ≤ 3 (or apply key shift if CFD > 3)".
- **Step 7 (Refine) — relaxation list** — update fallback values: "Relax H4 (BPM delta 45 → 55)" and "Relax H5 (CFD 3 → 4)".
- **Step 11 (Summary)** — add bullet: report DB↔cache source used (cached-stale-fresh indicator) in the user summary if preflight WARN-flagged.

## Tests to Add

In `ops/admin-cli/tests/test_songset_constructor_rules.py` (or equivalent existing test file):

- `test_h1_strict_counts_primary_phase_only_not_secondary` — proposal with phase-5 closer, secondary_phases=[1, 3]; strict H1 should pass (primary phase-1 count = 0 only when opener is non-phase-1, etc.).
- `test_h1_strict_fails_when_no_primary_phase1_opener` — proposal with only phase-2/3/4/5 songs (primary only) should fail strict H1.
- `test_h4_default_45_strict_applied` — transition with bpm_delta = 40 + gap_beats=0 (no crossfade) → passes strict (≤ 40 non-crossfade cap); bpm_delta = 44 + gap_beats=0 → passes (≤ 45 not relevant since non-crossfade uses 40); bpm_delta = 50 + gap_beats=2.0 + no crossfade → fails (50 > 40).
- `test_h4_beam_threshold_matches_hard_constraints` — assert `gap_beats > 0` (not `> 4`) gives the higher cap in both beam.py and hard_constraints.py on a synthetic transition with gap_beats=2.0 + no crossfade.
- `test_h5_strict_3_default_allows_cfd_3` — proposal with adjacent CFD=3 and no key shift → passes strict H5.
- `test_h5_relaxed_4_allows_cfd_4` — same proposal fails strict (config=relax_h5_cfd=None) when CFD=4 → relax turns to 4 → passes.
- `test_config_h4_h5_defaults_match_catalog_reality` — sanity: `RunConfig().h4_limit == 45`, `RunConfig(relax_h4=True).h4_limit == 55`, `RunConfig().h5_limit == 3`, `RunConfig(relax_h5=True).h5_limit == 4`.

For preflight.sh / fetch_pool.py (integration test under resources/):

- `test_preflight_warn_db_unreachable_when_cache_present` — simulate DB timeout via env var + a fake `~/.cache/sow/songset_constructor/pool_*.json`; assert exit code 0 and output contains `[WARN] Database reachable`.
- `test_fetch_pool_serves_stale_cache_on_db_failure` — populate stale cached pool (>24h), trigger DB failure, expect cached pool JSON on stdout with stderr WARN.
- `test_fetch_pool_errors_when_no_cache_and_no_db` — remove cache + failing DB → exit 1 with clear error message.

## Verification

```bash
# Constraints unit tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest \
  ops/admin-cli/tests/test_songset_constructor_rules.py -v

# Skill scripts (generic)
bash lab/skills/songset-constructor/scripts/preflight.sh
lab/skills/songset-constructor/scripts/fetch_pool.py | lab/skills/songset-constructor/scripts/enrich_pool.py

# Re-run a 25-set construction with strict defaults (no relaxation flags)
# — expect most proposals to pass without relax_h1/h4/h5
```

## Edge Cases / Notes

- **Existing tests likely break** by the new H1 primary-only logic: many existing test fixtures probably used multi-secondary-phase closers. Fixtures must be reviewed during implementation.
- **Beam filter threshold change** (gap_beats > 0 vs > 4) increases the number of non-dead-end songs; expect runtime on the synthesised beam to grow slightly with the larger fan-out. For 438-song pool this is negligible.
- **Catalog refresh**: when DB becomes reachable again, an explicit `--no-cache` run refreshes the cached pool. SKILL.md still recommends running `--no-cache` once per data update cycle.
- **Stale cache can produce unexpected pool** if a song was deleted/added since cache write — document this caveat in the WARN message and SKILL.md note.
- **Backwards compatibility**: CLI defaults (`relax_h1=false`, `relax_h4=false`, `relax_h5=false`) preserve "strict" behavior, but the calibrated defaults now naturally pass more proposals. `--no-auto-relax` still disables all escalation tiers.
