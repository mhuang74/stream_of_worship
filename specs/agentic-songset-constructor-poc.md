# Agentic Songset Constructor POC Plan

## Summary

Create a Python POC script under `lab/poc-scripts/` that reads the live published/analyzed catalog, uses LangGraph as an agentic planner/evaluator loop, and writes proposal artifacts only. The POC will not create `songsets` or `songset_items`.

LangGraph guidance used: [overview](https://docs.langchain.com/oss/python/langgraph/overview), [workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).

## Key Changes

- Add a POC CLI entry point, e.g. `lab/poc-scripts/construct_songset_agent.py`, plus testable helper modules under `lab/poc-scripts/poc/songset_constructor/`.
- Add a `songset_constructor` optional dependency group in `lab/poc-scripts/pyproject.toml` with `langgraph`, `langchain-core`, `langchain-openai`, `pydantic`, `typer`, and `rich`.
- Configure LLM calls through existing-style env vars: `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, and `SOW_LLM_MODEL`; default CLI mode is agentic and fails fast if these are missing.
- Query active songs with active, published, completed-analysis recordings from Postgres via existing `ConnectionProvider`/`psycopg` patterns; exclude `CPW` by default.
- Default to 5-song sets because `delivery/webapp/src/lib/constants.ts` currently sets `SONGSET_MAX_SONGS = 5`; allow 4-song compact sets, leave 6-song extended sets as future/non-webapp-compatible.

## Implementation Details

- Define Pydantic models:
  - `SongCandidate`: song metadata, recording hash, BPM, key/mode/confidence, album/composer, inferred themes, phase.
  - `TransitionCandidate`: adjacent pair metrics, CFD, BPM delta, shift suggestion, crossfade/gap recommendation, warnings.
  - `SongsetProposal`: ordered items, transition params, score breakdown, LLM rationale, validation warnings.
  - `ConstructorState`: graph state containing config, pool, transition matrix, candidate beams, LLM drafts, validation feedback, final proposals.
- Implement deterministic music rules from `reports/research_report_chinese_worship_songset.md`:
  - Theme keyword classifier and `infer_phase()`.
  - Circle-of-Fifths distance, compatibility score, and +/-2 semitone shift suggestion.
  - Hard gates H1-H8 and weighted fitness: theme 40%, tempo 30%, harmony 20%, diversity 10%.
  - Dead-end fan-out detection and relaxed fallback warnings for limited pools.
- LangGraph `StateGraph` nodes:
  - `load_catalog`: read DB pool and normalize rows.
  - `enrich_pool`: infer themes/phases and reject unusable rows.
  - `build_transition_matrix`: precompute tempo/key/shift compatibility.
  - `beam_seed_candidates`: deterministic beam search creates valid/near-valid candidates.
  - `llm_plan`: LLM proposes or revises ordered songsets using structured output constrained to known recording hashes.
  - `validate_score`: deterministic validator rejects invalid drafts and emits actionable feedback.
  - `llm_refine`: evaluator-optimizer loop, max 3 iterations.
  - `optional_review`: if `--interactive-review`, use `interrupt()` with JSON-serializable proposal summaries.
  - `write_artifacts`: write `proposals.json`, `proposal_report.md`, `candidate_pool.csv`, and `graph_trace.jsonl`.
- Use LangGraph persistence with an in-memory checkpointer for non-interactive runs and a local SQLite checkpointer under the output run directory when `--interactive-review` or `--resume-thread-id` is used.
- CLI interface:
  - `--songs 4|5`, default `5`
  - `--top-k`, default `3`
  - `--pool-limit`, default `200`
  - `--output-dir`, default centralized under `lab/poc-scripts/output/songset_constructor/`
  - `--album-series`, `--include-dev/--no-include-dev`, `--include-cpw`
  - `--intimate`, `--hymnal-mode`, `--season`
  - `--interactive-review`, `--resume-thread-id`
  - `--llm-model` override for `SOW_LLM_MODEL`

## Output Contract

- `proposals.json`: machine-readable top proposals with ordered `song_id`, `recording_hash_prefix`, `position`, `key_shift_semitones`, `tempo_ratio`, `gap_beats`, `crossfade_enabled`, `crossfade_duration_seconds`, score breakdown, and warnings.
- `proposal_report.md`: human-readable ranked songsets with Chinese titles, phase/theme labels, BPM/key transitions, and why the agent selected each set.
- `candidate_pool.csv`: normalized catalog subset used by the run.
- `graph_trace.jsonl`: node-level audit events, LLM draft IDs, validation failures, and final selection metadata.

## Test Plan

- Unit tests for key parsing, CFD, relative major/minor normalization, key scoring, and transposition suggestions.
- Unit tests for Chinese theme keyword classification, `infer_phase()`, CPW exclusion, and DEV/default phase behavior.
- Unit tests for H1-H8 validation and weighted fitness with synthetic `SongCandidate` fixtures.
- Graph tests using a fake structured-output LLM that first emits invalid drafts, then verifies the refinement loop repairs them.
- CLI smoke test with fixture rows and a temporary output directory; assert all four artifacts are written and no database mutation occurs.
- Run with:
  `uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v`

## Assumptions

- The POC is read-only against Postgres and output-only on disk.
- "Agentic planner" means the LLM may propose/revise song ordering, but deterministic validation and scoring are authoritative.
- The implementation will not add schema columns such as `songs.themes`; theme tags are inferred in-memory for this POC.
