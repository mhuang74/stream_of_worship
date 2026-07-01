# Songset Constructor — Rule Relaxation CLI Flags

## Goal

Add CLI flags to relax hard rules H1, H2, and H3 when songs cannot be constructed
because all pool songs are filtered out by those rules. Relaxation kicks in
automatically (always-on escalation) after the strict search fails, with CLI
flags acting as caps/toggles on the escalation.

The primary pain point is **H3** (closing tempo `<=90` / `<=80` intimate): when
no phase-4/5 song in the pool is calm enough, every candidate sequence fails and
no songset artifacts are written.

## Background — where "all songs filtered out" originates

- **H3** (closing tempo `<=90` / `<=80` intimate): enforced in
  `lab/poc-scripts/poc/songset_constructor/rules/hard_constraints.py:39-41` and
  counted as `valid_closers_h3` role-eligibility in
  `rules/diagnostics.py:51-57`. When `valid_closers_h3 == 0`, no sequence can
  pass.
- **H2** (opening tempo `>=110`): enforced in `hard_constraints.py:37-38`,
  counted as `valid_openers_h2`.
- **H1** (phase coverage): enforced in `hard_constraints.py:35-36`.
- The deterministic beam search in `rules/beam.py:search()` already has
  escalation tiers: strict → 4-song template (line 109) → H4/H5-relaxed
  (line 120). It spawns relaxed configs via
  `RunConfig(**{**config.to_dict(), ...})` (line 110), so **any new `RunConfig`
  fields are automatically inherited by relaxed child configs** — a property
  this plan relies on.
- The `validate()` function takes `relax_h4, relax_h5` booleans today; this plan
  extends that pattern with `relax_h1`.

## Design decisions

- **H3**: raise the BPM ceiling via auto-escalation tiers; `--relax-h3-bpm <n>`
  sets the max ceiling cap.
- **H2**: lower the BPM floor via auto-escalation tiers; `--relax-h2-bpm <n>`
  sets the min floor cap.
- **H1**: relax phase-coverage via auto-escalation; `--relax-h1/--no-relax-h1`
  toggles it.
- **Trigger**: always-on — escalation is attempted automatically after strict
  search fails (mirrors the existing H4/H5 relax tier). A `--no-auto-relax`
  master switch keeps strict-only behavior available.

Explicit CLI flags also feed into the config that the LLM `validate_score` node
reads, so agentic-mode drafts get the same relaxation as the deterministic beam
path.

## Files to change

### 1. `config.py` — add relax fields to `RunConfig`

Add 4 fields (the dataclass already uses `slots=True`):

- `relax_h3_bpm: int | None = None` — max closing-tempo ceiling for escalation;
  `None` → default cap of 120 (100 in intimate).
- `relax_h2_bpm: int | None = None` — min opening-tempo floor for escalation;
  `None` → default cap of 80.
- `relax_h1: bool = True` — whether H1 phase-coverage relaxation is allowed
  during escalation.
- `auto_relax: bool = True` — master switch for H1/H2/H3 auto-escalation.

Add two resolved properties used by `validate()` and `diagnostics`:

- `closing_limit` → `self.relax_h3_bpm if self.relax_h3_bpm is not None else
  (80 if self.intimate else 90)`
- `opening_floor` → `self.relax_h2_bpm if self.relax_h2_bpm is not None else
  110`

