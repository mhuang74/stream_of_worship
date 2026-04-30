# MVSEP Optional Stage 2 & Stem Rename (v2)

## Overview

Two coordinated changes to the analysis service:

1. **MVSEP Stage Configuration** — Replace hardcoded `sep_type`/`add_opt1` with configurable per-stage env vars, enabling MelBand Roformer as the default vocal separation model and making Stage 2 optional.

2. **Stem Naming Rename** — Rename output files and all code references:
   - `vocals_clean` → `vocals_dry` (Stage 2 output, de-reverb/dry)
   - `vocals_reverb` → `vocals` (Stage 1 output, raw separated vocals)
   - `instrumental_clean` → `instrumental` (Stage 1 output, no "clean" qualifier needed)

R2 read fallback chains maintain backward compatibility with existing data under old names.

### v2 Changes from v1

This spec incorporates review amendments identified against the codebase:

- **`@field_validator`** for `SOW_MVSEP_STAGE2_*` env vars — pydantic-settings `Optional[int]` rejects empty-string env vars; a validator converts `""` → `None`
- **`upload_stems()` return order** fixed to `(vocals_dry_url, vocals_url, instrumental_url)` — consistent with `separate_stems()` return tuple (v1 had vocals/instrumental swapped at positions [1]/[2])
- **Admin CLI aligned to FLAC** — was outputting WAV while analysis service uses FLAC; R2 keys now consistent
- **Cache directory renamed** `stems_clean/` → `stems/` with old directory fallback
- **Cache migration** — one-time rename of legacy cached files on fallback hit
- **Queue error path** — `vocals_dry_url or vocals_url` fallback placed before the "no vocals URL" error check
- **Integration test** — corrected command name to `sow-admin audio vocal` and extension to `.flac`
- **Migration notes** — documented silent default model change, empty-string skip behavior, eventual R2 garbage collection

---

## Part 1: MVSEP Stage Configuration

### New Environment Variables

**Stage 1 (Vocal Separation) — Required:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SOW_MVSEP_STAGE1_SEP_TYPE` | `48` | MelBand Roformer (vocals, instrumental) |
| `SOW_MVSEP_STAGE1_ADD_OPT1` | `11` | becruily deux, SDR vocals: 11.35 |
| `SOW_MVSEP_STAGE1_ADD_OPT2` | *(empty)* | Optional, not needed for most sep_types |

**Stage 2 (Reverb Removal) — Optional:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SOW_MVSEP_STAGE2_SEP_TYPE` | `22` | Reverb Removal |
| `SOW_MVSEP_STAGE2_ADD_OPT1` | `0` | FoxJoy MDX23C |
| `SOW_MVSEP_STAGE2_ADD_OPT2` | `1` | Use as is (we pass Stage 1 vocals) |

**Skip logic:** When `SOW_MVSEP_STAGE2_SEP_TYPE` is unset or set to an empty string, a `@field_validator` (see `config.py` Changes below) converts it to `None`, and Stage 2 is skipped entirely. `separate_stems()` returns `(None, vocals, instrumental)` — `vocals_dry` is `None` because no de-reverb was applied; `vocals` is the Stage 1 output.

> **Note:** To skip Stage 2 in `.env`, either comment out / omit `SOW_MVSEP_STAGE2_SEP_TYPE` or set it to empty (`SOW_MVSEP_STAGE2_SEP_TYPE=`). Both resolve to `None` via the validator. Setting it to `0` would be interpreted as integer `0`, not `None`.

**Removed variables:**

| Old Variable | Replaced By |
|--------------|-------------|
| `SOW_MVSEP_VOCAL_MODEL` | `SOW_MVSEP_STAGE1_SEP_TYPE` + `SOW_MVSEP_STAGE1_ADD_OPT1` |
| `SOW_MVSEP_DEREVERB_MODEL` | `SOW_MVSEP_STAGE2_SEP_TYPE` + `SOW_MVSEP_STAGE2_ADD_OPT1` + `SOW_MVSEP_STAGE2_ADD_OPT2` |

> **Migration note:** Deployments that don't update `.env` will silently switch from BS Roformer (sep_type=40, add_opt1=81) to MelBand Roformer (sep_type=48, add_opt1=11) since the field names and defaults both changed. The old `SOW_MVSEP_VOCAL_MODEL` / `SOW_MVSEP_DEREVERB_MODEL` env vars are silently ignored by pydantic-settings (`extra="ignore"`). No startup warning is emitted — update `.env` before deploying.

### `.env.example` Diff

