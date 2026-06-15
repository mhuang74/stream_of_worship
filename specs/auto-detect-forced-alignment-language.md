# Spec: Auto-Detect Language for Forced Alignment

**Date:** 2026-06-16
**Branch:** `trigger_alignment_via_admin`
**Status:** Draft

## Problem

The `sow-admin audio align-lrc` command defaults `--lang` to `zh` (Chinese). This means English songs get incorrect alignment unless the user manually specifies `--lang en`. Since the app handles both Chinese and English worship songs, the default should be auto-detection.

## Context

- `Qwen3ForcedAligner.align()` **requires** an explicit `language` parameter — it does NOT support auto-detection
- Auto-detection (`language=None`) is only available on `Qwen3ASRModel.transcribe()` (the ASR model, not the forced aligner)
- However, we already have the lyrics text in the job request, so we can detect language from that deterministically

## Design Decision

**Language detection happens in the Analysis Service** (not the Admin CLI), because:

1. All callers benefit — API users, future web UI, not just CLI
2. Service already has the lyrics text via `ForcedAlignmentJobRequest.lyrics_text`
3. Single source of truth — the heuristic lives in one place

## Changes

### 1. `ForcedAlignmentOptions` — Add `"auto"` language value

**File:** `services/analysis/src/sow_analysis/models.py`

- Change `language: Literal["zh", "en"] = "zh"` → `language: Literal["auto", "zh", "en"] = "auto"`

### 2. Language detection utility function

**File:** `services/analysis/src/sow_analysis/workers/forced_alignment.py`

Add `detect_language(text: str) -> str` function:

- Scan lyrics text for CJK Unified Ideographs (U+4E00–U+9FFF), CJK Extension A (U+3400–U+4DBF), and CJK Compatibility Ideographs (U+F900–U+FAFF)
- If any CJK characters found → return `"Chinese"`
- Otherwise → return `"English"`
- This is deterministic, no ML needed — Chinese lyrics always contain CJK characters

### 3. `_process_forced_alignment_job` — Resolve `"auto"` to concrete language

**File:** `services/analysis/src/sow_analysis/workers/queue.py`

- When `request.options.language == "auto"`, call `detect_language(request.lyrics_text)` to resolve
- When `"zh"` or `"en"`, use existing `language_map` as before
- Log the detected language for debugging

### 4. CLI `align-lrc` — Change default and validation

**File:** `src/stream_of_worship/admin/commands/audio.py`

- Change `--lang` default from `"zh"` to `"auto"`
- Update help text: `"Language: auto, zh, en"`
- Update validation: `if language not in {"auto", "zh", "en"}`
- No detection logic in CLI — just passes the value through

### 5. `AnalysisClient.submit_forced_alignment` — Update type

**File:** `src/stream_of_worship/admin/services/analysis.py`

- Update `language` parameter type hint to accept `"auto"`, `"zh"`, or `"en"`

### 6. Tests

**File:** `services/analysis/tests/test_forced_alignment.py`

- Add tests for `detect_language()`:
  - Chinese lyrics → `"Chinese"`
  - English lyrics → `"English"`
  - Mixed CJK + English → `"Chinese"` (CJK presence wins)
  - Empty string → `"English"` (safe default)
- Add test for `_process_forced_alignment_job` with `language="auto"`:
  - Verify language is resolved from lyrics text before calling `align()`

### 7. Update existing tests

- Update `ForcedAlignmentOptions` default test — default language is now `"auto"` not `"zh"`
- Update `_process_forced_alignment_job` language mapping test to cover `"auto"` path

## Files Changed

| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/models.py` | `ForcedAlignmentOptions.language` type + default |
| `services/analysis/src/sow_analysis/workers/forced_alignment.py` | Add `detect_language()` function |
| `services/analysis/src/sow_analysis/workers/queue.py` | Resolve `"auto"` in `_process_forced_alignment_job` |
| `src/stream_of_worship/admin/commands/audio.py` | `align-lrc` default + validation |
| `src/stream_of_worship/admin/services/analysis.py` | `submit_forced_alignment` type hint |
| `services/analysis/tests/test_forced_alignment.py` | New + updated tests |

## Not Changed

- `ForcedAlignerWrapper.align()` — still accepts `"Chinese"` / `"English"`, no changes
- API route `/api/v1/jobs/forced-alignment` — already passes `options` through, no changes needed
- `docker-compose.yml` / `.env` — no config changes
