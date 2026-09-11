# Songset Constructor — Component-Metadata Integration v2

**Date:** 2026-09-11
**Skill location:** `lab/skills/songset-constructor/` (canonical; sole skill target)
**Supersedes:** `specs/songset-constructor-component-metadata-integration-v1.md` (kept for history; do not edit it)
**Spec type:** Implementation plan (code enhancement + SKILL.md documentation)
**Audience:** Fresh implementing agent — this spec is self-contained.
**Decision record:** The components-primary theme policy is recorded in `docs/adr/0006-component-theme-primary-for-phase-inference.md` (unchanged by this revision).
**v2 changes from v1:** (1) H8 gate stays literal boundary-first with the regression disclosed and the false "unlocks" claim removed; (2) neutral weight-redistribution fix for the regression-test conflict; (3) distribution weighting pinned (both rules, each in its place); (4) `_sort_key_theme_diverse` fixed to count positive themes; (5) LangGraph path explicitly out of scope (deprecated); (6) boundary BPM per-side best-available provenance; (7) `_pool_prompt` extended with posture/energy; (8) `write_report.py` transitions input made real.

## Goal

Leverage component-level (per-section) metadata in the songset-constructor skill pipeline. Today the pipeline is 100% song-level; `song_components` — per-section BPM, key, theme, energy, and vocal posture already persisted by the analysis service — is read by the constructor **zero** times. This spec integrates all five signals:

1. **Boundary key** — transition compatibility (`cfd`, `key_compat`, `suggested_key_shift`) computed on song A's *exit* component vs song B's *entry* component, falling back to song-level key.
2. **Boundary BPM** — `bpm_delta` and hard constraints H2/H3/H4 computed on boundary BPM, falling back to song-level BPM.
3. **Component theme** — phase inference consumes component-derived, chorus-preference theme distributions as the **primary** signal; the existing 4-source text fusion (title 35% / lyrics 25% / song embedding 25% / line embedding 15%) becomes fallback for component-less songs.
4. **Energy** — new ordinal soft term `f_energy` on pool-percentile-normalized component energy.
5. **Posture** — new soft term `f_posture` scoring chorus-preference VocalPosture against the phase template.

**Policy decisions (user-confirmed via review interview, 2026-09-11):** components-primary theme (ADR-0006), boundary-first with fallback everywhere, soft-only scoring (no new H-codes), fallback+flag coverage policy, neutral-weight-redistribution six-way scoring, literal boundary-first H8 with disclosed regression, per-side best-available BPM, dense-vocab distributions with a fixed beam sort, skill-scripts-only scope (LangGraph path out of scope — deprecated), extended planner prompt. Backfill remains out of scope.

## Scope Clarifications

- **All five signals, full integration:** boundary key + BPM feed the transition matrix; component theme feeds phase inference; energy and posture become new soft score terms; all three new signals render in `write_report.py` per proposal.
- **Boundary-first with song-level fallback:** when both songs of an adjacent pair have populated boundary components (key present on both sides), the matrix computes exit(A)↔entry(B); otherwise it falls back to today's song-level computation on **both sides** — keys/modes never mix provenance within one pair. `boundary_source` on `TransitionCandidate` records which path produced each pair.
- **Boundary BPM is per-side best-available (user decision, supersedes pair-level for BPM only):** within a `boundary_source == "component"` pair, `bpm_delta = abs(exit_bpm(A) − entry_bpm(B))` using each side's boundary BPM when present; when one side's boundary BPM is missing, that side falls back to its song-level BPM and the transition gains a per-song warning ("missing exit bpm on <song>; used song-level"). When both boundary BPMs are missing but both boundary keys exist, delta falls fully back to song-level with warning "missing boundary bpm on both sides — delta unreliable". Provenance of the BPM inputs is therefore independent of the pair's key provenance; `boundary_source` describes the keys, warnings describe the BPM gaps.
- **H2/H3 become boundary-aware:** opener floor checks the opener's *entry* BPM, closer ceiling checks the *closer's exit* BPM; song-level BPM remains the fallback. Boundary BPM governs the adjacency (H4); H2/H3 check the boundary side the worship leader actually sings into/out of. These per-song checks are independent of `boundary_source`.
- **H8 stays literal boundary-first (user decision, knowingly accepted regression):** when the pair's destination song has a boundary key, transposition is gated by that row's `entry_key_confidence ≥ 0.6`; falls back to song-level `key_confidence` when the boundary key is absent. **Measured cost (disclosed, accepted):** pool transposable population drops 250 (56.3%) → 201 (45.3%) — 80 songs with song-level ≥ 0.6 lose transposition because their boundary confidence is lower; 31 songs gain it. Component key detection yields low confidence far more often than song-level analysis (200 of 316 boundary rows sit below 0.6). This degrades transition fan-out modestly; accepted as the price of honest boundary data. Fan-out impact is tracked in the report (Part 4) so the regression stays observable.
- **Components-primary theme (ADR-0006):** when a recording has ≥1 non-null component theme, phase inference consumes the chorus-preference, `theme_confidence`-weighted component distribution. The existing fusion becomes fallback. Seasonal bias applies after either path.
- **Distribution weighting pinned (user decision):** per-boundary *row selection* uses chorus-outrank tie-breaks (chorus type first, then confidence, then occurrence_index, then id); the *distribution* weights chorus-type rows at 1.0 and non-chorus rows at 0.5, each vote additionally scaled by its confidence. Worked example pinned in Part 0 so implementations agree.
- **Soft-only scoring:** no new hard-constraint codes. f_energy and f_posture join the weighted breakdown; failures surface as report warnings, never block a proposal.
- **Neutral weight redistribution (user decision):** when a new term's input is absent pool-wide, its 0.05 weight is redistributed across the four original weights instead of contributing `0.05·0.5`. Component-less pools therefore score **identically to today** — the "strictly additive" regression test passes verbatim.
- **Energy is ordinal, never absolute:** `song_components.energy_level` is raw mean-RMS dB (negative, ~-12..-30). All energy logic operates on **pool-percentile ranks**; no absolute dB thresholds anywhere.
- **Beam theme-diversity sort wakes up (user decision):** `_sort_key_theme_diverse` is fixed to count themes with score > 0 instead of dict keys. This changes beam-sort behavior for **all** songs (accepted; the term was degenerate before — `fuse_themes` always emits 12 keys, so `theme_count` was constant). Distributions remain dense 12-key zero-filled dicts matching `fuse_themes` output shape.
- **Fallback + flag coverage:** the 132/444 pool songs (29.7%) without components are **not** dropped; they use song-level values, are marked `has_components = false`, and the report shows per-proposal coverage.
- **Backfill of component-less songs is out of scope.**
- **SKILL.md is not edited by this spec's implementation phase until scripts ship** — see Rollout Order.
- **`.agents/skills/songset-constructor/` mirror is out of scope** (externally managed).
- **LangGraph `sow-admin songset construct` path is out of scope (user decision — deprecated):** `graph/nodes.py`, `runner.py`, `commands/songset.py` construct are NOT updated by this spec. The graph path keeps song-level behavior; the divergence is documented in SKILL.md's Overview ("applies to the skill scripts' pipeline; the deprecated `sow-admin songset construct` command retains song-level-only behavior"). No admin-side imports of skill scripts.
- **Webapp / Android / render-worker are out of scope.** Per ADR-0005 posture stays admin-side.

