# Songset Constructor — Component-Metadata Integration v1

**Date:** 2026-09-11
**Skill location:** `lab/skills/songset-constructor/` (canonical; sole skill target)
**Spec type:** Implementation plan (code enhancement + SKILL.md documentation, no edits in this spec itself)
**Audience:** Fresh implementing agent — this spec is self-contained.
**Decision record:** The components-primary theme policy is also recorded in `docs/adr/0006-component-theme-primary-for-phase-inference.md`.

## Goal

Leverage component-level (per-section) metadata in the songset-constructor pipeline. Today the pipeline is 100% song-level; `song_components` — per-section BPM, key, theme, energy, and vocal posture already persisted by the analysis service — is read by the constructor **zero** times. This spec integrates all five signals:

1. **Boundary key** — transition compatibility (`cfd`, `key_compat`, `suggested_key_shift`) computed on song A's *exit* component vs song B's *entry* component, falling back to song-level key.
2. **Boundary BPM** — `bpm_delta` and hard constraints H2/H3/H4 computed on boundary BPM, falling back to song-level BPM.
3. **Component theme** — phase inference consumes component-derived, chorus-preference theme distributions as the **primary** signal; the existing 4-source text fusion (title 35% / lyrics 25% / song embedding 25% / line embedding 15%) becomes fallback for component-less songs.
4. **Energy** — new ordinal soft term `f_energy` on pool-percentile-normalized component energy.
5. **Posture** — new soft term `f_posture` scoring chorus-preference VocalPosture against the phase template.

**Policy decisions (user-confirmed in grilling session, 2026-09-11):** components-primary theme (ADR-0006), boundary-first with fallback everywhere, soft-only scoring (no new H-codes), fallback+flag coverage policy, six-way weight renormalization, backfill out of scope.

## Scope Clarifications

- **All five signals, full integration** (per user decision): boundary key + BPM feed the transition matrix; component theme feeds phase inference; energy and posture become new soft score terms; all three new signals render in `write_report.py` per proposal.
- **Boundary-first with song-level fallback** (per user decision): when both songs of an adjacent pair have populated boundary components, the matrix computes exit(A)↔entry(B); otherwise it falls back to today's song-level computation. A `boundary_source` field on `TransitionCandidate` records which path produced each pair.
- **H2/H3 become boundary-aware** (per user decision): opener floor checks the opener's *entry* BPM, closer ceiling checks the *closer's exit* BPM; song-level BPM remains the fallback. Boundary BPM governs the adjacency (H4); H2/H3 check the boundary side the worship leader actually sings into/out of.
- **H8 becomes boundary-aware** (per user decision): boundary transposition is gated by the boundary row's `key_confidence ≥ 0.6` (entry side of the destination song); falls back to song-level `key_confidence` when the boundary key is missing. Component confidence covers 69% of the pool vs 56.3% at song level, unlocking transpositions today's H8 bars.
- **Components-primary theme** (per user decision, ADR-0006): when a recording has ≥1 non-null component theme, phase inference consumes the chorus-preference, `theme_confidence`-weighted component distribution. The existing fusion becomes fallback. Seasonal bias applies after either path.
- **Chorus-preference posture aggregation** (per user decision): same aggregation shape as theme — chorus postures win, `vocal_posture_confidence` weights, no confidence floor; fallback chain: recording-level aggregate (`recordings.vocal_posture`) → neutral 0.5.
- **Soft-only scoring** (per user decision): no new hard-constraint codes. f_energy and f_posture join the weighted breakdown; failures surface as report warnings, never block a proposal.
- **Energy is ordinal, never absolute** (per user decision): `song_components.energy_level` is raw mean-RMS dB (negative values, ~-12..-30, no normalized scale). All energy logic operates on **pool-percentile ranks**; no absolute dB thresholds anywhere.
- **Fallback + flag coverage** (per user decision): the 132/444 pool songs (29.7%) without components are **not** dropped; they use song-level values, are marked `has_components = false`, and the report shows per-proposal coverage.
- **Backfill of component-less songs is out of scope** (per user decision): the spec does not schedule or require a components backfill run.
- **SKILL.md is not edited by this spec's implementation phase until scripts ship** — see Rollout Order. The spec targets scripts + package code; SKILL.md updates land together with working scripts, never before.
- **`.agents/skills/songset-constructor/` mirror is out of scope** (externally managed), consistent with prior spec conventions.
- **Webapp / Android / render-worker are out of scope.** Per ADR-0005 posture stays admin-side; the webapp has no `song_components` access and this spec adds none.

