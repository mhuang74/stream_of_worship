# Standardize Cache & Directory Locations - Implementation Specification v2

**Status**: Draft  
**Date**: 2026-04-27  
**Priority**: Medium  
**Estimated Effort**: 1-2 days  
**Supersedes**: `specs/standardize-cache-directory-locations.md` (v1)

---

## 1. Executive Summary

This specification addresses the inconsistency in directory locations across all components of the Stream of Worship system. The v1 spec focused only on cache directories; v2 broadens scope to standardize **all** directory names and config paths to `sow`, since the codebase currently uses four different naming conventions (`stream_of_worship`, `StreamOfWorship`, `sow-admin`, `sow-app`).

**Goal**: Unify all directory names to `sow`, merge config directories, and centralize cache path resolution in the existing `core/paths.py` module.

---

## 2. Problem Statement

### 2.1 Current State

| Component/Command | Directory Type | Current Path | Issue |
|---|---|---|---|
| `core/paths.py` | Data dir | `~/.local/share/stream_of_worship/` | Inconsistent naming |
| `core/paths.py` | Cache dir | `~/.cache/stream_of_worship/` | Inconsistent naming |
| `core/paths.py` | macOS data | `~/Library/Application Support/StreamOfWorship/` | Inconsistent naming |
| `admin/config.py` | Config dir | `~/.config/sow-admin/` | Different prefix from core |
| `admin/config.py` | Config file | `~/.config/sow-admin/config.toml` | Generic name |
| `app/config.py` | Config dir | `~/.config/sow-app/` | Different prefix from admin |
| `app/config.py` | Config file | `~/.config/sow-app/config.toml` | Generic name |
| `app/config.py` | Cache dir | `~/.config/sow-app/cache` | XDG violation (cache in config dir) |
| `app/config.py` | Output dir | `~/StreamOfWorship/output` | Yet another naming variant |
| `audio.py:1436` | Vocal cache | `{db_path.parent}/cache` | Relative to admin db |
| `audio.py:2251` | Audio cache | `~/.config/sow-app/cache` | Hardcoded app path in admin |
| `audio.py:2545` | Playback cache | `~/.config/sow-app/cache` | Hardcoded app path in admin |
| `core/paths.py` | Env var | `STREAM_OF_WORSHIP_DATA_DIR` | Inconsistent with `SOW_R2_*` pattern |

### 2.2 Impact

1. **4 different directory names** for the same project (`stream_of_worship`, `StreamOfWorship`, `sow-admin`, `sow-app`)
2. **Duplicate storage**: same audio file downloaded by admin and app stored in different locations
3. **XDG violation**: cache stored in config directory (`~/.config/sow-app/cache`)
4. **Hardcoded paths** in admin commands pointing to app's config dir
5. **Config fragmentation**: two separate config dirs with identically-named `config.toml` files
6. **Env var inconsistency**: `STREAM_OF_WORSHIP_DATA_DIR` vs `SOW_R2_*`

### 2.3 Code Locations (verified)

```python
# 1. sow-admin audio vocal (src/stream_of_worship/admin/commands/audio.py:1436-1438)
cache_dir = config.db_path.parent / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)

# 2. sow-admin audio cache (src/stream_of_worship/admin/commands/audio.py:2251)
cache_dir = Path.home() / ".config" / "sow-app" / "cache"

# 3. sow-admin audio playback (src/stream_of_worship/admin/commands/audio.py:2545)
cache_dir = Path.home() / ".config" / "sow-app" / "cache"

# 4. sow-app TUI (src/stream_of_worship/app/config.py:108)
cache_dir: Path = field(default_factory=lambda: get_app_config_dir() / "cache")

# 5. sow-app output dir (src/stream_of_worship/app/config.py:109)
output_dir: Path = field(default_factory=lambda: Path.home() / "StreamOfWorship" / "output")

# 6. app config dir reference (src/stream_of_worship/app/app.py:72)
config_dir=config.db_path.parent.parent,  # ~/.config/sow-app
```

---

## 3. Design Decisions

