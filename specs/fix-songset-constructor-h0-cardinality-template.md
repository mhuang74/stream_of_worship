# Fix: Songset Constructor H0 Cardinality Rejection for `--songs 2` and `--songs 3`

## Status
Investigation complete. Implementation pending approval.

## Summary

When the songset constructor is invoked with `--songs 3` (the CLI default) or
`--songs 2`, the deterministic beam search produces sequences of length 5, which
are then uniformly rejected by the H0 (Cardinality) hard rule. The catalog size
(99 songs) is irrelevant to the failure — the bug is a hardwired phase template
that only handles `songs == 4` and silently falls through to the 5-phase arc for
every other supported song count.

## Reproduction

```bash
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --llm-judge --interactive-review --songs 3 --relax-h2-bpm 80
```

Observed trace:

```
start beam_seed_candidates
stop beam_seed_candidates in 0.17s candidates=0
start finalize_rank
stop finalize_rank in 4.34s proposals=0
Output files written: none
... beam validation rejected 8/8 generated sequences: H0=8 ...
LLM summary failed: 'H0'
```

## Root Cause

### Primary bug — `lab/poc-scripts/poc/songset_constructor/rules/beam.py:16-17`

```python
def _template(songs: int) -> tuple[int, ...]:
    return (1, 3, 4, 5) if songs == 4 else (1, 2, 3, 4, 5)
```