## Background — Current State (file:line verified 2026-09-11)

### The constructor pipeline is song-level only

Skill scripts in `lab/skills/songset-constructor/scripts/` are thin CLI wrappers that inject `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/` into `sys.path` (e.g. `fetch_pool.py:21-24`). Grep for `component|vocal_posture|energy|SongComponent|song_components` across the skill and the package: **no matches**. The DB layer never touches `song_components`.

### Data flow today

- **Fetch:** `fetch_catalog_pool` (`songset_constructor/db.py:99-108`) runs `POOL_QUERY` (`db.py:20-38`, joins `songs`+`recordings`+`song_embedding` vs `theme_anchors` → `song_theme_scores_raw`) and `LINE_THEME_QUERY` (`db.py:40-53`, line-embedding → `line_theme_scores_raw`). Recording-level columns: `tempo_bpm`, `musical_key` (AS `r_musical_key`, preferred over song key at `db.py:86`), `musical_mode`, `key_confidence`, `loudness_db`, `duration_seconds`. Row→`SongCandidate` at `_candidate_from_row` (`db.py:76-96`); model at `models.py:8-36`.
- **Cache:** `~/.cache/sow/songset_constructor/pool_<sha256(pool_limit:sorted_series)[:16]>.json` (`cache.py:17-23`), atomic write via tmp+`os.replace` (`cache.py:47-57`), `model_dump(mode="json")` round-trip — new optional `SongCandidate` fields flow through automatically; old caches validate to `None` defaults. CLI flags `--no-cache`, `--prefer-fresh`, `--allow-stale` (default True) at `fetch_pool.py:36-55`; stale fallback `_try_load_stale` (`fetch_pool.py:152-166`).
- **Enrich:** drops songs missing BOTH tempo and key (`enrich_pool.py:138-140`); theme fusion `fuse_themes` (`rules/phases.py:33-59`: title/lyrics agree → 0.45/0.35/0.15/0.05, disagree → 0.35/0.25/0.25/0.15; empty line-emb redistributes); seasonal bias `apply_seasonal_bias` (`phases.py:73-88`); phase inference `infer_phase` (`phases.py:91-105`: argmax theme → `THEME_TO_PHASE` (7-20); 聖靈+bpm<70 → phase 4; tempo-only fallback ladder ≥100→1, ≥90→2, ≥70→3, <70→4); `infer_secondary_phases` (`phases.py:108-134`, themes ≥ 0.85×max, ≤2); leader-range fields `_compute_range_fields` (`enrich_pool.py:94-129`, ±2 shift gate at `:108`).
- **Transitions:** all ordered pairs with CFD ≤ 6 (`build_transitions.py:53`); `recommend_transition(left, right)` (`rules/transitions.py:13-58`) reads song-level `musical_key/musical_mode/tempo_bpm/key_confidence` only. CFD: `fifth_distance_on_circle` (`rules/harmony.py:47-51`), `cfd()` on `relative_major_pc` (`harmony.py:41-44, 54-58`); key parse `normalize_key` (`harmony.py:17-29`, missing → C major); `key_compatibility_score` lookup 0/1/2/3/4/other → 1.0/0.92/0.78/0.55/0.32/0.15 (`harmony.py:61-72`); `suggest_key_shift` tries −2..+2 minimizing (cfd, |shift|) (`harmony.py:75-90`); technique+crossfade+gap ladder distance≤1 pivot 0s/2 · ≤2 relative-or-direct 0s/2 · shifted≤2 transposition 4s/4 · ==3 vamp 6s/4 · else direct_modulation 8s/6 (`transitions.py:25-44`); `crossfade_enabled = crossfade_seconds > 0` (`transitions.py:54`); low-confidence warnings `transitions.py:20-23`. **`bpm_delta = abs((to.tempo_bpm or 0) − (from.tempo_bpm or 0))` (`transitions.py:18`) — missing BPM collapses to 0 delta, silently "compatible."** Fan-out: `compute_fan_out` (`rules/beam.py:35-56`), counts transitions with bpm_delta ≤ h4 AND (cfd ≤ h5 OR shift ≠ 0) (`beam.py:47-51`).
- **Score:** stdin `{"items", "pool", "transitions", "config"}`; `proposal_from_draft` (`rules/proposals.py:56-95`); fitness in `rules/fitness.py`: f_theme (29-37, `1 − Σ|phase−template|/(4n)`, `_THEME_TEMPLATES` 12-22), f_tempo (40-47, `0.75·smoothness + 0.25·arc`, smoothness `1 − min(1, Σ|Δbpm|/(25n))`, arc bonus first ≥ last → 1.0 else 0.75, <2 known bpms → 0.5), f_harmony (50-60, mean key_compat, missing transition → 0.2), f_diversity (63-68, `0.7·song-uniqueness + 0.3·theme diversity`), total = `0.40·theme + 0.30·tempo + 0.20·harmony + 0.10·diversity` (77-93). Range penalty (`score_songset.py:95-124`): per item `min(0.05·circular_dist, 0.20)` on `(tonic_pc + key_shift) % 12` outside comfortable set, subtracted outside the weights; `ScoreBreakdown.range_penalty` at `models.py:92`. Validation H0–H9 in `rules/hard_constraints.py` (H2/H3 `:75-80`, H4 `:83-92`, H5 `:93-97`, H8 `:106-108`, H9 `:110-116`; relax knobs in `config.py:34-48`, properties 82-110).
- **Report:** `scripts/write_report.py` — `_run_summary` (132-167), `_pool_overview` (170-218), `_proposal_section` (221-303); `MAX_DURATION_SECONDS = 1500` hardcoded (`:35`); helpers in `artifacts/writer.py`.