The following decisions were made based on review of v1 spec:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Module location | Extend `core/paths.py` | Already has `get_cache_dir()`; no need for new module |
| Directory naming | `sow` everywhere | Single consistent brand across all dirs |
| Config dir merge | `~/.config/sow/` with distinct filenames | Unified parent dir, no confusion about where configs live |
| Env var naming | `SOW_CACHE_DIR`, `SOW_DATA_DIR` | Consistent with existing `SOW_R2_*` pattern |
| Config format | Read from each component's native TOML | Admin reads admin-config.toml, app reads app-config.toml |
| Backward compat | Break old paths, no auto-migration | Project is pre-release; users re-cache |
| Migration approach | Manual migration doc only | No code complexity for migration |
| CLI `--cache-dir` flag | Skip | Env var + config file sufficient |
| Vocal extraction cleanup | Delete on success | Save ~50-100MB/song; keep on failure for debugging |
| sow-app cache unification | Share `~/.cache/sow/` with admin | Single source of truth for cached assets |

---

## 4. Desired State

### 4.1 Directory Mapping

#### Linux

| Purpose | Old Path | New Path |
|---------|----------|----------|
| Data | `~/.local/share/stream_of_worship/` | `~/.local/share/sow/` |
| Cache | `~/.cache/stream_of_worship/` | `~/.cache/sow/` |
| Config (admin) | `~/.config/sow-admin/` | `~/.config/sow/` |
| Config (app) | `~/.config/sow-app/` | `~/.config/sow/` |
| Admin config file | `~/.config/sow-admin/config.toml` | `~/.config/sow/admin-config.toml` |
| App config file | `~/.config/sow-app/config.toml` | `~/.config/sow/app-config.toml` |
| DB (admin) | `~/.config/sow-admin/db/sow.db` | `~/.config/sow/db/sow.db` |
| DB (app) | `~/.config/sow-app/db/sow.db` | `~/.config/sow/db/sow.db` |
| Output | `~/StreamOfWorship/output` | `~/sow/output` |
| App songsets export | `~/Documents/sow-songsets` | `~/Documents/sow-songsets` (unchanged) |

#### macOS

| Purpose | Old Path | New Path |
|---------|----------|----------|
| Data | `~/Library/Application Support/StreamOfWorship/` | `~/Library/Application Support/sow/` |
| Cache | `~/Library/Caches/StreamOfWorship/` | `~/Library/Caches/sow/` |
| Config (admin) | `~/.config/sow-admin/` | `~/.config/sow/` |
| Config (app) | `~/.config/sow-app/` | `~/.config/sow/` |
| Output | `~/StreamOfWorship/output` | `~/sow/output` |

#### Windows

| Purpose | Old Path | New Path |
|---------|----------|----------|
| Data | `%APPDATA%\StreamOfWorship\` | `%APPDATA%\sow\` |
| Cache | `%LOCALAPPDATA%\StreamOfWorship\cache\` | `%LOCALAPPDATA%\sow\cache\` |
| Config (admin) | `%APPDATA%\sow-admin\` | `%APPDATA%\sow\` |
| Config (app) | `%APPDATA%\sow-app\` | `%APPDATA%\sow\` |
| Output | `~\StreamOfWorship\output` | `~\sow\output` |

### 4.2 Environment Variables

| Variable | Purpose | Priority |
|----------|---------|----------|
| `SOW_CACHE_DIR` | Override cache directory | Highest (for cache) |
| `SOW_DATA_DIR` | Override data directory | Highest (for data) |
| `XDG_CACHE_HOME` | Linux cache base | Used by default |
| `XDG_DATA_HOME` | Linux data base | Used by default |
| `XDG_CONFIG_HOME` | Linux config base | Used by default |

### 4.3 Cache Directory Structure

```
~/.cache/sow/                              # Unified cache root
├── {hash_prefix_1}/
│   ├── audio/
│   │   └── audio.mp3
│   ├── stems/
│   │   ├── vocals.wav
│   │   ├── drums.wav
│   │   ├── bass.wav
│   │   ├── other.wav
│   │   └── vocals_clean.wav
│   ├── lrc/
│   │   └── lyrics.lrc
│   └── vocal_extraction/                  # Deleted on success
│       ├── stage1_vocal_separation/
│       │   ├── {song}_(Vocals)_*.flac
│       │   └── {song}_(Instrumental)_*.flac
│       └── stage2_dereverb/
│           ├── {song}_(No Echo)_*.wav
│           └── {song}_(Echo)_*.wav
├── {hash_prefix_2}/
│   └── ...
├── whisper_cache/                          # Whisper model cache
└── temp/                                  # Temporary files
```

### 4.4 Config Directory Structure

```
~/.config/sow/                             # Unified config root
├── admin-config.toml                      # sow-admin configuration
├── app-config.toml                        # sow-app configuration
└── db/
    ├── sow.db                             # Shared catalog database
    └── songsets.db                        # App songsets database
