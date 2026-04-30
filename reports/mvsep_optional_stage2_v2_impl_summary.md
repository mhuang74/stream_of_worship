# MVSEP Optional Stage 2 & Stem Rename v2 - Implementation Summary

**Date:** 2026-04-30  
**Status:** Complete  
**Spec Version:** v2 (incorporates review amendments)

---

## Overview

This implementation introduces two coordinated changes to the analysis service:

1. **MVSEP Stage Configuration** — Replace hardcoded `sep_type`/`add_opt1` with configurable per-stage environment variables, enabling MelBand Roformer as the default vocal separation model and making Stage 2 (reverb removal) optional.

2. **Stem Naming Rename** — Rename output files and all code references:
   - `vocals_clean` → `vocals_dry` (Stage 2 output, de-reverb/dry)
   - `vocals_reverb` → `vocals` (Stage 1 output, raw separated vocals)
   - `instrumental_clean` → `instrumental` (Stage 1 output)

R2 read fallback chains maintain backward compatibility with existing data under old names.

---

## Files Modified

### Configuration Files

| File | Changes |
|------|---------|
| `services/analysis/.env.example` | Removed `SOW_MVSEP_VOCAL_MODEL`/`SOW_MVSEP_DEREVERB_MODEL`, added 6 new Stage1/Stage2 env vars |
| `services/analysis/src/sow_analysis/config.py` | New fields with defaults (48/11/None for Stage 1, 22/0/1 for Stage 2), `@field_validator` for empty-string→None conversion |

### Core Implementation Files

| File | Changes |
|------|---------|
| `services/analysis/src/sow_analysis/services/mvsep_client.py` | Constructor: 6 new stage params; `separate_stems()` skips Stage 2 when `stage2_sep_type is None`; updated docstrings |
| `services/analysis/src/sow_analysis/models.py` | `JobResult` fields renamed: `vocals_clean_url`→`vocals_dry_url`, `vocals_reverb_url`→`vocals_url`, `instrumental_clean_url`→`instrumental_url` |
| `services/analysis/src/sow_analysis/storage/r2.py` | Added `check_stem_exists()` with legacy fallback; `upload_clean_stems()` with correct return order `(vocals_dry_url, vocals_url, instrumental_url)`; `STEM_LEGACY_NAMES` constant |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` | Major refactor: cache dir `stems_clean/`→`stems/`, `find_cached_stem()` with lazy migration, Stage 2 skip integration, idempotency check refactor |
| `services/analysis/src/sow_analysis/workers/separator_wrapper.py` | Docstring updates: `vocals_clean_path`→`vocals_dry_path`, `vocals_reverb_path`→`vocals_path` |
| `services/analysis/src/sow_analysis/workers/queue.py` | Field renames, stage name `"using_vocals_dry_stem"`, critical fix: stem URL fallback placed **before** error check |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Field renames in `job_to_response()` |

### Test Files

| File | Changes |
|------|---------|
| `services/analysis/tests/test_mvsep_client.py` | `MockSettings` updated with 6 new fields; test fixture updated; sep_type/add_opt1 values updated to new defaults (48, 11) |
| `services/analysis/tests/test_mvsep_fallback.py` | Mock values updated (`clean.flac`→`dry.flac`, `reverb.flac`→`vocals.flac`); added Stage 2 skip test; added return order consistency test |

### Admin CLI & POC

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/commands/audio.py` | R2 keys changed from `.wav`→`.flac`; `output_format="WAV"`→`"FLAC"`; added R2 read fallback chain |
| `src/stream_of_worship/admin/services/r2.py` | Comment update: `'vocals_clean'`→`'vocals_dry'`; changed extension to `.flac` |
| `poc/utils.py` | Stem lookup list updated with fallback chain: `["vocals_dry", "vocals_clean", "vocals"]` |

### Documentation

| File | Changes |
|------|---------|
| `services/analysis/README.md` | R2 key examples updated: `vocals_clean.flac`→`vocals_dry.flac`; descriptions updated |