Add the 4 fields to `to_dict()` so relaxed child configs inherit them (required
by `beam.search`'s `RunConfig(**{**config.to_dict(), ...})` pattern).

Add validation in `__post_init__`: `relax_h3_bpm` and `relax_h2_bpm` must be
`>= 0`; raise `ValueError` for out-of-range.

### 2. `rules/hard_constraints.py` — read ceilings from config, add `relax_h1`

- Add `relax_h1: bool = False` to `validate()` signature (alongside `relax_h4`,
  `relax_h5`).
- H3 check (line 39-41): replace `closing_limit = 80 if config.intimate else 90`
  with `closing_limit = config.closing_limit`.
- H2 check (line 37-38): replace the `110` literal with `config.opening_floor`.
- H1 check (line 35-36): when `relax_h1` is True, skip the strict
  `phases.count(1) != 1` requirement and the "at least one phase 3/4"
  requirement, retaining only the weaker "must end on phase 4/5" constraint.
- Update `RULE_DESCRIPTIONS` text for H1/H2/H3 to note the relaxable behavior
  (optional, but helps the no-results LLM summary stay accurate).

### 3. `rules/beam.py` — add H1/H2/H3 escalation tiers in `search()`

After the existing H4/H5-relaxed tier (line 120-129), if `config.auto_relax` and
still no proposals, add two more tiers:

- **Tier D (H2/H3 ceilings)**: build a relaxed config copy:

  ```python
  relaxed_config = RunConfig(**{
      **config.to_dict(),
      "relax_h3_bpm": config.relax_h3_bpm or (100 if config.intimate else 120),
      "relax_h2_bpm": config.relax_h2_bpm or 80,
  })
  ```

  Re-run `_sequences` + `validate(...)`. Since `validate` now reads ceilings
  from config, no new args beyond existing `relax_h4/relax_h5` are needed here;
  pass `relax_h4=True, relax_h5=True` to keep prior gains. Tag proposals with
  `warnings=["relaxed_H2_H3", "relaxed_H4_H5"]`.

- **Tier E (also relax H1)**, only if `config.relax_h1`: same as Tier D but
  `validate(..., relax_h1=True)`. Warnings add `"relaxed_H1"`.

Each tier short-circuits as soon as proposals are found. Result still flows
through `rank_proposals`.

### 4. `rules/diagnostics.py` — reflect relaxed ceilings in role counts

- `role_eligibility_counts` (line 46-47): replace
  `closing_limit = 80 if config.intimate else 90` with `config.closing_limit`;
  replace the `110` literal in `valid_openers_h2` with `config.opening_floor`.
  This keeps the no-results diagnostics honest when the user passes explicit
  `--relax-h3-bpm`/`--relax-h2-bpm`.

Note: auto-escalation happens *inside* `beam.search` on local config copies, so
the diagnostics in `graph/nodes.py:beam_seed_candidates` — which use the
original strict config — still show the strict role shortfalls as the root
cause. That is the desired behavior: diagnostics explain *why* relaxation was
needed, and the artifacts' `hard_constraint_warnings` show *which* relaxations
were applied.

### 5. `cli.py` — add 4 Typer flags

Add to the `construct` command signature and forward into `RunConfig`:

- `--relax-h3-bpm` (int, optional)
- `--relax-h2-bpm` (int, optional)
- `--relax-h1/--no-relax-h1` (default True)
- `--auto-relax/--no-auto-relax` (default True)

Pass them through in the `RunConfig(...)` constructor call (cli.py:315).

### 6. `graph/nodes.py` — confirm no structural change needed

Currently diagnostics only run `if not proposals` (line 110). Proposals from
relaxed tiers will suppress diagnostics, which is correct. `search()` is already
called with the user config (line 107). For the LLM path, `validate_score`
(line 198) reads `state["config"]` — since explicit flags set the config fields,
agentic mode gets the same H2/H3 relaxation automatically.

## Tests to add

In `tests/test_songset_constructor_rules.py` and
`tests/test_songset_constructor_config.py`:

- `test_relax_h3_raises_ceiling_allows_loud_closer` — pool where no phase-4/5
  song is `<=90` (e.g. all closers 95–105 BPM); assert `search()` returns
  proposals with `"relaxed_H2_H3"` warning when ceilings raised, zero proposals
  when strict.
- `test_relax_h2_lowers_floor_allows_slow_opener` — pool with only a phase-1
  opener at 95 BPM; assert relaxation produces proposals.
- `test_relax_h1_skips_phase1_opener_requirement` — pool with no phase-1 song
  but a strong phase-2 opener; assert Tier E produces proposals when
  `relax_h1=True`.
- `test_no_auto_relax_keeps_strict_only` — same H3-failing pool,
  `auto_relax=False` → zero proposals.
- `test_config_closing_limit_respects_intimate_and_override` — assert
  `RunConfig(intimate=True).closing_limit == 80`,
  `RunConfig(relax_h3_bpm=115).closing_limit == 115`.
- `test_config_opening_floor_override` — assert
  `RunConfig(relax_h2_bpm=90).opening_floor == 90`.
- `test_to_dict_preserves_relax_fields` — confirm relaxed child-config copies
  inherit caps (critical for `beam.search`'s
  `RunConfig(**{**config.to_dict(), ...})` pattern).
- CLI test: `test_cli_relax_h3_flag_args_accepted` — invoke with
  `--relax-h3-bpm 110` and assert exit code 0 with the synthetic pool (which
  passes strict anyway).

## Verification

```bash
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_rules.py \
  lab/poc-scripts/tests/test_songset_constructor_config.py \
  lab/poc-scripts/tests/test_songset_constructor_cli.py -v
```

Also confirm no regressions in the graph tests:

```bash
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test pytest \
  lab/poc-scripts/tests/test_songset_constructor_graph.py -v
```

## Edge cases / notes

- **Tier interaction with H4/H5**: H2/H3 escalation tiers also pass
  `relax_h4=True, relax_h5=True` so all four relaxations stack — maximal yield
  at the most-relaxed tier.
- **Backward compatibility**: all new flags default to keeping current strict
  behavior reachable (`--no-auto-relax`); without explicit flags, escalation is
  strictly additive after existing tiers fail (never changes a previously-passing
  run's first-pass result).
- **`to_dict()` round-trip**: the 4 new fields must be in `to_dict()` or the
  existing 4-song-template fallback (line 110) and the new Tier D/E configs will
  silently drop the caps.