```

---

## 5. Implementation Design

### 5.1 Core Changes: `src/stream_of_worship/core/paths.py`

#### 5.1.1 Rename directory names

All platform-specific directory names change from `stream_of_worship`/`StreamOfWorship` to `sow`:

```python
def get_user_data_dir() -> Path:
    """Get the platform-specific user data directory."""
    # Env var: SOW_DATA_DIR (renamed from STREAM_OF_WORSHIP_DATA_DIR)
    if "SOW_DATA_DIR" in os.environ:
        return Path(os.environ["SOW_DATA_DIR"])

    # Legacy env var support (fallback, deprecated)
    if "STREAM_OF_WORSHIP_DATA_DIR" in os.environ:
        return Path(os.environ["STREAM_OF_WORSHIP_DATA_DIR"])

    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "sow"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            path = Path.home() / "AppData" / "Roaming" / "sow"
        else:
            path = Path(appdata) / "sow"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            path = Path(xdg_data_home) / "sow"
        else:
            path = Path.home() / ".local" / "share" / "sow"

    return path


def get_cache_dir() -> Path:
    """Get the platform-specific cache directory.

    Resolution order:
    1. SOW_CACHE_DIR environment variable
    2. XDG_CACHE_HOME or platform default
    """
    # Env var override (NEW)
    if "SOW_CACHE_DIR" in os.environ:
        return Path(os.environ["SOW_CACHE_DIR"])

    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / "sow"
    elif sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if not localappdata:
            path = Path.home() / "AppData" / "Local" / "sow" / "cache"
        else:
            path = Path(localappdata) / "sow" / "cache"
    else:
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            path = Path(xdg_cache_home) / "sow"
        else:
            path = Path.home() / ".cache" / "sow"

    return path