## Background — Current State (file:line verified 2026-09-11, all claims re-verified this session)

### The constructor pipeline is song-level only

Skill scripts in `lab/skills/songset-constructor/scripts/` are thin CLI wrappers that inject `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/` into `sys.path` (e.g. `fetch_pool.py:21-24`). Grep for `component|vocal_posture|energy|SongComponent|song_components` across the skill and the package: **no data references** (only incidental prose "Score components"). The DB layer never touches `song_components`.

### Data flow today

- **Fetch:** `fetch_catalog_pool` (`songset_constructor/db.py:99-108`) runs `POOL_QUERY` (`db.py:20-38`, joins `songs`+`recordings`+`song_embedding` vs `theme_anchors` → `song_theme_scores_raw`) and `LINE_THEME_QUERY` (`db.py:40-53`, line-embedding → `line_theme_scores_raw`). Recording-level columns: `tempo_bpm`, `musical_key` (AS `r_musical_key`, preferred over song key at `db.py:86`), `musical_mode`, `key_confidence`, `loudness_db`, `duration_seconds`. Row→`SongCandidate` at `_candidate_from_row` (`db.py:76-96`); model at `models.py:8-36`.
- **Cache:** `~/.cache/sow/songset_constructor/pool_<sha256(pool_limit:sorted_series)[:16]>.json` (`cache.py:17-23`), atomic write via tmp+`os.replace` (`cache.py:47-57`), `model_dump(mode="json")` round-trip — new optional `SongCandidate` fields flow through automatically; old caches validate to `None` defaults. CLI flags `--no-cache`, `--prefer-fresh`, `--allow-stale` (default True) at `fetch_pool.py:36-55`; stale fallback `_try_load_stale` (`fetch_pool.py:152-166`).
- **Enrich:** drops songs missing BOTH tempo and key (`enrich_pool.py:138-140`); theme fusion `fuse_themes` (`rules/phases.py:33-59`: title/lyrics agree → 0.45/0.35/0.15/0.05, disagree → 0.35/0.25/0.25/0.15; empty line-emb redistributes); seasonal bias `apply_seasonal_bias` (`phases.py:73-88`); phase inference `infer_phase` (`phases.py:91-105`: argmax theme → `THEME_TO_PHASE` (7-20); 聖靈+bpm<70 → phase 4; tempo-only fallback ladder ≥100→1, ≥90→2, ≥70→3, <70→4); `infer_secondary_phases` (`phases.py:108-134`, themes ≥ 0.85×max, ≤2); leader-range fields `_compute_range_fields` (`enrich_pool.py:94-129`, ±2 shift gate at `:108`).
- **Transitions:** all ordered pairs with CFD ≤ 6 (`build_transitions.py:53`); `recommend_transition(left, right)` (`rules/transitions.py:13-58`) reads song-level `musical_key/musical_mode/tempo_bpm/key_confidence` only. CFD: `fifth_distance_on_circle` (`rules/harmony.py:47-51`), `cfd()` on `relative_major_pc` (`harmony.py:41-44, 54-58`); key parse `normalize_key` (`harmony.py:17-29`, missing → C major); `key_compatibility_score` lookup 0/1/2/3/4/other → 1.0/0.92/0.78/0.55/0.32/0.15 (`harmony.py:61-72`); `suggest_key_shift` tries −2..+2 minimizing (cfd, |shift|) (`harmony.py:75-90`); technique+crossfade+gap ladder distance≤1 pivot 0s/2 · ≤2 relative-or-direct 0s/2 · shifted≤2 transposition 4s/4 · ==3 vamp 6s/4 · else direct_modulation 8s/6 (`transitions.py:25-44`); `crossfade_enabled = crossfade_seconds > 0` (`transitions.py:54`); low-confidence warnings `transitions.py:20-23`. `bpm_delta = abs((to.tempo_bpm or 0) − (from.tempo_bpm or 0))` (`transitions.py:18`) — missing BPM collapses to 0 delta, silently "compatible." Fan-out: `compute_fan_out` (`rules/beam.py:35-56`), counts transitions with bpm_delta ≤ h4 AND (cfd ≤ h5 OR shift ≠ 0) (`beam.py:47-51`).
- **Score:** stdin `{"items", "pool", "transitions", "config"}`; `proposal_from_draft` (`rules/proposals.py:56-95`); fitness in `rules/fitness.py`: f_theme (29-37, `1 − Σ|phase−template|/(4n)`, `_THEME_TEMPLATES` 12-22), f_tempo (40-47, `0.75·smoothness + 0.25·arc`, smoothness `1 − min(1, Σ|Δbpm|/(25n))`, arc bonus first ≥ last → 1.0 else 0.75, <2 known bpms → 0.5), f_harmony (50-60, mean key_compat, missing transition → 0.2), f_diversity (63-68, `0.7·song-uniqueness + 0.3·theme diversity`), total = `0.40·theme + 0.30·tempo + 0.20·harmony + 0.10·diversity` (77-93). Range penalty (`score_songset.py:95-124`): per item `min(0.05·circular_dist, 0.20)` on `(tonic_pc + key_shift) % 12` outside comfortable set, subtracted outside the weights; `ScoreBreakdown.range_penalty` at `models.py:92`. Validation H0–H9 in `rules/hard_constraints.py` (H2/H3 `:75-80`, H4 `:83-92`, H5 `:93-97`, H8 `:106-108`, H9 `:110-116`; relax knobs in `config.py:34-48`, properties 82-110).
- **Report:** `scripts/write_report.py` — `_run_summary` (132-167), `_pool_overview` (170-218), `_proposal_section` (221-303); `MAX_DURATION_SECONDS = 1500` hardcoded (`:35`); helpers in `artifacts/writer.py`. Note: the docstring (`:5-15`) already advertises `"transitions": [...]` in the stdin contract, but `main()` (`:56-69`) never parses it — Part 4 makes this real.
- **Beam sort:** `_SORT_STRATEGIES` (`beam.py:118-123`); `_sort_key_theme_diverse` (`beam.py:89-96`) currently counts dict keys (degenerate — always 12×items since `fuse_themes` emits dense zero-filled dicts).