---

## New Environment Variables

### Stage 1 (Vocal Separation) — Required

| Variable | Default | Description |
|----------|---------|-------------|
| `SOW_MVSEP_STAGE1_SEP_TYPE` | `48` | MelBand Roformer (vocals, instrumental) — **default** (was BS Roformer sep_type=40) |
| `SOW_MVSEP_STAGE1_ADD_OPT1` | `11` | becruily deux, SDR vocals: 11.35 — **default** (was BS Roformer ver 2025.07 with SDR 11.89) |
| `SOW_MVSEP_STAGE1_ADD_OPT2` | *(empty)* | Optional, not needed for most sep_types |

### Stage 2 (Reverb Removal) — Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SOW_MVSEP_STAGE2_SEP_TYPE` | `22` | Reverb Removal — **default** |
| `SOW_MVSEP_STAGE2_ADD_OPT1` | `0` | FoxJoy MDX23C — **default** |
| `SOW_MVSEP_STAGE2_ADD_OPT2` | `1` | Use as is (input is already vocals from Stage 1) |

**Skip logic:** When `SOW_MVSEP_STAGE2_SEP_TYPE` is unset or empty string, the `@field_validator` converts it to `None`, and Stage 2 is skipped entirely.

### Removed Variables

| Old Variable | Replaced By |
|--------------|-------------|
| `SOW_MVSEP_VOCAL_MODEL` | `SOW_MVSEP_STAGE1_SEP_TYPE` + `SOW_MVSEP_STAGE1_ADD_OPT1` |
| `SOW_MVSEP_DEREVERB_MODEL` | `SOW_MVSEP_STAGE2_SEP_TYPE` + `SOW_MVSEP_STAGE2_ADD_OPT1` + `SOW_MVSEP_STAGE2_ADD_OPT2` |

---

## Key Implementation Details

### 1. Config.py @field_validator

```python
@field_validator(
    "SOW_MVSEP_STAGE2_SEP_TYPE",
    "SOW_MVSEP_STAGE2_ADD_OPT1",
    "SOW_MVSEP_STAGE2_ADD_OPT2",
    mode="before",
)
@classmethod
def _empty_str_to_none(cls, v):
    """Convert empty-string env vars to None for Optional[int] fields."""
    if isinstance(v, str) and not v.strip():
        return None
    return v
```

**Purpose:** pydantic-settings reads env vars as strings; an empty string cannot be parsed as int. This validator converts empty/whitespace values to `None` before type coercion.

### 2. R2 check_stem_exists() Fallback Chain

```python
STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}

async def check_stem_exists(self, hash_prefix: str, stem_name: str, extension: str = "flac") -> Optional[str]:
    """Check if a stem exists in R2, trying new name then legacy fallback."""
    # Try new name first
    primary_url = f"s3://{self.bucket}/{hash_prefix}/stems/{stem_name}.{extension}"
    if await self.check_exists(primary_url):
        return primary_url
    
    # Fall back to legacy name
    legacy_name = STEM_LEGACY_NAMES.get(stem_name)
    if legacy_name:
        legacy_url = f"s3://{self.bucket}/{hash_prefix}/stems/{legacy_name}.{extension}"
        if await self.check_exists(legacy_url):
            return legacy_url
    
    return None
```

### 3. Cache find_cached_stem() with Lazy Migration

