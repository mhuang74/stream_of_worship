# Standardize Cache & Directory Locations — Reviewed Plan

## Context

The codebase currently has cache directories scattered across mismatched paths and naming schemes:
- `core/paths.py` defines `~/.cache/stream_of_worship/` (Linux) / `~/Library/Caches/StreamOfWorship/` (macOS) but **neither the app nor admin actually use it**.
- The app stores cache *inside its config dir* (`~/.config/sow-app/cache/`), violating XDG.
- The admin CLI hardcodes three different cache locations in `admin/commands/audio.py` — two of which point at the **app's** config dir (cross-component leak).
- POC scripts use at least 6 distinct cache roots (some user-specific absolute paths like `/Users/mhuang/.cache/whisper`).
- Logs live under `cache_dir/logs`, so they'd silently be auto-purged with cache.

This plan extends `specs/standardize-cache-directory-locations-v3.md` to cover the gaps that v3 missed, per the user's clarification: **single app cache, single admin cache, and POC scripts must use the standard for song/audio data** (model-weight caches stay separate).

Outcome: one cache dir per component, no cross-component reads, logs moved out of cache, POC song/audio caches funneled through the same helper, env-var + TOML override path on both components.

## Naming Scheme

| Component | Linux config | Linux cache | Linux data | macOS data | macOS cache |
|---|---|---|---|---|---|
| **App** | `~/.config/sow/` | `~/.cache/sow/` | `~/.local/share/sow/` | `~/Library/Application Support/sow/` | `~/Library/Caches/sow/` |
| **Admin** | `~/.config/sow-admin/` | `~/.cache/sow-admin/` | n/a | n/a | n/a |