```diff
-SOW_MVSEP_VOCAL_MODEL=81
-# MVSEP vocal separation model (sep_type=40, BS Roformer)
-# 81 = BS Roformer 2025.07, SDR 11.89 (default)
-# 29 = BS Roformer 2024.08, SDR 11.24
-
-SOW_MVSEP_DEREVERB_MODEL=0
-# MVSEP reverb removal model (sep_type=22)
-# 0 = FoxJoy MDX23C (default)
+SOW_MVSEP_STAGE1_SEP_TYPE=48
+# MVSEP Stage 1 separation type (see docs/MVSEP_API.md sep_type table)
+# 48 = MelBand Roformer (vocals, instrumental) — default
+# 40 = BS Roformer (vocals, instrumental)
+# 25 = MDX23C (vocals, instrumental)
+# 26 = Ensemble (vocals, instrumental)
+
+SOW_MVSEP_STAGE1_ADD_OPT1=11
+# MVSEP Stage 1 model variant (add_opt1, depends on sep_type)
+# For sep_type=48 (MelBand Roformer):
+#   11 = becruily deux, SDR vocals: 11.35 (default, best vocals)
+#   4  = ver 2024.10, SDR vocals: 11.28
+#   1  = ver 2024.08, SDR vocals: 11.17
+#   0  = Kimberley Jensen edition, SDR vocals: 11.01
+# For sep_type=40 (BS Roformer):
+#   81 = ver 2025.07, SDR vocals: 11.89
+#   29 = ver 2024.08, SDR vocals: 11.24
+
+SOW_MVSEP_STAGE1_ADD_OPT2=""
+# MVSEP Stage 1 additional option (add_opt2, optional)
+# Not needed for sep_type=48 or 40; used by Ensemble types.
+
+SOW_MVSEP_STAGE2_SEP_TYPE=22
+# MVSEP Stage 2 separation type (optional — leave empty or comment out to skip Stage 2)
+# The @field_validator converts empty string to None, which skips Stage 2.
+# 22 = Reverb Removal (default)
+# Some models (e.g., DeReverb Roformer) do reverb removal in Stage 1,
+# making Stage 2 unnecessary.
+
+SOW_MVSEP_STAGE2_ADD_OPT1=0
+# MVSEP Stage 2 model variant (add_opt1, depends on STAGE2_SEP_TYPE)
+# For sep_type=22 (Reverb Removal):
+#   0 = FoxJoy MDX23C (default)
+#   1 = anvuew MelRoformer
+#   4 = Sucial MelRoformer
+
+SOW_MVSEP_STAGE2_ADD_OPT2=1
+# MVSEP Stage 2 additional option (add_opt2)
+# For sep_type=22 (Reverb Removal):
+#   0 = Extract vocals first (needed for Mel/BS Roformer dereverb)
+#   1 = Use as is (input is already vocals from Stage 1)
```

### `config.py` Changes

```python
from pydantic import field_validator

# MVSEP Cloud API Configuration
SOW_MVSEP_API_KEY: str = ""
SOW_MVSEP_ENABLED: bool = True

# Stage 1 (Vocal Separation)
SOW_MVSEP_STAGE1_SEP_TYPE: int = 48
SOW_MVSEP_STAGE1_ADD_OPT1: int = 11
SOW_MVSEP_STAGE1_ADD_OPT2: Optional[int] = None

# Stage 2 (Reverb Removal) — None = skip Stage 2
SOW_MVSEP_STAGE2_SEP_TYPE: Optional[int] = 22
SOW_MVSEP_STAGE2_ADD_OPT1: Optional[int] = 0
SOW_MVSEP_STAGE2_ADD_OPT2: Optional[int] = 1

# Timeouts & limits (unchanged)
SOW_MVSEP_HTTP_TIMEOUT: int = 60
SOW_MVSEP_STAGE_TIMEOUT: int = 300
SOW_MVSEP_TOTAL_TIMEOUT: int = 900
SOW_MVSEP_DAILY_JOB_LIMIT: int = 50

@field_validator(
    "SOW_MVSEP_STAGE2_SEP_TYPE",
    "SOW_MVSEP_STAGE2_ADD_OPT1",
    "SOW_MVSEP_STAGE2_ADD_OPT2",
    mode="before",
)
@classmethod
def _empty_str_to_none(cls, v):
    """Convert empty-string env vars to None for Optional[int] fields.

    pydantic-settings reads env vars as strings; an empty string (e.g.
    SOW_MVSEP_STAGE2_SEP_TYPE=) cannot be parsed as int. This validator
    converts "" / whitespace-only values to None before type coercion.
    """
    if isinstance(v, str) and not v.strip():
        return None
    return v
```

