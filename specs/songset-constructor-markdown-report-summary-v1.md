# Songset Constructor Markdown Report Enhancements v1

## Goal

Enhance `proposal_report.md` (produced by `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py`) to include:

1. **Per-songset brief summary block** at the top of each `## Rank N` section, with:
   - Song sequence with titles
   - Narrative arc description (LLM-generated when enabled, deterministic fallback otherwise)
   - Key & tempo journey
   - Score + warning highlights
   - Rationale line
2. **Cross-songset Diversity Summary** appended at the end of the report, surfacing bottlenecks (over-reused songs, missing phases, composer concentration, theme coverage gaps).
3. **Stdout echo** of the brief summary block via the CLI after a successful run.

No new output files. No changes to `proposals.json`, `songset_review.md`, `candidate_pool.csv`, or `graph_trace.jsonl`.

## LLM Call Budget (Option 1 — Single Batched Call)

- **+1 LLM call total** per run, regardless of `top_k` (e.g., 20 songsets = 1 added call, not 20).
- Combined with the existing `songset_review.md` LLM call: **2 LLM calls/run** worst case.
- On any failure (parse error, wrong count, exception): all songsets fall back to deterministic narrative. Partial failures are not possible because the response is parsed atomically.
- When `config.no_llm is True` or `len(proposals) < 2`: deterministic only, 0 added LLM calls.

## Scope of Changes

| File | Change |
|---|---|
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | Extend `write_report` signature; add batched LLM summary generator, deterministic narrative helpers, diversity summary helpers; export `brief_summary_block` for CLI reuse |
| `lab/poc-scripts/poc/songset_constructor/cli.py` | Add `_print_brief_summaries` invoked after `_print_output_files`; reuse narratives returned by `write_artifacts` to avoid a second LLM call |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | Add unit tests for new helpers and end-to-end `write_report` snapshot |

No changes to: `models.py`, `config.py`, `graph/*`, `rules/fitness.py`, `rules/proposals.py`, `rules/phases.py`, or any node implementation. The report extracts information already produced by the graph.

## Per-Songset Brief Summary Block

### Placement

Inserted at the **top** of each `## Rank N` section, before the existing score/components/table content. Existing content is preserved verbatim under a new `### Details` subheading.

### Format

```markdown
## Rank N — Score 0.7800

> **Brief Summary**
> Songs: 1. 主你荣耀  →  2. 恩典已降临  →  3. 耶稣我爱祢
> Arc: Phase 1 → 3 → 5 (call → worship → commitment). Opens with an uplifting call, settles into intimate adoration, lands in reflective surrender.
> Journey: C maj → G maj → C maj  |  76 → 110 → 82 BPM arc
> Score: f_theme 0.850, f_tempo 0.720, f_harmony 0.910, f_diversity 0.670  |  Warnings: H4 relaxed
> Rationale: Balanced praise-to-thanksgiving flow.

### Details
<existing score/components/origin/warnings/rationale + table unchanged>
```

### Deterministic Helpers (all in `writer.py`)

- `_song_sequence_line(proposal) -> str` — `1. {title}  →  2. {title}  →  ...`. Uses `proposal.items[*].position` and `.title`. Empty items list returns `"(no songs)"`.
- `_key_tempo_journey_line(proposal) -> str` — enumerates each song's `key mode` (e.g., `C maj`) and `BPM`. Format: `C maj → G maj → C maj  |  76 → 110 → 82 BPM arc`. Missing key → `?`; missing BPM → `?` in the BPM list.
- `_score_warnings_line(proposal) -> str` — `f_theme 0.850, f_tempo 0.720, f_harmony 0.910, f_diversity 0.670  |  Warnings: H4 relaxed` or `...  |  Warnings: none` when `hard_constraint_warnings` is empty.
- `_deterministic_arc_narrative(proposal) -> str` — synthesizes 1-2 sentences from the phase pattern and primary themes. Uses the canonical phase-name table below. Example: `Phase 1 → 3 → 5 (call → worship → commitment). Themes: 赞美, 敬拜, 差遣.`
- `brief_summary_block(proposal, *, config, pool, llm_narrative=None) -> list[str]` — public export. Assembles the blockquote lines. When `llm_narrative` is provided and non-empty, it replaces the deterministic arc narrative; otherwise deterministic is used. Always returns the Songs / Journey / Score / Rationale lines from deterministic helpers (these are factual, not LLM-generated).

