# Songset Constructor POC — Implementation Plan

> Detailed plan for a LangGraph-based agentic songset constructor POC under `lab/poc-scripts/`.
> Drafted from `reports/research_report_chinese_worship_songset.md` (sections 1–5) and refined via clarification interview.
> Reference (not authoritative): `specs/agentic-songset-constructor-poc.md`.

| | |
|---|---|
| **Date** | 2026-06-30 |
| **Status** | Plan — pending implementation |
| **Component** | `lab/poc-scripts/poc/songset_constructor/` |
| **Read-only** | Postgres reads only; no writes to `songsets` / `songset_items` |
| **Output** | Proposal artifacts only, written to `lab/poc-scripts/output/songset_constructor/` |

---

## 1. Decisions Locked (from clarification interview)

| # | Decision | Choice |
|---|---|---|
| D1 | Run modes | Agentic (default; LLM creds required) **and** `--no-llm` deterministic mode (beam + validator only; no creds needed). |
| D2 | Theme inference depth | Four signals: title-keyword + lyrics-keyword + `song_embedding` cosine + `song_line_embedding` max-line cosine. |
| D3 | Theme signal fusion | Weighted: title 0.35 · lyrics 0.25 · song-embedding 0.25 · max line-embedding 0.15. `infer_phase` = phase of argmax weighted theme. |
| D4 | Theme anchor embeddings | Pre-computed fixture file (`lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json`), model `text-embedding-3-small`, 1536-dim. Committed to repo. Regeneration script provided but optional. |
| D5 | pgvector reads | `SELECT embedding::text …` parsed to `numpy.ndarray`. No `pgvector` Python dependency. |
| D6 | Planner/seeder balance | Beam search authoritative; LLM revises. Deterministic beam emits top-K valid candidates (H1–H8 passing). LLM planner picks/reorders among them via structured output constrained to known `hash_prefix` values. Validator re-scores. |
| D7 | Interactive review (`--interactive-review`) | `interrupt()` surfaces top proposal summary. Resume with `Command(resume={'action': 'approve'\|'reject'\|'edit', 'edits': {...}})`. `edit` re-enters `validate_score` before `write_artifacts`. |
| D8 | Checkpointer | `InMemorySaver` for non-interactive runs. `SqliteSaver` at `<output_dir>/checkpoint.db` when `--interactive-review` or `--resume-thread-id`. |
| D9 | LLM-as-judge | Deterministic validator authoritative end-to-end. Optional `--llm-judge` flag adds an `llm_judge` node that re-ranks the top-K finalists with LLM reasoning. |
| D10 | Song counts | 5-song default (webapp-compatible). 4-song compact supported. 6-song extended OUT OF SCOPE. |
| D11 | Default pool scope | SOP-only: `album_series IN ('PW','DEV')`. `--album-series` opt-in narrows further (e.g. `JB`, `BLCC`, `WoW`, `BR`, `HYMN`, `XMAS`). `--include-cpw` opts in to `CPW` (children's). |
| D12 | Top-K | 3 default, configurable via `--top-k`. Ties broken by: fitness → composer diversity → `hash_prefix` lexicographic (reproducibility). |
| D13 | LangSmith tracing | Opt-in via `SOW_LANGSMITH_TRACING=true` env var (off by default). |

---

## 2. Architecture Overview

```
┌────────────────── lab/poc-scripts/ ──────────────────────┐
│                                                            │
│  construct_songset_agent.py      ← CLI entry (typer)       │
│                                                                │
│  poc/songset_constructor/                                  │
│    __init__.py                                                │
│    cli.py                  ← typer app, flag parsing        │
│    config.py               ← RunConfig dataclass + env      │
│    db.py                   ← pool query (clone _list_lrc)  │
│    models.py               ← Pydantic state + IO schemas   │
│    theme_anchors.json      ← fixture (D4)                   │
│    rules/                                                  │
│      __init__.py                                              │
│      harmony.py            ← §3.3 cfd / key_compat / shift  │
│      themes.py             ← §4.2/4.3 classifiers + fusion │
│      phases.py             ← infer_phase orchestration       │
│      fitness.py            ← §5.3 F_theme/F_tempo/...        │
│      hard_constraints.py   ← §5.1 H1–H8                     │
│      beam.py               ← deterministic beam search      │
│      transitions.py        ← §3.2 transition recommendations│
│      embeddings.py         ← pgvector::text parse + cosine  │
│    graph/                                                  │
│      __init__.py                                              │
│      state.py              ← ConstructorState TypedDict      │
│      nodes.py              ← all LangGraph node functions   │
│      builder.py            ← StateGraph wiring + compile    │
│      llm.py                ← ChatOpenAI + structured output │
│      checkpointer.py       ← InMemory/Sqlite chooser        │
│    artifacts/                                              │
│      __init__.py                                              │
│      writer.py             ← proposals.json/md/csv/jsonl    │
│      trace.py              ← graph_trace.jsonl emitter      │
│    regen_theme_anchors.py  ← one-shot fixture regenerator   │
│                                                                │
│  tests/                                                    │
│    test_songset_constructor_harmony.py                        │
│    test_songset_constructor_themes.py                         │
│    test_songset_constructor_fitness.py                        │
│    test_songset_constructor_hard_constraints.py               │
│    test_songset_constructor_beam.py                            │
│    test_songset_constructor_graph.py                           │
│    conftest_songset.py       ← shared fixtures (synthetic)    │
└────────────────────────────────────────────────────────────┘
```

**Key composition choices (LangGraph):**
- **Graph API** (`StateGraph` + `add_node` / `add_edge` / `add_conditional_edges`) — not the functional `@entrypoint` API. Needed for `interrupt()` and checkpointer semantics.
- **State schema** = `TypedDict` with `Annotated[list, operator.add]` reducers for accumulating trace events, validation feedback, and beam candidates.
- **LLM structured output** via `ChatOpenAI(...).with_structured_output(SongsetDraft, method="json_schema")`. The `SongsetDraft` Pydantic schema constrains every `hash_prefix` to a member of the known pool (validated post-invoke; rejects hallucinated hashes by re-mapping to the closest valid candidate or sending feedback).
- **Conditional edges** drive the evaluator-optimizer loop: `validate_score → route_validation → {Accepted | Refine}` where `Refine → llm_refine → validate_score`, capped at 3 iterations via an `iterations` counter in state.

---

## 3. Optional Dependency Group

Add to `lab/poc-scripts/pyproject.toml`:

```toml
songset_constructor = [
    "langgraph>=0.2.50",
    "langchain-core>=0.3.0",
    "langchain-openai>=0.2.0",
    "pydantic>=2.7.0",
    "typer>=0.12.0",
    "rich>=13.0.0",
    "numpy>=1.26.0",
]
```

Rationale:
- `langgraph` brings `langgraph.graph`, `langgraph.checkpoint.memory`, `langgraph.checkpoint.sqlite`, `langgraph.types`.
- `langchain-openai` ships `ChatOpenAI` with `with_structured_output(method="json_schema")`.
- `numpy` for cosine similarity and array parsing of `embedding::text`.
- No `pgvector` dep (D5). No ML/torch (matches Admin CLI's "never import PyTorch" boundary).
- Test deps reuse the existing `test = ["pytest>=7.4.0", "pytest-mock>=3.12.0"]` extra — no new test-only deps needed. `pytest-asyncio` is already configured via `asyncio_mode = "auto"` in pyproject.

Run command: `uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v`.

---

## 4. Configuration & Environment

### 4.1 `RunConfig` (`config.py`)

Pydantic `BaseSettings`-free plain dataclass (keep POC simple; no `pydantic-settings` dep). Fields:

| Field | Source | Default |
|---|---|---|
| `songs` | CLI `--songs` | `5` (allowed: `4`, `5`) |
| `top_k` | CLI `--top-k` | `3` |
| `pool_limit` | CLI `--pool-limit` | `200` |
| `output_dir` | CLI `--output-dir` | `lab/poc-scripts/output/songset_constructor/<timestamp>` |
| `album_series` | CLI `--album-series` (repeatable) | `["PW","DEV"]` |
| `include_cpw` | CLI `--include-cpw` | `False` |
| `intimate` | CLI `--intimate` | `False` (tightens H3 closing ≤ 80 BPM) |
| `hymnal_mode` | CLI `--hymnal-mode` | `False` |
| `season` | CLI `--season` | `None` (one of `advent`, `christmas`, `lent`, `easter`, `pentecost`) |
| `interactive_review` | CLI `--interactive-review` | `False` |
| `resume_thread_id` | CLI `--resume-thread-id` | `None` |
| `no_llm` | CLI `--no-llm` | `False` |
| `llm_judge` | CLI `--llm-judge` | `False` |
| `llm_model` | CLI `--llm-model` | env `SOW_LLM_MODEL` |
| `embedding_model` | (fixture-baked, not runtime) | `text-embedding-3-small` |

### 4.2 Environment Variables

| Var | Required | Purpose |
|---|---|---|
| `SOW_DATABASE_URL` | Yes (agentic + no-llm) | Postgres DSN; falls back to `AppConfig.load().get_connection_url()`. |
| `SOW_LLM_API_KEY` | Yes (agentic only) | OpenAI-compatible API key. Fail fast with clear message if missing and not `--no-llm`. |
| `SOW_LLM_BASE_URL` | Recommended (agentic) | OpenAI-compatible gateway URL. |
| `SOW_LLM_MODEL` | Yes (agentic) | Default model id (e.g. `gpt-4o-mini`). |
| `SOW_LANGSMITH_TRACING` | Optional | Set to `true` to enable `LANGSMITH_TRACING=true` + reads `LANGSMITH_API_KEY`. Off by default. |

`--no-llm` mode requires only `SOW_DATABASE_URL`. Agentic mode fails fast on missing `SOW_LLM_API_KEY` / `SOW_LLM_MODEL`.

---

## 5. Data Models (`models.py`)

All Pydantic v2 `BaseModel`. The existing repo dataclasses (`Song`, `Recording`, `SongWithRecording`) are used inside `db.py` to deserialize rows; conversion to Pydantic happens at the `enrich_pool` boundary.

### 5.1 `SongCandidate`

```python
class SongCandidate(BaseModel):
    song_id: str
    title: str
    title_pinyin: Optional[str] = None
    composer: Optional[str] = None
    lyricist: Optional[str] = None
    album_name: Optional[str] = None
    album_series: Optional[str] = None
    recording_hash_prefix: str
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None        # from recordings; fallback songs.musical_key
    musical_mode: Optional[str] = None       # "maj" | "min"
    key_confidence: Optional[float] = None
    loudness_db: Optional[float] = None
    lyrics_raw: Optional[str] = None
    # Inferred in enrich_pool:
    themes: dict[str, float] = {}            # theme -> fused score in [0,1]
    phase: int = 0                           # 1..5 via infer_phase
    fan_out: int = 0                         # dead-end detection (§5.5)
    is_dead_end: bool = False
    is_hymn: bool = False                    # album_series == "HYMN"
```

### 5.2 `TransitionCandidate`

```python
class TransitionCandidate(BaseModel):
    from_hash_prefix: str
    to_hash_prefix: str
    cfd: int
    bpm_delta: float
    key_compat: float                       # key_compatibility_score(cfd)
    suggested_key_shift: int                 # {-2..+2} minimising CFD; 0 if already ≤2
    transition_technique: str                # §3.2 enum: pivot/direct/vamp/relative/half_step/transposition
    crossfade_enabled: bool
    crossfade_duration_seconds: float        # 0.0 unless CFD>2 or technique=vamp
    gap_beats: float                         # 2.0 default; ≥4 when direct modulation
    warnings: list[str] = []
```

### 5.3 `SongsetDraft` (LLM structured-output schema)

```python
class DraftItem(BaseModel):
    position: int                            # 1-indexed
    recording_hash_prefix: str               # MUST be in known pool
    key_shift_semitones: int = 0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: float = 0.0
    gap_beats: float = 2.0
    tempo_ratio: float = 1.0

class SongsetDraft(BaseModel):
    items: list[DraftItem]                    # len == RunConfig.songs
    rationale: str                           # LLM explanation (for proposal_report.md)
```

### 5.4 `SongsetProposal`

```python
class ProposalItem(DraftItem):
    song_id: str
    title: str
    phase: int
    themes: list[str]                        # top-2 themes by fused score
    bpm: Optional[float]
    key: Optional[str]
    mode: Optional[str]

class ScoreBreakdown(BaseModel):
    f_theme: float
    f_tempo: float
    f_harmony: float
    f_diversity: float
    total: float                             # weighted sum per §5.3

class SongsetProposal(BaseModel):
    rank: int
    items: list[ProposalItem]
    score: ScoreBreakdown
    rationale: str
    hard_constraint_warnings: list[str] = [] # surfaced when §5.5 relaxation kicked in
    llm_origin: bool                         # True if LLM revised, False if pure-beam
```

### 5.5 `ValidationFeedback`

```python
class ValidationFeedback(BaseModel):
    passed: bool
    violated: list[str]                      # H1..H8 codes that failed
    errors: list[str]                        # human-readable per-violation
    repair_hints: list[str]                  # actionable suggestions for llm_refine
```

### 5.6 `ConstructorState` (LangGraph TypedDict — `graph/state.py`)

```python
class ConstructorState(TypedDict):
    config: RunConfig
    pool: list[SongCandidate]
    transition_matrix: dict[tuple[str, str], TransitionCandidate]
    beam_candidates: Annotated[list[SongsetProposal], operator.add]
    llm_drafts: Annotated[list[SongsetDraft], operator.add]
    current_draft: Optional[SongsetDraft]
    feedback: Optional[ValidationFeedback]
    iterations: int                          # refinement loop counter (manual replace)
    final_proposals: list[SongsetProposal]
    trace: Annotated[list[dict], operator.add]  # graph_trace.jsonl events
    approved: Optional[bool]                 # set by interactive review
    edits: Optional[dict]                    # set when reviewer returns action=edit
```

Note: `iterations`, `current_draft`, `feedback` are overwrite-reduced (default dict-merge semantics — last write wins), which is what we want for non-accumulating scalars.

---

## 6. DB Pool Query (`db.py`)

Clone `lab/sow-app/src/sow_lab_app/services/catalog.py:253-301` (`_list_lrc_songs`) shape. Use `SONG_COLUMNS_FOR_JOIN` + `RECORDING_COLUMNS_FOR_JOIN` from `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:220-238`. Also JOIN `song_embedding` (one row per `song_id`) and fetch `song_line_embedding` (0..N rows per song) lazily into a small dict.

```python
POOL_QUERY = """
SELECT {song_cols},
       {recording_cols},
       se.embedding::text   AS song_embedding_text,
       se.model_version      AS song_embedding_model
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status = 'published'
  AND r.analysis_status = 'completed'
  AND r.lrc_status = 'completed'
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND s.album_series = ANY(%s)
ORDER BY s.title
LIMIT %s
"""
```

- `album_series` parameter list built from `RunConfig.album_series` (+ `CPW` if `include_cpw`; + seasonal series if `season`).
- `pool_limit` parameter.
- Row → `Song` / `Recording` via existing `from_row` methods (`ops/admin-cli/src/stream_of_worship/admin/db/models.py`). Then build `SongCandidate` Pydantic.
- Separate `fetch_line_embeddings(song_ids: list[str]) -> dict[str, list[SongLineEmbedding]]` query (only the lines for songs in the pool; bounded by `pool_limit`).
- All access via `ReadOnlyClient` wrapping `ConnectionProvider(AppConfig.load().get_connection_url())` — matches the existing `lab/poc-scripts/utils.py` bootstrap (lines 96-216).
- Connection closed in `finally:`.

### 6.1 pgvector parsing (`rules/embeddings.py`)

```python
def parse_pgvector_text(s: str) -> np.ndarray:
    # s looks like "[0.0123,-0.45,...]" ; may also bare "0.01,0.02"
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return np.array([float(x) for x in s.split(",")], dtype=np.float32)
```

Cosine: `np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + eps)`.

---

## 7. Music Rules (`rules/`)

### 7.1 `harmony.py` — §3.3

Transcribe the report's verbatim `pitch_class`, `relative_major_pc`, `fifth_distance_on_circle`, `cfd`, `key_compatibility_score`. Pure functions, no I/O. Also add:

- `normalize_key(raw: str) -> tuple[str, str]`: handle `C`, `C#`, `Db`, `Bb`, `Cmaj`, `Cmin`, `C major`, `A minor`, trailing whitespace; return `(note, mode)` with `mode` lower-cased to `"maj"|"min"` (default `"maj"`).
- `suggest_key_shift(from_key, from_mode, to_key, to_mode) -> tuple[int, int]`: returns `(best_shift, resulting_cfd)` minimising CFD over shifts `{-2,-1,0,+1,+2}` applied to `to_key`. Prefers 0, then min absolute shift. Returns 0-shift when CFD already ≤2.

### 7.2 `themes.py` — §4.2 classifier + D3 fusion

- `THEME_VOCAB`: the 12 themes from §1, each with keyword regex sets (Chinese + English + pinyin) operating over `title` and `lyrics_raw`.
- `classify_title_themes(title, title_pinyin) -> dict[str, float]`: returns per-theme confidence ∈ [0,1] based on keyword presence.
- `classify_lyrics_themes(lyrics_raw) -> dict[str, float]`: sample 2-line sliding windows; count keyword hits per theme; normalise by total hits.
- `classify_embedding_themes(song_vec, line_vecs, theme_anchors) -> tuple[dict[str,float], dict[str,float]]`: returns per-theme cosine score for the song-level vector (max across the 12 anchors) AND a per-theme max-line score (max cosine across lyric-line vectors and the anchor).

### 7.3 `phases.py` — §4.3

- `THEME_TO_PHASE`: `{赞美, 感恩}: 1 or 2 (per song band), {敬拜, 祈祷, 信心}: 3, {奉献, 认罪, 十字架}: 4, {差遣, 跟随, 复兴}: 5, {圣灵}: 3 or 4 (by BPM fallback)`.
- `fuse_themes(title, lyrics, song_emb, line_emb, anchors) -> dict[str, float]`: weighted per D3.
- `infer_phase(fused: dict[str, float], tempo_bpm: Optional[float]) -> int`: per §4.3 in priority order — keyword/anchor override first, tempo fallback last. Apply `season` override (e.g. `advent`/`christmas` forces `赞美` + tag `圣诞` on applicable songs).
- `hymnal_mode_override(candidate, position, hymnal_mode) -> bool`: when `hymnal_mode` and `candidate.is_hymn` and `position == songs`, prefer this candidate.

### 7.4 `hard_constraints.py` — §5.1 H1–H8

`validate(candidate: SongsetProposal, config: RunConfig, matrix) -> ValidationFeedback`.

Each gate is a separate function returning `Optional[tuple[code, message]]`; `validate` aggregates them into `ValidationFeedback`. Gates:

- `H1` Phase coverage: exactly one phase-1; at least one phase-3 or 4; last song phase ∈ {4,5}.
- `H2` Opening tempo ≥ 110.
- `H3` Closing tempo ≤ 90 (≤ 80 if `intimate`).
- `H4` Adjacent |ΔBPM| ≤ 20; ≤ 15 unless `crossfade_duration_seconds > 0` or `gap_beats > 4`.
- `H5` Adjacent CFD ≤ 2 OR crossfade_duration>0 OR `key_shift_semitones` brings CFD ≤ 2.
- `H6` No duplicate `song_id`.
- `H7` Adjacent phase `phase[i+1] >= phase[i] - 1`.
- `H8` If song `key_confidence < 0.6`, force its `key_shift_semitones = 0`.

`repair_hints` produce targeted guidance for `llm_refine` (e.g. "H5 violated at positions 2→3: CFD=4 between G and F#. Suggest transposing song at position 3 by +1 or selecting a neighbour in keys D/A/E.").

### 7.5 `fitness.py` — §5.3

Implement `F_theme`, `F_tempo`, `F_harmony`, `F_diversity` exactly as the report, normalising each to [0,1]. The 4-/5-song template vectors come from §5.4 tables (hard-coded constants `TEMPLATE_PHASES_5 = (1,2,3,4,5)`, `TEMPLATE_PHASES_4 = (1,3,4,5)`).

`score(proposal, config, matrix) -> ScoreBreakdown`. Fitness weights: theme 0.40, tempo 0.30, harmony 0.20, diversity 0.10.

### 7.6 `beam.py` — §5.3 constructor algorithm + §5.5 dead-ends

Deterministic beam search:
1. Group `pool` by phase.
2. For each phase-1 song (using fan_out from §5.5), greedily extend with phase-compatible successor; beam width `k=8`.
3. Apply H1–H8 at each extension step; prune early.
4. Score completed sequences; keep top-K (default 3) via heap.
5. §5.5 relaxation: if no valid 5-song survives 1000 iterations, fall back to 4-song; then relax H4 (20→25 BPM), H5 (CFD 2→3) and emit `hard_constraint_warnings`.
6. `fan_out` per song = count of pool songs with CFD ≤ 2 **and** |ΔBPM| ≤ 20. Mark dead-end (place at position N).

Output: `list[SongsetProposal]` with `llm_origin=False`.

### 7.7 `transitions.py` — §3.2

`recommend_transition(from_cand, to_cand) -> TransitionCandidate`: choose technique from the §3.2 table keyed on CFD and relative mode. Populate `crossfade_*`, `gap_beats`, `key_shift_semitones` accordingly. Surface warnings (e.g. `key_confidence < 0.6`).

### 7.8 `theme_anchors.json` (fixture)

```json
{
  "model_version": "text-embedding-3-small",
  "dim": 1536,
  "anchors": {
    "赞美":  [0.0, 0.0, ...],
    "感恩":  [0.0, 0.0, ...],
    "敬拜":  [0.0, 0.0, ...],
    "奉献":  [0.0, 0.0, ...],
    "认罪":  [0.0, 0.0, ...],
    "差遣":  [0.0, 0.0, ...],
    "信心":  [0.0, 0.0, ...],
    "祈祷":  [0.0, 0.0, ...],
    "复兴":  [0.0, 0.0, ...],
    "圣灵":  [0.0, 0.0, ...],
    "十字架": [0.0, 0.0, ...],
    "跟随":  [0.0, 0.0, ...]
  }
}
```

Anchor text seeded per theme via a small phrase list (4–6 phrases each, Chinese + English). Generated by `regen_theme_anchors.py` (one-shot, run by maintainer) using `langchain_openai.OpenAIEmbeddings(model="text-embedding-3-small", api_key=..., base_url=...)` against the existing `SOW_LLM_*` env. Output written via `pathlib.Path.write_text(json.dumps(...))`. Documented run instruction in the new module README.

---

## 8. LangGraph Nodes (`graph/nodes.py`)

All nodes are `def` (sync; matches the synchronous psycopg/`ConnectionProvider` codebase). Each takes `state: ConstructorState` and returns a **partial state dict**. Each node also appends a `trace` event.

| Node | Responsibility |
|---|---|
| `load_catalog` | Instantiate `ReadOnlyClient` / `ConnectionProvider`, run `POOL_QUERY` + line-embedding fetch, populate `pool` (list of `SongCandidate` without inferred fields) and `themes`/`phase` are absent. Closes connection in `finally`. |
| `enrich_pool` | For each `SongCandidate`: run D3 fusion (title + lyrics + song-embedding + max line-embedding vs fixture anchors), `infer_phase`, compute `fan_out` / `is_dead_end` / `is_hymn`. Drops candidates missing both `tempo_bpm` and `musical_key` (unusable) — emit a trace warning. |
| `build_transition_matrix` | Precompute `TransitionCandidate` for every ordered pair in the pool with CFD ≤ 3 (cap matrix size at `min(pool, 200)^2` via `pool_limit`). Store in `transition_matrix` dict keyed by `(from_hash, to_hash)`. |
| `beam_seed_candidates` | Run `beam.search`; populate `beam_candidates` (top-K). Trace candidate count + relaxation flags. |
| `llm_plan` | Bind `SongsetDraft` schema to `ChatOpenAI`; construct a prompt with the pool summary (id, title, phase, BPM, key, themes, fan_out, dead_end flag) and the target template; invoke; **post-validate** every `hash_prefix` against the known pool set — if any is hallucinated, replace with the closest valid candidate by embedding cosine and append a `repair_hint`. Sets `current_draft`. Only runs in agentic mode. |
| `validate_score` | Run H1–H8 (§5.1) on `current_draft`; compute `fitness()` (§5.3); produce `ValidationFeedback`. Writes a `SongsetProposal` into `beam_candidates` (with `llm_origin=True`) so the LLM draft competes with the beam drafts in final ranking. |
| `llm_refine` | Evaluator-optimizer: invoke LLM with `SongsetDraft` schema again, including the `feedback.errors` + `feedback.repair_hints` and the prior draft in the prompt; sets `current_draft`; bumps `iterations`. Only when `iterations < 3`. |
| `llm_judge` | Optional (`--llm-judge`): invoke LLM with a `JudgeRanking` schema (`list[{rank, hash_prefix, reason}]`) over the top-K finalists; records per-proposal judge score into `trace`. Does NOT override deterministic ranking — surfaces as an additional `judge_score` field on `SongsetProposal`. |
| `optional_review` | Only when `interactive_review`: `interrupt({question, top_proposal_summary, alternates})`. Caller resumes with `Command(resume={'action': ..., 'edits': {...}})`. On `approve`: set `approved=True`. On `reject`: set `approved=False`, route to END. On `edit`: apply edits to `current_draft`, route back to `validate_score`. |
| `write_artifacts` | Materialise `final_proposals` (top-K ranked by fitness → composer_diversity → hash_prefix) into the four artifact files. Appends final trace event. |

### 8.1 Graph wiring (`graph/builder.py`)

```python
b = StateGraph(ConstructorState)
b.add_node("load_catalog",          load_catalog)
b.add_node("enrich_pool",            enrich_pool)
b.add_node("build_transition_matrix", build_transition_matrix)
b.add_node("beam_seed_candidates",  beam_seed_candidates)
b.add_node("llm_plan",              llm_plan)
b.add_node("validate_score",        validate_score)
b.add_node("llm_refine",           llm_refine)
b.add_node("llm_judge",            llm_judge)
b.add_node("optional_review",      optional_review)
b.add_node("write_artifacts",      write_artifacts)

b.add_edge(START, "load_catalog")
b.add_edge("load_catalog",            "enrich_pool")
b.add_edge("enrich_pool",            "build_transition_matrix")
b.add_edge("build_transition_matrix","beam_seed_candidates")

# Branch: --no-llm skips llm_plan entirely
b.add_conditional_edges(
    "beam_seed_candidates",
    route_after_beam,                       # "llm_plan" | "finalize"
    {"llm_plan": "llm_plan", "finalize": "finalize_rank"},
)

b.add_edge("llm_plan",     "validate_score")

# Evaluator-optimizer loop (capped at 3 iterations)
b.add_conditional_edges(
    "validate_score",
    route_validation,
    {"Accepted": "finalize_rank",
     "Refine":   "llm_refine",
     "Rejected": END},                       # exhausted iterations
)
b.add_edge("llm_refine", "validate_score")

# finalize_rank is a lightweight virtual node expressed as a conditional edge
# routing to llm_judge (if --llm-judge) or optional_review (if --interactive-review)
# or write_artifacts otherwise.
b.add_node("finalize_rank", finalize_rank_node)   # merges beam + LLM drafts, sorts by tie-break
b.add_conditional_edges("finalize_rank", route_finalize, {
    "judge":      "llm_judge",
    "review":     "optional_review",
    "write":      "write_artifacts",
    "end_no_proposals": END,                   # empty pool case
})
b.add_edge("llm_judge", "optional_review") if interactive else b.add_edge("llm_judge","write_artifacts")
# (edges finalised at compile time based on RunConfig)
b.add_conditional_edges("optional_review", route_review, {
    "approve": "write_artifacts",
    "reject":  END,
    "edit":    "validate_score",
})
b.add_edge("write_artifacts", END)

checkpointer = choose_checkpointer(config)
graph = b.compile(checkpointer=checkpointer)
```

**Routing functions:**
- `route_after_beam(state) -> "llm_plan" | "finalize_rank"`: returns `"finalize_rank"` when `config.no_llm` or when `beam_candidates` already empty.
- `route_validation(state) -> "Accepted"|"Refine"|"Rejected"`: `feedback.passed` → `Accepted`; else if `iterations < 3` → `Refine`; else `Rejected`.
- `route_finalize(state)`: `"judge"` if `config.llm_judge`; `"review"` if `config.interactive_review`; `"write"` if neither; `"end_no_proposals"` if `final_proposals` empty.
- `route_review(state)`: reads `state["edits"]["action"]` (set by `Command(resume=...)`).

### 8.2 `llm.py`

```python
def build_chat_model(config: RunConfig) -> ChatOpenAI:
    if config.no_llm:
        return None
    return ChatOpenAI(
        model=config.llm_model,
        api_key=os.environ["SOW_LLM_API_KEY"],
        base_url=os.environ.get("SOW_LLM_BASE_URL"),
        temperature=0.2,
        max_retries=2,
    )

planner_llm = chat.with_structured_output(SongsetDraft, method="json_schema")
refiner_llm = chat.with_structured_output(SongsetDraft, method="json_schema")
judge_llm   = chat.with_structured_output(JudgeRanking,  method="json_schema")
```

`method="json_schema"` (OpenAI native strict) when `SOW_LLM_BASE_URL` looks like OpenAI; auto-fallback path: try `json_schema`, on `TypeError`/import error fall back to `method="function_calling"`. Documented in `graph/llm.py` docstring.

### 8.3 Checkpointer (`graph/checkpointer.py`)

```python
def choose_checkpointer(config: RunConfig):
    if config.interactive_review or config.resume_thread_id:
        db_path = Path(config.output_dir) / "checkpoint.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False))
    return InMemorySaver()
```

`thread_id` = `config.resume_thread_id or f"songset-{timestamp}-{songs}s-top{top_k}"`. Passed to `graph.invoke(input, {"configurable": {"thread_id": thread_id}})`.

### 8.4 Resume flow for `--interactive-review`

Initial drive:
```python
stream = graph.stream_events(initial_state, {"configurable": {"thread_id": tid}}, version="v3")
_ = stream.output
if stream.interrupted:
    payload = stream.interrupts[0].value
    # CLI prints payload, prompts user, returns dict(action, edits)
    decision = cli.prompt_review(payload)
    graph.stream_events(Command(resume=decision), {"configurable": {"thread_id": tid}}, version="v3")
```

`optional_review` node body:
```python
def optional_review(state):
    summary = render_proposal_summary(state["final_proposals"][0])
    alternates = [render_proposal_summary(p) for p in state["final_proposals"][1:]]
    decision = interrupt({"question": "Approve top proposal?", "top": summary, "alternates": alternates})
    action = decision.get("action", "approve")
    if action == "edit":
        return {"edits": decision, "current_draft": apply_edits(state["current_draft"], decision["edits"])}
    return {"approved": action == "approve", "edits": decision}
```

(Idempotent per LangGraph interrupt rule: only one `interrupt()` call per node invocation; edit side-effect — applying edits to `current_draft` — happens *after* `interrupt()`, so it's safe on replay.)

---

## 9. Artifacts (`artifacts/`)

Output directory: `lab/poc-scripts/output/songset_constructor/<run_id>/` where `run_id = f"{timestamp}-{songs}s-top{top_k}"`.

### 9.1 `proposals.json`

```json
{
  "run_id": "...",
  "config": { ...RunConfig serialisable... },
  "generated_at": "2026-06-30T12:00:00Z",
  "proposals": [
    {
      "rank": 1,
      "items": [
        {"position": 1, "song_id": "...", "title": "...", "recording_hash_prefix": "...",
         "key_shift_semitones": 0, "tempo_ratio": 1.0, "gap_beats": 2.0,
         "crossfade_enabled": false, "crossfade_duration_seconds": 0.0,
         "phase": 1, "themes": ["赞美"], "bpm": 120.0, "key": "G", "mode": "maj"}
      ],
      "score": {"f_theme": 0.92, "f_tempo": 0.88, "f_harmony": 1.0, "f_diversity": 0.8, "total": 0.91},
      "rationale": "...",
      "llm_origin": true,
      "hard_constraint_warnings": [],
      "judge_reason": "..."   // only when --llm-judge
    }
  ]
}
```

### 9.2 `proposal_report.md`

Human-readable, ranked. Per proposal: Chinese title list, phase/theme labels, BPM/key transitions (ΔBPM, CFD, suggested technique), why the agent selected it (`rationale`), score breakdown, warnings. Rendered by `rich` Markdown tables. Designed for a worship leader to paste into a planning doc.

### 9.3 `candidate_pool.csv`

Headers: `song_id,title,title_pinyin,composer,album_name,album_series,recording_hash_prefix,tempo_bpm,musical_key,musical_mode,key_confidence,loudness_db,phase,top_themes,fan_out,is_dead_end,is_hymn`. One row per `SongCandidate` in the run pool.

### 9.4 `graph_trace.jsonl`

One JSON object per line, per node entry/exit + LLM invocations + validation outcomes. Fields: `ts`, `node`, `event` (`enter`/`exit`/`llm_call`/`validation`/`interrupt`/`resume`/`artifact_written`), `iteration`, `data`. Examples:
- `{"node":"beam_seed_candidates","event":"exit","data":{"candidates":3,"relaxed":false}}`
- `{"node":"llm_plan","event":"llm_call","data":{"model":"...","tokens_in":1234,"tokens_out":567,"hallucinated_hashes":0}}`
- `{"node":"validate_score","event":"validation","iteration":1,"data":{"passed":false,"violated":["H5"],"errors":[...]}}`

Emitters live in `artifacts/trace.py`; each node calls `trace.log(state, node, event, data)` which appends to the in-memory `trace` list (reducer-accumulated). `write_artifacts` flushes the list to disk at the end.

### 9.5 No DB mutation guarantee

- DB access only via `ReadOnlyClient` (which wraps `ReadOnlyClient` — the read-side catalog, never the write-side `SongsetClient`).
- Tests assert no writes: run against a fixture-backed `ReadOnlyClient` mock and assert `cursor.execute` was never called with `INSERT/UPDATE/DELETE`.
- A defensive `assert_no_mutation` test uses a wrapper connection that raises on any write verb.

---

## 10. CLI (`cli.py` + entry point)

Entry: `lab/poc-scripts/construct_songset_agent.py` (matches draft spec naming).

```python
import typer
app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

@app.command()
def construct(
    songs: int = typer.Option(5, "--songs", min=4, max=5),
    top_k: int = typer.Option(3, "--top-k", min=1, max=10),
    pool_limit: int = typer.Option(200, "--pool-limit"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir"),
    album_series: list[str] = typer.Option(["PW","DEV"], "--album-series"),
    include_cpw: bool = typer.Option(False, "--include-cpw/--no-include-cpw"),
    intimate: bool = False,
    hymnal_mode: bool = False,
    season: Optional[str] = None,
    interactive_review: bool = False,
    resume_thread_id: Optional[str] = None,
    no_llm: bool = False,
    llm_judge: bool = False,
    llm_model: Optional[str] = None,
):
    """Construct Chinese worship songsets via LangGraph agentic + deterministic pipeline."""
    ...
```

- Validates `songs ∈ {4,5}` (6 explicitly rejected with explanatory error).
- Validates `season` against the 5 valid values.
- Builds `RunConfig`, calls `graph/builder.build_graph(config)`, invokes graph with appropriate `thread_id`.
- Pretty progress via `rich` (live node-enter/exit panel).
- On `--interactive-review`, drives `stream_events` + `Command(resume=...)` flow with interactive prompts.

Script runnable as `uv run --project lab/poc-scripts --extra songset_constructor python lab/poc-scripts/construct_songset_agent.py construct --help`.

---

## 11. Test Plan (`lab/poc-scripts/tests/`)

All tests live under `testpaths=["tests"]` (pyproject `:107`). Follow the class-per-feature convention seen in `test_eval_lrc.py`. Mock the LLM with a fake structured-output stub; testcontainers NOT required.

### Unit tests (pure, no DB, no LLM)

| File | Coverage |
|---|---|
| `test_songset_constructor_harmony.py` | `pitch_class`, `relative_major_pc`, `fifth_distance_on_circle`, `cfd` (incl. relative-minor normalisation, enharmonic inputs, tritone), `key_compatibility_score`, `normalize_key`, `suggest_key_shift` (minimises shift; returns 0 when CFD ≤ 2). |
| `test_songset_constructor_themes.py` | `classify_title_themes` (positive + negative cases), `classify_lyrics_themes` (2-line window sampling), embedding-cosine against a tiny in-test fixture, D3 weighted fusion produces expected argmax, `infer_phase` priority + tempo fallback + seasonal override. CPW exclusion in pool filter (separate fixture). |
| `test_songset_constructor_fitness.py` | All four `F_*` components against synthetic `SongCandidate` sequences (5-song ascending arc → fitness near 1.0; random order → lower; edge: empty pool). |
| `test_songset_constructor_hard_constraints.py` | One test method per H1–H8 with passing + failing fixtures; §5.5 relaxation toggling (H4 20→25, H5 2→3) emits warnings; H8 forces `key_shift_semitones=0` when `key_confidence<0.6`. |
| `test_songset_constructor_beam.py` | Beam search returns N valid candidates, prunes invalid early, falls back to 4-song when 5-song unsatisfiable, marks dead-ends, respects fan_out. Deterministic (same pool → same output). |

### Graph tests (`test_songset_constructor_graph.py`)

- `FakeStructuredLLM`: a callable returning a `SongsetDraft`; configurable to first emit an *invalid* draft (hallucinated `hash_prefix`, H5 violation), then a valid one. Drives the evaluator-optimizer loop: assert `iterations` incremented, `feedback.passed` flipped True, draft actually repaired. Assert `route_validation` routing.
- `FakeLLMJudge`: returns a re-ranking; assert `judge_reason` annotated on proposals but deterministic rank ordering unchanged.
- `interrupt()` + `Command(resume=...)` test: stub the review node with `interrupt()`; drive `stream_events`; assert `stream.interrupted` True; resume with approve/reject/edit and assert correct edge taken; assert edits re-enter `validate_score`.
- `--no-llm` graph variant: assert `graph.nodes` excludes `llm_plan`/`llm_refine`/`llm_judge` paths; final proposals come purely from `beam_candidates`.

### CLI smoke test (`test_songset_constructor_cli.py`)

- Use `typer.testing.CliRunner`, monkeypatch `ConnectionProvider`/`ReadOnlyClient` with a fixture returning ~12 synthetic `Song`/`Recording` rows (mix of PW/DEV phases 1-5, varied keys/BPMs).
- Fixture `theme_anchors.json` copied to a tmp path.
- Assert: exit code 0; four artifacts written under tmp output dir; no DB write SQL executed (assert on mock cursor calls); proposals respect `--songs` count; ranking reproducible across two runs with same seed pool.
- One smoke test variant exercising `--no-llm` (no env vars set) — confirms graceful offline run.
- One smoke test exercising `--interactive-review` with a stubbed prompt that approves on first interrupt.

### Shared fixtures (`conftest_songset.py`)

- `synthetic_pool()` → `list[SongCandidate]` (15 entries) covering all 5 phases, a dead-end, a low-confidence-key song, a hymnal (`album_series="HYMN"`).
- `tiny_theme_anchors()` → in-memory `dict[str, np.ndarray]` of 12 anchors (small random vectors normalised; used only to exercise cosine code path, not to validate semantic correctness).
- `fake_llm_invalid_then_valid()` → `FakeStructuredLLM` instance.
- `mock_read_only_client()` → `MagicMock` whose `cursor.execute` raises if SQL starts with INSERT/UPDATE/DELETE.

Run: `uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v`.

---

## 12. Implementation Phases (suggested order)

1. **pyproject extra + module skeleton**: add `songset_constructor` extra; create empty package tree; add `construct_songset_agent.py` entry stub with `--help` only.
2. **Rules layer (pure, fully testable)**: `harmony.py`, `themes.py`, `phases.py`, `fitness.py`, `hard_constraints.py`, `transitions.py`, `beam.py`, `embeddings.py`. Land all unit tests in this phase. No DB, no LLM.
3. **Fixture generation**: write `regen_theme_anchors.py`; run it once; commit `theme_anchors.json` (or commit placeholder zeros + regenerate in a follow-up — confirm with maintainer).
4. **DB layer**: `db.py` + `parse_pgvector_text`. Smoke-test against real Neon (read-only) using `--no-llm` mode, verify pool fetch + enrichment on real catalog.
5. **LangGraph scaffolding**: `graph/state.py`, `graph/llm.py`, `graph/checkpointer.py`, `graph/builder.py`, node stubs. Compile graph; assert `--no-llm` end-to-end writes artifacts.
6. **LLM nodes**: implement `llm_plan` / `llm_refine` with `with_structured_output`; fake-LLM graph tests; real end-to-end agentic run against Neon.
7. **Interactive review + judge**: `optional_review` node + `Command(resume=...)` CLI flow; `llm_judge` node.
8. **Artifacts polish**: `proposal_report.md` rendering, `graph_trace.jsonl` field coverage, CSV headers.
9. **CLI smoke tests + `--no-llm` regression**.
10. **Docs**: brief `poc/songset_constructor/README.md` covering env vars, run commands, output contract, and `regen_theme_anchors.py` instructions. (Only if requested — per repo convention of not auto-creating docs.)

---

## 13. Assumptions & Boundaries

- **Read-only POC**: never inserts into `songsets` / `songset_items`. Output artifacts mirror the `SongsetItem` field shape so a future importer could materialise them, but no such importer is in scope.
- **LLM agency is bounded**: the LLM proposes/reorders among pre-validated beam candidates and is constrained to known `hash_prefix` values via structured output + post-invoke validation. It cannot invent songs.
- **Determinism where it matters**: `fitness()`, H1–H8, beam search, and tie-break ordering are fully deterministic. The LLM is the only nondeterministic component (mitigated by `temperature=0.2`, `max_retries=2`, max 3 refinements, and the option `--no-llm`/`--llm-judge` flags).
- **No schema migration**: no new DB columns. `themes` are inferred in-memory per run. The research report's §5.6 `themes text[]` proposal is explicitly out of scope.
- **Pool size bound**: `pool_limit` (default 200) caps both the SQL `LIMIT` and the transition-matrix size. Warns if pool < 3 songs per phase (§5.5 limited-pool fallback).
- **Embedding coverage**: songs without a `song_embedding` row fall back to title + lyrics signals only (weights renormalised). Tracked in `trace`.
- **No new ML deps**: stays within the Admin-CLI lightweight boundary (no torch). Embeddings are pre-computed in DB; the only external API call is the chat completion (and the optional one-shot `regen_theme_anchors.py`).