> **Design note:** The validator applies to all three Stage 2 fields so that any of them can be left empty in `.env`. When `SOW_MVSEP_STAGE2_SEP_TYPE` is `None`, Stage 2 is skipped and `ADD_OPT1`/`ADD_OPT2` are irrelevant, but making them also nullable keeps the API consistent.

### `mvsep_client.py` Changes

**Constructor** — Replace `vocal_model`/`dereverb_model` with stage params:

```python
def __init__(
    self,
    api_token: Optional[str] = None,
    enabled: Optional[bool] = None,
    stage1_sep_type: Optional[int] = None,
    stage1_add_opt1: Optional[int] = None,
    stage1_add_opt2: Optional[int] = None,
    stage2_sep_type: Optional[int] = None,
    stage2_add_opt1: Optional[int] = None,
    stage2_add_opt2: Optional[int] = None,
    http_timeout: Optional[int] = None,
    stage_timeout: Optional[int] = None,
    daily_job_limit: Optional[int] = None,
) -> None:
```

**`separate_vocals()`** — Use configurable stage1 params:

```python
async def separate_vocals(self, input_path, output_dir, stage_callback=None):
    job_hash = await self._submit_job(
        audio_path=input_path,
        sep_type=self.stage1_sep_type,
        add_opt1=self.stage1_add_opt1,
        add_opt2=self.stage1_add_opt2,
        output_format=2,
    )
    # ... rest unchanged
```

**`remove_reverb()`** — Use configurable stage2 params:

```python
async def remove_reverb(self, vocals_path, output_dir, stage_callback=None):
    job_hash = await self._submit_job(
        audio_path=vocals_path,
        sep_type=self.stage2_sep_type,
        add_opt1=self.stage2_add_opt1,
        add_opt2=self.stage2_add_opt2,
        output_format=2,
    )
    # ... rest unchanged
```

**`separate_stems()`** — Skip Stage 2 when `stage2_sep_type` is `None`:

```python
async def separate_stems(self, input_path, output_dir, stage_callback=None):
    # Stage 1
    vocals_file, instrumental_file = await self.separate_vocals(...)

    # Stage 2 (optional)
    if self.stage2_sep_type is None:
        return None, vocals_file, instrumental_file

    dry_vocals_file, _ = await self.remove_reverb(vocals_file, stage2_dir, stage_callback)
    return dry_vocals_file, vocals_file, instrumental_file
```

> **Return tuple order convention:** `separate_stems()` and `upload_stems()` both return `(vocals_dry, vocals, instrumental)` — positions [0], [1], [2] are consistent across both functions. This fixes the v1 inconsistency where `upload_clean_stems()` returned vocals/instrumental in swapped positions.

**`_submit_job()` docstring** — Update `sep_type` description:

```python
sep_type: Separation type code (e.g., 48 = MelBand Roformer, 40 = BS Roformer, 22 = Reverb Removal)
```

---

## Part 2: Stem Naming Rename

### R2 Key Rename

| Old Key | New Key |
|---------|---------|
| `{hash_prefix}/stems/vocals_clean.flac` | `{hash_prefix}/stems/vocals_dry.flac` |
| `{hash_prefix}/stems/vocals_reverb.flac` | `{hash_prefix}/stems/vocals.flac` |
| `{hash_prefix}/stems/instrumental_clean.flac` | `{hash_prefix}/stems/instrumental.flac` |

> **All stems use `.flac` extension.** The admin CLI previously uploaded `.wav`; it is now aligned to `.flac` (see Part 3, `audio.py`).

### R2 Read Fallback Chain (backward compatibility)

When checking if a stem exists, try new name first, then legacy name:

| New Key (try first) | Legacy Fallback |
|---------------------|-----------------|
| `vocals_dry.flac` | `vocals_clean.flac` |
| `vocals.flac` | `vocals_reverb.flac` |
| `instrumental.flac` | `instrumental_clean.flac` |

> **Admin CLI legacy stems:** Previously uploaded as `vocals_clean.wav`. Since admin is now aligned to FLAC (`.flac`), new uploads use `vocals_dry.flac`. The R2 fallback helper only checks `.flac` — legacy `vocals_clean.wav` keys from the admin CLI are **not** found by the fallback chain. If any exist, they can be migrated by re-running `sow-admin audio vocal` which will upload `vocals_dry.flac`.

### Code Model Field Rename

**`models.py` — `JobResult`:**

| Old Field | New Field |
|-----------|-----------|
| `vocals_clean_url` | `vocals_dry_url` |
| `vocals_reverb_url` | `vocals_url` |
| `instrumental_clean_url` | `instrumental_url` |

### Local Cache Rename

