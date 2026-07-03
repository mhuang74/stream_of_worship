# Songset Constructor — Full Beam Filter Consistency (v3)

## Goal

Generalize the H2/H3 endcap fix to every rule whose relaxation flag is wired through `RunConfig` but never consulted by the beam's sequence builder. Today `_sequences` (`rules/beam.py:50-80`) filters only by phase, leaving H1/H4/H5 to `validate()` after the beam has already converged — so the relax flags cannot influence which sequences are generated, and diagnostics report rejection counts that don't match the beam's actual escape tiers.

This spec supersedes `songset-constructor-beam-in-sequence-filtering-v2.md` (which it subsumes entirely) and incorporates four decisions made during review of v2:

1. **Add user-facing `--relax-h4-bpm` / `--relax-h5-cfd` CLI flags now** (v2 deferred these to a follow-up).
2. **Keep the current auto-escalation tier order** (strict → relaxed_H4_H5 → relaxed_H2_H3 → relaxed_H1+H2+H3+H4+H5). H1 default-True is the norm but the tier order prioritizes keeping hard musical limits intact for as long as possible.
3. **Parameterize `compute_fan_out` in this spec** (v2 deferred this to a follow-up). Dead-end marking must reflect the same relaxations used in `_sequences` to avoid over-pruning in relaxed tiers.
4. **Fix the LangGraph `validate_score` mismatch in this spec** (v2 deferred this to a follow-up). The one-line fix plus a basic regression test prevents divergence between agentic and beam paths.

## Background

The H2/H3 spec established the principle: **filter at the source, using the runtime config, before `validate()` rejects dead-end candidates.** That principle applies to:

1. **H1** — `relax_h1` (defaults `True`); `validate()` at `hard_constraints.py:53-60` drops the strict phase-1 count when relaxed, but `_sequences:61` keeps `candidate.phase != 1` hard-coded. So under the default config, `validate()` accepts a phase-2 opener while the beam never proposes one.
2. **H4** — adjacent BPM-delta limit (strict 20 / 15 without crossfade / 25 relaxed). `_sequences` sort key *minimizes* delta (`beam.py:73`) but never *rejects* jumps over the limit; pools with no zero-delta path converge on H4-violating middle pairs.
3. **H5** — circle-of-fifths distance (strict 2 / relaxed 3). `compute_fan_out` (`beam.py:31`) uses `cfd <= 2` only to mark dead-ends; `_sequences` never checks CFD as it extends a beam, so H5 violations only surface in `validate()`.
4. **`compute_fan_out` hard-coded limits** — exactly the same anti-pattern: `cfd <= 2 and bpm_delta <= 20` (`beam.py:31`) mark `is_dead_end` under strict limits; `_sequences:67-68` then skips dead-ends even in the `relaxed_H4_H5` and `auto_relax` tiers, which lookup the matrix but use the same conservative constants. **Fixed in v3 (see below).**
5. **Diagnostics sort-key drift** — `beam_diagnostics` (`diagnostics.py:109-118`) re-implements `_candidate_sort_key` inline rather than importing `beam._candidate_sort_key`. If they drift, the reported rejection counts reflect sequences different from what `beam.search` actually generated.
6. **`hard_rule_rejection_counts` strict-only validation** — `diagnostics.py:90` calls `validate(...)` with no relax kwargs, so the reported `rejected_sequences` reflect strict-mode rejection even when the beam escaped via the `relaxed_H4_H5` / `auto_relax` tiers (`beam.py:151, 171, 195-197`). After v1 lands, H2/H3 columns drop to 0/0 while H4/H5/H1 stay inflated — a misleading picture.
7. **LangGraph `validate_score` mismatch** — `graph/nodes.py:199-204` validates with `relax_h1=config.relax_h1` only, never propagating `relax_h4` / `relax_h5`. The agentic path therefore disagrees with the deterministic beam fallback on what counts as valid. **Fixed in v3 (see below).**

## Design decisions

