# Spec: Auto-Detect Language for Forced Alignment (Revised)

**Date:** 2026-06-16
**Branch:** `trigger_alignment_via_admin`
**Status:** Draft
**Revises:** `specs/auto-detect-forced-alignment-language.md`

## Problem

The `sow-admin audio align-lrc` command defaults `--lang` to `zh` (Chinese). This means English songs get incorrect alignment unless the user manually specifies `--lang en`. Since the app handles both Chinese and English worship songs, the default should be auto-detection.

## Context

- `Qwen3ForcedAligner.align()` **requires** an explicit `language` parameter — it does NOT support auto-detection
- Auto-detection (`language=None`) is only available on `Qwen3ASRModel.transcribe()` (the ASR model, not the forced aligner)
- However, we already have the lyrics text in the job request, so we can detect language from that deterministically
- **The codebase already has robust language detection** in `services/analysis/src/sow_analysis/workers/lrc.py`:
  - `_contains_cjk(text)` — comprehensive CJK regex (Extensions A–F)
  - `_contains_latin(text)` — Latin script detection
  - `resolve_lrc_language(language, song_title, lyrics_text)` — resolves `"auto"` → `"zh"` or `"en"` using title + lyrics heuristics
  - `warn_if_lrc_language_script_mismatch(language, lyrics_text)` — warns on script/language mismatch

## Design Decision

**Language detection happens in the Analysis Service** (not the Admin CLI), because:

1. All callers benefit — API users, future web UI, not just CLI
2. Service already has the lyrics text via `ForcedAlignmentJobRequest.lyrics_text`
3. Single source of truth — reuse the existing `resolve_lrc_language()` heuristic instead of creating a duplicate

**Key revision from v1:** Instead of adding a new `detect_language()` function in `forced_alignment.py`, we reuse the existing `resolve_lrc_language()` from `lrc.py`. This avoids duplication, uses more comprehensive CJK detection (covers Extensions B–F), and leverages the song title as a detection signal.

## Changes

### 1. `ForcedAlignmentOptions` — Add `"auto"` language value

**File:** `services/analysis/src/sow_analysis/models.py`

- Change `language: Literal["zh", "en"] = "zh"` → `language: Literal["auto", "zh", "en"] = "auto"`

### 2. `_process_forced_alignment_job` — Resolve `"auto"` to concrete language

**File:** `services/analysis/src/sow_analysis/workers/queue.py`

- Import `resolve_lrc_language` from `.lrc` (already optionally imported in the try/except block)
- When `request.options.language == "auto"`, call:
  ```python
  resolution = resolve_lrc_language("auto", request.song_title, request.lyrics_text)
  detected_lang = resolution.resolved  # "zh" or "en"
  logger.info("Auto-detected language: %s (reason: %s)", detected_lang, resolution.reason)
  ```
- Map `detected_lang` to model-native values: `"zh"` → `"Chinese"`, `"en"` → `"English"`
- When `"zh"` or `"en"` explicitly, use existing `language_map` as before
- Optionally call `warn_if_lrc_language_script_mismatch()` after resolution for diagnostics

### 3. CLI `align-lrc` — Change default and validation

**File:** `src/stream_of_worship/admin/commands/audio.py`

- Change `--lang` default from `"zh"` to `"auto"`
- Update help text: `"Language: auto, zh, en"`
- Update validation: `if language not in {"auto", "zh", "en"}`
- No detection logic in CLI — just passes the value through

### 4. `AnalysisClient.submit_forced_alignment` — Update type

**File:** `src/stream_of_worship/admin/services/analysis.py`

- Update `language` parameter type hint and docstring to accept `"auto"`, `"zh"`, or `"en"`
- Change default from `"zh"` to `"auto"`

### 5. Tests

**File:** `services/analysis/tests/test_forced_alignment.py`

- Update `ForcedAlignmentOptions` default test — default language is now `"auto"` not `"zh"`
- Add test for `_process_forced_alignment_job` with `language="auto"`:
  - Mock `resolve_lrc_language` to return `"en"`
  - Verify language is resolved from lyrics text before calling `align()`
  - Verify `align()` receives `"English"` as the mapped language
- Add test for `_process_forced_alignment_job` with `language="auto"` and CJK lyrics:
  - Mock `resolve_lrc_language` to return `"zh"`
  - Verify `align()` receives `"Chinese"`
- Add test for explicit `language="zh"` and `language="en"` — ensure they bypass auto-detection

### 6. Update existing tests

- Update `ForcedAlignmentJobRequest` model validation test — default language is now `"auto"`
- Update `_process_forced_alignment_job` language mapping test to cover `"auto"` path

## Files Changed

| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | `ForcedAlignmentOptions.language` type + default |
| `services/analysis/src/sow_analysis/workers/queue.py` | Resolve `"auto"` in `_process_forced_alignment_job` using `resolve_lrc_language()` |
| `src/stream_of_worship/admin/commands/audio.py` | `align-lrc` default + validation |
| `src/stream_of_worship/admin/services/analysis.py` | `submit_forced_alignment` type hint + default |
| `services/analysis/tests/test_forced_alignment.py` | Updated + new tests |

## Not Changed

- `services/analysis/src/sow_analysis/workers/forced_alignment.py` — no new function needed; reuse `resolve_lrc_language()` from `lrc.py`
- `ForcedAlignerWrapper.align()` — still accepts `"Chinese"` / `"English"`, no changes
- API route `/api/v1/jobs/forced-alignment` — already passes `options` through, no changes needed
- `docker-compose.yml` / `.env` — no config changes

## Rationale for Reusing `resolve_lrc_language()`

| Aspect | Existing `resolve_lrc_language()` | Hypothetical new `detect_language()` |
|--------|-----------------------------------|--------------------------------------|
| CJK coverage | Full (Extensions A–F) | Partial (Basic + Ext A only) |
| Uses song title | Yes (strong signal) | No |
| Uses lyrics text | Yes | Yes |
| Returns | `"zh"` / `"en"` | `"Chinese"` / `"English"` |
| Already tested | Yes (used in production LRC pipeline) | New, needs tests |
| Warnings on mismatch | Yes | No |
| Maintenance burden | Single source of truth | Duplicate to keep in sync |

## Migration Notes

- Existing jobs/submissions with explicit `language="zh"` or `language="en"` continue to work unchanged
- Default behavior changes from `"zh"` to `"auto"` — English songs will now be correctly detected without manual override
- The `resolve_lrc_language()` fallback is `"zh"` when no CJK or Latin is detected, so Chinese songs without clear script signals still default safely