| Old Path | New Path |
|----------|----------|
| `/cache/stems_clean/{hash32}/vocals_clean.flac` | `/cache/stems/{hash32}/vocals_dry.flac` |
| `/cache/stems_clean/{hash32}/vocals_reverb.flac` | `/cache/stems/{hash32}/vocals.flac` |
| `/cache/stems_clean/{hash32}/instrumental_clean.flac` | `/cache/stems/{hash32}/instrumental.flac` |

Same fallback chain applies for cache reads. Additionally, the cache directory itself is renamed from `stems_clean/` to `stems/`; the `find_cached_stem()` helper checks the new directory first, then falls back to the old `stems_clean/` directory.

---

## Part 3: File-by-File Changes

### `services/analysis/.env.example`

- Remove `SOW_MVSEP_VOCAL_MODEL`, `SOW_MVSEP_DEREVERB_MODEL`
- Add 6 new Stage1/Stage2 env vars with MelBand Roformer defaults
- Update comments to reference `docs/MVSEP_API.md`
- Update Stage 2 comments: clarify that empty string or commenting out the var both skip Stage 2 (via `@field_validator`)

### `services/analysis/src/sow_analysis/config.py`

- Remove `SOW_MVSEP_VOCAL_MODEL: int = 81`, `SOW_MVSEP_DEREVERB_MODEL: int = 0`
- Add `SOW_MVSEP_STAGE1_SEP_TYPE: int = 48`, `SOW_MVSEP_STAGE1_ADD_OPT1: int = 11`, `SOW_MVSEP_STAGE1_ADD_OPT2: Optional[int] = None`
- Add `SOW_MVSEP_STAGE2_SEP_TYPE: Optional[int] = 22`, `SOW_MVSEP_STAGE2_ADD_OPT1: Optional[int] = 0`, `SOW_MVSEP_STAGE2_ADD_OPT2: Optional[int] = 1`
- Add `@field_validator` for `SOW_MVSEP_STAGE2_SEP_TYPE`, `SOW_MVSEP_STAGE2_ADD_OPT1`, `SOW_MVSEP_STAGE2_ADD_OPT2` — converts empty string to `None` before type coercion (see Part 1 `config.py` Changes)

### `services/analysis/src/sow_analysis/models.py`

- `vocals_clean_url` → `vocals_dry_url`
- `vocals_reverb_url` → `vocals_url`
- `instrumental_clean_url` → `instrumental_url`

### `services/analysis/src/sow_analysis/services/mvsep_client.py`

- Constructor: replace `vocal_model`/`dereverb_model` with `stage1_*`/`stage2_*` params
- `separate_vocals()`: use `self.stage1_sep_type`/`self.stage1_add_opt1`/`self.stage1_add_opt2`
- `remove_reverb()`: use `self.stage2_sep_type`/`self.stage2_add_opt1`/`self.stage2_add_opt2`
- `separate_stems()`: skip Stage 2 when `self.stage2_sep_type is None`
- Update docstrings: `vocals_clean_path` → `vocals_dry_path`, `vocals_reverb_path` → `vocals_path`
- Update class docstring: "BS Roformer" → "configurable sep_type"

### `services/analysis/src/sow_analysis/workers/stem_separation.py`

- Variable renames throughout: `vocals_clean_*` → `vocals_dry_*`, `vocals_reverb_*` → `vocals_*`
- R2 keys: `vocals_clean.flac` → `vocals_dry.flac`, `vocals_reverb.flac` → `vocals.flac`, `instrumental_clean.flac` → `instrumental.flac`
- Cache paths: same renames
- **Cache directory rename**: `stems_clean/` → `stems/` (update `cache_manager.cache_dir / "stems"` instead of `"stems_clean"`)
- **Cache directory fallback**: in `find_cached_stem()`, if not found in `stems/` dir, also try `stems_clean/` dir (old location)
- **Add R2 fallback chain** in idempotency check: check new name, then legacy name
- **Add cache fallback chain**: check new name, then legacy name (including old directory)
- **Stage 2 skip integration**: When MVSEP Stage 2 is skipped (`stage2_sep_type is None`), `vocals_dry_path` is `None`; only `vocals` + `instrumental` are uploaded/cached
- **Idempotency check update**: When Stage 2 is enabled, require all 3 stems. When Stage 2 is skipped, require `vocals` + `instrumental` only.
- `get_clean_vocals_url()` → `get_vocals_dry_url()`: check `vocals_dry.flac`, fallback to `vocals_clean.flac`
- Method `upload_clean_stems` call → `upload_stems`
- **Update destructure** of `upload_stems()` return to match new order: `(vocals_dry_url, vocals_url, instrumental_url)` (was `(vocals_url, instrumental_url, vocals_reverb_url)`)
- Warning msg update: `"No vocals_reverb (Stage 1 vocals) file generated"` → `"No vocals (Stage 1) file generated"`