- Whisper cache subdir: `whisper_cache` → `whisper` (matches services).
- Logs: `cache_dir/logs` → `data_dir/logs` (decouple from cache so they're not purged).
- App output: `~/StreamOfWorship/output` → `~/sow/output`.
- POC `output_transitions`/`output_songs` → `output/transitions`, `output/songs`.

## Environment Variables

| Var | Component | Purpose |
|---|---|---|
| `SOW_CACHE_DIR` | App | Override app cache dir |
| `SOW_DATA_DIR` | App | Override app data dir (replaces `STREAM_OF_WORSHIP_DATA_DIR`) |
| `SOW_ADMIN_CACHE_DIR` | Admin | Override admin cache dir |
| `STREAM_OF_WORSHIP_DATA_DIR` | App | Deprecated legacy fallback for `SOW_DATA_DIR` |

Resolution order (uniform): env var > TOML config file > platform default.

## Implementation

### 1. `src/stream_of_worship/core/paths.py`

- Rename all `stream_of_worship`/`StreamOfWorship` directory strings to `sow`.
- `get_user_data_dir()`: read `SOW_DATA_DIR` first, then legacy `STREAM_OF_WORSHIP_DATA_DIR`, then platform default.
- `get_cache_dir()`: read `SOW_CACHE_DIR` first, then platform default.
- `get_whisper_cache_path()` (line 175–181): rename subdir `whisper_cache` → `whisper`.
- `ensure_directories()` (line 116): update `cache_dir / "whisper_cache"` → `cache_dir / "whisper"`.
- Add helper:
  ```python
  def get_recording_cache_path(hash_prefix: str, cache_dir: Optional[Path] = None) -> Path:
      base = cache_dir or get_cache_dir()
      return base / hash_prefix
  ```

### 2. `src/stream_of_worship/admin/config.py`

- `get_config_dir()` and `get_config_path()`: unchanged (`~/.config/sow-admin/`, `config.toml`).
- Add `get_cache_dir()` admin-local helper with resolution `SOW_ADMIN_CACHE_DIR` env > platform default (`~/.cache/sow-admin/` on Linux, `~/Library/Caches/sow-admin/` on macOS, `%LOCALAPPDATA%/sow-admin/cache` on Windows).
- Add `cache_dir: Path` field to `AdminConfig` (currently absent). Resolution in `AdminConfig.load()`: parsed TOML `cache_dir`, then env override after parse, default `get_cache_dir()`.

### 3. `src/stream_of_worship/app/config.py`

- `get_app_config_dir()`: `sow-app` → `sow`.
- `AppConfig.cache_dir` default: `field(default_factory=get_cache_dir)` from `core.paths` (was `get_app_config_dir() / "cache"`).
- `AppConfig.output_dir` default: `Path.home() / "sow" / "output"`.
- **New** `AppConfig.log_dir` field: `field(default_factory=lambda: get_user_data_dir() / "logs")`. Decouples logs from cache so they aren't auto-purged.
- `AppConfig.load()`: parse optional `cache_dir`, `output_dir`, `log_dir` from TOML root or `[paths]`. After parse, apply env overrides (`SOW_CACHE_DIR` wins over TOML `cache_dir`).

### 4. `src/stream_of_worship/app/main.py`

- Lines 179, 184, 462: replace `config.cache_dir / "logs"` with `config.log_dir`.

### 5. `src/stream_of_worship/admin/commands/audio.py`

Replace the 3 hardcoded cache references with `config.cache_dir`:

- Line 1436 (`vocal_clean`): `config.db_path.parent / "cache"` → `config.cache_dir`.
- Line 2251 (`cache_audio`): `Path.home() / ".config" / "sow-app" / "cache"` → `config.cache_dir`.
- Line 2545 (`playback_audio`): same → `config.cache_dir`.
- Line 2250 (docstring): update `~/.config/sow-app/cache` reference to `~/.cache/sow-admin/`.

Vocal extraction cleanup (success path of `vocal_clean`): after R2 upload of `vocals_clean.wav` succeeds, `shutil.rmtree(cache_dir / hash_prefix / "vocal_extraction")`. Preserve on exception. Locate exact upload-success line during implementation.

### 6. `src/stream_of_worship/app/app.py:72`

Replace `config_dir=config.db_path.parent.parent` with `config_dir=get_app_config_dir()` (import from `app.config`). Update inline comment to `~/.config/sow`.

### 7. `src/stream_of_worship/core/config.py:21-22`

- `output_folder` default: `<data>/output_transitions` → `<data>/output/transitions`.
- `output_songs_folder` default: `<data>/output_songs` → `<data>/output/songs`.

### 8. POC scripts (song/audio caches only)

Per clarification: route song/audio caches through `core.paths.get_cache_dir()`. **Leave ML model weight caches alone** (`~/.cache/whisper`, `~/.cache/qwen3_asr`, `~/.cache/qwen3_tts`, `~/.cache/huggingface`).

Update:
- `poc/test_miniaudio.py:8` — `~/.config/sow-app/cache/c105e75972f7/audio/audio.mp3` → `get_cache_dir() / "c105e75972f7" / "audio" / "audio.mp3"`.
- `poc/score_lrc_quality.py:898` — `~/.cache/stream-of-worship` (song-asset cache) → `get_cache_dir()`.
- `poc/experiment_lrc_signals.py:64,132` — `~/.cache/stream-of-worship` and `~/.config/sow-app/cache` → `get_cache_dir()`.
- `poc/poc_analysis_allinone.py:71,797` — `CACHE_DIR = OUTPUT_DIR / "cache"` is a POC-output artifact dir under `poc_output_allinone/`, **not** a song/audio cache — leave as-is (clearly tied to the script's own output layout).

Leave unchanged (model-weight caches, per user direction):
- `poc/test_whisper.py:25` (hardcoded `/Users/mhuang/.cache/whisper` — whisper weights; flag in migration doc that this remains a per-machine absolute path).
- `poc/gen_lrc_qwen3_asr_local.py:1628` (`~/.cache/qwen3_asr`).
- `poc/gen_lrc_qwen3_force_align.py` (HF cache helpers at lines 41–70, 250–264).
- `poc/score_lrc_quality.py:441,617` (`~/.cache/qwen3_tts`).
- `poc/experiment_lrc_signals.py:639` (`~/.cache/qwen3_tts`).

Already correct (use `AppConfig.cache_dir`): `poc/utils.py:154`, `poc/eval_lrc.py:2611`.

### 9. Tests

- `src/stream_of_worship/tests/unit/test_paths.py`: update path expectations to `sow`; add `SOW_CACHE_DIR`, `SOW_DATA_DIR` tests; keep one `STREAM_OF_WORSHIP_DATA_DIR` legacy-fallback test; update whisper subdir assertion (`whisper_cache` → `whisper`).
- `src/stream_of_worship/tests/unit/test_lrc_generator.py:115–118`, `tests/integration/test_lrc_pipeline.py:291,339,380,410,448`: verify `get_whisper_cache_path` patches still work post-rename.
- `tests/app/test_config.py:27`: change `"sow-app"` substring assertion to exact dir name `"sow"` (avoid false-match with `sow-admin`).
- `tests/admin/test_config.py:174,206`: still asserts `sow-admin` (verify still passing — unchanged).
- New tests:
  - Admin `cache_dir` honors `SOW_ADMIN_CACHE_DIR` > TOML > default.
  - App `cache_dir` honors `SOW_CACHE_DIR` > TOML > default.
  - App `log_dir` defaults to `data_dir / "logs"`, NOT under cache.
  - All 3 admin commands resolve to the same `config.cache_dir`.
  - Vocal cleanup test: mock R2 success → `vocal_extraction/` removed; mock failure → preserved.

### 10. Documentation

- New `docs/migration-v0.x-directory-rename.md`: manual move instructions for `stream_of_worship`/`StreamOfWorship` → `sow`, `sow-app` → `sow`, env var rename, whisper subdir rename, log location change. Note admin DB and app DB stay separate (don't merge). Note POC model-weight caches intentionally remain untouched.
- `src/stream_of_worship/app/README.md`: update `~/.config/sow-app/...` references to `~/.config/sow/...`.
- `CLAUDE.md`: update "Output Directories" section for `output/transitions`, `output/songs` rename.

## Files Modified

| File | Change |
|---|---|
| `src/stream_of_worship/core/paths.py` | Rename `stream_of_worship`/`StreamOfWorship` → `sow`; `whisper_cache` → `whisper`; add `SOW_CACHE_DIR`, `SOW_DATA_DIR` env support + legacy fallback; add `get_recording_cache_path()` |
| `src/stream_of_worship/core/config.py` | Rename `output_transitions` → `output/transitions`, `output_songs` → `output/songs` |
| `src/stream_of_worship/admin/config.py` | Add `get_cache_dir()` helper, add `cache_dir` field on `AdminConfig`, parse `cache_dir` from TOML |
| `src/stream_of_worship/app/config.py` | `sow-app` → `sow`; `cache_dir` default → `core.paths.get_cache_dir()`; `output_dir` default → `~/sow/output`; new `log_dir` field defaulting to `data_dir/logs`; parse new fields from TOML |
| `src/stream_of_worship/app/main.py` | Lines 179, 184, 462: `config.cache_dir / "logs"` → `config.log_dir` |
| `src/stream_of_worship/app/app.py` | Line 72: switch to `get_app_config_dir()` |
| `src/stream_of_worship/admin/commands/audio.py` | Replace 3 hardcoded cache paths with `config.cache_dir`; update docstring at line 2250; add vocal_extraction cleanup on success |
| `src/stream_of_worship/tests/unit/test_paths.py` | Updated path expectations + new env-var tests + whisper subdir |
| `tests/app/test_config.py` | Substring assertion `sow-app` → `sow`; add `log_dir` test |
| `tests/admin/test_config.py` | Verify still correct + add `cache_dir` resolution tests |
| `poc/test_miniaudio.py` | Use `get_cache_dir()` |
| `poc/score_lrc_quality.py` | Replace `~/.cache/stream-of-worship` (song asset) with `get_cache_dir()` |
| `poc/experiment_lrc_signals.py` | Replace `~/.cache/stream-of-worship` and `~/.config/sow-app/cache` with `get_cache_dir()` |
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
2. `SOW_CACHE_DIR=/tmp/sow-test uv run --extra app sow-app run` — cache writes under `/tmp/sow-test`, not `~/.cache/sow/`.
3. `SOW_ADMIN_CACHE_DIR=/tmp/admin-test uv run --extra admin sow-admin audio cache <song_id>` — cache writes under `/tmp/admin-test`.
4. With no env vars, `sow-admin audio vocal <id>` end-to-end: confirm `~/.cache/sow-admin/<hash>/vocal_extraction/` is deleted on success and retained on simulated failure.
5. Confirm `sow-app` and `sow-admin` use *different* `~/.cache/sow/` and `~/.cache/sow-admin/` trees: cache an asset via admin, confirm app must re-cache (validates separation).
6. Confirm app logs land at `~/.local/share/sow/logs/` (Linux) — NOT under `~/.cache/sow/`.
7. Run a song-touching POC (e.g. `poc/experiment_lrc_signals.py`) and confirm it now reads/writes `~/.cache/sow/` rather than `~/.cache/stream-of-worship/`.
8. Verify model-weight POC caches (`~/.cache/whisper`, `~/.cache/qwen3_asr`, `~/.cache/qwen3_tts`) are untouched after the change.

## Out of Scope

- Renaming the Python package `src/stream_of_worship/` or CLI entry points (`sow-admin`, `sow-app`, `stream-of-worship`).
- Auto-migration code (manual migration doc only).
- CLI `--cache-dir` flag (env var + TOML sufficient).
- Renaming admin's config dir (intentionally kept separate).
- POC ML model-weight caches (`~/.cache/whisper`, `~/.cache/qwen3_asr`, `~/.cache/qwen3_tts`, `~/.cache/huggingface`) — left as-is per user direction.
- `poc/test_whisper.py:25` hardcoded user-absolute path (`/Users/mhuang/.cache/whisper`) — flagged in migration doc but not refactored.
- Service caches under `services/analysis/`, `services/qwen3/` — Docker-isolated, separate from host paths.
