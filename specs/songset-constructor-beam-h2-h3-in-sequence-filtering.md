# Songset Constructor — Apply H2/H3 Tempo Filters Inside the Beam Sequence Builder

## Goal

Make `--relax-h3-bpm` (and the symmetric `--relax-h2-bpm`) actually unblock results when the beam's own sort key converges on tempo-ineligible closers/openers. Today, the beam filters the closer/opener slots only by *phase*; the tempo ceilings are enforced later by `validate()`, so the relax flags can't influence which sequences are generated. This results in every sequence failing the same rule (e.g. `H3=8/8`) even when the pool clearly contains under-ceiling closers.

## Background — root cause

Reproduction:
```
uv run --project lab/poc-scripts --extra songset_constructor python \
  lab/poc-scripts/construct_songset_agent.py \
  --env-file /opt/sow/.env --relax-h3-bpm 110 --no-llm
```
Output: `beam validation rejected 8/8 generated sequences: H3=8`.

Live diagnostic of a 99-song pool with `--relax-h3-bpm 110`:
- `closing_limit=110`, `opening_floor=110` (flag is wiring through to `RunConfig` correctly — `config.py:89-99`).
- `role_eligibility`: `valid_closers_h3=8`, `valid_openers_h2=5`, `phase_4_or_5_candidates_h1=11`. There are 8 phase 4/5 songs with BPM ≤ 110.
- All 8 generated beam sequences converge on the **same closer**, `為榮耀的創造` (phase 5, BPM **161.5**) — well over the 110 ceiling.

Why the beam converges on a tempo-ineligible closer:
1. `_sequences` in `lab/poc-scripts/poc/songset_constructor/rules/beam.py:50-80` only filters the closer slot by phase (`beam.py:63-64`: `candidate.phase not in {4, 5}`) and the opener slot by phase only (`beam.py:61-62`: `candidate.phase != 1`). H2/H3 *tempo* limits are never consulted.
2. The sequence sort key (`beam.py:70-76`) minimizes `(total_phase_score, total_bpm_delta_across_adjacent_pairs, hash)`. To minimize `bpm_delta`, it prefers closers whose BPM matches the preceding songs.
3. The catalog has a tempo-doubling artifact: many songs are detected at both ~107.7 BPM and ~161.5 BPM (~1.5×). The beam, optimizing for zero BPM delta, builds a 5-song run of 161.5 BPM tracks; the only phase-5 closer available at 161.5 BPM is `為榮耀的創造` (the three other phase-5 closers are 107.7 BPM, which would add ~54 BPM delta). Every one of the 8 beams lands on that single closer → all fail H3.
4. Auto-escalation (`beam.py:153-199`) cannot help: it raises the ceiling after the fact via relaxed child configs, but the same sort key still produces the same closer. The relax flag is effectively a no-op in pools with this tempo-doubling pattern.

Net: H3 (and `--relax-h3-bpm`) is enforced *after* the beam has picked the sequence. The flag never has a chance to influence which closers/opener are considered.

## Design decisions

- **Filter at the source.** Apply the runtime `closing_limit`/`opening_floor` directly inside `_sequences()` so the beam never even suggests tempo-ineligible endcaps. This is the smallest change that restores the relax flag's intended effect.
- **No new config plumbing.** `RunConfig.closing_limit` / `RunConfig.opening_floor` already exist (`config.py:89-99`) and already respect the relax flags. Pass `RunConfig` into `_sequences` and `beam_diagnostics` instead of the bare `songs` integer.
- **Strict compatibility.** When no relax flags are set, behavior on previously-passing pools is unchanged: under-ceiling closers were already the only ones that could pass `validate()`, so removing them up-front just skips dead-end work. Auto-escalation tiers (`beam.py:132-199`) continue to run as before; they now re-rank among H2/H3-eligible endcaps.
- **Diagnostics stay honest.** `role_eligibility_counts` (`rules/diagnostics.py:41-65`) already reads `config.closing_limit`/`config.opening_floor`, so the reported "valid closers" matches the in-beam filter. The previously-reported `8/8 H3` rejection will drop to `0/0` (no sequences rejected because none are generated that violate H3), which correctly reflects that the relax flag now has teeth.
- **Do not** change the sort key itself. Tuning the BPM-delta heuristic is risky and out of scope. The fix here is "filter, don't rank," keeping the change surgical.
- The original diagnostic message (`No songset artifacts were written because ...`) continues to work when the pool genuinely lacks any closer under the ceiling (i.e., the relax flag truly cannot help) — that path now produces `valid_closers_h3=0`, which surfaces as `role_eligibility shortfalls: valid_closers_h3` in `diagnostic_lines`.
- **Out-of-scope follow-up (worth a separate spec):** the underlying 1.5× tempo-doubling catalog issue should be audited in `lab/sow-app` analysis, since it pollutes both the beam's delta heuristic and H4 adjacency. This plan unblocks constructor results regardless of that fix.

## Files to change

### 1. `lab/poc-scripts/poc/songset_constructor/rules/beam.py` — pass config into `_sequences` and filter endcaps by tempo

- Change signature `def _sequences(pool, songs, width=8)` (`beam.py:50`) → `def _sequences(pool, config, width=8)` so the closer/opener filters can read `config.closing_limit` and `config.opening_floor`. Use `config.songs` inside the body for the template lookup (`beam.py:51`).
- **Opener slot** (`beam.py:61-62`): tighten to include H2 tempo floor:
  ```python
  if position == 1:
      if candidate.phase != 1:
          continue
      if candidate.tempo_bpm is None or candidate.tempo_bpm < config.opening_floor:
          continue
  ```