### `services/analysis/src/sow_analysis/workers/separator_wrapper.py`

- Return tuple: `vocals_clean_path` → `vocals_dry_path`, `vocals_reverb_path` → `vocals_path`
- Update docstrings

### `services/analysis/src/sow_analysis/storage/r2.py`

- `upload_clean_stems()` → `upload_stems()`
- Params: `vocals_clean` → `vocals_dry`, `vocals_reverb` → `vocals`, `instrumental_clean` → `instrumental`
- R2 keys: `vocals_clean.flac` → `vocals_dry.flac`, `vocals_reverb.flac` → `vocals.flac`, `instrumental_clean.flac` → `instrumental.flac`
- **Return order fix**: `(vocals_dry_url, vocals_url, instrumental_url)` — consistent with `separate_stems()` return order `(vocals_dry, vocals, instrumental)`. The v1 `upload_clean_stems()` returned `(vocals_clean_url, instrumental_clean_url, vocals_reverb_url)` which had vocals and instrumental swapped at positions [1]/[2].

### `services/analysis/src/sow_analysis/workers/queue.py`

- `vocals_clean_url` → `vocals_dry_url`
- Stage name: `"using_vocals_clean_stem"` → `"using_vocals_dry_stem"`
- Local filename: `f"vocals_clean{ext}"` → `f"vocals_dry{ext}"`
- **Stem URL fallback**: Place `vocals_dry_url or vocals_url` fallback *before* the "no vocals URL" error check. When `vocals_dry_url` is `None` (Stage 2 skipped), use `vocals_url` (Stage 1 vocals) for transcription instead of raising an error:
  ```python
  # Prefer dry vocals for transcription; fall back to raw vocals
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
  This replaces the current code that errors when `vocals_clean_url` is `None` — with Stage 2 optional, `vocals_dry_url` being `None` is a valid state.

### `services/analysis/src/sow_analysis/routes/jobs.py`

- `vocals_clean_url=` → `vocals_dry_url=`
- `vocals_reverb_url=` → `vocals_url=`
- `instrumental_clean_url=` → `instrumental_url=`

### `services/analysis/tests/test_mvsep_client.py`

- `MockSettings`: remove `SOW_MVSEP_VOCAL_MODEL`/`SOW_MVSEP_DEREVERB_MODEL`, add 6 new stage params
- Fixtures: update constructor call with new param names
- `_submit_job` test calls: update `sep_type`/`add_opt1` to use new defaults (48, 11)
- Add test: Stage 2 skipped when `stage2_sep_type=None`
- Add test: `@field_validator` converts empty string to `None` for `SOW_MVSEP_STAGE2_SEP_TYPE`

### `services/analysis/tests/test_mvsep_fallback.py`

- Update mock return values: `clean.flac` → `dry.flac`, `reverb.flac` → `vocals.flac`
- Update assertions referencing old field names
- Add test: Stage 2 skipped when `mvsep_client.stage2_sep_type is None`
- The last test `test_httpx_500_retriable` references `client` fixture from `test_mvsep_client.py` — needs the fixture imported or moved
- Add test: `upload_stems()` return order matches `separate_stems()` — `(vocals_dry, vocals, instrumental)`

### `src/stream_of_worship/admin/commands/audio.py`

- `vocals_clean_key` → `vocals_dry_key`
- **R2 key**: `vocals_clean.wav` → `vocals_dry.flac` (extension changed from `.wav` to `.flac` to align with analysis service)
- **Output format**: Change `output_format="WAV"` → `"FLAC"` in the Separator constructor (Stage 2 de-reverb step)
- Log: `"vocals_clean.wav already exists"` → `"vocals_dry.flac already exists"`
- Stem name: `"vocals_clean"` → `"vocals_dry"`
- **Read fallback**: Check `vocals_dry.flac` first, then `vocals_clean.flac` (legacy)
- **Note**: The command name remains `sow-admin audio vocal` (unchanged — it's an action, not a stem name)

### `src/stream_of_worship/admin/services/r2.py`

- Comment: `'vocals_clean'` → `'vocals_dry'`

### `poc/utils.py`

- Stem name lookup: `"vocals_clean"` → `"vocals_dry"` in the lookup list
- Add fallback: try `"vocals_dry"` first, then `"vocals_clean"` (legacy)

### `services/analysis/README.md`

- Update R2 key examples: `vocals_clean.flac` → `vocals_dry.flac`
- Update description: "Clean vocals" → "Dry vocals (de-reverb)"

---

## Part 4: R2 & Cache Fallback Helpers

To avoid scattering fallback logic, add helpers in `r2.py` and `stem_separation.py`.

### `r2.py` — `check_stem_exists()`

```python
STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}