### The component data model (admin side)

- **Table:** `song_components` — 29 columns; DDL `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:228-249` + v5 ALTERs (294-307) + v6 (311-313); unique `(song_id, component_type, occurrence_index, role)` v3 (251-270). Fields relevant here: `bpm`, `key` (note: **`key`**, not `musical_key`; e.g. `"G"`), `key_confidence` (sigmoid of chroma margin, default 0.7), `bpm_confidence` (duration tier 0.9/0.7/0.4 — soft weight only, NOT an H8-style gate), `energy_level` (mean RMS in dB, negative, `components.py:1735`), `theme` (12-value enum CHECK, `schema.py:300-301`), `theme_confidence`, `vocal_posture` (3-value CHECK `'To God'/'About God'/'To Congregation'`, `schema.py:94-95, 302-303`), `vocal_posture_confidence`, `role` (`entry|exit|loop_target|entry_exit|none`, `schema.py:236-237`), `component_type`, `occurrence_index`, `line_start/line_end`.
- **Model:** `SongComponent` dataclass `admin/db/models.py:589-649` (+ `from_row` 651-683, `to_dict` 685-716); column list `SONG_COMPONENT_COLUMNS_SELECT` (`schema.py:333-344`).
- **Linkage:** every row carries BOTH `song_id` and `content_hash` (FK recordings) (`schema.py:231-232`). Pool scope is 1:1 song↔recording (444 songs = 444 hash prefixes), so per-song boundary fields are unambiguous.
- **Readers:** `DatabaseClient.get_song_components[_by_role|_by_type|_entry_exit]` (`admin/db/client.py:2159-2231`).
- **Populated by default only on essential roles** (entry/exit/loop_target/entry_exit + first bridge): theme/posture/audio fields NULL on verses and later choruses unless `--all-components` (`commands/audio.py:3323`, `classifier.py:170-182`). Boundary rows (entry/exit) are the best-populated rows.
- **Per-component audio features** are independently detected per slice (`ops/analysis-service/.../components.py:1568-1772`): component BPM via beat grid/tempo on slice (1624-1638), key from sliced chroma returning None when top-2 margin < 0.03 (435-440, 1646-1661), energy = mean RMS dB of vocals stem else full mix (1713-1735), confidences (1739-1772). Component BPM/key CAN disagree with song-level values; per-component theme/posture come from the LLM classifier (`classifier.py:214+`) with Chinese-pronoun pre-pass (祢→To God, 祂→About God, 讓我們/當/應當/要/彼此/眾/凡→To Congregation, `classifier.py:42-55`), confidence-adjusted (556-601), out-of-enum coerced to None on DB write (`db/client.py:2127-2132`).
- **Recording-level aggregates already exist and are unread by the skill:** `recordings.theme` / `recordings.vocal_posture` (schema.py:91-95), aggregated by `_aggregate_recording_theme` (`commands/audio.py:2781-2865`) with chorus-preference tie-breaking. Posture persisted-but-not-surfaced per `docs/adr/0005-posture-persisted-not-surfaced.md`, which names the songset constructor as posture's intended consumer.
- **Theme vocabulary:** 12 values `SONG_COMPONENT_THEMES` (`schema.py:285-288`) — identical to the constructor's `THEMES` (`songset_constructor/rules/themes.py:9`) and `THEME_VOCAB` (`themes.py:11-24`). No new vocabulary is introduced by this spec.

