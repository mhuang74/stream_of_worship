# PROMPT: Generate Implementation Plan — Chorus-Based Song Component Metadata

## Context

You are generating a detailed Implementation Plan for enhancing three components of the Stream of Worship (SOW) project to support smooth chorus-based song transitions. The plan will be handed to a developer (human or agent) who will implement it. You are NOT implementing code — you are producing the plan.

## Goal

Enhance the **Analysis Service**, **PostgreSQL Database Schema**, and **Admin CLI** to generate, persist, and surface additional song metadata — specifically per-component structural data (chorus entry/exit, verse loop target) with per-component audio features — so that transition logic can use chorus sections as jumping points between songs.

## Proposed Schema (Normalized Long-Format)

The user has specified a normalized schema that avoids column-per-component-type sprawl. Add a new `song_components` table:

**`song_components` table** (one row per detected/tagged component instance):

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PRIMARY KEY | Auto-increment PK |
| `song_id` | TEXT NOT NULL REFERENCES songs(id) | FK to songs |
| `content_hash` | TEXT NOT NULL REFERENCES recordings(content_hash) | FK to recordings (the audio that produced this component data) |
| `component_type` | TEXT NOT NULL CHECK IN ('chorus','verse','prechorus','bridge','intro','outro','instrumental') | Component category |
| `occurrence_index` | INTEGER NOT NULL DEFAULT 1 | 1st chorus, 2nd chorus, etc. |
| `role` | TEXT NOT NULL DEFAULT 'none' CHECK IN ('entry','exit','loop_target','none') | Transition role |
| `start_time` | REAL | Start time in seconds |
| `end_time` | REAL | End time in seconds |
| `bpm` | REAL | Per-component tempo (may differ from global) |
| `key` | TEXT | Per-component detected key (e.g., "G") |
| `groove_density` | REAL | Onset/note density metric for this segment |
| `backbeat_strength` | REAL | Backbeat (beats 2&4) accent strength |
| `energy_level` | REAL | RMS/energy for this segment |
| `confidence` | REAL | Detection confidence (0.0–1.0) |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | |
| `updated_at` | TIMESTAMPTZ DEFAULT NOW() | |

Unique constraint: `(song_id, component_type, occurrence_index)` to prevent duplicate component instances.

**For the current milestone**, each analyzed song should produce at minimum three rows:

1. `component_type='chorus', occurrence_index=1, role='entry'` — first chorus (entry point for transition INTO this song)
2. `component_type='chorus', occurrence_index=2, role='exit'` — last chorus (exit point for transition OUT of this song)
3. `component_type='verse', occurrence_index=1, role='loop_target'` — verse immediately preceding the first chorus (only `start_time` is strictly needed; `end_time` can be the chorus `start_time`)

Future milestones can add bridge rows, additional verse rows, etc. — **rows, not columns**, so no schema migration is needed.

## Current Architecture (Reference — DO NOT Redesign All Of It, Only Extend It)

### 1. Analysis Service (`ops/analysis-service/`)

- **FastAPI** service with SQLite-backed `JobStore` (`storage/db.py`) for job persistence.
- **Job types** defined in `models.py` via `JobType` enum: `analyze`, `fast_analyze`, `lrc`, `stem_separation`, `embedding`, `forced_alignment`. Each job type has a Pydantic request model and returns a `JobResult` (or `EmbeddingJobResult`).
- **Routes** in `routes/jobs.py` — POST endpoints create jobs in the queue; GET endpoints poll status and return `JobResponse` with `JobResult`.
- **Analyzer** (`workers/analyzer.py`):
  - `analyze_audio()` — full analysis using allin1 (tempo, beats, downbeats, **sections** with labels, embeddings) + librosa (key). Returns a flat dict with `sections` as a list of `{"label": str, "start": float, "end": float}`.
  - `analyze_audio_fast()` — librosa-only fast analysis (no allin1, no sections, no beats). Returns only duration/bpm/key/loudness.
  - `detect_key()` — Krumhansl-Schuckler key detection (`detect_key_fulltrack`, `detect_key_segment_vote`). Key detection can accept `segments` as an optional parameter for segment-vote aggregation.
  - `compute_loudness()` — simple RMS-based loudness.
  - `KeyDetectionResult` dataclass with `to_analysis_fields()` that flattens into the analysis result dict.