- **Closer slot** (`beam.py:63-64`): tighten to include H3 tempo ceiling:
  ```python
  if position == len(target):
      if candidate.phase not in {4, 5}:
          continue
      if candidate.tempo_bpm is None or candidate.tempo_bpm > config.closing_limit:
          continue
  ```
- Update all callers:
  - `beam.search` (`beam.py:128`): `_sequences(sorted_pool, config, width=width)`.
  - `beam.search` 4-song fallback (`beam.py:134`): use `_sequences(sorted_pool, compact_config, width=width)` (note: `compact_config` has `songs=4` via `to_dict()` roundtrip; verify the new signature uses `config.songs`, not the bare integer).
  - Both auto-relax tiers (`beam.py:163`, `beam.py:184`): pass the local `relaxed_config`.
  - `_proposal_for_diagnostics`/`hard_rule_rejection_counts` callers unchanged (they already take `config`).
- **Optionally** (defensive, same file): in `exhaustive_fallback` (`beam.py:203-213`), no `_sequences` call, so no change required. Confirm via grep.

### 2. `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` — pass config to `_sequences`

- `beam_diagnostics` (`diagnostics.py:118`) currently calls `_sequences(sorted_pool, config.songs, width=width)`. Update to `_sequences(sorted_pool, config, width=width)`.
- `_candidate_sort_key` and the local reimplementation of the sort key (`diagnostics.py:109-117`) can be refactored to call `beam._candidate_sort_key` directly to avoid drift, but this is optional and out of scope unless it falls out naturally.

### 3. Tests — `lab/poc-scripts/tests/test_songset_constructor_rules.py`

Add regression cases that simulate the 1.5× tempo-doubling pool pattern:

- `test_beam_filters_closer_by_h3_ceiling` — pool where phase-4/5 closers exist at 120 and 160 BPM, opener valid at 110 BPM; assert `search()` returns proposals with the 120 BPM closer when `relax_h3_bpm=130`, and zero proposals with default strict ceiling (90).
- `test_beam_filters_opener_by_h2_floor` — pool with phase-1 openers at 95 and 115 BPM; assert `search()` uses only the 115 BPM opener under strict mode, and the 95 BPM opener surfaces only when `relax_h2_bpm=90`.
- `test_relax_h3_unblocks_when_only_high_bpm_closer_matches_preceding` — direct regression for the reported bug: pool with all 1.5× BPM cluster around 161.5 and exactly one phase-5 closer at 110 BPM (which would normally add a ~51 BPM delta); assert `--relax-h3-bpm 110` produces proposals with that closer.
- `test_diagnostics_beam_sequences_uses_config_ceiling` — call `beam_diagnostics` with `relax_h3_bpm=110` and assert `generated_sequences > rejected_sequences` for a pool that previously produced `8/8 H3` rejections.
- Mirror existing H3-relax tests at `tests/test_songset_constructor_rules.py:101,152,172` for style/fixtures.

### 4. Tests — `lab/poc-scripts/tests/test_songset_constructor_config.py`

Optional but cheap: extend existing `closing_limit`/`opening_floor` tests near `tests/test_songset_constructor_config.py:52-53` to assert that `beam._sequences` consults these properties (covered by the rules test; only add if a unit-level assertion helps).

## Verification

```bash
# Targeted regression
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_rules.py -v

# Config + cli
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_config.py \
  lab/poc-scripts/tests/test_songset_constructor_cli.py -v

# Graph end-to-end
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_graph.py -v
```

End-to-end smoke (the original failing run):
```bash
uv run --project lab/poc-scripts --extra songset_constructor python \
  lab/poc-scripts/construct_songset_agent.py \
  --env-file /opt/sow/.env --relax-h3-bpm 110 --no-llm
```
Expected: `candidates>0` and at least one artifact path printed (previously `candidates=0`, no artifacts). The selected closer should be one of the 8 phase 4/5 songs with BPM ≤ 110 (e.g., `喜樂河流`, `與祢漫步`).

## Edge cases / risks

- **`_sequences` signature is part of a private API** (leading underscore). Callers are limited to `beam.search` and `diagnostics.beam_diagnostics`, both updated above. Grep for external use before merging to confirm no POC script imports `_sequences` directly.
- **`compact_config` round-trip** (`beam.py:133`): when the 4-song fallback runs, it constructs `RunConfig(**{**config.to_dict(), "songs": 4})`. Confirm `to_dict()` includes `relax_h3_bpm`/`relax_h2_bpm`/`intimate` so the 4-song path inherits the relaxed ceilings. Already true after spec `songset-constructor-rule-relaxation-flags.md` landed; verify with `test_to_dict_preserves_relax_fields`.
- **`auto_relax` interaction**: the auto-escalation tiers (`beam.py:153-199`) build their own `relaxed_config` copies. After this change, those tiers will now *also* filter the closer slot strictly — which is the desired behavior, since the goal of escalation is "find a passing sequence," not "produce sequences that fail the same rule again."
- **Dead-end mismatch**: `compute_fan_out` (`beam.py:20-34`) uses a `cfd<=2 and bpm_delta<=20` filter; songs with no fan-out are marked `is_dead_end=True` and skipped (`beam.py:67-68`). After the H3 filter tightens the closer slot, a previously-passing closer might now be excluded if its own BPM exceeds the ceiling even though it had fan-out. This is the correct behavior — fan-out describes peer compatibility, not the H3 ceiling — but worth asserting in tests that under-ceiling closers with positive fan-out are what survive.
- **Symmetric fix for H2 is included** so the next user-facing symptom (opener-side convergence) doesn't require a second pass. Same surgical approach, same test pattern.
- **Out-of-scope follow-up** (separate spec, not this one): audit tempo-doubling in the catalog/analysis pipeline. The beam fix here unblocks results regardless of the upstream data-quality issue, but the 1.5× doubling will keep distorting fitness scores in `score()`.