This ternary only special-cases `songs == 4` and falls through to the 5-phase
template `(1, 2, 3, 4, 5)` for **everything else**, including the supported
values `songs=2` and `songs=3` (both are valid per `RunConfig.__post_init__` at
`config.py:63` and the CLI's `--songs min=2 max=5`).

### Failure trace for `--songs 3`

1. `beam_seed_candidates` node (`graph/nodes.py:107`) invokes
   `search(pool, config, matrix)` → `_sequences(...)`.
2. `_sequences` calls `_template(config.songs)` → with `songs=3`, returns
   `target = (1, 2, 3, 4, 5)` (length 5).
3. The beam expands through 5 positions, producing **length-5 sequences** (all
   8 of them).
4. `_proposal_for_sequence` wraps them as `SongsetProposal(items=[5 items])`.
5. `validate()` immediately hits H0 at
   `rules/hard_constraints.py:39`:
   `len(proposal.items)=5 != config.songs=3` → all rejected, `H0=8`.
6. `beam_candidates` ends up empty → `route_after_beam`
   (`graph/nodes.py:350`) routes to `finalize_rank` instead of `llm_plan`, so
   the agentic LLM planner/repair path is also skipped → `proposals=0`.

### Secondary bug — `lab/poc-scripts/poc/songset_constructor/rules/fitness.py:8-9,17`

```python
TEMPLATE_PHASES_5 = (1, 2, 3, 4, 5)
TEMPLATE_PHASES_4 = (1, 3, 4, 5)
...
def f_theme(proposal: SongsetProposal, songs: int) -> float:
    template = TEMPLATE_PHASES_4 if songs == 4 else TEMPLATE_PHASES_5
    distances = [abs((item.phase or 3) - template[index]) for index, item in enumerate(proposal.items)]
    return _clamp(1.0 - sum(distances) / (4.0 * len(template)))
```

Same broken ternary. `f_theme` would compute distance against a length-5
template for a 3-song proposal (using denominator `4.0 * len(template) == 20.0`).
It doesn't IndexError (it only iterates `proposal.items`), but the theme score
is silently wrong for 2/3-song sets even after the beam fix.

## Why the catalog is not the bottleneck

The pool size (99) is irrelevant to this failure. The catalog has plenty of
phase-1 openers >= 110 BPM and phase-4/5 closers <= 90 BPM; the beam simply
never gets to assemble a 3-song sequence because the template is hardwired to
length 5.

## Why tests didn't catch it

- The CLI _default_ `--songs` is `3` (`cli.py:306`), so out-of-the-box runs
  always hit this bug.
- The default `RunConfig.songs=5` masks it for unit tests.
- Existing tests only cover `songs=4` and default-5; none cover `songs=2` or
  `songs=3`.

## Proposed Fix

### Phase template mapping

Map the 5-phase worship arc onto all supported lengths:

| `songs` | template          | notes                                    |
|---------|-------------------|------------------------------------------|
| 2       | `(1, 4)`          | opener + closer; satisfies strict H1     |
| 3       | `(1, 3, 5)`       | opener + worship + closer                |
| 4       | `(1, 3, 4, 5)`    | unchanged                                |
| 5       | `(1, 2, 3, 4, 5)` | unchanged                                |

This choice keeps strict-H1 (`phases.count(1)==1`, at least one phase-3/4,
ends in {4,5}) satisfiable for every length.

### Files to change

1. **`lab/poc-scripts/poc/songset_constructor/rules/beam.py`** (lines 16-17)
   Replace `_template` body with an explicit length-to-phases lookup:

   ```python
   _TEMPLATES: dict[int, tuple[int, ...]] = {
       2: (1, 4),
       3: (1, 3, 5),
       4: (1, 3, 4, 5),
       5: (1, 2, 3, 4, 5),
   }

   def _template(songs: int) -> tuple[int, ...]:
       return _TEMPLATES[songs]
   ```

2. **`lab/poc-scripts/poc/songset_constructor/rules/fitness.py`** (lines 8-9, 17)
   - Add `TEMPLATE_PHASES_2 = (1, 4)` and `TEMPLATE_PHASES_3 = (1, 3, 5)`.
   - Replace the ternary with an explicit lookup:

     ```python
     _THEME_TEMPLATES = {
         2: TEMPLATE_PHASES_2,
         3: TEMPLATE_PHASES_3,
         4: TEMPLATE_PHASES_4,
         5: TEMPLATE_PHASES_5,
     }
     ...
     template = _THEME_TEMPLATES[songs]
     ```

3. **`lab/poc-scripts/tests/test_songset_constructor_rules.py`**
   - New test: `test_template_returns_correct_phase_arc_for_each_song_count`
     - Asserts `_template(2)==(1,4)`, `_template(3)==(1,3,5)`,
       `_template(4)==(1,3,4,5)`, `_template(5)==(1,2,3,4,5)`.
   - New test: `test_beam_search_passes_h0_for_three_songs`
     - Uses `synthetic_pool`, `RunConfig(songs=3, no_llm=True)`.
     - Asserts `proposals` non-empty, `len(proposals[0].items)==3`, and the
       top proposal's validate feedback passes.
   - New test: `test_beam_search_passes_h0_for_two_songs`
     - Same shape for `songs=2`.
   - New test: `test_f_theme_uses_correct_template_for_short_sets`
     - Verifies `f_theme` on a 3-item proposal produces a value close to 1.0
       when phases match `(1,3,5)` and is not heavily suppressed by
       denominator mismatch.

### Out of scope (no changes needed)

- `rules/hard_constraints.py` — H0 check is correct as-is.
- `rules/proposals.py` — `proposal_from_draft` is length-agnostic.
- `rules/diagnostics.py` — uses `_sequences` which inherits the fix.
- `graph/nodes.py` — `beam_seed_candidates` and routers are correct.
- `config.py` — already validates `songs in {2, 3, 4, 5}`.
- `cli.py` — already allows `--songs min=2 max=5`.
- The 4-song fallback at `beam.py:180-181` only triggers when
  `config.songs == 5`, so it doesn't interfere with the now-supported short
  sets.

## Verification

```bash
# Run targeted unit tests
PYTHONPATH=lab/poc-scripts uv run --project lab/poc-scripts \
  --extra songset_constructor --extra test \
  pytest lab/poc-scripts/tests/test_songset_constructor_rules.py -v

# Reproduce the original failing invocation (deterministic path)
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py \
  --no-llm --songs 3 --relax-h2-bpm 80
```

### Expected after fix

Command writes `proposals.json`, `proposal_report.md`, `candidate_pool.csv`,
`graph_trace.jsonl`, and `songset_review.md` to
`output/songset_constructor/<run_id>/`, and prints `Output files written:`
instead of the H0 rejection summary.

## Post-implementation

- Run `graphify update .` per AGENTS.md to refresh the knowledge graph.
- Run Ruff/Black (line length 100, py311) before declaring complete.
- Surface `git status` for review; do not auto-commit.