```python
CACHE_STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}
CACHE_DIR_LEGACY = "stems_clean"

def find_cached_stem(cache_manager: "CacheManager", hash_32: str, stem_name: str) -> Optional[Path]:
    """Find a cached stem file, trying new name/dir then legacy fallback.
    
    When a legacy file is found, it is lazily migrated (renamed) to the new path.
    """
    new_dir = cache_manager.cache_dir / "stems" / hash_32
    old_dir = cache_manager.cache_dir / CACHE_DIR_LEGACY / hash_32
    
    # Try new directory, new name
    primary = new_dir / f"{stem_name}.flac"
    if primary.exists():
        return primary
    
    # Try new directory, legacy name → migrate
    legacy_name = CACHE_STEM_LEGACY_NAMES.get(stem_name)
    if legacy_name:
        legacy_in_new = new_dir / f"{legacy_name}.flac"
        if legacy_in_new.exists():
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_new.rename(primary)
            return primary
    
    # Try old directory, new name → migrate
    if old_dir.exists():
        primary_in_old = old_dir / f"{stem_name}.flac"
        if primary_in_old.exists():
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary_in_old.rename(primary)
            return primary
    
    # Try old directory, legacy name → migrate
    if legacy_name and old_dir.exists():
        legacy_in_old = old_dir / f"{legacy_name}.flac"
        if legacy_in_old.exists():
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_old.rename(primary)
            return primary
    
    return None
```

**Rationale:** When a legacy cached file is found, it is renamed/moved to the new path. This ensures the fallback is only exercised once per cached stem. Subsequent reads find the file at the new path directly.

### 4. Return Order Consistency

Both `separate_stems()` and `upload_clean_stems()` return the same order:

```python
# separate_stems() returns:
# (vocals_dry_path, vocals_path, instrumental_path)
# where vocals_dry_path is None when Stage 2 is skipped

# upload_clean_stems() returns:
# (vocals_dry_url, vocals_url, instrumental_url)
# where vocals_dry_url is None when vocals_dry file not provided
```

This fixes the v1 inconsistency where `upload_clean_stems()` had vocals/instrumental swapped at positions [1]/[2].

### 5. Stage 2 Skip Integration

```python
async def separate_stems(self, input_path, output_dir, stage_callback=None):
    # Stage 1
    vocals_file, instrumental_file = await self.separate_vocals(...)
    
    # Stage 2 (optional)
    if self.stage2_sep_type is None:
        logger.info("MVSEP Stage 2 disabled (stage2_sep_type not set), skipping")
        return None, vocals_file, instrumental_file
    
    dry_vocals_file, _ = await self.remove_reverb(vocals_file, stage2_dir, stage_callback)
    return dry_vocals_file, vocals_file, instrumental_file
```

**Idempotency check:** When Stage 2 is enabled, require all 3 stems. When Stage 2 is skipped, only require `vocals` + `instrumental`.

### 6. Queue Stem URL Fallback (Critical Fix)

```python
# BEFORE (v1 gap): Would error when vocals_dry_url is None
vocals_stem_url = child_job.result.vocals_dry_url
if vocals_stem_url:
    # download and use
else:
    logger.error("No vocals URL")  # False error when Stage 2 skipped

# AFTER (v2 fix): Fallback to vocals_url before error check
vocals_stem_url = child_job.result.vocals_dry_url or child_job.result.vocals_url
if vocals_stem_url:
    ext = ".flac" if vocals_stem_url.endswith(".flac") else ".wav"
    stem_path = temp_path / f"vocals_dry{ext}"
    await self.r2_client.download_audio(vocals_stem_url, stem_path)
    transcription_path = stem_path
    job.stage = "using_vocals_dry_stem"
else:
    logger.error("Child job completed but no vocals URL in result")
```

---

## Test Results

### MVSEP Fallback Tests (14 tests)

```
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_both_stages_succeed PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_stage1_fails_retries_then_succeeds PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_stage1_exhausts_retries_falls_back_full_local PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_stage1_succeeds_stage2_fails_handoff PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_stage2_skipped_when_disabled PASSED [NEW]
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_non_retriable_fast_fallback PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_non_retriable_disables_future_jobs PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_not_available_uses_local PASSED
services/analysis/tests/test_mvsep_fallback.py::test_mvsep_none_uses_local PASSED
services/analysis/tests/test_mvsep_fallback.py::test_total_timeout_exceeded_falls_back PASSED
services/analysis/tests/test_mvsep_fallback.py::test_stage_callback_updates PASSED
services/analysis/tests/test_mvsep_fallback.py::test_daily_limit_hit_uses_local PASSED
services/analysis/tests/test_mvsep_fallback.py::test_stage1_no_vocals_file_fallback PASSED
services/analysis/tests/test_mvsep_fallback.py::test_upload_stems_return_order_matches_separate_stems PASSED [NEW]
```