### Canonical Phase-Name Table

Derived from `rules/phases.py:THEME_TO_PHASE`. Hard-coded in `writer.py` as a module-level constant:

```python
PHASE_NAMES = {
    1: "call",        # 赞美 (praise)
    2: "thanksgiving",# 感恩
    3: "worship",     # 敬拜 / 祈祷 / 信心 / 圣灵
    4: "response",    # 奉献 / 认罪 / 十字架
    5: "commitment",  # 差遣 / 跟随 / 复兴
}
```

This is for human-readable narrative only; it does not change phase inference logic.

## Batched LLM Call

### `generate_brief_summaries(config, proposals) -> list[str]`

Returns exactly `len(proposals)` strings, aligned by index to `proposals`. Each string is the LLM-generated narrative for that songset (≤2 sentences), or the deterministic fallback when LLM is disabled or fails.

### Behavior

1. If `config.no_llm` or `len(proposals) < 2`: return `[_deterministic_arc_narrative(p) for p in proposals]`. No LLM call.
2. Build `build_chat_model(config)` (reuses existing helper from `graph.llm`). If `None`: deterministic fallback for all.
3. Build a single prompt containing all proposals' structured data, separated by `---PROPOSAL N---` markers. For each proposal, include:
   - Position-ordered list of `(title, phase, themes, key, mode, bpm)`
   - Score breakdown (`f_theme`, `f_tempo`, `f_harmony`, `f_diversity`, `total`)
   - `hard_constraint_warnings`
   - `rationale` (if present)
4. Prompt template (factual guardrails mirror the existing review prompt):

   ```
   You are reviewing Chinese worship songsets. For each proposal below, write
   ≤2 sentences describing the emotional and musical arc — call themes, worship
   arc, and key/tempo trajectory. Use only the facts provided. Do not invent
   songs, scores, or warnings.

   Format your response EXACTLY as:
   <<<SUMMARY 1>>>
   <narrative for proposal 1>
   <<<END SUMMARY 1>>>
   <<<SUMMARY 2>>>
   <narrative for proposal 2>
   <<<END SUMMARY 2>>>
   ... (one block per proposal, in order)

   Proposals:
   ---PROPOSAL 1---
   <structured data>
   ---PROPOSAL 2---
   <structured data>
   ...
   ```

5. Invoke `chat_model.invoke(prompt)`. Extract content via the same `getattr(response, "content", response)` + list-join pattern used in `build_review_report` (writer.py:170-172).
6. Parse the response by scanning for `<<<SUMMARY N>>>` ... `<<<END SUMMARY N>>>` delimiters. Collect into a dict keyed by index.
7. **Validation**: if the parsed dict does not contain exactly `len(proposals)` entries (indices 1..N), OR any entry is empty after stripping, fall back entirely to deterministic for ALL proposals. No partial LLM results.
8. Truncate each narrative to ≤2 sentences via split on `. ` (defensive; the prompt already requests ≤2).
9. Return the list aligned to `proposals` order.

### Failure Modes