### Coverage (measured 2026-09-11, pool scope = 444 recordings)

| Population | Count | % |
|---|---|---|
| ≥1 `song_components` row | 312 | 70.3% |
| Entry row with bpm AND key non-null | 309 | 69.6% |
| Exit row with bpm AND key non-null | 302 | 68.0% |
| Entry/exit theme non-null | 309 / 306 | ~69% |
| Entry/exit vocal_posture non-null | 309 / 306 | ~69% |
| Entry/exit energy_level non-null | 312 / 308 | ~70% |
| Recording aggregates theme/posture non-null | 311 | 70.0% |
| Song-level `key_confidence ≥ 0.6` (today's H8 population) | 250 | 56.3% |
| Song-level tempo+key (no confidence bar) | 444 | 100% |

132/444 (29.7%) have no components at all — they ride the fallback path. 1:1 song↔recording; no dedup ambiguity.

### Why the matrix must carry boundary values (structural constraint)

The transition matrix is precomputed over the whole pool, O(n²) pairs keyed `(from_hash_prefix, to_hash_prefix)` (`build_transitions.py:48-54`). `score_songset.py` receives only the matrix — never raw keys. **Any boundary-aware cfd/bpm_delta/key_compat must be baked into `TransitionCandidate` inside `build_transitions.py`.** The LLM planner reads the matrix; it cannot compute pair compatibility at plan time. This is why placement is scripts-first (user decision).

## Design

### Part 0 — Data ingestion (fetch + models)

#### 0.1 Extend `SongCandidate`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py`

New optional fields (all default `None`/`False` so stale caches validate unchanged):

```python
has_components: bool = False
# Boundary (role='entry') values, from song_components
entry_bpm: float | None = None
entry_key: str | None = None          # e.g. "G"
entry_mode: str | None = None         # defaults to song's musical_mode (boundary rows carry key only)
entry_energy_level_db: float | None = None
# Boundary (role='exit') values
exit_bpm: float | None = None
exit_key: str | None = None
exit_mode: str | None = None
exit_key_confidence: float | None = None
exit_energy_level_db: float | None = None
# Aggregates (chorus-preference, confidence-weighted)
component_theme_scores: dict[str, float] | None = None   # 12-value vocabulary
component_posture: str | None = None                     # To God | About God | To Congregation
component_posture_confidence: float | None = None
```

**Selection rule (per song):** choose the entry row deterministically as the lowest `occurrence_index`, then lowest `id` (stable). Exit row same rule among `role='exit'` rows. (Most songs have exactly one entry/exit row; the tiebreak exists for multi-row edge cases.)

**File:** `songset_constructor/db.py`

Add a third query alongside `POOL_QUERY`/`LINE_THEME_QUERY`, mirroring the existing second-query pattern (line scores fetched via `song_ids` + second query):

```sql
SELECT sc.song_id, sc.role, sc.component_type, sc.occurrence_index, sc.id,
       sc.bpm, sc.key, sc.key_confidence, sc.energy_level,
       sc.theme, sc.theme_confidence, sc.vocal_posture, sc.vocal_posture_confidence
FROM song_components sc
WHERE sc.song_id = ANY(%s)
  AND (sc.role IN ('entry', 'exit', 'entry_exit', 'loop_target')
       OR sc.theme IS NOT NULL OR sc.vocal_posture IS NOT NULL)
```

`role = 'entry_exit'` rows satisfy both boundaries (single-chorus songs store entry+exit as two rows on occurrence_index=1, but some songs may hold the combined role) — treat an `entry_exit` row as both the entry and the exit candidate.

**Post-fetch aggregation (Python, in `db.py` or a new `songset_constructor/components.py`):**

- **Chorus preference:** among candidate rows, chorus rows outrank non-chorus rows (`component_type == 'chorus'` first), then highest `theme_confidence` / `vocal_posture_confidence`, then lowest `occurrence_index`, then lowest `id`. This mirrors `_aggregate_recording_theme`'s chorus-preference tie-break (`audio.py:2781-2865`) — reuse its tie-break ordering conceptually; do not import admin command code into the constructor package.
- **Theme distribution:** for the 12 themes, collect component rows' `(theme, theme_confidence)`; weight each row's vote by `theme_confidence`; normalize to sum 1.0 → `component_theme_scores`. Missing theme on a row contributes no vote. If no votes → `component_theme_scores = None` (song falls back to fusion).
- **Posture:** same weighting over `vocal_posture`/`vocal_posture_confidence` → argmax → `component_posture` + its confidence. Chorus rows outrank. No vote → None (fallback chain below).
- **Chorus vote weighting:** the chorus-preference rule above governs which row wins per-boundary selection; for the theme/posture *distributions*, chorus-type rows contribute votes at full weight and non-chorus rows at half weight (chorus identity dominates without discarding verse testimony). The WHERE admits every row that can vote (essential roles + any row with a non-null theme or posture); rows with no LLM fields contribute nothing and are harmless.

**Posture fallback fetch:** the fallback chain's middle rung — `recordings.vocal_posture` (and `recordings.theme` while the query is open) — is fetched in the same POOL_QUERY join (recording-level columns, `schema.py:91-95`), added to `SongCandidate` as `recording_posture: str | None = None`. Aggregate rows exist for 311/444 pool songs, nearly identical to entry-row coverage, so this fallback is rarely needed — but f_posture's chain must be total, so it is fetched rather than assumed.

**Cache:** no changes needed — `model_dump(mode="json")` round-trips the new fields; old caches fill `None`/`False`.

### Part 1 — Enrichment (component theme primary, posture, energy normalization)

**File:** `lab/skills/songset-constructor/scripts/enrich_pool.py` (thin wrapper) + `songset_constructor/rules/phases.py` + new `songset_constructor/rules/components.py`

#### 1.1 Component-primary theme → phase inference

In the enrichment pass, replace the unconditional `fuse_themes(...)` call with:

```python
if cand.component_theme_scores:                       # primary path
    fused = dict(cand.component_theme_scores)
    theme_source = "component"
else:                                                 # fallback (132 component-less songs)
    fused = fuse_themes(title, lyrics, song_emb, line_emb)   # existing phases.py:33-59
    theme_source = "fusion"
fused = apply_seasonal_bias(fused, season)            # after either path (existing)
phase = infer_phase(fused, tempo_bpm)                 # existing phases.py:91-105
secondary_phases = infer_secondary_phases(fused, phase, tempo_bpm)
```

- Add `theme_source: str | None` to `SongCandidate` (values `"component" | "fusion" | None`) for report visibility.
- No confidence floor (user decision): any non-null component theme qualifies as primary.
- `infer_phase`/`infer_secondary_phases`/`THEME_TO_PHASE` are untouched — only the input distribution's source changes.

#### 1.2 Posture aggregation helper

In `rules/components.py` (new):

```python
def aggregate_posture(rows) -> tuple[str, float] | None:
    """Chorus-preference, confidence-weighted argmax over vocal_posture."""
```

Implementation mirrors 0.2's weighting; runs at fetch time (0.2) so enrichment only reads the field.

#### 1.3 Energy percentile normalization

Pool-wide pass in `enrich_pool.py` after per-song enrichment:

1. Collect all `entry_energy_level_db` and `exit_energy_level_db` values (non-null).
2. Compute each value's percentile rank within the pool → `entry_energy_pct` / `exit_energy_pct` (0.0–1.0). Persist both on `SongCandidate` (new fields).
3. Songs with `None` energy carry `None` percentiles and are skipped (not penalized) in f_energy; if fewer than 2 songs in the pool have any energy value, f_energy returns neutral 0.5 for all proposals (degenerate-pool guard).

Normalization is **within the fetched pool only** (not catalog-wide) — the arc is relative to tonight's candidate set. This is a deliberate choice: absolute dB is not comparable across masters, and pool-relative ranks are stable under the pool filter actually in play.

### Part 2 — Transition matrix (boundary-first)

**Files:** `songset_constructor/rules/transitions.py`, `rules/harmony.py`, `scripts/build_transitions.py`, `models.py`

#### 2.1 Boundary-aware `recommend_transition`

`TransitionCandidate` gains:

```python
boundary_source: str = "song_level"   # "component" | "song_level"
```

New computation, inside `recommend_transition` (or a boundary-aware wrapper it delegates to):

```python
# Boundary-first selection (one helper on SongCandidate or inline in recommend_transition)
def _boundary_pair(left, right):
    # From side: prefer the song's exit boundary; fall back to song-level key
    if left.exit_key:
        from_key, from_mode, from_conf = left.exit_key, left.exit_mode, left.exit_key_confidence
        left_is_boundary = True
    else:
        from_key, from_mode, from_conf = left.musical_key, left.musical_mode, left.key_confidence
        left_is_boundary = False
    # To side: prefer the destination's entry boundary
    if right.entry_key:
        to_key, to_mode, to_conf = right.entry_key, right.entry_mode, right.entry_key_confidence
        right_is_boundary = True
    else:
        to_key, to_mode, to_conf = right.musical_key, right.musical_mode, right.key_confidence
        right_is_boundary = False
    boundary_source = "component" if (left_is_boundary and right_is_boundary) else "song_level"
    return from_key, from_mode, from_conf, to_key, to_mode, to_conf, boundary_source
```

Note: `SongCandidate` gains `exit_mode` / `entry_mode` alongside the key fields in 0.1 (component rows carry key only; mode defaults to the song's `musical_mode`, since boundary rows do not store a separate mode).

- `cfd()` and `suggest_key_shift()` are called with the selected boundary keys/modes (their signatures already take key+mode strings — no change needed beyond the call site).
- **`and` semantics (confirmed design):** a pair is boundary-sourced only when **both** sides have boundary components; any one-sided pair falls back to song-level on both sides. This keeps the matrix's `boundary_source` an honest statement about the pair, not a per-song flag — mixed pairs (boundary key on one side, song-level on the other) are explicitly not in v1 scope.
- `key_compat` from the same `key_compatibility_score(distance)` table — unchanged.
- `bpm_delta`: `abs(exit_bpm(A) − entry_bpm(B))` when both boundary BPMs exist, else today's `abs((to.tempo_bpm or 0) − (from.tempo_bpm or 0))`. **Fix the silent-0 trap while here:** when the winning path has a `None` BPM on either side, append a warning to `TransitionCandidate.warnings` ("missing boundary bpm on <song>, fell back to song-level" / "missing bpm on both sides — delta unreliable").

#### 2.2 H2/H3 boundary semantics (validator change)

**File:** `rules/hard_constraints.py:75-80`

- **H2 (opener tempo floor):** effective value = `entry_bpm` if present else `tempo_bpm`. Missing both → H2 fails (unchanged behavior for unknown tempo).
- **H3 (closer ceiling):** effective value = `exit_bpm` if present else `tempo_bpm`.
- The relax knobs (`relax_h2_bpm`, `relax_h3_bpm`) keep operating on the effective boundary-aware value.

#### 2.3 H8 boundary gate

**File:** `rules/hard_constraints.py:106-108`

Concretely: when `boundary_source == "component"`, H8 checks the to-side `entry_key_confidence >= 0.6` of the destination song (the row whose key is being transposed *into*). Fallback to song-level confidence when the boundary key is absent (boundary_source == "song_level"). The from-side confidence is not separately gated in v1: the from-side boundary key is *departed*, not transposed-into, and `suggest_key_shift` already refuses shifts whose resulting CFD is worse (harmony.py:75-90). `enrich_pool.py:108`'s range-shift gate stays song-level (singing-range shifts transpose the whole song, not a boundary).

#### 2.4 H4/H5 and fan-out

- H4 reads `transition.bpm_delta` (already boundary-aware after 2.1) — no code change beyond the fallback warning.
- H5 reads `transition.cfd` + `suggested_key_shift` (boundary-aware after 2.1).
- Fan-out (`beam.py:47-51`) consumes transition fields only — inherits boundary awareness for free.
- The one semantic trap (scout-confirmed): everything must be finalized at matrix build time; `score_songset.py` cannot recompute. Assert this in a test.

### Part 3 — Scoring (six-way weights, f_energy, f_posture)

**Files:** `rules/fitness.py`, `rules/proposals.py`, `score_songset.py`, `models.py`

#### 3.1 Weight renormalization

**File:** `rules/fitness.py:77-93`

```python
total = 0.35*f_theme + 0.25*f_tempo + 0.20*f_harmony + 0.10*f_diversity + 0.05*f_energy + 0.05*f_posture
```

`ScoreBreakdown` (models.py) gains `f_energy: float`, `f_posture: float`. Old breakdowns (stale caches of score output, if any) don't exist as persisted artifacts — proposals are recomputed each run, so no compat concern.

#### 3.2 f_energy

**File:** `rules/fitness.py` (new function)

Per item, arc energy value = `entry_energy_pct` (the energy the song *arrives* with). For the closer, also consider `exit_energy_pct` (the energy the set leaves with). Ordinal expectations per phase template (expressed as ordering constraints, not values):

- **Adjacency smoothness:** for each adjacent pair, penalize `|entry_pct(B) − exit_pct(A)|` on the percentile scale.
- **Arc shape:** opener arrival should not exceed closer departure by a wide margin for the default templates (i.e., sets should generally land softer than they open); encode as `max(0, entry_pct(first) − exit_pct(last))` penalized. No reward for matching an absolute level.

**v1 simplification (explicit deviation from the R2 wording):** the arc-shape term above is a single opener-vs-closer constraint, not the full per-template ordinal expectations the R2 discussion sketched (e.g., phase-3 peak for the 5-song template). Implement exactly the two bullet terms above; the per-template extension is a deliberate deferral — add it only if score distributions on real pools show sets gaming the opener/closer check mid-arc.

```python
def f_energy(items, n) -> float:
    # 0.5·adjacency smoothness + 0.5·arc-shape (ordinal, percentile inputs)
    # items lacking percentiles are skipped; <2 usable items → 0.5 (neutral)
```

**Asymmetry note (deliberate):** f_tempo stays song-level (`item.bpm`, proposals.py:34,78) while H2/H3 are boundary-aware — whole-song tempo remains the liturgical "feel" for the arc term, boundary BPM only governs adjacency (H4) and the opener/closer floor/ceiling checks. Do not "fix" this asymmetry without a user decision.

#### 3.3 f_posture

**File:** `rules/fitness.py` (new) + fit matrix constant in `rules/phases.py` (or `rules/components.py`)

```python
POSTURE_PHASE_FIT = {          # rows: posture, cols: phase 1..5
    "To God":          {1: 0.5, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.5},
    "About God":       {1: 1.0, 2: 0.5, 3: 0.5, 4: 0.0, 5: 1.0},
    "To Congregation": {1: 1.0, 2: 0.0, 3: 0.5, 4: 1.0, 5: 1.0},
}
# missing posture → 0.5 (neutral, no penalty)
```

`f_posture = mean(fit(item.component_posture, item.phase) for items)`. Liturgical rationale (confirmed in session): direct address (To God) peaks in Worship/Response; testimony/declaration (About God) fits Call and Commission; congregational exhortation (To Congregation) fits Call/Response/Commission but is not Worship.

#### 3.4 `ProposalItem` fields

**File:** `rules/proposals.py` — populate when rebuilding from the pool: `has_components`, `theme_source`, `component_posture`, `entry_energy_pct`, `exit_energy_pct`, and boundary key/BPM for report rendering.

### Part 4 — Report

**Files:** `scripts/write_report.py`, `artifacts/writer.py`

- **Pool overview:** component coverage (count + % with components), `theme_source` distribution (component vs fusion).
- **Per-proposal item table:** add columns — `boundary (entry/exit) key+BPM` alongside song-level, `theme_source`, `posture` (or `—` when missing), `energy pct` (entry/exit).
- **Per-adjacency:** `boundary_source` marker (`component`/`song-level`) next to each transition's cfd/bpm_delta, plus boundary-BPM warnings.
- **Per-proposal arcs:** energy trajectory (entry/exit pct per position), posture sequence vs phase, with the fit values from the matrix.
- **Score breakdown table:** six components incl. `f_energy`, `f_posture`; note the weights changed from 0.40/0.30/0.20/0.10.
- **Warnings section:** songs with `has_components = false` used in a proposal ("song-level fallback: theme via fusion, no energy/posture data").

### Part 5 — SKILL.md updates (Phase 2 only)

After scripts ship (Rollout below), update `lab/skills/songset-constructor/SKILL.md`:

- Step 2 (fetch): mention boundary fields + `has_components` on SongCandidate.
- Step 3 (enrich): document `theme_source`, percentile normalization, and that fusion is now fallback.
- Step 4 (transitions): document `boundary_source` and boundary-first semantics.
- Step 5 (planning): note posture/energy arc considerations in planning guidelines; keep H-table unchanged (no new H-codes).
- Step 6 (scoring): new weight table (.35/.25/.20/.10/.05/.05) + interpretation rows for f_energy/f_posture.
- Step 10 (report): new columns/arcs.
- Never describe flags or fields that don't exist yet — SKILL.md must always match shipped scripts.

## Rollout

1. **Phase A (this spec's code work):** Parts 0–4. All additive; component-less songs behave exactly as today (verified by the regression test below).
2. **Phase B:** Part 5 SKILL.md update, in the same PR as Phase A or immediately after — SKILL.md never describes unshipped behavior.
3. **Optional later (out of scope):** components backfill for the 132 songs (coverage 70%→~100%), H-code elevation if score distributions justify it, energy/posture hard-constraint elevation.

## Tests & Verification

Deterministic JSON-in/JSON-out tests in the admin-cli test suite (existing convention; run with `NO_COLOR=1 uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v`):

1. **Regression (critical):** pool with zero components → output of enrich/build_transitions/score identical to pre-change behavior (same scores, same transitions, same validation). This is the "strictly additive" guarantee.
2. Boundary math: pairs with populated exit/entry keys produce `boundary_source="component"`, cfd computed on boundary keys; missing one side → fallback + `boundary_source="song_level"`.
3. Silent-0 fix: missing boundary BPM on a fallback pair yields a warning in `TransitionCandidate.warnings`.
4. H2/H3: opener floor on entry BPM, closer ceiling on exit BPM; song-level fallback when absent.
5. H8: boundary gate (entry-side confidence) with song-level fallback; song below 0.6 at song level but boundary ≥ 0.6 becomes transposable.
6. Theme primary/fallback: songs with component themes use them (`theme_source="component"`); component-less songs use fusion; seasonal bias applies in both paths.
7. Posture fit: matrix values match the confirmed table; missing posture → 0.5; f_posture = mean fit.
8. Energy: percentile normalization math (pool-relative, ties handled); f_energy neutral 0.5 when <2 songs have energy; adjacency + arc-shape terms behave on synthetic pools.
9. Weights: six-way breakdown sums to 1.0; existing four components' values unchanged from today's formulas.
10. Matrix-only visibility: score/H-checks consume boundary values only via `TransitionCandidate` (no direct key reads) — pin the structural constraint.
11. Report smoke: run `write_report.py` on a proposal containing both component-having and component-less songs; assert coverage lines, boundary_source markers, and warnings render.
12. Cache: old `pool_*.json` (no new fields) validates to `None`/`False` defaults.

## Notes for the Implementer

- `song_components.key` field name (not `musical_key`); enum values are English postures, Chinese themes.
- Component `bpm_confidence` is a duration tier — soft weight only, never an H8-style gate.
- Component key detection returns `None` when chroma margin < 0.03 — coverage numbers above already account for this.
- Do NOT import admin command code (`commands/audio.py`) into the constructor; mirror the aggregation logic instead (package boundary: constructor reads DB via its own `db.py` only).
- Fan-out/dead-end computation consumes transition fields only — verify it stays honest when boundary values shift which pairs pass H4/H5 thresholds.
- Keep every new field optional; the fallback chain (boundary → song-level → neutral) must be total. No code path may assume components exist.