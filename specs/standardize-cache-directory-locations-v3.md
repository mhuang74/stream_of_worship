# Standardize Cache & Directory Locations — Final Plan

## Context

The codebase currently uses four inconsistent directory names (`stream_of_worship`, `StreamOfWorship`, `sow-admin`, `sow-app`) for the same project, plus three hardcoded cache paths in `admin/commands/audio.py` (two of which point at the *app's* config dir). This causes XDG violations (cache inside config dir), hardcoded cross-component paths, and config fragmentation.

**Scope decision (from clarification):** Keep admin and app entirely separate so they can coexist on the same machine for sync testing and so cache issues aren't masked. Rename the user app's dirs from `sow-app` → `sow`; admin keeps `sow-admin`. Do **not** rename the Python package or CLI script entry points.

## Naming Scheme

| Component | Linux config | Linux cache | Linux data | macOS data | macOS cache |
|---|---|---|---|---|---|
| **App** (sow-app) | `~/.config/sow/` | `~/.cache/sow/` | `~/.local/share/sow/` | `~/Library/Application Support/sow/` | `~/Library/Caches/sow/` |
| **Admin** (sow-admin) | `~/.config/sow-admin/` | `~/.cache/sow-admin/` | n/a | n/a | n/a |

Files inside config dirs keep their existing names: `config.toml`, `db/sow.db`, `db/songsets.db` (app only).

App output dir: `~/StreamOfWorship/output` → `~/sow/output`.

App songsets export: `~/Documents/sow-songsets` (unchanged).

POC/legacy `output_transitions`/`output_songs` in `core/config.py:21-22` → `output/transitions` and `output/songs` (nested under `output/`). Update CLAUDE.md to match.

## Environment Variables

| Var | Component | Purpose |
|---|---|---|
| `SOW_CACHE_DIR` | App | Override app cache dir |
| `SOW_DATA_DIR` | App | Override app data dir (replaces `STREAM_OF_WORSHIP_DATA_DIR`) |
| `SOW_ADMIN_CACHE_DIR` | Admin | Override admin cache dir |
| `SOW_ADMIN_DATA_DIR` | Admin | Reserved (admin currently has no data dir; not implemented unless needed) |
| `STREAM_OF_WORSHIP_DATA_DIR` | App | Deprecated legacy fallback for `SOW_DATA_DIR` |

**Resolution order (uniform, both components):** env var > TOML config file > platform default.

## Implementation

### 1. `src/stream_of_worship/core/paths.py`

- Rename all `stream_of_worship`/`StreamOfWorship` directory strings to `sow` (this is the app's data/cache).
- `get_user_data_dir()`: read `SOW_DATA_DIR` first, then fall back to `STREAM_OF_WORSHIP_DATA_DIR` (deprecated), then platform default.
- `get_cache_dir()`: read `SOW_CACHE_DIR` first (new), then platform default.
- Add `from typing import Optional` and helper:
  ```python
  def get_recording_cache_path(hash_prefix: str, cache_dir: Optional[Path] = None) -> Path:
      base = cache_dir or get_cache_dir()
      return base / hash_prefix
  ```
- Update tests in `src/stream_of_worship/tests/unit/test_paths.py` for new expected paths and new env vars; keep a legacy-fallback test for `STREAM_OF_WORSHIP_DATA_DIR`.

### 2. `src/stream_of_worship/admin/config.py`

- `get_config_dir()`: unchanged (`~/.config/sow-admin/`). Confirms admin stays separate.
- `get_config_path()`: unchanged (`config.toml`).
- Add `get_cache_dir()` admin-local helper:
  ```python
  def get_cache_dir() -> Path:
      if "SOW_ADMIN_CACHE_DIR" in os.environ:
          return Path(os.environ["SOW_ADMIN_CACHE_DIR"])
      if sys.platform == "darwin":
          return Path.home() / "Library" / "Caches" / "sow-admin"
      if sys.platform == "win32":
          base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
          return Path(base) / "sow-admin" / "cache"
      xdg = os.environ.get("XDG_CACHE_HOME")
      return (Path(xdg) if xdg else Path.home() / ".cache") / "sow-admin"
  ```
- Add `cache_dir` property on `AdminConfig`: env var > TOML `cache_dir` > `get_cache_dir()`. Implementation reads from already-loaded TOML data on `AdminConfig` (parse `cache_dir` in `AdminConfig.load()`), not by re-reading the file.

### 3. `src/stream_of_worship/app/config.py`

- `get_app_config_dir()`: `sow-app` → `sow`.
- `get_app_config_path()`: unchanged filename `config.toml`.
- `get_default_db_path()`, `get_default_songsets_db_path()`: auto-update via `get_app_config_dir()`.
- `AppConfig` defaults:
  - `cache_dir`: `field(default_factory=get_cache_dir)` from `core.paths` (was `get_app_config_dir() / "cache"`).
  - `output_dir`: `field(default_factory=lambda: Path.home() / "sow" / "output")` (was `~/StreamOfWorship/output`).
- `AppConfig.load()`: parse optional `cache_dir` and `output_dir` from TOML root or `[paths]` section. Env var `SOW_CACHE_DIR` is honored automatically through `get_cache_dir()` when no explicit value is set in TOML; if `cache_dir` is in TOML, env var still wins (apply env override after TOML parse).

### 4. `src/stream_of_worship/admin/commands/audio.py`

Replace 3 hardcoded cache locations with `config.cache_dir`:

- Line 1436 (`vocal_clean`): `config.db_path.parent / "cache"` → `config.cache_dir`.
- Line 2251 (`cache_audio`): `Path.home() / ".config" / "sow-app" / "cache"` → `config.cache_dir`.
- Line 2545 (`playback_audio`): same → `config.cache_dir`.

Vocal extraction cleanup (success path of `vocal_clean`): after R2 upload of `vocals_clean.wav` succeeds, `shutil.rmtree(cache_dir / hash_prefix / "vocal_extraction")` if it exists. Preserve on exception. Find the exact upload-success line in the function during implementation.

### 5. `src/stream_of_worship/app/app.py:72`

Replace fragile `config_dir=config.db_path.parent.parent` with `config_dir=get_app_config_dir()` (import from `app.config`). Update inline comment to `~/.config/sow`.

### 6. `src/stream_of_worship/core/config.py:21-22`

Rename POC default folders:
- `output_folder` default: `<data>/output_transitions` → `<data>/output/transitions`.
- `output_songs_folder` default: `<data>/output_songs` → `<data>/output/songs`.

Update `CLAUDE.md` "Output Directories" section to match.

### 7. Tests

- `src/stream_of_worship/tests/unit/test_paths.py`: update all expected strings to `sow`; add `SOW_CACHE_DIR`, `SOW_DATA_DIR` tests; keep one `STREAM_OF_WORSHIP_DATA_DIR` legacy-fallback test.
- `tests/admin/test_config.py:174,206`: still asserts `sow-admin` (unchanged after this rename — verify still correct).
- `tests/app/test_config.py:27`: change `"sow-app"` substring assertion to `"sow"` (and ensure it doesn't false-match `sow-admin`; assert exact dir name).
- New tests:
  - Admin `cache_dir` property honors `SOW_ADMIN_CACHE_DIR`, then TOML, then default `~/.cache/sow-admin/`.
  - App `cache_dir` honors `SOW_CACHE_DIR`, then TOML, then default `~/.cache/sow/`.
  - All 3 admin commands resolve to the same `config.cache_dir` (smoke-level: read code or assert via mock config).
  - Vocal cleanup test: mock R2 success, assert `vocal_extraction/` removed; mock failure, assert preserved.

### 8. Documentation

- New `docs/migration-v0.x-directory-rename.md`: manual move instructions for `stream_of_worship`/`StreamOfWorship` → `sow`, `sow-app` → `sow`, env var rename. Admin paths unchanged. Note that admin DB and app DB stay separate (don't merge).
- `src/stream_of_worship/admin/README.md`: paths unchanged but verify accuracy.
- `src/stream_of_worship/app/README.md`: update `~/.config/sow-app/...` references to `~/.config/sow/...`.
- `CLAUDE.md`: update "Output Directories" section for the `output/transitions`, `output/songs` rename.

## Files Modified

| File | Change |
|---|---|
| `src/stream_of_worship/core/paths.py` | Rename `stream_of_worship` → `sow`; add `SOW_CACHE_DIR`, `SOW_DATA_DIR` env vars + legacy fallback; add `get_recording_cache_path()` |
| `src/stream_of_worship/core/config.py` | Rename `output_transitions` → `output/transitions`, `output_songs` → `output/songs` |
| `src/stream_of_worship/admin/config.py` | Add `get_cache_dir()` helper, add `cache_dir` property on `AdminConfig`, parse `cache_dir` from TOML |
| `src/stream_of_worship/app/config.py` | `sow-app` → `sow`; `cache_dir` default → `core.paths.get_cache_dir()`; `output_dir` default → `~/sow/output`; parse `cache_dir`/`output_dir` from TOML |
| `src/stream_of_worship/admin/commands/audio.py` | Replace 3 hardcoded cache paths with `config.cache_dir`; add vocal_extraction cleanup on success |
| `src/stream_of_worship/app/app.py` | Line 72: switch to `get_app_config_dir()` |
| `src/stream_of_worship/tests/unit/test_paths.py` | Updated path expectations + new env-var tests |
| `tests/app/test_config.py` | Substring assertion `sow-app` → `sow` |
| `tests/admin/test_config.py` | Verify still correct (admin path unchanged) |
| `src/stream_of_worship/app/README.md` | Update path examples |
| `CLAUDE.md` | Update output dir documentation |
| `docs/migration-v0.x-directory-rename.md` | New: manual migration guide |

## Verification

1. Test suite (excluding heavyweight services):
   ```
   PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
     --ignore=tests/services/analysis \
     --ignore=services/qwen3/tests \
     --ignore=services/analysis/tests -v
   ```
2. Manual: `SOW_CACHE_DIR=/tmp/sow-test uv run --extra app sow-app run` — cache writes under `/tmp/sow-test`, not `~/.cache/sow/`.
3. Manual: `SOW_ADMIN_CACHE_DIR=/tmp/admin-test uv run --extra admin sow-admin audio cache <song_id>` — cache writes under `/tmp/admin-test`.
4. Manual: with no env vars, run `sow-admin audio vocal <id>` end-to-end and confirm `~/.cache/sow-admin/<hash>/vocal_extraction/` is deleted on success and retained on simulated failure.
5. Manual: `sow-app` and `sow-admin` use *different* `~/.cache/sow/` and `~/.cache/sow-admin/` trees; cache an asset via admin, confirm app must re-cache (validates separation).

## Out of Scope (deferred)

- Renaming the Python package `src/stream_of_worship/` or CLI entry points (`sow-admin`, `sow-app`, `stream-of-worship`).
- Auto-migration code (manual migration doc only).
- CLI `--cache-dir` flag (env var + TOML deemed sufficient).
- Renaming admin's config dir (intentionally kept separate from app).