- **Filter at the source — for every relaxable rule.** `_sequences` consults `RunConfig` for H1 (opener phase set), H2 (opening floor), H3 (closing ceiling), H4 (per-pair delta limit), and H5 (per-pair CFD limit). Endcap filters come from v1; per-pair filters are new.
- **Consult the transition matrix during beam expansion,** not afterward. `_proposal_for_sequence` currently applies vamp/crossfade/transposition settings from `matrix.get((left, right))` *after* the sequence is built (`beam.py:96-110`). For H4's `allowed` to honor the crossfade/gap branch of `validate()` (`hard_constraints.py:74`), `_sequences` must look up `transition.crossfade_duration_seconds > 0 or transition.gap_beats > 4` when deciding whether `bpm_delta <= h4_limit` or `bpm_delta <= min(15, h4_limit)`. Matrix is already a parameter to `_sequences` via `search()`; thread it through.
- **H1 relaxation in the beam = opener slot accepts `{1, 2}`** when `config.relax_h1` is set (graceful, preserves the worship arc intent). Strict mode keeps `phase == 1`. This matches `validate()` semantics while keeping mid-set sanity.
- **No new config plumbing for H4/H5 limits** — compute them inline from a new pair of `RunConfig` properties (`h4_limit`, `h5_limit`) that mirror `closing_limit`/`opening_floor`. H4 has the crossfade branch, handled by consulting the matrix per-pair inside `_sequences` (see above).
- **Diagnostics stay honest — for every tier.** `beam_diagnostics` imports `beam._candidate_sort_key` (kills Item 5 drift), and `hard_rule_rejection_counts` accepts an optional `relax_kwargs: dict | None = None` parameter forwarded to `validate()`. Default `beam_diagnostics` reports strict-mode counts (matching v1's "honest about dead-ends") plus an additional `relaxed_tier_rejections` sub-report mirroring the `relaxed_H4_H5` escape tier, so users see both.
- **Do not change sort key weights.** Filter, don't rank — same surgical principle as v1. The BPM-delta and CFD *weights* in the sort key stay; the filters just prune candidates that violate the relaxable ceilings at each expansion step.
- **`compute_fan_out` parameterized in v3.** Pass `config` into `compute_fan_out` so `is_dead_end` reflects the same relaxations used in `_sequences`. This fixes Item 4. Call sites (`graph/nodes.py:99` and `cli.py` via `search()`) are updated to pass `config`. Previously hard-coded `cfd <= 2 and bpm_delta <= 20` become `cfd <= config.h5_limit and bpm_delta <= config.h4_limit`.
- **LangGraph mismatch (Item 7) fixed in v3.** `graph/nodes.py:199-204` propagates `relax_h4=config.relax_h4` and `relax_h5=config.relax_h5` alongside `relax_h1`. A regression test is added to `test_songset_constructor_graph.py`.
- **User-facing `--relax-h4-bpm` / `--relax-h5-cfd` CLI flags added in v3.** They mirror `--relax-h3-bpm` / `--relax-h2-bpm`: optional numeric overrides with sensible defaults. When absent, fall back to the standard strict/relaxed thresholds (20/25 for H4, 2/3 for H5). These are wired through `RunConfig` and surface in `--help`.
- **Auto-escalation tier order unchanged.** The current sequence (strict → relaxed_H4_H5 → relaxed_H2_H3 → relaxed_H1+H2+H3+H4+H5) is retained. This prioritizes keeping hard musical limits intact for as long as possible, which matches the user's goal of "general musical rules (but not constrained by them)" — strict rules are preferred, relaxation is a graceful fallback.
- **Strict compatibility preserved.** When no relax flags are set, previous behavior is unchanged: endcaps already failing H2/H3 are removed up-front (v1), per-pair H4/H5 violations that would have failed in `validate()` are now also removed up-front (v3 marginal cost: dropped candidates that would never have passed anyway). Auto-escalation tiers re-rank among rule-eligible candidates — the desired behavior.

## Files to change

### 1. `lab/poc-scripts/poc/songset_constructor/config.py` — new H4/H5 limit properties + CLI plumbing

Add `relax_h4_bpm` / `relax_h5_cfd` optional override fields, `relax_h4`/`relax_h5` boolean fields (mirrors existing `relax_h1` plumbing), and limit properties next to `closing_limit` / `opening_floor` (`config.py:89-99`):

```python
relax_h4: bool = False
relax_h5: bool = False
relax_h4_bpm: int | None = None
relax_h5_cfd: int | None = None

@property
def h4_limit(self) -> int:
    if self.relax_h4_bpm is not None:
        return self.relax_h4_bpm
    return 25 if self.relax_h4 else 20

@property
def h5_limit(self) -> int:
    if self.relax_h5_cfd is not None:
        return self.relax_h5_cfd
    return 3 if self.relax_h5 else 2
```

Add validation guards in `__post_init__` (`config.py:57-87`):
```python
if self.relax_h4_bpm is not None and self.relax_h4_bpm < 0:
    raise ValueError("--relax-h4-bpm must be >= 0")
if self.relax_h5_cfd is not None and self.relax_h5_cfd < 0:
    raise ValueError("--relax-h5-cfd must be >= 0")
```

Update `to_dict()` (`config.py:123-145`) to round-trip `relax_h4` / `relax_h5` / `relax_h4_bpm` / `relax_h5_cfd` (guardrail for `compact_config` roundtrip at `beam.py:133`).

### 2. `lab/poc-scripts/poc/songset_constructor/rules/beam.py` — full source filtering + compute_fan_out parameterization

- **`compute_fan_out` signature change** (`beam.py:20-34`):
  ```python
  def compute_fan_out(
      pool: list[SongCandidate],
      matrix: dict[tuple[str, str], TransitionCandidate],
      config: RunConfig,
  ) -> list[SongCandidate]:
  ```
  Replace hard-coded `cfd <= 2 and bpm_delta <= 20` with `cfd <= config.h5_limit and bpm_delta <= config.h4_limit`.

- **`_sequences` signature**: `def _sequences(pool, config, matrix, width=8)` (matrix threaded through for H4/H5 per-pair lookup; supersedes v1's `config`-only threading).
- **Opener slot** (H1 + H2): when `config.relax_h1`, accept `candidate.phase in {1, 2}`; else `candidate.phase == 1`. Then enforce H2 floor exactly as v1 specifies.
- **Closer slot** (H3): identical to v1.
- **Per-pair expansion filter** (H4 + H5): when extending a beam by `candidate` after `beam[-1] = left`, look up `transition = matrix.get((left.recording_hash_prefix, candidate.recording_hash_prefix))` and apply:
  - `bpm_delta = transition.bpm_delta if transition else abs((candidate.tempo_bpm or 0) - (left.tempo_bpm or 0))`
  - `allowed = config.h4_limit if (transition and (transition.crossfade_duration_seconds > 0 or transition.gap_beats > 4)) else min(15, config.h4_limit)`
  - `if bpm_delta > allowed: continue`
  - `distance = transition.cfd if transition else 6`
  - `shifted_ok = transition is not None and transition.suggested_key_shift != 0  # beam picks shift=0 by default; _proposal_for_sequence applies the suggestion later`
  - `if distance > config.h5_limit and not shifted_ok: continue`
- **Auto-escalation tiers** (`beam.py:153-199`): build `relaxed_config` with `relax_h4=True, relax_h5=True` (and `relax_h1=True` for the H1 tier) so the source filter widens to match the relaxation:
  ```python
  relaxed_config = RunConfig(**{
      **config.to_dict(),
      "relax_h4": True,
      "relax_h5": True,
      # ...existing relax_h3_bpm / relax_h2_bpm overrides...
  })
  ```
- **Callers** updated: `search()` (`beam.py:128, 134, 144, 163, 184`), all now pass `matrix` alongside `config`. `compute_fan_out` call in `search` updated to pass `config`. `exhaustive_fallback` (`beam.py:203-213`) does not call `_sequences`; no change beyond `compute_fan_out` signature if called.

- **Dead-end handling**: `candidate.is_dead_end and position != len(target)` now respects the widened limits from parameterized `compute_fan_out`, so relaxed tiers don't over-prune.

### 3. `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` — honest tier reporting

- `beam_diagnostics` (`diagnostics.py:102-122`):
  - Replace inline sort-key lambda (`diagnostics.py:109-117`) with `beam._candidate_sort_key` (kills Item 5 drift).
  - Call `_sequences(sorted_pool, config, matrix, width=width)` (matrix threaded).
  - Produce both:
    - `hard_rule_rejections` — strict-mode rejection counts (current behavior; `validate(proposal, config, matrix)`).
    - `relaxed_tier_rejections` — rejection counts under the `relaxed_H4_H5` escape tier (`validate(proposal, config, matrix, relax_h4=True, relax_h5=True)`), so users see how the beam would have actually escaped.
- `hard_rule_rejection_counts` (`diagnostics.py:79-99`) gains an optional `relax_kwargs: dict | None = None` parameter forwarded to `validate()`. Defaults to strict (None).
- `diagnostic_lines` (`diagnostics.py:125-166`): emit a second line when `relaxed_tier_rejections` differs from `hard_rule_rejections`:
  ```
  beam relaxed-tier fallback would still reject N/M sequences: H4=X, H5=Y
  ```

### 4. `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` — LangGraph relaxation parity

`validate_score` (`nodes.py:199-204`) propagates `relax_h1`, `relax_h4`, and `relax_h5` from `state["config"]`:
```python
feedback = validate(
    proposal,
    state["config"],
    state.get("transition_matrix", {}),
    relax_h1=state["config"].relax_h1,
    relax_h4=state["config"].relax_h4,
    relax_h5=state["config"].relax_h5,
)
```

### 5. `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` — compute_fan_out caller update

In `build_transition_matrix` (`nodes.py:89-104`), update line 99:
```python
pool = compute_fan_out(pool, matrix, state["config"])
```

### 6. `lab/poc-scripts/poc/songset_constructor/cli.py` — new `--relax-h4-bpm` and `--relax-h5-cfd` flags

Add to the `construct()` command signature (`cli.py:294-316`):
```python
relax_h4: Annotated[bool, typer.Option("--relax-h4/--no-relax-h4")] = False,
relax_h5: Annotated[bool, typer.Option("--relax-h5/--no-relax-h5")] = False,
relax_h4_bpm: Annotated[int | None, typer.Option("--relax-h4-bpm", min=0)] = None,
relax_h5_cfd: Annotated[int | None, typer.Option("--relax-h5-cfd", min=0)] = None,
```

Forward to `RunConfig(...)` constructor call (`cli.py:319-339`).

### 7. Tests — `lab/poc-scripts/tests/test_songset_constructor_rules.py`

Add to the existing H2/H3 v1 test set (which v3 subsumes):

- `test_beam_filters_closer_by_h3_ceiling` — v1, retained.
- `test_beam_filters_opener_by_h2_floor` — v1, retained.
- `test_relax_h3_unblocks_when_only_high_bpm_closer_matches_preceding` — v1, retained.
- `test_diagnostics_beam_sequences_uses_config_ceiling` — v1, retained.
- `test_relax_h1_opener_accepts_phase_2` — pool with no phase-1 opener above `opening_floor` but a valid phase-2 opener at 115 BPM; assert `search(relax_h1=True)` produces proposals, `search(relax_h1=False)` returns nothing.
- `test_beam_rejects_h4_violating_middle_pair` — pool where every zero-delta path goes through a 50-BPM jump; assert strict mode yields 0 proposals; assert the `relaxed_H4_H5` fallback yields proposals (H4 limit 25).
- `test_beam_rejects_h5_violating_pair` — pool where adjacent keys have CFD 4 everywhere; assert strict 0 / relaxed (>0).
- `test_beam_h4_honors_crossfade_branch` — matrix entry with `crossfade_duration_seconds > 0` and `bpm_delta=22` (which would fail strict-15 but pass the 25 ceiling when crossfade is set); assert the pair survives `_sequences`.
- `test_beam_h5_honors_suggested_key_shift` — pair with CFD 4 and `suggested_key_shift != 0`; assert `_sequences` keeps the pair (matches `shifted_ok` carve-out at `hard_constraints.py:79`).
- `test_beam_h5_shifted_ok_after_proposal_applies_shift` — invariant guard: `_proposal_for_sequence` applies `transition.suggested_key_shift` to `key_shift_semitones`, so by the time `validate()` runs the shift is in place and the H5 carve-out is consistent end-to-end.
- `test_diagnostics_relaxed_tier_report_present` — pool that fails strict H4 but passes `relax_h4=True`; assert `beam_diagnostics` returns both `hard_rule_rejections` (non-zero H4) and `relaxed_tier_rejections` (zero).
- `test_diagnostics_uses_beam_sort_key` — monkeypatch `beam._candidate_sort_key`; assert `beam_diagnostics` calls it (fails if the inline lambda were still present). Guards against Item 5 regression.
- `test_to_dict_preserves_relax_h4_h5` — extend v1's `test_to_dict_preserves_relax_fields`; verify the 4-song fallback's `compact_config` inherits `relax_h4` / `relax_h5` / `relax_h4_bpm` / `relax_h5_cfd` (guardrail for `compact_config` roundtrip).
- `test_compute_fan_out_uses_config_limits` — create pool where strict limits mark all as dead-end but relaxed limits leave some alive; assert `compute_fan_out(pool, matrix, strict_config)` all dead-end, `compute_fan_out(pool, matrix, relaxed_config)` some not.

### 8. Tests — `lab/poc-scripts/tests/test_songset_constructor_config.py`

- Extend near `tests/test_songset_constructor_config.py:52-53` to assert `h4_limit` / `h5_limit` properties exist and respond to `relax_h4=True` / `relax_h5=True` / `relax_h4_bpm=30` / `relax_h5_cfd=4`.
- Add validation tests for negative `--relax-h4-bpm` and `--relax-h5-cfd` raising `ValueError`.

### 9. Tests — `lab/poc-scripts/tests/test_songset_constructor_graph.py`

- `test_validate_score_propagates_relax_h4_h5` — mock state with `config.relax_h4=True`, `config.relax_h5=True`, a valid draft, and a transition matrix; assert `validate_score` calls `validate(..., relax_h4=True, relax_h5=True)`. Monkeypatch `validate` to capture kwargs.

### 10. Tests — `lab/poc-scripts/tests/test_songset_constructor_cli.py`

- `test_cli_relax_h4_flag_args_accepted` — mirror existing `test_cli_relax_h3_flag_args_accepted` style; invoke with `--relax-h4-bpm 25 --no-llm` and assert exit_code == 0.
- `test_cli_relax_h5_flag_args_accepted` — invoke with `--relax-h5-cfd 3 --no-llm` and assert exit_code == 0.

## Verification

```bash
# Targeted regression (v1 + v3)
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_rules.py -v

# Config + cli
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_config.py \
  lab/poc-scripts/tests/test_songset_constructor_cli.py -v

# Graph end-to-end (now includes validate_score parity test)
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_graph.py -v
```

End-to-end smoke (the original failing run from v1):
```bash
uv run --project lab/poc-scripts --extra songset_constructor python \
  lab/poc-scripts/construct_songset_agent.py \
  --env-file /opt/sow/.env --relax-h3-bpm 110 --no-llm
```
Expected (v1 + v3): `candidates>0`, artifact paths printed, and (new for v3) the diagnostics block surfaces a `relaxed_tier_rejections` sub-report identical to (or stricter than) the strict block when no relax flags take effect, and visibly lower when they do.

Additional smoke for H1 default-True behavior:
```bash
uv run --project lab/poc-scripts --extra songset_constructor python \
  lab/poc-scripts/construct_songset_agent.py \
  --env-file /opt/sow/.env --no-relax-h1 --no-llm
```
Expected: identical to today's behavior (no phase-2 openers proposed); the v3 change only adds candidates when relax is the default-True state, so flipping to `--no-relax-h1` must prune exactly to today's pass set.

Additional smoke for new H4/H5 CLI flags:
```bash
uv run --project lab/poc-scripts --extra songset_constructor python \
  lab/poc-scripts/construct_songset_agent.py \
  --env-file /opt/sow/.env --relax-h4-bpm 25 --relax-h5-cfd 3 --no-llm
```
Expected: `candidates>0` if the pool benefits from widened limits; the `--help` output includes both new flags.

## Edge cases / risks

- **`_sequences` signature stability** — adding `matrix` is a second positional/keyword change beyond v1. Grep `_sequences` imports beyond `beam.search` / `diagnostics.beam_diagnostics` before merging. (Confirmed: only those two call sites in this repo.)
- **H4 crossfade branch depends on matrix contents.** Pools with a sparse matrix (many missing transitions) will fall back to the `min(15, h4_limit)` branch and the CFD=6 sentinel, which is *stricter* than today's "validate after the fact" behavior and may over-prune. Mitigation: the `relaxed_H4_H5` tier is unchanged in semantics and remains the escape hatch. Document that strict-mode beam may yield 0 proposals where it previously yielded dead-on-validate proposals — that's the desired "filter at the source" trade-off, identical to v1's stance on H2/H3.
- **H5 `shifted_ok` carve-out depends on `key_shift_semitones`** which `_sequences` does not yet set (the default draft keeps `key_shift_semitones=0`; shifts are applied in `_proposal_for_sequence:104`). This means the beam treats every `suggested_key_shift != 0` pair as "shifted ok" purely because matrix says a shift is *suggested*, not because the draft *applied* it. The subsequent `_proposal_for_sequence` call applies the suggestion, so by the time `validate()` runs the shift is in place — therefore the carving is consistent. Guarded by `test_beam_h5_shifted_ok_after_proposal_applies_shift`.
- **`compact_config` round-trip** (`beam.py:133`): now must preserve `relax_h4` / `relax_h5` / `relax_h4_bpm` / `relax_h5_cfd`. Covered by `test_to_dict_preserves_relax_h4_h5`.
- **`auto_relax` interaction** (`beam.py:153-199`): the tiers now filter per-pair strictly; they will also reject H4/H5-violating sequences at the source, so the `relaxed_H4_H5` escape is narrower. The tiers' explicit `relax_h4=True / relax_h5=True` in the `RunConfig(**...)` construction widens `h4_limit`/`h5_limit` so the source filter matches the relaxation. Net behavior unchanged; tests guard.
- **Diagnostics overhead** — running `validate()` twice per sequence in `beam_diagnostics` doubles cost. Beam width is small (8) and this is a diagnostic path; acceptable. If it shows up in profiles, cache `_proposal_for_diagnostics` results.
- **`compute_fan_out` parameterization impact** — changing `cfd <= 2 and bpm_delta <= 20` to config-driven limits means `is_dead_end` now varies by tier. Under strict mode this is identical to today; under relaxed tiers it may mark fewer dead-ends, which widens the expansion pool. This is the desired behavior and is covered by `test_compute_fan_out_uses_config_limits`.
- **LangGraph path** — now consistent with the deterministic beam thanks to the `nodes.py` fix. The agentic path will use the same `relax_h4`/`relax_h5` semantics when validating drafts.

## Out of scope — follow-up specs

- **1.5× tempo-doubling catalog audit** — v1 noted this; v3's per-pair H4 filter exposes the doubling more loudly (every doubles-pair becomes a ~54 BPM delta rejection). Catalog/analysis pipeline fix remains separate.