### The component data model (admin side)

- **Table:** `song_components` — 29 columns; DDL `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:228-249` + v5 ALTERs (294-307) + v6 (311-313); unique `(song_id, component_type, occurrence_index, role)` v3 (251-270). Fields relevant here: `bpm`, `key` (note: **`key`**, not `musical_key`; e.g. `"G"`), `key_confidence` (sigmoid of chroma margin, default 0.7), `bpm_confidence` (duration tier 0.9/0.7/0.4 — soft weight only, NOT an H8-style gate), `energy_level` (mean RMS in dB, negative, `components.py:1735`), `theme` (12-value enum CHECK, `schema.py:300-301`), `theme_confidence`, `vocal_posture` (3-value CHECK `'To God'/'About God'/'To Congregation'`, `schema.py:94-95, 302-303`), `vocal_posture_confidence`, `role` (`entry|exit|loop_target|entry_exit|none`, `schema.py:236-237`), `component_type`, `occurrence_index`, `line_start/line_end`.
- **Model:** `SongComponent` dataclass `admin/db/models.py:589-649` (+ `from_row` 651-683, `to_dict` 685-716); column list `SONG_COMPONENT_COLUMNS_SELECT` (`schema.py:333-344`).
- **Linkage:** every row carries BOTH `song_id` and `content_hash` (FK recordings) (`schema.py:231-232`). Pool scope is 1:1 song↔recording (444 songs = 444 hash prefixes — measured 444/444/444), so per-song boundary fields are unambiguous.
- **Readers:** `DatabaseClient.get_song_components[_by_role|_by_type|_entry_exit]` (`admin/db/client.py:2159-2231`). The constructor package does NOT import these — it queries `song_components` via its own `db.py` (Part 0).
- **Populated by default only on essential roles** (entry/exit/loop_target/entry_exit + first bridge): theme/posture/audio fields NULL on verses and later choruses unless `--all-components` (`commands/audio.py:3323`, `classifier.py:170-182`). Boundary rows (entry/exit) are the best-populated rows.
- **Per-component audio features** are independently detected per slice (`ops/analysis-service/.../components.py:1568-1772`): component BPM via beat grid/tempo on slice (1624-1638), key from sliced chroma returning None when top-2 margin < 0.03 (435-440, 1646-1661), energy = mean RMS dB of vocals stem else full mix (1713-1735), confidences (1739-1772). Component BPM/key CAN disagree with song-level values; per-component theme/posture come from the LLM classifier (`classifier.py:214+`) with Chinese-pronoun pre-pass (祢→To God, 祂→About God, 讓我們/當/應當/要/彼此/眾/凡→To Congregation, `classifier.py:42-55`), confidence-adjusted (556-601), out-of-enum coerced to None on DB write (`db/client.py:2127-2132`).
- **Recording-level aggregates already exist and are unread by the skill:** `recordings.theme` / `recordings.vocal_posture` (schema.py:91-95), aggregated by `_aggregate_recording_theme` (`commands/audio.py:2781-2865`) with chorus-preference tie-breaking. Posture persisted-but-not-surfaced per `docs/adr/0005-posture-persisted-not-surfaced.md`, which names the songset constructor as posture's intended consumer.
- **Theme vocabulary:** 12 values `SONG_COMPONENT_THEMES` (`schema.py:285-288`) — identical to the constructor's `THEMES` (`songset_constructor/rules/themes.py:9`) and `THEME_VOCAB` (`themes.py:11-24`). No new vocabulary is introduced by this spec.

### Coverage (measured 2026-09-11 against the live DB, pool scope = 444 recordings)

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
| **Entry-side boundary `key_confidence ≥ 0.6` (with key present)** | **116/316** | **36.7%** |
| Song-level tempo+key (no confidence bar) | 444 | 100% |

132/444 (29.7%) have no components at all — they ride the fallback path. 1:1 song↔recording; no dedup ambiguity. Additional measured data facts for the implementer: boundary `key` values are always bare note names (`B,E,F#,C#,D,A,C,A#,G,F,G#` — never mode-suffixed); 49/316 entry keys differ from the recording-level tonic; `entry_exit` combined-role rows do not exist in production data (0 songs) but the code handles them for future-proofing; exactly 6 songs have >1 row for the same boundary role (the deterministic tiebreak is needed); `theme_confidence`/`vocal_posture_confidence` are never NULL when theme/posture is set (0 rows) — keep the `or 0.0` guard anyway.

### Why the matrix must carry boundary values (structural constraint)

The transition matrix is precomputed over the whole pool, O(n²) pairs keyed `(from_hash_prefix, to_hash_prefix)` (`build_transitions.py:48-54`). `score_songset.py` receives only the matrix — never raw keys (`score_songset.py:64-71` reconstructs `matrix` from JSON transitions only). **Any boundary-aware cfd/bpm_delta/key_compat must be baked into `TransitionCandidate` inside `build_transitions.py`.** The LLM planner reads the matrix; it cannot compute pair compatibility at plan time. This is why placement is scripts-first.

## Design

### Part 0 — Data ingestion (fetch + models)

#### 0.1 Extend `SongCandidate`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py`

New optional fields (all default `None`/`False` so stale caches validate unchanged):