async def check_stem_exists(
    self, hash_prefix: str, stem_name: str, extension: str = "flac"
) -> Optional[str]:
    """Check if a stem exists in R2, trying new name then legacy fallback.

    Returns:
        S3 URL if found, None otherwise.
    """
    primary_key = f"{hash_prefix}/stems/{stem_name}.{extension}"
    primary_url = f"s3://{self.bucket}/{primary_key}"
    if await self.check_exists(primary_url):
        return primary_url

    legacy_name = STEM_LEGACY_NAMES.get(stem_name)
    if legacy_name:
        legacy_key = f"{hash_prefix}/stems/{legacy_name}.{extension}"
        legacy_url = f"s3://{self.bucket}/{legacy_key}"
        if await self.check_exists(legacy_url):
            return legacy_url

    return None
```

> **Extension note:** All stems use `.flac`. The admin CLI previously uploaded `.wav` but is now aligned to `.flac`. Legacy `.wav` keys from admin uploads are **not** covered by the fallback chain. Re-run `sow-admin audio vocal` to generate `.flac` versions.

### `stem_separation.py` — `find_cached_stem()`

```python
CACHE_STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}

CACHE_DIR_LEGACY = "stems_clean"

def find_cached_stem(cache_manager: "CacheManager", hash_32: str, stem_name: str) -> Optional[Path]:
    """Find a cached stem file, trying new name/dir then legacy fallback.

    Checks the new cache directory (stems/) first, then the old directory
    (stems_clean/) for backward compatibility. When a legacy file is found,
    it is lazily migrated (renamed) to the new path to avoid repeated
    fallback lookups.

    Returns:
        Path if found (possibly after migration), None otherwise.
    """
    new_dir = cache_manager.cache_dir / "stems" / hash_32
    old_dir = cache_manager.cache_dir / CACHE_DIR_LEGACY / hash_32

    # Try new directory, new name
    primary = new_dir / f"{stem_name}.flac"
    if primary.exists():
        return primary

    # Try new directory, legacy name
    legacy_name = CACHE_STEM_LEGACY_NAMES.get(stem_name)
    if legacy_name:
        legacy_in_new = new_dir / f"{legacy_name}.flac"
        if legacy_in_new.exists():
            # Migrate: rename legacy file to new name in new dir
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_new.rename(primary)
            return primary

    # Try old directory, new name
    if old_dir.exists():
        primary_in_old = old_dir / f"{stem_name}.flac"
        if primary_in_old.exists():
            # Migrate: move file from old dir to new dir
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary_in_old.rename(primary)
            return primary

    # Try old directory, legacy name
    if legacy_name and old_dir.exists():
        legacy_in_old = old_dir / f"{legacy_name}.flac"
        if legacy_in_old.exists():
            # Migrate: move file from old dir to new dir with new name
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_old.rename(primary)
            return primary

    return None
```

> **Lazy migration rationale:** When a legacy cached file is found, it is renamed/moved to the new path. This ensures the fallback is only exercised once per cached stem. Subsequent reads find the file at the new path directly. Migration is safe because the cache is a local artifact — if anything goes wrong, stems can always be re-generated from R2.

### Idempotency Check Refactor (in `stem_separation.py`)

Replace the current hardcoded 3-file R2 existence check with:

```python
# Check if stems already exist in R2 (with legacy fallback)
vocals_dry_url = await r2_client.check_stem_exists(hash_prefix, "vocals_dry", "flac")
vocals_url = await r2_client.check_stem_exists(hash_prefix, "vocals", "flac")
instrumental_url = await r2_client.check_stem_exists(hash_prefix, "instrumental", "flac")

stage2_enabled = mvsep_client and mvsep_client.stage2_sep_type is not None

if not request.options.force:
    if stage2_enabled:
        # All 3 stems required when Stage 2 is enabled
        if vocals_dry_url and vocals_url and instrumental_url:
            logger.info("Stems already exist in R2, skipping")
            # ... return cached result
    else:
        # Only vocals + instrumental required when Stage 2 is skipped
        if vocals_url and instrumental_url:
            logger.info("Stems already exist in R2, skipping")
            # ... return cached result (vocals_dry_url may be None)