- **JobResult** (Pydantic `models.py:168`) currently has flat fields: `duration_seconds`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `key_algorithm_version`, `key_score_margin`, `key_window_agreement`, `key_candidates`, `key_detected_at`, `loudness_db`, `beats`, `downbeats`, `sections`, `embeddings_shape`, `stems_url`, and LRC/stem fields.
- **Config** (`config.py`) — settings like `KEY_ALGORITHM_VERSION`, `BPM_ALGORITHM_VERSION`.
- **Cache** (`storage/cache.py`) — `CacheManager` with `get_analysis_result` / `save_analysis_result` / `get_fast_analyze_result` / `save_fast_analyze_result`.

Key insight: `allin1.analyze()` already returns **sections with labels** (verse, chorus, bridge, etc.) and **beats/downbeats**. The existing `sections` output in the analysis result is a raw list of `{"label", "start", "end"}` dicts. The enhancement needs to **parse those sections**, identify chorus occurrences, identify the verse before the first chorus, and compute per-component audio features (groove density, backbeat strength, energy level, per-component key/BPM).

### 2. Database Schema (`ops/admin-cli/src/stream_of_worship/db/`)

- **`admin/db/schema.py`** — DDL constants: `CREATE_SONGS_TABLE`, `CREATE_RECORDINGS_TABLE`, `CREATE_INDEXES`, `CREATE_SONG_EMBEDDING_TABLE`, `CREATE_SONG_LINE_EMBEDDING_TABLE`, `CREATE_THEME_ANCHORS_TABLE`, column select lists (`SONG_COLUMNS_SELECT`, `RECORDING_COLUMNS_SELECT`), column counts (`SONG_COLUMN_COUNT=24`, `RECORDING_COLUMN_COUNT=34`), JOIN column lists.
- **`postgres_schema.py`** — Unified ordered DDL list `ALL_SCHEMA_STATEMENTS` that combines catalog, auth, app, and per-user schema statements. Uses `CREATE ... IF NOT EXISTS` so re-running is safe (idempotent). Re-exports constants from `admin/db/schema.py`.
- **Current tables**: `songs` (24 columns), `recordings` (34 columns, includes `sections` TEXT column storing JSON), `song_embedding`, `song_line_embedding`, `theme_anchors`, plus auth/app tables.
- **`admin/db/models.py`** — `Song` and `Recording` dataclasses with `from_row()` / `to_dict()`. `Recording.from_row()` has version-tolerant deserialization based on row length. `Recording.sections` is `Optional[str]` (JSON string of section list).

### 3. Admin CLI (`ops/admin-cli/src/stream_of_worship/admin/`)

- **`admin/db/client.py`** — `DatabaseClient` class with psycopg operations. Key method: `update_recording_analysis()` (line ~953) writes flat columns to `recordings`. Has separate branches for `analysis_status='partial'` (fast-tier, preserves full-only columns) vs `'completed'`. Uses `transaction()` context manager.
- **`admin/commands/audio.py`** — Typer CLI with commands: `download`, `delete`, `list`, `show`, `set-visibility`, plus analysis/LRC submission helpers (`_submit_analysis_job`, `_submit_lrc_job`). The `download` command can chain `--analyze` / `--lrc` / `--all`.
- **`admin/services/analysis.py`** — `AnalysisClient` HTTP client with `submit_analysis()`, `submit_lrc()`, `wait_for_completion()`. `AnalysisResult` dataclass mirrors `JobResult`. `JobInfo` for polling.
- The `_submit_analysis_job()` helper submits to the analysis service, then the caller (elsewhere in the codebase) polls and calls `db_client.update_recording_analysis()` to persist the flat fields.

## What the Implementation Plan Must Cover

### A. Analysis Service Enhancements