- LLM exception → deterministic fallback for all proposals (caught broadly, mirroring `build_review_report`'s `except Exception`).
- Malformed response (missing delimiters, wrong count) → deterministic fallback for all.
- Empty content → deterministic fallback for all.
- No partial-success state: the validation in step 7 is atomic.

## Cross-Songset Diversity Summary

### Placement

Appended at the **end** of `proposal_report.md`, after the last `## Rank N` section. Skipped entirely (returns empty list) when `len(proposals) <= 1`.

### Format

```markdown
## Diversity Summary

Across 3 proposals (9 song slots total):

| Metric | Value |
|---|---|
| Unique songs | 7 / 9 (78%) |
| Unique themes | 6 / 12 |
| Unique composers | 5 |
| Unique phases | 4 / 5 |
| Middle-song reuse | 2 (across 3 middle slots) |

### Song Overlap Matrix

| | R1 | R2 | R3 |
|---|---|---|---|
| R1 | — | 2 | 1 |
| R2 | 2 | — | 1 |
| R3 | 1 | 1 | — |

### Song Frequency

| Song ID | Title | Times Used |
|---|---|---:|
| s1 | 主你荣耀 | 3 |
| s4 | 恩典已降临 | 2 |

### Theme Coverage

Present (8): 赞美, 感恩, 敬拜, 奉献, 认罪, 十字架, 差遣, 复兴
Missing (4): 祈祷, 信心, 圣灵, 跟随

### Bottlenecks

- Most-reused song: "主你荣耀" appears in 3/3 songsets.
- Phase gap: Phase 2 (thanksgiving) absent from all top-k songsets.
- Composer concentration: composer "张三" authored 4/9 slots (44%).
```

### Helpers (all in `writer.py`)

- `_diversity_summary(proposals, pool) -> list[str]` — top-level orchestrator. Returns `[]` when `len(proposals) <= 1`.
- `_diversity_metrics(proposals, pool) -> dict` — computes:
  - `total_slots` = sum of `len(p.items)` across proposals
  - `unique_songs` = set of `song_id` across all items
  - `unique_themes` = set of themes across all items
  - `unique_composers` = set of composers (looked up via `pool` by `song_id`)
  - `unique_phases` = set of `phase` across all items
  - `middle_song_ids` = union of `middle_song_ids(p)` (reuse from `rules.fitness`) across proposals
  - `middle_reuse_count` = `total_middle_slots - len(unique_middle_songs)` where `total_middle_slots` is the sum of middle-slot counts across proposals
- `_song_overlap_matrix(proposals) -> list[str]` — symmetric matrix rows. Cell `(i, j)` = `len(set(songs_i) & set(songs_j))`. Diagonal is `—`. Header row + one row per proposal.
- `_song_frequency_table(proposals) -> list[str]` — table of songs appearing in >1 proposal, sorted by `Times Used` desc, then `Title` asc. Empty table → omit subsection (return only a header note).
- `_theme_coverage_lines(proposals) -> list[str]` — `Present (N): ...` and `Missing (M): ...` lines. The full theme set is the 12 themes from `rules.themes.THEMES` (imported).
- `_bottleneck_lines(metrics, proposals, pool) -> list[str]` — heuristic findings, empty list when none:
  - **Most-reused song**: any song appearing in >50% of proposals. List all that qualify, sorted by count desc.
  - **Phase gap**: any phase in `{1, 2, 3, 4, 5}` absent from all proposals. Use `PHASE_NAMES` for the label.
  - **Composer concentration**: any composer providing >33% of total slots. List all that qualify.

### Composer Lookup

Reuse `composer_diversity()` in `rules/proposals.py:85-92` for per-proposal composer counts. For cross-songset composer totals, build a `song_id -> composer` map from `pool` once and aggregate.

## Refactor: `write_report` Signature

### Current

```python
def write_report(path: Path, proposals: list[SongsetProposal]) -> None:
```

### New

```python
def write_report(
    path: Path,
    *,
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
) -> list[str]:
```

- `config` and `pool` are now required (keyword-only). Both are already in scope at the call site in `write_artifacts` (writer.py:39).
- Returns `list[str]` — the generated LLM narratives (aligned to `proposals`), so the CLI can reuse them for stdout echo without a second LLM call.

### Internal Flow

1. Call `narratives = generate_brief_summaries(config, proposals)` once.
2. For each proposal (with index), assemble `## Rank N` section: brief summary block (using `narratives[i]` if non-empty) + existing `### Details` content.
3. Append `_diversity_summary(proposals, pool)` lines.
4. Write to `path`.
5. Return `narratives`.

### `write_artifacts` Update

Current `write_artifacts` returns `dict[str, str]` (name → path). To let the CLI reuse narratives without a second LLM call, change the return type to `dict[str, str]` plus a side-channel for narratives. Two options:

**Option A (preferred)**: `write_artifacts` returns `dict[str, str]` unchanged; narratives are stored in a module-level `_last_narratives` cache (single-entry, keyed by `config.thread_id`). CLI reads from cache. Simple, no signature break for downstream callers.

**Option B**: `write_artifacts` returns `tuple[dict[str, str], list[str]]`. Cleaner but breaks any caller expecting a dict. Check callers before adopting.

Adopt **Option A** unless `write_artifacts` has external callers that would be affected; the test file `test_songset_constructor_artifacts.py` calls it but only uses the returned dict, so Option A is safe.

## CLI Stdout Echo

### New helper in `cli.py`

```python
def _print_brief_summaries(config: RunConfig, result: dict) -> None:
    proposals = result.get("final_proposals", []) or []
    pool = result.get("pool", []) or []
    if not proposals:
        return
    from poc.songset_constructor.artifacts import writer as writer_mod

    narratives = writer_mod.get_cached_narratives(config.thread_id)
    if narratives is None:
        narratives = writer_mod.generate_brief_summaries(config, proposals)
    console.print("[green]Proposed songsets (brief):[/green]")
    for proposal, narrative in zip(proposals, narratives):
        block = writer_mod.brief_summary_block(
            proposal, config=config, pool=pool, llm_narrative=narrative
        )
        console.rule(f"Rank {proposal.rank}")
        console.print("\n".join(block))
```

### Call Site

In `construct()` (cli.py:384-388), after `_print_output_files(paths)` and the `if not paths:` guard:

```python
paths = result.get("artifact_paths", {})
_print_output_files(paths)
if not paths:
    _print_no_results_summary(config, result)
else:
    _print_brief_summaries(config, result)
```

### Cache Helper

Add to `writer.py`:

```python
_LAST_NARRATIVES: dict[str, list[str]] = {}

def cache_narratives(thread_id: str, narratives: list[str]) -> None:
    _LAST_NARRATIVES[thread_id] = narratives

def get_cached_narratives(thread_id: str) -> list[str] | None:
    return _LAST_NARRATIVES.get(thread_id)
```

`write_report` calls `cache_narratives(config.thread_id, narratives)` before returning. CLI reads via `get_cached_narratives`. Falls back to a fresh `generate_brief_summaries` call only if the cache miss (e.g., when `write_artifacts` was skipped — should not happen in normal flow, but defensive).

## Tests

Add to `lab/poc-scripts/tests/test_songset_constructor_artifacts.py`. Reuse the existing `_proposal()` and `synthetic_pool` fixtures.

### Deterministic Helpers

- `test_song_sequence_line` — 3 items, 1 item, 0 items.
- `test_key_tempo_journey_line` — all keys/BPMs present; missing key on one item; missing BPM on one item; both missing.
- `test_score_warnings_line` — with warnings, without warnings.
- `test_deterministic_arc_narrative` — phase patterns 1→3→5, 1→4, 2→3→4→5, single-phase edge case.

### `brief_summary_block`

- `test_brief_summary_block_deterministic` — no `llm_narrative`; all 5 lines present; deterministic arc narrative used.
- `test_brief_summary_block_with_llm` — `llm_narrative` provided; LLM narrative replaces deterministic arc line; other lines unchanged.

### `generate_brief_summaries`

- `test_generate_brief_summaries_no_llm` — `config.no_llm=True`; returns deterministic for all; no LLM invoked.
- `test_generate_brief_summaries_single_proposal` — `len(proposals)==1`; deterministic; no LLM invoked.
- `test_generate_brief_summaries_llm_success` — mocked `build_chat_model` returns a `FakeChat` whose `invoke` returns a properly-delimited response for 3 proposals; parsed narratives returned in order.
- `test_generate_brief_summaries_llm_malformed` — `FakeChat` returns garbage without delimiters; falls back to deterministic for all.
- `test_generate_brief_summaries_llm_wrong_count` — `FakeChat` returns 2 summaries for 3 proposals; falls back to deterministic for all.
- `test_generate_brief_summaries_llm_exception` — `FakeChat.invoke` raises; falls back to deterministic for all.

### Diversity Summary

- `test_diversity_summary_empty` — `[]` → `[]`.
- `test_diversity_summary_single` — 1 proposal → `[]`.
- `test_diversity_summary_three_overlapping` — 3 proposals with shared songs; metrics table, overlap matrix, frequency table, theme coverage, and bottleneck lines all present and correct.
- `test_song_overlap_matrix_symmetric` — matrix is symmetric; diagonal is `—`.
- `test_bottleneck_lines_none` — proposals with no over-reused songs, no missing phases, no concentrated composers → empty list.

### End-to-End

- `test_write_report_with_summary` — snapshot-style: 3 proposals, deterministic mode (`no_llm=True`); assert report contains `> **Brief Summary**`, `> Songs:`, `> Arc:`, `> Journey:`, `> Score:`, `### Details`, and `## Diversity Summary` sections. Reuse existing `_proposal()` fixture, varying ranks.
- `test_write_report_returns_narratives` — `write_report` returns a list of length `len(proposals)`.

## Non-Goals

- No new output file (no `songset_summary.md`).
- No changes to `songset_review.md` prompt or output (kept verbose LLM review separate).
- No changes to `proposals.json` payload structure.
- No new fields on `SongsetProposal` / `ProposalItem` / `ScoreBreakdown`.
- No changes to `rules/*` or `graph/*`.
- No caching layer beyond the single-entry `_LAST_NARRATIVES` dict for CLI reuse.
- No structured-output schema for the LLM (plain text with delimiters is sufficient and avoids Pydantic overhead).

## Open Questions Resolved

1. **Phase naming source**: `rules/phases.py` does not expose a phase-number → name table; it maps themes to phases. A new `PHASE_NAMES` constant is added to `writer.py` for narrative purposes only, derived from the theme-to-phase mapping in `rules/phases.py:THEME_TO_PHASE`.
2. **Cache approach**: Option A (module-level single-entry cache keyed by `thread_id`). Avoids breaking `write_artifacts` return type. CLI reads from cache; falls back to fresh `generate_brief_summaries` only on cache miss.

## Implementation Order

1. Add `PHASE_NAMES` constant and deterministic helpers (`_song_sequence_line`, `_key_tempo_journey_line`, `_score_warnings_line`, `_deterministic_arc_narrative`).
2. Add `brief_summary_block` public export.
3. Add `generate_brief_summaries` with LLM batching + delimiter parsing + fallback.
4. Add diversity helpers (`_diversity_metrics`, `_song_overlap_matrix`, `_song_frequency_table`, `_theme_coverage_lines`, `_bottleneck_lines`, `_diversity_summary`).
5. Add `_LAST_NARRATIVES` cache + `cache_narratives` / `get_cached_narratives`.
6. Refactor `write_report` signature and internal flow; update `write_artifacts` call site.
7. Add `_print_brief_summaries` to `cli.py`; wire into `construct()`.
8. Add tests per the test plan above.
9. Run `uv run --project lab/poc-scripts --extra test pytest tests/test_songset_constructor_artifacts.py tests/test_songset_constructor_cli.py -v` and fix failures.