```

#### 5.1.2 Add helper for recording cache paths

```python
def get_recording_cache_path(
    hash_prefix: str,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Get cache directory for a specific recording.

    Args:
        hash_prefix: Recording hash prefix
        cache_dir: Optional override cache directory

    Returns:
        Path to recording's cache directory
    """
    base = cache_dir or get_cache_dir()
    return base / hash_prefix
```

**Import needed**: add `from typing import Optional` at top of file.

### 5.2 Admin Config Changes: `src/stream_of_worship/admin/config.py`

#### 5.2.1 Unify config directory to `~/.config/sow/`

```python
def get_config_dir() -> Path:
    """Get the platform-specific config directory.

    Returns:
        Path to the config directory (shared: ~/.config/sow/).
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "sow"
        return Path.home() / ".config" / "sow"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "sow"
        return Path.home() / "AppData" / "Roaming" / "sow"
    else:
        return Path.home() / ".config" / "sow"


def get_config_path() -> Path:
    """Get the path to the admin config.toml file.

    Returns:
        Path to admin-config.toml
    """
    return get_config_dir() / "admin-config.toml"
```

#### 5.2.2 Add `cache_dir` property to `AdminConfig`

```python
from stream_of_worship.core.paths import get_cache_dir

class AdminConfig:
    # ... existing fields ...

    @property
    def cache_dir(self) -> Path:
        """Get unified cache directory.

        Resolution order:
        1. cache_dir setting in admin-config.toml (if set)
        2. SOW_CACHE_DIR environment variable (handled by get_cache_dir)
        3. Platform default (handled by get_cache_dir)
        """
        # Check if cache_dir is set in config file
        config_path = get_config_path()
        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
                if "cache_dir" in data:
                    return Path(data["cache_dir"])
            except (tomllib.TOMLDecodeError, KeyError, IOError):
                pass

        return get_cache_dir()
```

#### 5.2.3 Update `get_default_db_path()`

No change needed — already uses `get_config_dir() / "db" / "sow.db"`, which will now resolve to `~/.config/sow/db/sow.db`.

### 5.3 App Config Changes: `src/stream_of_worship/app/config.py`

#### 5.3.1 Unify config directory to `~/.config/sow/`

```python
def get_app_config_dir() -> Path:
    """Get the platform-specific config directory for sow-app.

    Returns:
        Path to the config directory (shared: ~/.config/sow/).
    """
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "sow"
        return Path.home() / ".config" / "sow"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "sow"
        return Path.home() / "AppData" / "Roaming" / "sow"
    else:
        return Path.home() / ".config" / "sow"


def get_app_config_path() -> Path:
    """Get the path to the app config.toml file.

    Returns:
        Path to app-config.toml
    """
    return get_app_config_dir() / "app-config.toml"
```

#### 5.3.2 Update `AppConfig` dataclass defaults

```python
from stream_of_worship.core.paths import get_cache_dir

@dataclass
class AppConfig:
    # ... existing fields ...

    # App-specific paths
    cache_dir: Path = field(default_factory=get_cache_dir)
    output_dir: Path = field(default_factory=lambda: Path.home() / "sow" / "output")
```

#### 5.3.3 Update `AppConfig.load()` to read cache_dir from TOML

Add `cache_dir` reading in the `load()` method. If set in `app-config.toml`, it overrides the default:

```toml
# app-config.toml
cache_dir = "/mnt/large-disk/sow-cache"
```

The `load()` method should check for `cache_dir` key in the TOML data and set it if present. The `SOW_CACHE_DIR` env var takes precedence over the config file setting (handled by `get_cache_dir()` when no explicit `cache_dir` is set in config).

### 5.4 Audio Command Changes: `src/stream_of_worship/admin/commands/audio.py`

#### 5.4.1 `vocal_clean()` — Line 1436

**Current**:
```python
cache_dir = config.db_path.parent / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

**New**:
```python
cache_dir = config.cache_dir
cache_dir.mkdir(parents=True, exist_ok=True)
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

#### 5.4.2 `cache_audio()` — Line 2251

**Current**:
```python
cache_dir = Path.home() / ".config" / "sow-app" / "cache"
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

**New**:
```python
cache_dir = config.cache_dir
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

#### 5.4.3 `playback_audio()` — Line 2545

**Current**:
```python
cache_dir = Path.home() / ".config" / "sow-app" / "cache"
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

**New**:
```python
cache_dir = config.cache_dir
cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
```

#### 5.4.4 Vocal extraction cleanup

After successful upload of `vocals_clean.wav` to R2, delete the `vocal_extraction/` intermediate directory:

```python
import shutil

# After successful R2 upload of vocals_clean.wav
output_dir = cache_dir / hash_prefix / "vocal_extraction"
if output_dir.exists():
    shutil.rmtree(output_dir)
    console.print(f"[dim]Cleaned up intermediate files: {output_dir}[/dim]")
```

This should be added at the end of the success path in `vocal_clean()`. On failure (exception), the intermediate files are preserved for debugging.

### 5.5 App Init Changes: `src/stream_of_worship/app/app.py`

#### 5.5.1 Line 72 — Update `config_dir` reference

**Current**:
```python
config_dir=config.db_path.parent.parent,  # ~/.config/sow-app
```

**New**:
```python
config_dir=config.db_path.parent.parent,  # ~/.config/sow
```

This is a comment-only change since the path is already computed from `db_path` (which now resolves to `~/.config/sow/db/sow.db`).

---

## 6. Configuration Interface

### 6.1 Environment Variables

```bash
# Override cache directory (all components)
export SOW_CACHE_DIR=/mnt/large-disk/sow-cache

# Override data directory (all components)
export SOW_DATA_DIR=/mnt/large-disk/sow-data

# Single command override
SOW_CACHE_DIR=/tmp/sow-cache sow-admin audio cache <song_id>
```

### 6.2 Configuration Files

**admin-config.toml** (`~/.config/sow/admin-config.toml`):
```toml
[service]
analysis_url = "http://localhost:8000"

[r2]
bucket = "sow-audio"
endpoint_url = "..."
region = "auto"

[turso]
database_url = "..."

[database]
path = "~/.config/sow/db/sow.db"

# Optional: override cache directory
cache_dir = "/mnt/large-disk/sow-cache"
```

**app-config.toml** (`~/.config/sow/app-config.toml`):
```toml
# Optional: override cache directory
cache_dir = "/mnt/large-disk/sow-cache"
```

### 6.3 Resolution Priority

For cache directory:
1. `SOW_CACHE_DIR` environment variable
2. `cache_dir` in component's TOML config
3. Platform default (`~/.cache/sow/` on Linux)

For data directory:
1. `SOW_DATA_DIR` environment variable
2. `STREAM_OF_WORSHIP_DATA_DIR` environment variable (deprecated fallback)
3. Platform default (`~/.local/share/sow/` on Linux)

---

## 7. Migration Guide

A standalone document will be created at `docs/migration-v0.x-directory-rename.md` with manual steps for existing users. No auto-migration code will be implemented.

### 7.1 Migration Steps (Linux)

```bash
# 1. Stop all SOW components

# 2. Move data directory
mv ~/.local/share/stream_of_worship ~/.local/share/sow

# 3. Move cache directory
mv ~/.cache/stream_of_worship ~/.cache/sow

# 4. Create unified config directory
mkdir -p ~/.config/sow/db

# 5. Move admin config
cp ~/.config/sow-admin/config.toml ~/.config/sow/admin-config.toml

# 6. Move app config
cp ~/.config/sow-app/config.toml ~/.config/sow/app-config.toml

# 7. Move database files
cp ~/.config/sow-admin/db/sow.db ~/.config/sow/db/sow.db
cp ~/.config/sow-app/db/sow.db ~/.config/sow/db/sow.db
# Note: if both db files exist, decide which to keep (they should be in sync via Turso)

# 8. Move app songsets DB
cp ~/.config/sow-app/db/songsets.db ~/.config/sow/db/songsets.db

# 9. Move output directory
mv ~/StreamOfWorship/output ~/sow/output

# 10. Update environment variables in shell config
# Change: export STREAM_OF_WORSHIP_DATA_DIR=...
# To:     export SOW_DATA_DIR=...

# 11. Remove old directories (after verifying everything works)
rm -rf ~/.config/sow-admin ~/.config/sow-app ~/StreamOfWorship

# 12. Update admin-config.toml database.path
# Change: path = "~/.config/sow-admin/db/sow.db"
# To:     path = "~/.config/sow/db/sow.db"
```

### 7.2 Migration Steps (macOS)

Same as Linux with these differences:
```bash
# Data dir
mv ~/Library/Application\ Support/StreamOfWorship ~/Library/Application\ Support/sow

# Cache dir
mv ~/Library/Caches/StreamOfWorship ~/Library/Caches/sow

# Config and output dirs: same as Linux
```

---

## 8. Testing Requirements

### 8.1 Unit Tests: `src/stream_of_worship/tests/unit/test_paths.py`

Update all expected paths from `stream_of_worship`/`StreamOfWorship` to `sow`:

```python
class TestGetUserDataDir:
    def test_env_override(self):
        with patch.dict(os.environ, {"SOW_DATA_DIR": "/custom/path"}):
            result = get_user_data_dir()
            assert result == Path("/custom/path")

    def test_legacy_env_override(self):
        """STREAM_OF_WORSHIP_DATA_DIR still works (deprecated)."""
        with patch.dict(os.environ, {"STREAM_OF_WORSHIP_DATA_DIR": "/legacy/path"}, clear=False):
            # Remove new var if present
            os.environ.pop("SOW_DATA_DIR", None)
            result = get_user_data_dir()
            assert result == Path("/legacy/path")

    def test_linux_paths(self):
        # ...
        expected = Path.home() / ".local" / "share" / "sow"
        # ...

    def test_macos_paths(self):
        # ...
        expected = Path.home() / "Library" / "Application Support" / "sow"
        # ...

    def test_windows_paths(self):
        # ...
        expected = Path(appdata) / "sow"
        # ...


class TestGetCacheDir:
    def test_env_override(self):
        with patch.dict(os.environ, {"SOW_CACHE_DIR": "/custom/cache"}):
            result = get_cache_dir()
            assert result == Path("/custom/cache")

    def test_linux_cache(self):
        # ...
        expected = Path.home() / ".cache" / "sow"
        # ...

    def test_macos_cache(self):
        # ...
        expected = Path.home() / "Library" / "Caches" / "sow"
        # ...

    def test_windows_cache(self):
        # ...
        expected = Path(localappdata) / "sow" / "cache"
        # ...
```

### 8.2 Integration Tests: Admin Config Consistency

Verify all admin commands use the same cache directory:

```python
class TestCacheConsistency:
    """Verify all commands use unified cache location."""

    def test_vocal_command_uses_config_cache_dir(self, mock_config):
        """vocal command should use config.cache_dir."""
        pass

    def test_cache_command_uses_config_cache_dir(self, mock_config):
        """cache command should use config.cache_dir."""
        pass

    def test_playback_command_uses_config_cache_dir(self, mock_config):
        """playback command should use config.cache_dir."""
        pass
```

### 8.3 Config Path Tests

Verify admin and app config paths point to the same directory with different filenames:

```python
class TestConfigPaths:
    def test_admin_and_app_share_config_dir(self):
        from stream_of_worship.admin.config import get_config_dir as admin_config_dir
        from stream_of_worship.app.config import get_app_config_dir
        assert admin_config_dir() == get_app_config_dir()

    def test_admin_config_filename(self):
        from stream_of_worship.admin.config import get_config_path
        assert get_config_path().name == "admin-config.toml"

    def test_app_config_filename(self):
        from stream_of_worship.app.config import get_app_config_path
        assert get_app_config_path().name == "app-config.toml"
```

### 8.4 Vocal Cleanup Test

```python
class TestVocalExtractionCleanup:
    def test_cleanup_on_success(self, tmp_path):
        """vocal_extraction dir is removed after successful upload."""
        pass

    def test_no_cleanup_on_failure(self, tmp_path):
        """vocal_extraction dir is preserved when upload fails."""
        pass
```

---

## 9. Rollout Plan

### Phase 1: Core Path Changes (Day 1, ~2 hours)

- [ ] Update `core/paths.py`: rename dirs to `sow`, add `SOW_CACHE_DIR` / `SOW_DATA_DIR` env vars, add legacy fallback
- [ ] Update `admin/config.py`: `get_config_dir()` → `sow`, `get_config_path()` → `admin-config.toml`, add `cache_dir` property
- [ ] Update `app/config.py`: `get_app_config_dir()` → `sow`, `get_app_config_path()` → `app-config.toml`, `cache_dir` default, `output_dir` default
- [ ] Update `admin/commands/audio.py`: replace 3 hardcoded cache paths, add vocal cleanup
- [ ] Update `app/app.py`: comment fix

### Phase 2: Test Updates (Day 1, ~1 hour)

- [ ] Update `tests/unit/test_paths.py`: all expected paths → `sow`
- [ ] Add `SOW_CACHE_DIR` env var tests
- [ ] Add `SOW_DATA_DIR` env var tests
- [ ] Add config path consistency tests
- [ ] Add vocal cleanup tests

### Phase 3: Migration Doc (Day 1, ~30 min)

- [ ] Create `docs/migration-v0.x-directory-rename.md`

### Phase 4: Verification (Day 1, ~30 min)

- [ ] Run full test suite
- [ ] Verify `sow-admin` commands work with new paths
- [ ] Verify `sow-app` TUI works with new paths
- [ ] Test `SOW_CACHE_DIR` env var override

---

## 10. Files Changed Summary

| File | Changes |
|------|---------|
| `src/stream_of_worship/core/paths.py` | Rename dirs to `sow`, add `SOW_CACHE_DIR`/`SOW_DATA_DIR` env vars, add `get_recording_cache_path()`, legacy env var fallback |
| `src/stream_of_worship/admin/config.py` | `get_config_dir()` → `sow`, `get_config_path()` → `admin-config.toml`, add `cache_dir` property |
| `src/stream_of_worship/app/config.py` | `get_app_config_dir()` → `sow`, `get_app_config_path()` → `app-config.toml`, `cache_dir` default → `get_cache_dir()`, `output_dir` default → `~/sow/output` |
| `src/stream_of_worship/admin/commands/audio.py` | Replace 3 hardcoded cache paths (lines 1436, 2251, 2545), add vocal cleanup on success |
| `src/stream_of_worship/app/app.py` | Comment update (line 72) |
| `src/stream_of_worship/tests/unit/test_paths.py` | Update all expected paths to `sow`, add env var tests |
| `docs/migration-v0.x-directory-rename.md` | New: manual migration guide |

---

## 11. Success Criteria

- [ ] All directory names use `sow` consistently across all platforms
- [ ] Admin and app share `~/.config/sow/` with distinct config filenames
- [ ] `sow-admin audio vocal`, `sow-admin audio cache`, and `sow-admin audio playback` share the same cache directory
- [ ] `sow-app` TUI uses `~/.cache/sow/` (not `~/.config/sow-app/cache`)
- [ ] `SOW_CACHE_DIR` env var overrides cache directory for all components
- [ ] `SOW_DATA_DIR` env var overrides data directory (replaces `STREAM_OF_WORSHIP_DATA_DIR`)
- [ ] `STREAM_OF_WORSHIP_DATA_DIR` still works as deprecated fallback
- [ ] `cache_dir` can be set in `admin-config.toml` and `app-config.toml`
- [ ] Vocal extraction intermediate files are cleaned up on success
- [ ] All unit tests pass with updated path expectations
- [ ] Migration guide document exists