1. **New component extraction logic** — a new module (e.g., `workers/components.py`) that:
   - Takes allin1 sections (list of `{label, start, end}`), beats, downbeats, and the audio waveform
   - Identifies chorus sections (by label — allin1 labels include "chorus", "verse", "bridge", "intro", "outro", "instrumental")
   - Picks `occurrence_index=1` (first chorus) as `role="entry"`
   - Picks the last chorus as `occurrence_index=N, role="exit"` (or if only one chorus, `occurrence_index=1` serves both roles — the plan should address this edge case)
   - Identifies the verse immediately preceding the first chorus as `role="loop_target"` with `occurrence_index=1`

2. **Per-component audio feature computation** — for each identified component:
   - `start_time` / `end_time`: from section boundaries (may need snap-to-beat alignment)
   - `bpm`: re-estimate within the segment using beat intervals, or use global rhythm
   - `key`: run Krumhansl-Schmuckler within the component's audio slice (reuse `detect_key_segment_vote` with the component as the segment, or extract the slice and call key detection)
   - `groove_density`: onset envelope density (onsets/second or mean onset strength within the segment)
   - `backbeat_strength`: measure accentuation of beats 2 and 4 relative to 1 and 3 using RMS/energy envelope aligned to beats
   - `energy_level`: mean RMS energy within the segment
   - `confidence`: heuristic based on section label clarity and feature extraction success

3. **Integration with existing analysis pipeline** — the plan must specify:
   - Whether to add component extraction to the existing `analyze_audio()` full path, or create a new job type (e.g., `JobType.COMPONENT_ANALYSIS`)
   - New/extended `JobResult` fields to carry component data (or a new result type)
   - New request model if it's a separate job type
   - Cache implications
   - How the result is returned in `JobResponse` / `job_to_response()` in `routes/jobs.py`

4. **Backfill strategy** — how to re-run component extraction on songs that already have `analyze` results (the `sections` and audio data are already cached, so ideally component extraction can run from cached data without re-downloading audio or re-running allin1)

### B. Database Schema Changes

1. **New `song_components` table DDL** — `CREATE_SONG_COMPONENTS_TABLE` constant in `admin/db/schema.py`, including:
   - The columns specified in the table above
   - Unique constraint on `(song_id, component_type, occurrence_index)`
   - Indexes: on `song_id`, on `content_hash`, on `(component_type, role)`
   - Update trigger for `updated_at` (matching existing pattern for songs/recordings)

2. **Schema registration** — add `CREATE_SONG_COMPONENTS_TABLE` and its indexes/triggers to:
   - `ALL_SCHEMA_STATEMENTS` in `admin/db/schema.py`
   - `ALL_SCHEMA_STATEMENTS` in `postgres_schema.py` (the unified ordered DDL)

3. **Migration/idempotency** — since all DDL uses `IF NOT EXISTS`, existing databases can run `sow-admin db init` to add the new table without data loss.

4. **New dataclass models** — `SongComponent` dataclass in `admin/db/models.py` with `from_row()` / `to_dict()`, following the existing pattern of `Song` / `Recording`.

5. **DB client methods** in `admin/db/client.py`:
   - `upsert_song_components(content_hash, song_id, components: list[SongComponent])` — bulk upsert (delete old rows for this recording + insert new, or use `ON CONFLICT` upsert)
   - `get_song_components(song_id) -> list[SongComponent]`
   - `get_song_components_by_role(song_id, role) -> list[SongComponent]`
   - Integrate with existing `update_recording_analysis()` or create a separate persistence path

6. **Column count constants** and select/join lists — add `SONG_COMPONENT_COLUMNS_SELECT`, `SONG_COMPONENT_COLUMN_COUNT`, etc. if needed for JOIN queries.

### C. Admin CLI Enhancements

1. **New CLI command(s)** under `audio` (e.g., `sow-admin audio components <song_id>` or extending the existing `show` command) that:
   - Trigger component analysis (submit job to analysis service if not already done)
   - Display component metadata in a Rich table (component_type, occurrence, role, start–end time, BPM, key, groove, backbeat, energy, confidence)
   - Support `--all` alongside analysis and LRC in `download` command