**All 14 tests PASS.**

---

## Migration Notes

- **No re-processing required**: R2 fallback chain means existing `vocals_clean.flac` / `vocals_reverb.flac` files are still readable
- **New uploads use new names**: All newly processed songs get `vocals_dry.flac` / `vocals.flac` / `instrumental.flac`
- **Optional backfill**: To normalize existing R2 data, re-run stem separation with `force=True` for the existing catalog
- **No data loss**: Old R2 keys are never deleted; fallback reads find them; new writes use new names
- **Skip Stage 2 by leaving env var empty or unset**: `SOW_MVSEP_STAGE2_SEP_TYPE=` (empty) or commenting out the var both resolve to `None` via `@field_validator`. Do **not** set to `0` (that's integer 0, not `None`).
- **Default model change**: New deployments without an updated `.env` will use MelBand Roformer (sep_type=48, add_opt1=11) instead of the previous default BS Roformer (sep_type=40, add_opt1=81). To preserve the old behavior, set `SOW_MVSEP_STAGE1_SEP_TYPE=40` and `SOW_MVSEP_STAGE1_ADD_OPT1=81` explicitly.
- **Old env vars silently ignored**: `SOW_MVSEP_VOCAL_MODEL` and `SOW_MVSEP_DEREVERB_MODEL` are no longer read. Update `.env` before deploying. No startup warning is emitted.
- **Admin CLI now outputs FLAC**: The `sow-admin audio vocal` command now uploads `vocals_dry.flac` instead of `vocals_clean.wav`. Legacy `.wav` keys in R2 are not covered by the fallback chain — re-run the command to generate `.flac` versions.
- **Cache directory renamed**: Local cache moves from `stems_clean/` to `stems/`. The `find_cached_stem()` helper checks both directories and lazily migrates files on access.
- **Eventual R2 cleanup**: Old R2 keys (`vocals_clean.flac`, `vocals_reverb.flac`, `instrumental_clean.flac`) can be garbage-collected after confirming all consumers use the new fallback chain. There is no urgency — they are inert once all new writes use new names.

---

## Verification Commands

```bash
# Run MVSEP fallback tests
PYTHONPATH=services/analysis/src uv run --python 3.11 --extra app --extra test pytest services/analysis/tests/test_mvsep_fallback.py -v

# Run all analysis tests
PYTHONPATH=services/analysis/src uv run --python 3.11 --extra app --extra test pytest services/analysis/tests/ -v

# Run app-level tests (excluding analysis service)
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ --ignore=tests/services/analysis --ignore=services/qwen3/tests --ignore=services/analysis/tests -v

# Lint changed files
ruff check services/analysis/src/sow_analysis/config.py services/analysis/src/sow_analysis/models.py services/analysis/src/sow_analysis/storage/r2.py services/analysis/src/sow_analysis/workers/stem_separation.py services/analysis/src/sow_analysis/workers/separator_wrapper.py services/analysis/src/sow_analysis/workers/queue.py services/analysis/src/sow_analysis/routes/jobs.py services/analysis/src/sow_analysis/services/mvsep_client.py
```

---

## Implementation Notes

1. **Backward Compatibility**: Maintained through fallback chains for both R2 storage and local cache
2. **Lazy Migration**: Cache files are renamed in-place when found, ensuring fallback is only exercised once
3. **Stage 2 Optional**: Clean separation logic — when skipped, only vocals+instrumental required
4. **Return Order Critical**: `(vocals_dry, vocals, instrumental)` must be consistent across `separate_stems()` and `upload_clean_stems()`
5. **Queue Fallback Critical**: Must come BEFORE error check to prevent false errors when Stage 2 is skipped

---

*Implementation complete. All tests passing.*