```python
has_components: bool = False
# Boundary (role='entry') values, from song_components
entry_bpm: float | None = None
entry_key: str | None = None          # e.g. "G" (bare note name, never mode-suffixed)
entry_mode: str | None = None         # copied from the song's recording musical_mode at aggregation (see 0.2)
entry_key_confidence: float | None = None
entry_energy_level_db: float | None = None
# Boundary (role='exit') values
exit_bpm: float | None = None
exit_key: str | None = None
exit_mode: str | None = None          # same rule: copied from recording musical_mode
exit_key_confidence: float | None = None
exit_energy_level_db: float | None = None
# Aggregates (chorus-preference, confidence-weighted)
component_theme_scores: dict[str, float] | None = None   # dense 12-key zero-filled; None = no votes at all
component_posture: str | None = None                     # To God | About God | To Congregation
component_posture_confidence: float | None = None
recording_posture: str | None = None                     # recordings.vocal_posture (fallback rung)
recording_theme: str | None = None                       # recordings.theme (unused in scoring; diagnostic only)
theme_source: str | None = None                          # "component" | "fusion" | None; set in Part 1
# Energy percentiles (set in Part 1.3, pool-wide pass)
entry_energy_pct: float | None = None
exit_energy_pct: float | None = None
```

(Note vs v1: `entry_key_confidence` is added — H8's gate needs it; `theme_source` moves here from Part 1.1 for one canonical field list.)

**Selection rule (per song):** choose the entry row deterministically as the lowest `occurrence_index`, then lowest `id` (stable). Exit row same rule among `role='exit'` rows. Measured: 6 songs have >1 row per boundary role — the tiebreak is load-bearing for them, hypothetical for the rest.

**File:** `songset_constructor/db.py`

Add a third query alongside `POOL_QUERY`/`LINE_THEME_QUERY`, mirroring the existing second-query pattern (fetched via `song_ids` + second query, exactly like `fetch_line_theme_scores` at `db.py:111-119`):

```sql
SELECT sc.song_id, sc.role, sc.component_type, sc.occurrence_index, sc.id,
       sc.bpm, sc.key, sc.key_confidence, sc.energy_level,
       sc.theme, sc.theme_confidence, sc.vocal_posture, sc.vocal_posture_confidence
FROM song_components sc
WHERE sc.song_id = ANY(%s)
  AND (sc.role IN ('entry', 'exit', 'entry_exit', 'loop_target')
       OR sc.theme IS NOT NULL OR sc.vocal_posture IS NOT NULL)
```

`role = 'entry_exit'` rows satisfy both boundaries — treat an `entry_exit` row as both the entry and the exit candidate. (0 such rows exist today; keep the handling for schema-conformance.)

**Fetch function (new, in `db.py`):**

```python
def fetch_component_rows(song_ids: list[str], *, client: ReadOnlyClient) -> dict[str, list[tuple]]:
    """Component rows for each song_id; empty list when the song has none."""
```

Called from `fetch_catalog_pool` after `fetch_line_theme_scores`, then per-song aggregation (below) populates the new fields via `model_copy(update=...)` — same pattern as the existing `line_theme_scores_raw` merge at `db.py:105-108`.

**Recording-level additions to POOL_QUERY (0.1b):** extend `CONSTRUCTOR_RECORDING_COLUMNS` (`db.py:15-18`) with `r.theme AS r_recording_theme, r.vocal_posture AS r_recording_posture`; thread both through `_candidate_from_row` into `recording_theme`/`recording_posture` (new row indices; the 17-column tuple layout becomes 19 — update the layout comment in `tests/songset_construct/test_db_queries.py:10-15` and every test row tuple in that file).

#### 0.2 Post-fetch aggregation (new module `songset_constructor/components.py`)

Pure-Python, no admin imports (package boundary: constructor reads DB via its own `db.py` only; mirror `_aggregate_recording_theme`'s chorus-preference tie-break conceptually — do NOT import `commands/audio.py`).

```python
def aggregate_components(rows: list[dict]) -> dict:
    """Aggregate one song's component rows into SongCandidate field updates.

    Returns {} when rows is empty (song has no components).
    Output keys: has_components, entry_bpm, entry_key, entry_mode, entry_key_confidence,
    entry_energy_level_db, exit_bpm, exit_key, exit_mode, exit_key_confidence,
    exit_energy_level_db, component_theme_scores, component_posture, component_posture_confidence.
    """
```

Rules (all pinned):

1. **Boundary row selection:** among rows with `role='entry'` (or `'entry_exit'`), pick lowest `occurrence_index`, then lowest `id` → entry row; same among `role='exit'` (or `'entry_exit'`) → exit row. From the selected row take `bpm`, `key`, `key_confidence`, `energy_level` (None stays None). `entry_mode`/`exit_mode` are NOT from the row — the component rows store bare note names only; copy the song's recording-level `musical_mode` (the pool row's `musical_mode`) into both. This matters: `cfd()` receives `(key, mode)`; a None mode makes `normalize_key` treat the key as major, silently corrupting CFD for minor-key songs.
2. **Theme distribution:** start `{theme: 0.0 for theme in THEMES}` (dense, zero-filled — required by beam sort's count-positive fix in Part 2.5 and by the report's rendering). For every row with non-null `theme`: vote weight = `(1.0 if row.component_type == 'chorus' else 0.5) * (row.theme_confidence or 0.0)`; add to that theme's slot. After all rows, if total weight > 0, normalize the 12 values to sum 1.0 → `component_theme_scores`; else `component_theme_scores = None`.
   - **Worked example (pinned for tests):** 2 choruses (敬拜, conf 0.8; 差遣, conf 0.6) + 1 verse (感恩, conf 0.9). Weights: 敬拜 1.0·0.8=0.8; 差遣 1.0·0.6=0.6; 感恩 0.5·0.9=0.45. Total 1.85 → distribution `{敬拜: 0.432, 差遣: 0.324, 感恩: 0.243, …zeros}` (rounded for readability). The chorus-majority outcome (敬拜 argmax) is intended; a 2-vs-1 chorus-count majority is diluted but preserved by the 1.0-vs-0.5 ratio.
3. **Posture:** same weighting over `vocal_posture`/`vocal_posture_confidence` → argmax → `component_posture` + its (weighted-average) confidence. Chorus rows outrank via the 1.0/0.5 weight — the separate "outrank" language from v1 line 132 applies ONLY to boundary-row selection (rule 1), never to distributions. No vote → None.
4. **`has_components = True` iff the song had ≥1 row.** A song with rows but all-None theme/posture/key still gets `has_components = True` (it has boundary data potentially; per-field fallbacks handle the gaps).

**Cache:** no changes needed — `model_dump(mode="json")` round-trips the new fields; old caches fill `None`/`False` (verified mechanism at `cache.py:26-40,47-57`).

### Part 1 — Enrichment (component theme primary, posture, energy normalization)

**Files:** `lab/skills/songset-constructor/scripts/enrich_pool.py` (thin wrapper) + `songset_constructor/rules/phases.py` + new `songset_constructor/rules/components.py`

#### 1.1 Component-primary theme → phase inference

In the enrichment pass, replace the unconditional `fuse_themes(...)` call (`enrich_pool.py:146`) with:

```python
if cand.component_theme_scores:                       # primary path (dense dict; truthy only when any vote exists)
    fused = dict(cand.component_theme_scores)
    theme_source = "component"
else:                                                 # fallback (132 component-less songs)
    title = classify_title_themes(cand.title, cand.title_pinyin)
    lyrics = classify_lyrics_themes(cand.lyrics_raw)
    song_emb = normalise_cosine_scores(cand.song_theme_scores_raw)
    line_emb = normalise_cosine_scores(cand.line_theme_scores_raw)
    fused = fuse_themes(title, lyrics, song_emb, line_emb)
    theme_source = "fusion"
fused = apply_seasonal_bias(fused, season)            # after either path (existing, phases.py:73-88)
phase = infer_phase(fused, tempo_bpm)                 # existing phases.py:91-105
secondary_phases = infer_secondary_phases(fused, phase, tempo_bpm)
```

- `theme_source: str | None` on `SongCandidate` (values `"component" | "fusion" | None`) — set during enrichment (None on raw fetch output), report visibility only.
- No confidence floor: any non-null component theme qualifies as primary.
- `infer_phase`/`infer_secondary_phases`/`THEME_TO_PHASE` untouched — only the input distribution's source changes.
- The same conditional replaces the fusion block in the enrichment loop of BOTH consumers that exist today: `enrich_pool.py:142-148` and `enrich_pool.py`'s stderr summary stays as-is (theme_source distribution added to the summary line, e.g. `Theme source: component=312, fusion=132`).

#### 1.2 Posture resolution order (pinned)

Effective posture on `SongCandidate` is resolved at *consumption* time, not stored: `component_posture` → `recording_posture` → None. f_posture (Part 3.3) and the report implement exactly this chain. No new field; both inputs already exist after Part 0.

#### 1.3 Energy percentile normalization

Pool-wide pass in `enrich_pool.py` after per-song enrichment:

1. Collect all `entry_energy_level_db` and `exit_energy_level_db` values (non-null) into one pool-wide list.
2. Each value's percentile rank within the pool → `entry_energy_pct` / `exit_energy_pct` (0.0–1.0). Rank definition (pinned): for value v, `pct(v) = (number of values strictly less than v + 0.5 × number equal to v) / N` (midpoint/ties-averaged percentile). Persist both on `SongCandidate`.
3. Songs with `None` energy carry `None` percentiles and are skipped (not penalized) in f_energy; if fewer than 2 usable values pool-wide, all songs get `None` percentiles and f_energy returns neutral (which, per Part 3.1's redistribution rule, contributes zero weight — not 0.5).

Normalization is **within the fetched pool only** (not catalog-wide) — the arc is relative to tonight's candidate set; absolute dB is not comparable across masters.

### Part 2 — Transition matrix (boundary-first)

**Files:** `songset_constructor/rules/transitions.py`, `rules/harmony.py` (no change), `scripts/build_transitions.py` (no change — it calls `recommend_transition`), `models.py`

#### 2.1 Boundary-aware `recommend_transition`

`TransitionCandidate` gains:

```python
boundary_source: str = "song_level"   # "component" | "song_level"
```

New computation, inside `recommend_transition` (helper `_boundary_pair(left, right)` module-local or on the rules module):

```python
# Key/mode/provenance selection — PAIR-LEVEL (all-or-nothing on keys):
def _boundary_pair(left, right):
    left_is_boundary = left.exit_key is not None
    right_is_boundary = right.entry_key is not None
    if left_is_boundary and right_is_boundary:
        boundary_source = "component"
        from_key, from_mode, from_conf = left.exit_key, left.exit_mode, left.exit_key_confidence
        to_key, to_mode, to_conf = right.entry_key, right.entry_mode, right.entry_key_confidence
    else:
        boundary_source = "song_level"
        from_key, from_mode, from_conf = left.musical_key, left.musical_mode, left.key_confidence
        to_key, to_mode, to_conf = right.musical_key, right.musical_mode, right.key_confidence
    return from_key, from_mode, from_conf, to_key, to_mode, to_conf, boundary_source
```

- `cfd()` and `suggest_key_shift()` are called with the selected keys/modes (their signatures already take key+mode strings — no harmony.py change).
- **Keys are pair-level (all-or-nothing):** a pair is boundary-sourced only when both sides have boundary keys; mixed pairs (key on one side only) compute entirely on song-level keys. `boundary_source` is an honest statement about the pair.
- **BPM is per-side best-available (user decision — deliberately different from keys):** inside `boundary_source == "component"` pairs, `bpm_delta = abs(left_exit_bpm_or_fallback − right_entry_bpm_or_fallback)`, where each side independently uses its boundary BPM when present else that song's `tempo_bpm` (the pool's best per-side estimate). Missing boundary BPM on a side with a boundary key → per-song warning on the transition (`"missing exit bpm on <song title>; used song-level bpm"` / `"missing entry bpm on <song title>; used song-level bpm"`); both missing → `"missing boundary bpm on both sides — delta unreliable"`. In `song_level` pairs, bpm_delta is today's formula (`transitions.py:18`) and the existing silent-0 trap fix applies: either side's `tempo_bpm` None → warning `"missing bpm on <song title> — delta unreliable"`.
- `key_compat` from the same `key_compatibility_score(distance)` table — unchanged.
- Existing low-key-confidence warnings (`transitions.py:20-23`) keep operating on the selected (boundary or song-level) confidences.

#### 2.2 H2/H3 boundary semantics (validator change)

**File:** `rules/hard_constraints.py:75-80`

- **H2 (opener tempo floor):** effective value = `entry_bpm` if present else `tempo_bpm`. Missing both → H2 fails (unchanged behavior for unknown tempo).
- **H3 (closer ceiling):** effective value = `exit_bpm` if present else `tempo_bpm`.
- These are per-song checks; they do NOT consult `boundary_source`.
- The relax knobs (`relax_h2_bpm`, `relax_h3_bpm` via `config.opening_floor`/`closing_limit`) keep operating on the effective boundary-aware value.
- Implementation: `validate()` currently reads `bpms = [item.bpm ...]` (`hard_constraints.py:50`) — H2/H3 need boundary BPMs on `ProposalItem`; see 3.4 for the field additions.

#### 2.3 H8 boundary gate (literal boundary-first, regression disclosed)

**File:** `rules/hard_constraints.py:106-108`

Concretely: when the transition into this item is `boundary_source == "component"`, H8 checks the destination's `entry_key_confidence >= 0.6`; when `boundary_source == "song_level"`, H8 checks the item's song-level `key_confidence >= 0.6` (unchanged today-behavior). The from-side confidence is not separately gated: the from-side boundary key is *departed*, not transposed-into, and `suggest_key_shift` already refuses shifts whose resulting CFD is worse (`harmony.py:75-90`). `enrich_pool.py:108`'s range-shift gate stays song-level (singing-range shifts transpose the whole song, not a boundary).

**Disclosed regression (user-accepted, must appear in SKILL.md Step 6 and report):** under this gate the pool's transposable population shrinks from 250 (56.3%) to ~201 (45.3%): ~80 songs with song-level confidence ≥ 0.6 sit on low-confidence boundary keys (< 0.6) and lose transposition; ~31 songs with unreliable song-level keys but confident boundary keys gain it. This is the accepted cost of honest boundary data, NOT an unlock. Fan-out impact is visible in the report's pool overview (Part 4) and the per-proposal warnings.

Implementation detail: H8 currently reads `item.key_confidence` (`hard_constraints.py:107`). After 3.4, the item carries `boundary_source` (of its incoming transition) and `entry_key_confidence`; the validator needs the transition matrix anyway (it already receives `matrix`) — H8 resolves per item: `transition = matrix.get((items[i-1].hash, item.hash))` for i>0; opener has no incoming transition → song-level rule. Pin this exact lookup in the spec's implementation and test.

#### 2.4 H4/H5 and fan-out

- H4 reads `transition.bpm_delta` (already boundary-aware after 2.1) — no code change beyond the fallback warning.
- H5 reads `transition.cfd` + `suggested_key_shift` (boundary-aware after 2.1).
- Fan-out (`beam.py:47-51`) consumes transition fields only — inherits boundary awareness for free.
- **Beam theme-diversity fix (user decision):** `_sort_key_theme_diverse` (`beam.py:90`) changes `len({t for item in seq for t in (item.themes or {})})` to count **positive** themes: `len({t for item in seq for t, v in (item.themes or {}).items() if v > 0})`. This wakes up a previously degenerate term for ALL songs (fusion songs included) — accepted behavior change, disclosed here so the implementer does not "fix" it back.
- The one semantic trap: everything must be finalized at matrix build time; `score_songset.py` cannot recompute. Assert this in a test.

### Part 3 — Scoring (six-way weights, f_energy, f_posture, neutral redistribution)

**Files:** `rules/fitness.py`, `rules/proposals.py`, `score_songset.py` (no signature change — reads new fields via pool), `models.py`

#### 3.1 Weight renormalization with neutral redistribution

**File:** `rules/fitness.py:77-93`

```python
W_BASE = {"theme": 0.40, "tempo": 0.30, "harmony": 0.20, "diversity": 0.10}

def score(proposal, config, matrix) -> ScoreBreakdown:
    ...
    theme = f_theme(proposal, config.count)
    tempo = f_tempo(proposal)
    harmony = f_harmony(proposal, matrix)
    diversity = f_diversity(proposal)
    energy = f_energy(proposal)      # None when the signal is absent pool-wide (see 3.2)
    posture = f_posture(proposal)    # None when the signal is absent pool-wide (see 3.3)
    weights = dict(W_BASE)
    if energy is None:
        # redistribute 0.05 proportionally across the four base terms
        pass  # weights already sum to 1.0 without the energy term
    else:
        for k in weights: weights[k] = round(weights[k] * 0.95, 4)   # scale base by (1 − 0.05)
        # posture term handled symmetrically below
    ...
```

Concretely (pinned arithmetic, avoid float drift):

- Neither new signal present pool-wide → total = today's `0.40·theme + 0.30·tempo + 0.20·harmony + 0.10·diversity` (identical to current behavior; regression test passes verbatim). `ScoreBreakdown.f_energy`/`f_posture` are **absent-as-None** — model fields typed `float | None = None`.
- Only energy present → base weights scale by 0.95 (0.40→0.38, 0.30→0.285, 0.20→0.19, 0.10→0.095), total adds `0.05·f_energy`.
- Only posture present → same scaling, total adds `0.05·f_posture`.
- Both present → six-way weights `0.35/0.25/0.20/0.10/0.05/0.05` (base scaled by 0.90: 0.40→0.36, 0.30→0.27, 0.20→0.18, 0.10→0.09; plus 0.05+0.05).

Wait — pinned numbers: with both terms active, base scaling must be `0.90`, giving `0.36·theme + 0.27·tempo + 0.18·harmony + 0.09·diversity + 0.05·energy + 0.05·posture`. **This supersedes the v1 table (`0.35/0.25/0.20/0.10/0.05/0.05`); SKILL.md Part 5 documents the 0.90/0.95/1.00 three-mode table.** The redistribution is proportional (each base weight × (1 − absent-weights-sum)), preserving the base ratios exactly.

`ScoreBreakdown` (models.py:86-92) gains `f_energy: float | None = None`, `f_posture: float | None = None` (None = term absent from the weighted sum). The three `ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0)` placeholder constructions (`score_songset.py:90`, `beam.py:260`, `graph/nodes.py:232`) still validate (new fields default None) — no change needed at those sites; `graph/nodes.py` is out of scope but the shared model change must not break it (it doesn't: defaults).

#### 3.2 f_energy

**File:** `rules/fitness.py` (new function)

Per item, arc energy value = `entry_energy_pct` (the energy the song *arrives* with). For the closer, also `exit_energy_pct`. Ordinal expectations:

- **Adjacency smoothness:** for each adjacent pair, penalize `|entry_pct(B) − exit_pct(A)|` on the percentile scale.
- **Arc shape:** sets should generally land softer than they open: penalize `max(0, entry_pct(first) − exit_pct(last))`.

**v1 simplification kept:** the arc-shape term is a single opener-vs-closer constraint, not per-template ordinal expectations. Implement exactly the two bullet terms; the per-template extension is a deliberate deferral — add it only if score distributions on real pools show sets gaming the opener/closer check mid-arc.

```python
def f_energy(proposal: SongsetProposal) -> float | None:
    # returns None when <2 items carry percentiles (pool-wide absence → neutral redistribution)
    # 0.5·adjacency smoothness + 0.5·arc-shape (ordinal, percentile inputs)
```

- Items lacking percentiles are skipped per-adjacency; <2 usable adjacency values AND no closer-exit value → None.
- Pool-wide absence (Part 1.3's degenerate guard) → None → weight redistributed (3.1).
- f_tempo stays song-level (`item.bpm`, proposals.py:34,78) while H2/H3 are boundary-aware — whole-song tempo remains the liturgical "feel" for the arc term; boundary BPM only governs adjacency (H4) and the opener/closer floor/ceiling checks. **Do not "fix" this asymmetry without a user decision.**

#### 3.3 f_posture

**File:** `rules/fitness.py` (new) + fit matrix constant in new `rules/components.py`:

```python
POSTURE_PHASE_FIT = {          # rows: posture, cols: phase 1..5
    "To God":          {1: 0.5, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.5},
    "About God":       {1: 1.0, 2: 0.5, 3: 0.5, 4: 0.0, 5: 1.0},
    "To Congregation": {1: 1.0, 2: 0.0, 3: 0.5, 4: 1.0, 5: 1.0},
}
```

```python
def f_posture(proposal: SongsetProposal) -> float | None:
    # effective posture per item = item.component_posture (already resolved through
    # component → recording fallback at proposal build, see 3.4); missing → skipped
    # returns None when NO item has any posture (pool-wide absence → redistribution)
    # otherwise mean(POSTURE_PHASE_FIT[posture][phase]) over items with posture
```

Liturgical rationale (confirmed in session): direct address (To God) peaks in Worship/Response; testimony/declaration (About God) fits Call and Commission; congregational exhortation (To Congregation) fits Call/Response/Commission but is not Worship.

#### 3.4 `ProposalItem` fields

**File:** `rules/proposals.py` — populate when rebuilding from the pool (both `item_from_candidate` at `:24-43` and `proposal_from_draft` at `:66-88`):

- `has_components: bool = False`
- `theme_source: str | None = None`
- `component_posture: str | None = None` — **effective posture**: `candidate.component_posture or candidate.recording_posture or None` (component → recording-level → None chain pinned here; f_posture then skips None items)
- `entry_energy_pct: float | None = None`, `exit_energy_pct: float | None = None`
- `entry_bpm: float | None = None`, `exit_bpm: float | None = None`, `entry_key: str | None = None`, `exit_key: str | None = None`, `entry_key_confidence: float | None = None` (H2/H3/H8 + report rendering)
- `incoming_boundary_source: str | None = None` — the `boundary_source` of the transition INTO this item (None for the opener). Set in `proposal_from_draft` via an optional `matrix` parameter (see below), or — since `score_songset.py` builds the proposal before the matrix lookup exists — via a post-pass in `score_songset.py`/`beam.py` that stamps `incoming_boundary_source` from the matrix after `proposal_from_draft`. Pinned approach: **post-pass** (avoids changing `proposal_from_draft`'s signature for the three call sites at `score_songset.py:91`, `beam.py:261`, `graph/nodes.py:233`): `proposal = proposal.model_copy(update={"items": [item.model_copy(update={"incoming_boundary_source": matrix.get((prev.hash, item.hash)).boundary_source if transition else None}) for ...]})` — implemented once as a helper `stamp_boundary_sources(proposal, matrix) -> proposal` in `rules/proposals.py`, called from `score_songset.py` and `beam.py` right after proposal construction. (`graph/nodes.py` is out of scope; it simply never stamps — its H8 then falls back to song-level, matching its song-level enrichment.)

H8/H2/H3 consume `ProposalItem` fields only (validator never reads `SongCandidate`) — this keeps the matrix-only visibility invariant testable (test 10).

### Part 3.5 — LLM planner prompt (user decision: extend)

**File:** `graph/nodes.py` is OUT of scope (deprecated path). The skill's planner is the SKILL.md workflow itself (Step 5): the planner reads the enriched pool JSON the skill feeds it. Extend SKILL.md Step 5's guidance to instruct the planner to consider, per song: boundary entry/exit BPM+key, `theme_source`, posture, energy pct — all present on the enriched `SongCandidate` JSON. No code change needed for the prompt itself beyond SKILL.md (the "pool" the skill planner sees IS the enriched candidate JSON). This decision's only code impact is documentation (Part 5).

### Part 4 — Report

**Files:** `scripts/write_report.py`, `artifacts/writer.py`

- **Transitions input becomes real (bug-fix adjacent):** `main()` (`:56-69`) parses `data.get("transitions", [])` and builds `matrix: dict[(from,to), TransitionCandidate]` exactly like `score_songset.py:67-71`; thread `matrix` into `_build_report` → `_proposal_section`. The docstring contract (`:5-15`) already promises this input — no SKILL.md input-contract change needed, only actual parsing.
- **Pool overview:** component coverage (count + % with components), `theme_source` distribution (component vs fusion), transposable-population note (song-level ≥ 0.6 count vs boundary-gated count — makes the H8 regression observable run-over-run).
- **Per-proposal item table:** add columns — `boundary (entry/exit) key+BPM` alongside song-level, `theme_source`, `posture` (or `—` when missing), `energy pct` (entry/exit).
- **Per-adjacency:** `boundary_source` marker (`component`/`song-level`) next to each transition's cfd/bpm_delta, plus boundary-BPM warnings (from `TransitionCandidate.warnings` — now reachable because the matrix is threaded in).
- **Per-proposal arcs:** energy trajectory (entry/exit pct per position), posture sequence vs phase, with the fit values from the matrix.
- **Score breakdown table:** the active weight mode (six-way / five-way / four-way per 3.1's redistribution) with `f_energy`/`f_posture` shown as values or `(n/a)`; note the weights changed from v0's 0.40/0.30/0.20/0.10.
- **Warnings section:** songs with `has_components = false` used in a proposal ("song-level fallback: theme via fusion, no energy/posture data"); boundary-BPM warnings per adjacency.
- **Adjacent cleanup while editing `_proposal_section`:** replace the hardcoded `MAX_DURATION_SECONDS = 1500` (`write_report.py:35`) with an import of `SONGSET_MAX_DURATION_SECONDS` from `stream_of_worship.admin.constants` (same constant `hard_constraints.py:5` uses). Pure unification of a duplicated constant; no behavior change (both are 1500).

### Part 5 — SKILL.md updates (Phase 2 only)

After scripts ship (Rollout below), update `lab/skills/songset-constructor/SKILL.md`:

- Step 2 (fetch): mention boundary fields + `has_components` on SongCandidate.
- Step 3 (enrich): document `theme_source`, percentile normalization, and that fusion is now fallback.
- Step 4 (transitions): document `boundary_source`, per-side BPM provenance, and boundary-first semantics.
- Step 5 (planning): the planner now sees posture/energy/boundary data on the enriched pool JSON; add guidance to consider energy arc (soft opener→closer descent) and posture fit when choosing between candidates; keep H-table unchanged (no new H-codes).
- Step 6 (scoring): the three-mode weight table (four-way today / +energy / +posture / six-way) with the redistribution rule; interpretation rows for f_energy/f_posture; H8's boundary gate + disclosed transposable-population regression (~201 vs 250).
- Step 10 (report): new columns/arcs and the transitions-backed adjacency markers.
- Overview: one line noting the deprecated `sow-admin songset construct` LangGraph path retains song-level behavior (divergence is intentional; skill scripts are canonical).
- Never describe flags or fields that don't exist yet — SKILL.md must always match shipped scripts.

## Rollout

1. **Phase A (this spec's code work):** Parts 0–4. All additive for data ingestion; component-less songs behave exactly as today (verified by the regression test below); beam-sort diversity fix is the one intentional behavior change for all songs (disclosed in 2.4).
2. **Phase B:** Part 5 SKILL.md update, in the same PR as Phase A or immediately after — SKILL.md never describes unshipped behavior.
3. **Optional later (out of scope):** components backfill for the 132 songs (coverage 70%→~100%), H-code elevation if score distributions justify it, energy/posture hard-constraint elevation, LangGraph path parity (if `songset construct` is ever de-deprecated).

## Tests & Verification

Deterministic JSON-in/JSON-out tests in `ops/admin-cli/tests/songset_construct/` (existing convention; run with `NO_COLOR=1 uv run --project ops/admin-cli --python 3.11 --extra admin --extra test --extra constructor pytest -v`):

1. **Regression (critical, now passes by construction):** pool with zero components → enrich/build_transitions/score output identical to pre-change behavior — same transitions, same validation, same `f_theme/f_tempo/f_harmony/f_diversity` values, same **totals** (neutral redistribution makes the weights identical), same ranking. This is the "strictly additive" guarantee.
2. Boundary math: pairs with populated exit/entry keys produce `boundary_source="component"`, cfd computed on boundary keys; missing one side → fallback + `boundary_source="song_level"` on both sides.
3. BPM provenance: boundary pair with both boundary BPMs → delta on boundary values; boundary key present but boundary BPM missing on one side → per-side song-level fallback + per-song warning; both missing → "delta unreliable" warning; song-level pair with missing tempo → today's silent-0 becomes a warning.
4. H2/H3: opener floor on entry BPM, closer ceiling on exit BPM; song-level fallback when absent; missing both → fails as today.
5. H8: boundary gate (entry-side confidence) with song-level fallback; song below 0.6 at song level but boundary ≥ 0.6 becomes transposable; song ≥ 0.6 at song level with boundary key present but boundary confidence < 0.6 **loses** transposability (pin the accepted regression); opener (no incoming transition) always song-level-gated.
6. Theme primary/fallback: songs with component themes use them (`theme_source="component"`); component-less songs use fusion; seasonal bias applies in both paths; distribution shape is dense 12-key.
7. Distribution weighting: the 0.2 worked example (2 chorus + 1 verse weights 0.8/0.6/0.45 → normalized 0.432/0.324/0.243); posture argmax + weighted-average confidence; loop_target rows vote (255 songs have them).
8. Energy: percentile math (pool-relative, ties-averaged); f_energy returns None when <2 usable values → weight redistribution keeps totals at four-way values; adjacency + arc-shape terms behave on synthetic pools.
9. Weights: three-mode breakdown — zero-component pool totals exactly match four-way formula; one-signal pools scale base by 0.95; both signals → base × 0.90 + 0.05 + 0.05; sums to 1.0 in every mode.
10. Matrix-only visibility: score/H-checks consume boundary values only via `TransitionCandidate`/`ProposalItem` (no direct key reads) — pin the structural constraint.
11. Beam sort: `_sort_key_theme_diverse` counts positive themes (a sequence with 3 distinct positive themes sorts before an equal-scoring sequence with 2); degenerate case (all-zero themes) does not crash.
12. Report smoke: run `write_report.py` on a proposal containing both component-having and component-less songs with transitions in stdin; assert coverage lines, boundary_source markers, and warnings render.
13. Cache: old `pool_*.json` (no new fields) validates to `None`/`False` defaults.
14. Aggregation determinism: 6-song multi-row tiebreak (lowest occurrence_index, then id) picks the same row across runs; `entry_exit` handled as both boundaries.

## Notes for the Implementer

- `song_components.key` field name (not `musical_key`); enum values are English postures, Chinese themes.
- Component `bpm_confidence` is a duration tier — soft weight only, never an H8-style gate.
- Component key detection returns `None` when chroma margin < 0.03 — coverage numbers already account for this.
- Do NOT import admin command code (`commands/audio.py`) into the constructor; mirror the aggregation logic instead (package boundary: constructor reads DB via its own `db.py` only).
- Do NOT touch `graph/nodes.py`, `runner.py`, or `commands/songset.py` construct — deprecated path, intentionally divergent (documented in SKILL.md Overview). The shared-model change (ScoreBreakdown None defaults) must not break it; it doesn't (fields default None).
- Fan-out/dead-end computation consumes transition fields only — verify it stays honest when boundary values shift which pairs pass H4/H5 thresholds.
- Keep every new field optional; the fallback chain (boundary → song-level → neutral/redistributed) must be total. No code path may assume components exist.
- Mode handling: component rows never carry mode; `entry_mode`/`exit_mode` are copied from recording-level `musical_mode` at aggregation (Part 0.2 rule 1). `normalize_key`'s missing-mode default (major) is the trap this avoids.
- `entry_exit` role: 0 rows exist today; the code path is cheap future-proofing — keep, don't test against live data (test 14 covers it synthetically).