2. **Analysis submission flow** — extend `_submit_analysis_job()` to also trigger component extraction, or create `_submit_component_analysis_job()`.

3. **Result persistence** — when analysis job completes, persist component data to `song_components` table (in `update_recording_analysis()` or a new method).

4. **`show` command enhancement** — display component metadata alongside existing analysis results.

### D. Testing Strategy

1. **Analysis service tests** — unit tests for component identification (mock allin1 sections), per-component feature extraction (fixture audio), edge cases (no chorus detected, only one chorus, no verse before chorus).
2. **Schema tests** — verify `song_components` DDL is idempotent and that `ALL_SCHEMA_STATEMENTS` includes it in the right position.
3. **DB client tests** — upsert/get round-trip, conflict handling, FK integrity.
4. **CLI tests** — command output formatting, error cases.
5. **Integration** — end-to-end: submit analysis job → poll → component data persisted to DB → queryable via CLI.

### E. Cross-Cutting Concerns

1. **Backward compatibility** — existing recordings with analysis results but no component data should not break. Component extraction should be optional/deferred.
2. **Performance** — per-component key/BPM computation adds CPU cost. The plan should estimate whether this adds seconds or minutes per song.
3. **allin1 label reliability** — allin1 section labels are ML-predicted and may be imperfect (e.g., a chorus labeled as "verse"). The plan should specify the fallback strategy when labels are ambiguous or missing.

## Deliverable Format

Produce a structured Implementation Plan with:

1. **Phase 0: Schema & Models** — DDL, dataclasses, column constants, indexes/triggers, DB client methods
2. **Phase 1: Analysis Service — Component Extraction** — new module, labeling logic, per-component feature computation
3. **Phase 2: Analysis Service — Job Integration** — job type, request/result models, route changes, cache strategy
4. **Phase 3: Admin CLI — Persistence & Display** — result persistence, new/extended CLI commands
5. **Phase 4: Backfill & Migration** — re-running component extraction on existing analyzed songs
6. **Phase 5: Testing** — test files, test cases, edge cases
7. **Appendix: File-by-file Change List** — every file that will be created or modified, with a one-line description of the change

For each phase, specify:
- **Files to create/modify** (full paths from repo root)
- **Functions/classes to add or change** with signatures
- **Dependencies** on other phases
- **Estimated complexity** (S/M/L)
- **Verification steps** (commands to run, expected output)

## Constraints

- Python 3.11, `uv` package manager, `pathlib.Path` for all paths
- No new heavy ML dependencies beyond what's already in the analysis service (allin1 + librosa are available)
- Admin CLI must never import heavy ML libraries — it communicates with the analysis service via HTTP
- Follow existing code conventions: Black (line 100), Ruff, `from_row()` / `to_dict()` patterns, `ALL_SCHEMA_STATEMENTS` idempotent DDL
- The `song_components` table must use the normalized long-format schema described above (one row per component instance, not one column set per component type)

## Key Files for Reference

When implementing, the developer should read these files first:

- `ops/analysis-service/src/sow_analysis/workers/analyzer.py` — existing analysis pipeline (sections, beats, key detection, loudness)
- `ops/analysis-service/src/sow_analysis/models.py` — JobType, JobResult, request/response Pydantic models
- `ops/analysis-service/src/sow_analysis/routes/jobs.py` — endpoint definitions, `job_to_response()`
- `ops/analysis-service/src/sow_analysis/storage/db.py` — SQLite JobStore, migration patterns
- `ops/analysis-service/src/sow_analysis/config.py` — settings
- `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` — DDL constants, column lists, column counts
- `ops/admin-cli/src/stream_of_worship/admin/db/models.py` — Song/Recording dataclasses
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — DatabaseClient, `update_recording_analysis()`
- `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` — unified `ALL_SCHEMA_STATEMENTS`
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — CLI commands, analysis/LRC job submission
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — AnalysisClient HTTP client

---

This prompt is designed to be given to an implementation-agent that will produce a clear, comprehensive, file-level Implementation Plan document.
