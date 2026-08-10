# Chorus Component Metadata Pipeline v5 — Implementation Summary

**Date:** 2026-08-10  
**Branch:** `sow_chorus_transition_analysis_pipeline`  
**PR:** [#139](https://github.com/mhuang74/stream_of_worship/pull/139)  
**Spec:** `specs/chorus-component-metadata-impl-plan-v5.md`  
**Commit:** `4aa42d1e`

---

## Overview

Implements the v5 chorus component metadata pipeline, adding rich per-component metadata (theme, vocal posture, per-field confidence, energy-aware roles, downbeat snapping) to the existing chorus/verse component extraction system. All v5 options are opt-in; the v3 path remains unchanged when flags are not specified.

---

## Phases Completed

### Phase 0: Schema & Models

**Files modified:**
- `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`
- `ops/admin-cli/src/stream_of_worship/admin/db/models.py`
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py`
- `ops/analysis-service/src/sow_analysis/storage/cache.py`
- `ops/analysis-service/src/sow_analysis/models.py`

**Changes:**
- 11 new columns on `song_components` table via `ALTER TABLE IF NOT EXISTS` (idempotent):
  - Per-field confidence: `bpm_confidence`, `key_confidence`, `groove_confidence`, `backbeat_confidence`, `energy_confidence`
  - LLM theme/posture: `theme`, `vocal_posture`, `theme_confidence`, `vocal_posture_confidence`
  - Reasoning: `theme_reasoning`, `posture_reasoning`
- CHECK constraints on `theme` (12 Chinese categories) and `vocal_posture` (3 categories)
- `SONG_COMPONENT_COLUMNS_SELECT` updated to 27 columns
- `SONG_COMPONENT_COLUMN_COUNT = 27`
- `SongComponent` dataclass: 11 new fields, `from_row()` (indices 0–26), `to_dict()`
- `upsert_song_components` INSERT updated to 24 columns + 24 placeholders
- `COMPONENT_SCHEMA_VERSION` bumped to 2 (invalidates stale v1 cache)
- `ComponentResult` model: 11 new fields
- `ComponentAnalysisOptions`: 4 new options (`snap_to_downbeat`, `energy_aware_roles`, `classify_theme`, `classify_vocal_posture`)

**12 Chinese theme categories** (from `songset_constructor/rules/themes.py`):
```
讚美, 感恩, 敬拜, 奉獻, 認罪, 差遣, 信心, 祈禱, 復興, 聖靈, 十字架, 跟隨
```

**3 vocal posture categories:**
```
To God, About God, To Congregation
```

---

### Phase 1: Enhanced Component Extraction

**File modified:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**New functions:**
- `_snap_to_downbeat()`: Snaps component boundaries to nearest downbeat timestamp
- `_detect_phrases_via_onset()`: Onset-based phrase detection for lyrics-only path
- `_snap_to_edit_point()`: Tiered snapping priority (downbeat > phrase > beat)
- `_assign_roles_by_energy()`: Energy-based entry/exit role assignment using drums stem (0.4×rms + 0.3×drums_onset + 0.3×backbeat), deduplicates by time range

**Enhanced functions:**
- `compute_component_features()`: Added `stems_dir` param, per-field confidence scoring, composite confidence as mean of per-field scores
- `extract_components()`: Added `use_stems`, `snap_to_downbeat`, `energy_aware_roles` params
- `identify_from_allin1_sections()`: Added `snap_to_downbeat` + `downbeats` params
- `identify_from_lyrics_repetition()`: Added `snap_to_downbeat` param
- `_serialize_components()` / `_deserialize_components()`: Updated with all 11 new fields

---

### Phase 2: madmom Downbeat Detection

**Files modified:**
- `ops/analysis-service/src/sow_analysis/workers/components.py`
- `ops/analysis-service/src/sow_analysis/workers/queue.py`

**New function:** `_detect_downbeats_madmom(audio_path: Path) -> Optional[list[float]]`

Uses madmom's two-stage pipeline:
1. `RNNDownBeatProcessor()` — takes file path, returns activations at 100 fps
2. `DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)` — takes activations, returns `[[time, beat_in_bar], ...]`
3. Downbeats = rows where `beat_in_bar == 1`

**Queue integration:** When `snap_to_downbeat=True` and downbeats not provided in the request, runs madmom detection before `extract_components()`. Falls back to beat-only snapping if madmom fails.

---

### Phase 3: LLM Theme & Vocal Posture Classification

**New file:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

**`ThemeClassifier` class:**
- Classifies song components using LLM for theme (12 Chinese categories) and vocal posture (3 categories)
- Reuses shared LLM semaphore from `llm_rate_limit.py` for consistent throttling across all LLM consumers
- Parallel classification via `asyncio.gather` (one LLM call per component)

**Chinese pronoun heuristic pre-pass** (`_classify_posture_heuristic()`):
- Rule 1: Religious pronoun (祢/祂) present → "To God" (strong signal)
- Rule 2: Imperative/exhortation markers (讓我們, 當, 彼此) → "To Congregation"
- Rule 3: Casual 你 OR 他 present, no 祢/祂 → "About God" (conservative)
- Rule 4: No pronouns/markers → None (let LLM decide)

**Per-component lyrics extraction** (`_extract_lyrics_for_component()`):
- Parses LRC content using `parse_lrc()` from `lrc_parser.py`
- Filters lines whose timestamps fall within `[start_time, end_time]`

**Confidence adjustment scheme:**
- Heuristic agrees with LLM → +0.05 (capped at 0.95)
- Heuristic="To God" AND LLM="To Congregation" → −0.2; flag if < 0.6
- Heuristic="About God" AND LLM="To God" → −0.1 (no auto-flag)
- All other disagreements → −0.2 (flag if < 0.6)
- Heuristic is None → no posture adjustment

**Decisiveness penalty** (per-field, NOT cross-applied):
- `theme_reasoning` mentions "or", "either", "possibly", "maybe" → −0.1 on `theme_confidence` only
- `posture_reasoning` mentions same words → −0.1 on `vocal_posture_confidence` only

**Retry:** On JSON parse failure, retries LLM call once and re-runs heuristic cross-check.

---

### Phase 4: Job Integration & Cache

**File modified:** `ops/analysis-service/src/sow_analysis/workers/queue.py`

**Changes to `_process_component_analysis_job()`:**
1. Added import of `_detect_downbeats_madmom` from components module
2. madmom downbeat detection when `snap_to_downbeat=True` and downbeats missing
3. LLM classification via `ThemeClassifier.classify_components()` when `classify_theme` or `classify_vocal_posture` is True
4. Full `ComponentInstance` → `ComponentResult` conversion (all 22 mapped fields including 11 new v5 fields)

---

### Phase 5: Admin CLI Persistence & Display

**Files modified:**
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

**`_parse_component_results()`:** Maps 11 new fields from ComponentResult dicts to SongComponent objects.

**`_render_components_table()`:** Added "Theme" and "Posture" columns to the Rich table output.

**`components_recording` CLI command:** 5 new flags:
- `--snap-to-downbeat`: Snap component boundaries to downbeats (madmom if needed)
- `--energy-roles`: Use energy-based entry/exit role assignment
- `--use-stems`: Use Demucs stems for feature extraction
- `--classify-theme`: LLM theme classification (12 Chinese themes)
- `--classify-posture`: LLM vocal posture classification

**`submit_component_analysis()`:** Accepts and passes v5 options (`snap_to_downbeat`, `energy_aware_roles`, `use_stems`, `classify_theme`, `classify_vocal_posture`) in the job payload `options` dict.

---

## Testing Results

| Test Suite | Tests | Result |
|---|---|---|
| `test_components.py` (analysis-service) | 34 | All pass |
| `test_cache.py` (analysis-service) | 32 | All pass |
| `test_llm_rate_limit.py` (analysis-service) | 34 | All pass |
| `test_song_components.py` (admin-CLI) | 9 | All pass |
| `test_audio_commands.py` (admin-CLI) | 20 | All pass |
| `test_analysis_client.py` (admin-CLI) | 18 | All pass |
| `test_db/` (admin-CLI) | 28 | All pass |
| **Total** | **175** | **All pass** |

---

## Files Changed

| File | Change |
|---|---|
| `ops/admin-cli/.../db/schema.py` | ALTER TABLE for 11 new columns, CHECK constraints, SELECT column list, COLUMN_COUNT=27 |
| `ops/admin-cli/.../db/models.py` | 11 new fields on SongComponent, update from_row/to_dict |
| `ops/admin-cli/.../db/client.py` | Update upsert INSERT (24 cols, 24 placeholders) |
| `ops/admin-cli/.../commands/audio.py` | Update _parse_component_results, _render_components_table, CLI flags |
| `ops/admin-cli/.../services/analysis.py` | Update submit_component_analysis with v5 options |
| `ops/admin-cli/tests/admin/test_song_components.py` | Update test data for 27-column schema |
| `ops/analysis-service/.../storage/cache.py` | Bump COMPONENT_SCHEMA_VERSION to 2 |
| `ops/analysis-service/.../models.py` | 11 new fields on ComponentResult, 4 new options on ComponentAnalysisOptions |
| `ops/analysis-service/.../workers/components.py` | Energy-aware roles, madmom downbeat detection, stem features, per-field confidence, serialize/deserialize |
| `ops/analysis-service/.../workers/classifier.py` | **NEW** — LLM classifier with 12 Chinese themes, 3 postures, heuristic cross-check, shared rate-limiting |
| `ops/analysis-service/.../workers/queue.py` | Dispatch v5 options, madmom detection, LLM classification, ComponentInstance→ComponentResult conversion |

**Total:** 11 files, +1324 insertions, −67 deletions

---

## Migration Notes

- Schema migration is idempotent (`ALTER TABLE IF NOT EXISTS`); re-running `sow-admin db init` is safe
- `COMPONENT_SCHEMA_VERSION = 2` invalidates stale v1 cache entries automatically
- v5 options are opt-in; existing v3 component analysis continues to work unchanged
- No breaking changes to existing API endpoints or CLI commands

---

## Usage Examples

**Single song with all v5 options:**
```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components song_0001 \
    --classify-theme --classify-posture --snap-to-downbeat --energy-roles --use-stems
```

**Batch backfill via stdin:**
```bash
uv run --project ops/admin-cli --extra admin sow-admin audio list --analysis completed --format ids \
  | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin \
    --use-stems --snap-to-downbeat --energy-roles --classify-theme --classify-posture
```

**View results:**
```bash
uv run --project ops/admin-cli --extra admin sow-admin audio show song_0001
```