```

Same pattern for local cache check, using `find_cached_stem()`.

---

## Part 5: `_separate_with_mvsep_fallback()` Stage 2 Skip

Current flow (always runs Stage 2):

```
Stage 1 MVSEP → Stage 2 MVSEP → (vocals_dry, vocals, instrumental)
```

New flow (Stage 2 optional):

```python
async def _separate_with_mvsep_fallback(
    input_path, output_dir, job, mvsep_client, separator_wrapper
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Try MVSEP per-stage with cross-backend handoff; fall back to local on failure.

    Returns:
        Tuple of (vocals_dry_path, vocals_path, instrumental_path).
        vocals_dry_path is None when Stage 2 is disabled or skipped.
    """
    # ... (Stage 1 unchanged) ...

    vocals, instrumental = stage1_result

    # --- Stage 2: De-reverb (optional) ---
    stage2_enabled = mvsep_client.stage2_sep_type is not None

    if not stage2_enabled:
        logger.info("MVSEP Stage 2 disabled (stage2_sep_type not set), skipping")
        return None, vocals, instrumental

    # ... (Stage 2 retry loop unchanged) ...
```

**Cross-backend handoff still works:** When Stage 2 is enabled but MVSEP Stage 2 fails, local `remove_reverb()` is called with MVSEP Stage 1 vocals — this path is unchanged.

**When Stage 2 is skipped entirely:** No local `remove_reverb()` is called either. The result is `(None, vocals, instrumental)`.

### LRC Worker Stem URL Fallback (in `queue.py`)

When `vocals_dry_url` is `None` (Stage 2 skipped), the LRC worker should use `vocals_url` (Stage 1 vocals) for transcription. The fallback must be placed **before** the error check so that a missing `vocals_dry_url` doesn't trigger a false error:

```python
# Prefer dry vocals for transcription; fall back to raw vocals
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

> **v1 gap:** The original code at `queue.py:779-791` would error when `vocals_clean_url` is `None`. With Stage 2 optional, `vocals_dry_url` being `None` is valid — the fallback to `vocals_url` must come first.

---

## Part 6: Testing Plan

### Unit Tests

1. **`test_mvsep_client.py`** — Verify new constructor params, Stage 1 submission uses `stage1_*` values, Stage 2 submission uses `stage2_*` values, Stage 2 skipped when `stage2_sep_type=None`
2. **`test_mvsep_client.py`** — Add test: `@field_validator` converts empty string `""` to `None` for `SOW_MVSEP_STAGE2_SEP_TYPE`
3. **`test_mvsep_fallback.py`** — Verify fallback logic still works with new param names
4. **`test_mvsep_fallback.py`** — Add test: `upload_stems()` return order is `(vocals_dry_url, vocals_url, instrumental_url)` — consistent with `separate_stems()`
5. **R2 fallback** — Add test for `check_stem_exists()` returning legacy URL when new name absent
6. **Cache fallback** — Add test for `find_cached_stem()` returning legacy path when new name absent
7. **Cache migration** — Add test for `find_cached_stem()` lazily migrating legacy file to new path (rename)
8. **Cache directory fallback** — Add test for `find_cached_stem()` finding files in old `stems_clean/` directory and migrating to `stems/`
9. **Stage 2 skip** — Add test for `_separate_with_mvsep_fallback()` returning `(None, vocals, instrumental)` when `stage2_sep_type=None`
10. **LRC stem URL fallback** — Add test for LRC worker using `vocals_url` when `vocals_dry_url` is `None` (no false error raised)

### Integration Tests (manual)

1. **MVSEP with MelBand Roformer**: Set `SOW_MVSEP_STAGE1_SEP_TYPE=48`, submit job, verify `vocals_dry.flac` + `vocals.flac` + `instrumental.flac` appear in R2
2. **MVSEP Stage 2 skip**: Set `SOW_MVSEP_STAGE2_SEP_TYPE=` (empty), submit job, verify only `vocals.flac` + `instrumental.flac` appear (no `vocals_dry.flac`)
3. **MVSEP Stage 2 skip (unset)**: Comment out `SOW_MVSEP_STAGE2_SEP_TYPE` entirely, submit job, verify Stage 2 is skipped (same result as empty string)
4. **R2 backward compatibility**: Verify existing `vocals_clean.flac` / `vocals_reverb.flac` are still found by the fallback chain
5. **Switch to BS Roformer**: Set `SOW_MVSEP_STAGE1_SEP_TYPE=40`, `SOW_MVSEP_STAGE1_ADD_OPT1=81`, verify it works
6. **Local fallback**: Set invalid `SOW_MVSEP_API_KEY`, verify full local pipeline with new naming
7. **Admin CLI**: Run `sow-admin audio vocal`, verify `vocals_dry.flac` is uploaded (not `vocals_clean.wav`)
8. **Upload return order**: Submit a job, verify `upload_stems()` return destructure matches `separate_stems()` order

---

## Part 7: Migration Notes

- **No re-processing required**: R2 fallback chain means existing `vocals_clean.flac` / `vocals_reverb.flac` files are still readable
- **New uploads use new names**: All newly processed songs get `vocals_dry.flac` / `vocals.flac` / `instrumental.flac`
- **Optional backfill**: To normalize existing R2 data, re-run stem separation with `force=True` for the existing 21-song catalog
- **No data loss**: Old R2 keys are never deleted; fallback reads find them; new writes use new names
- **Skip Stage 2 by leaving env var empty or unset**: `SOW_MVSEP_STAGE2_SEP_TYPE=` (empty) or commenting out the var both resolve to `None` via `@field_validator`. Do **not** set to `0` (that's integer 0, not `None`).
- **Default model change**: New deployments without an updated `.env` will use MelBand Roformer (sep_type=48, add_opt1=11) instead of the previous default BS Roformer (sep_type=40, add_opt1=81). To preserve the old behavior, set `SOW_MVSEP_STAGE1_SEP_TYPE=40` and `SOW_MVSEP_STAGE1_ADD_OPT1=81` explicitly.
- **Old env vars silently ignored**: `SOW_MVSEP_VOCAL_MODEL` and `SOW_MVSEP_DEREVERB_MODEL` are no longer read. Update `.env` before deploying. No startup warning is emitted.
- **Admin CLI now outputs FLAC**: The `sow-admin audio vocal` command now uploads `vocals_dry.flac` instead of `vocals_clean.wav`. Legacy `.wav` keys in R2 are not covered by the fallback chain — re-run the command to generate `.flac` versions.
- **Cache directory renamed**: Local cache moves from `stems_clean/` to `stems/`. The `find_cached_stem()` helper checks both directories and lazily migrates files on access.
- **Eventual R2 cleanup**: Old R2 keys (`vocals_clean.flac`, `vocals_reverb.flac`, `instrumental_clean.flac`) can be garbage-collected after confirming all consumers use the new fallback chain. There is no urgency — they are inert once all new writes use new names.

---

## Part 8: Implementation Order

Execute changes in this order to keep the codebase compilable at each step:

### Step 1: Config layer (no behavior change yet)

1. Update `.env.example` with new env var names and MelBand Roformer defaults
2. Update `config.py` — add new fields, remove old fields, add `@field_validator` for Stage 2 empty-string → None
3. Update `test_mvsep_client.py` `MockSettings` — add new fields, remove old

### Step 2: MvsepClient layer

4. Update `mvsep_client.py` — constructor, `separate_vocals()`, `remove_reverb()`, `separate_stems()`, docstrings
5. Update `test_mvsep_client.py` fixtures and test calls
6. Add Stage 2 skip test
7. Add `@field_validator` empty-string test

### Step 3: Stem naming rename (all files at once)

8. Update `models.py` — field renames
9. Update `r2.py` — method rename, R2 key renames, **fix return order to `(vocals_dry_url, vocals_url, instrumental_url)`**, add `check_stem_exists()` + `STEM_LEGACY_NAMES`
10. Update `stem_separation.py` — variable renames, R2/cache key renames, cache dir rename `stems_clean/` → `stems/` with old dir fallback, add `find_cached_stem()` + `CACHE_STEM_LEGACY_NAMES` + `CACHE_DIR_LEGACY` with lazy migration, idempotency check refactor, Stage 2 skip integration, `get_vocals_dry_url()` rename, **update `upload_stems()` destructure to match new return order**
11. Update `separator_wrapper.py` — variable renames, docstrings
12. Update `queue.py` — field renames, stage name, **stem URL fallback before error check**
13. Update `routes/jobs.py` — field renames
14. Update `test_mvsep_fallback.py` — mock return values, assertions, **add return order consistency test**

### Step 4: Admin CLI & POC

15. Update `audio.py` — key renames, **`output_format="WAV"` → `"FLAC"`**, R2 key `.wav` → `.flac`, read fallback `.flac` → `.flac` (legacy)
16. Update `admin/services/r2.py` — comment
17. Update `poc/utils.py` — stem name lookup + fallback

### Step 5: Documentation

18. Update `services/analysis/README.md`

### Step 6: Verify

19. Run `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ --ignore=tests/services/analysis --ignore=services/qwen3/tests --ignore=services/analysis/tests -v` (app-level tests)
20. Run `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest services/analysis/tests/ -v` (analysis service tests)
