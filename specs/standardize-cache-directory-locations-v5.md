# Standardize Cache & Directory Locations — v5

## Context

This plan supersedes `specs/standardize-cache-directory-locations-v4.md` with a simpler, more consistent approach:

1. **Full names in paths**: Use `stream-of-worship` and `stream-of-worship-admin` instead of abbreviations `sow` and `sow-admin`
2. **Working directory concept**: User App has a single `working_dir` (default `~/stream-of-worship`) under which all output artifacts go
3. **No cache override**: Cache is always at standard platform location, not configurable
4. **Rename export/import to backup/restore**: Songset JSON operations renamed for clarity
5. **macOS/Linux consistency**: Same paths on both platforms (XDG-compliant)

## Directory Map (macOS & Linux unified)

| Component | Config | Cache | Working Dir | Logs | Output | Backup |
|-----------|--------|-------|-------------|------|--------|--------|
| **Admin CLI** | `~/.config/stream-of-worship-admin` | `~/.cache/stream-of-worship-admin` | N/A | N/A | N/A | N/A |
| **User App** | `~/.config/stream-of-worship` | `~/.cache/stream-of-worship` | `~/stream-of-worship` | `<wd>/logs` | `<wd>/output` | `<wd>/backup` |

### Key Points

- **Config**: Always at `~/.config/<name>` — not configurable
- **Cache**: Always at `~/.cache/<name>` — not configurable
- **Working Dir**: Only configurable path for User App (via `working_dir` in TOML)
- **Derived paths**: `log_dir`, `output_dir`, `songsets_backup_dir` are computed from `working_dir`, not stored separately
- **macOS note**: Cache uses `~/.cache/` instead of `~/Library/Caches/` for consistency with Linux; this departs from Apple platform conventions but simplifies cross-platform support

## Config File Structure

### Admin CLI (`~/.config/stream-of-worship-admin/config.toml`)

```toml
[service]
analysis_url = "http://localhost:8000"

[r2]
bucket = "stream-of-worship"
endpoint_url = ""
region = "auto"

[database]
url = ""
```

No `[paths]` section. Cache is always at `~/.cache/stream-of-worship-admin`.

**Note:** R2 bucket default changes from `sow-audio` to `stream-of-worship`. Existing configs with explicit bucket setting are preserved.

### User App (`~/.config/stream-of-worship/config.toml`)

```toml
[database]
url = ""

[songsets]
backup_retention = 5

[r2]
bucket = "stream-of-worship"
endpoint_url = ""
region = "auto"

[app]
working_dir = "~/stream-of-worship"
preview_buffer_ms = 500
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
```

Only `working_dir` is configurable for paths. Cache is always at `~/.cache/stream-of-worship`.

**Note:** R2 bucket default changes from `sow-audio` to `stream-of-worship`. Existing configs with explicit bucket setting are preserved.

## Implementation

### 1. `src/stream_of_worship/core/paths.py`

**Changes:**
- `get_cache_dir()`: On macOS, change from `~/Library/Caches/sow` to `~/.cache/stream-of-worship`
- `get_cache_dir()`: On Linux, change from `~/.cache/sow` to `~/.cache/stream-of-worship`
- Update docstrings to reflect new paths
- Keep `get_user_data_dir()` as-is (used by legacy components only)

```python
def get_cache_dir() -> Path:
    """Get the platform-specific cache directory for the app.

    Resolution order: SOW_CACHE_DIR env > platform default.

    Examples:
        >>> get_cache_dir()  # doctest: +SKIP
        Path('/home/user/.cache/stream-of-worship')  # Linux
        Path('/Users/user/.cache/stream-of-worship')  # macOS
        Path('C:\\Users\\user\\AppData\\Local\\stream-of-worship\\cache')  # Windows
    """
    if "SOW_CACHE_DIR" in os.environ:
        return Path(os.environ["SOW_CACHE_DIR"])

    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if not localappdata:
            return Path.home() / "AppData" / "Local" / "stream-of-worship" / "cache"
        return Path(localappdata) / "stream-of-worship" / "cache"
    else:
        # macOS and Linux: XDG_CACHE_HOME or ~/.cache
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home) / "stream-of-worship"
        return Path.home() / ".cache" / "stream-of-worship"
```

### 2. `src/stream_of_worship/admin/config.py`

**Changes:**
- `get_config_dir()`: Change from `~/.config/sow-admin` to `~/.config/stream-of-worship-admin`
- `get_cache_dir()`: Change from `~/.cache/sow-admin` to `~/.cache/stream-of-worship-admin`
- Remove `cache_dir` field from `AdminConfig` dataclass
- Remove `[paths]` section from TOML save/load
- Update `config show` to display cache dir from standalone function, not from config object

```python
def get_config_dir() -> Path:
    """Get the platform-specific config directory for sow-admin."""
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "stream-of-worship-admin"
        return Path.home() / ".config" / "stream-of-worship-admin"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "stream-of-worship-admin"
        return Path.home() / "AppData" / "Roaming" / "stream-of-worship-admin"
    else:
        return Path.home() / ".config" / "stream-of-worship-admin"


def get_cache_dir() -> Path:
    """Get the platform-specific cache directory for sow-admin."""
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            return Path(xdg_cache) / "stream-of-worship-admin"
        return Path.home() / ".cache" / "stream-of-worship-admin"
    elif sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            return Path(localappdata) / "stream-of-worship-admin" / "cache"
        return Path.home() / "AppData" / "Local" / "stream-of-worship-admin" / "cache"
    else:
        return Path.home() / ".cache" / "stream-of-worship-admin"


@dataclass
class AdminConfig:
    analysis_url: str = "http://localhost:8000"
    r2_bucket: str = "stream-of-worship"
    r2_endpoint_url: str = ""
    r2_region: str = "auto"
    database_url: str = ""
    # cache_dir removed - always use get_cache_dir()
```

### 3. `src/stream_of_worship/admin/main.py`

**Changes:**
- Update `config show` panel to display cache dir from `get_cache_dir()` function
- Remove `cache_dir` from the config object display

```python
from stream_of_worship.admin.config import get_config_path, ensure_config_exists, get_cache_dir

# In config show action:
table = Panel.fit(
    f"[cyan]Database URL:[/cyan] {cfg.database_url or '[not set]'}\n"
    f"[cyan]R2 Bucket:[/cyan] {cfg.r2_bucket}\n"
    f"[cyan]R2 Endpoint:[/cyan] {cfg.r2_endpoint_url or '[not set]'}\n"
    f"[cyan]R2 Region:[/cyan] {cfg.r2_region}\n"
    f"[dim]──────────────────────[/dim]\n"
    f"[cyan]Analysis URL:[/cyan] {cfg.analysis_url}\n"
    f"[dim]──────────────────────[/dim]\n"
    f"[cyan]Cache dir:[/cyan] {get_cache_dir()}",  # From function, not config object
    title="Configuration",
    border_style="green",
)
```

### 4. `src/stream_of_worship/app/config.py`

**Changes:**
- `get_app_config_dir()`: Change from `~/.config/sow` to `~/.config/stream-of-worship`
- Remove `cache_dir` field from `AppConfig` dataclass
- Add `working_dir: Path` field (default `~/stream-of-worship`)
- Remove `log_dir`, `output_dir`, `songsets_export_dir` as stored fields
- Add `@property` methods: `log_dir`, `output_dir`, `songsets_backup_dir`
- Remove `get_default_export_dir()` function
- Remove import of `_get_core_data_dir`
- Update TOML load: read `working_dir` from `[app]`, ignore old keys for backward compat
- Update TOML save: write `working_dir` to `[app]`, remove `log_dir`/`output_dir`/`export_dir`
- Update `ensure_directories()` to use working_dir-derived paths
- Update `set()`/`get()` to support `working_dir`

```python
from stream_of_worship.core.paths import get_cache_dir as _get_core_cache_dir


def get_app_config_dir() -> Path:
    """Get the platform-specific config directory for sow-app."""
    if sys.platform == "darwin" or sys.platform == "linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "stream-of-worship"
        return Path.home() / ".config" / "stream-of-worship"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "stream-of-worship"
        return Path.home() / "AppData" / "Roaming" / "stream-of-worship"
    else:
        return Path.home() / ".config" / "stream-of-worship"


@dataclass
class AppConfig:
    database_url: str = ""
    songsets_backup_retention: int = 5
    r2_bucket: str = "stream-of-worship"
    r2_endpoint_url: str = ""
    r2_region: str = "auto"
    working_dir: Path = field(default_factory=lambda: Path.home() / "stream-of-worship")
    preview_buffer_ms: int = 500
    preview_volume: float = 0.8
    default_gap_beats: float = 2.0
    default_video_template: str = "dark"
    default_video_resolution: str = "1080p"

    @property
    def cache_dir(self) -> Path:
        """Cache directory - always at standard platform location."""
        return _get_core_cache_dir()

    @property
    def log_dir(self) -> Path:
        """Log directory - derived from working_dir."""
        return self.working_dir / "logs"

    @property
    def output_dir(self) -> Path:
        """Output directory - derived from working_dir."""
        return self.working_dir / "output"

    @property
    def songsets_backup_dir(self) -> Path:
        """Songset backup directory - derived from working_dir."""
        return self.working_dir / "backup"

    # Backward compat alias
    @property
    def songsets_export_dir(self) -> Path:
        """Deprecated: Use songsets_backup_dir instead."""
        return self.songsets_backup_dir

    def ensure_directories(self) -> None:
        """Ensure all configured directories exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.songsets_backup_dir.mkdir(parents=True, exist_ok=True)
```

### 5. `src/stream_of_worship/app/main.py`

**Changes:**
- Rename `songsets` subcommand help from "export/import" to "backup/restore"
- Rename `export` command → `backup`
- Rename `export-all` command → `backup-all`
- Rename `import` command → `restore`
- Update `songsets_export_dir` → `songsets_backup_dir`
- Update config display panel to show `working_dir` and `backup_dir`
- Remove `cache_dir` from panel (it's always at standard location)

```python
# Songsets subcommand group
songsets_app = typer.Typer(help="Songset backup/restore operations")
app.add_typer(songsets_app, name="songsets")


@songsets_app.command("backup")
def backup_songset(
    songset_id: str = typer.Argument(..., help="Songset ID to backup"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path (default: <name>_<id>.json in backup dir)",
    ),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Backup a songset to JSON file."""
    # ... implementation using config.songsets_backup_dir


@songsets_app.command("backup-all")
def backup_all_songsets(
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: backup dir from config)",
    ),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Backup all songsets to JSON files."""
    # ... implementation


@songsets_app.command("restore")
def restore_songset(
    input_file: Path = typer.Argument(..., help="JSON file to restore", exists=True),
    on_conflict: str = typer.Option(
        "rename",
        "--on-conflict",
        help="How to handle conflicts: rename, replace, or skip",
    ),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Restore a songset from JSON file."""
    # ... implementation using io_service.restore_songset()


# In config show action:
panel = Panel.fit(
    f"[cyan]Database URL:[/cyan] {cfg.database_url or '[not set]'}\n"
    f"[cyan]R2 Bucket:[/cyan] {cfg.r2_bucket}\n"
    f"[cyan]R2 Endpoint:[/cyan] {cfg.r2_endpoint_url or '[not set]'}\n"
    f"[cyan]R2 Region:[/cyan] {cfg.r2_region}\n"
    f"[dim]──────────────────────[/dim]\n"
    f"[cyan]Default gap beats:[/cyan] {cfg.default_gap_beats}\n"
    f"[cyan]Default video resolution:[/cyan] {cfg.default_video_resolution}\n"
    f"[cyan]Default video template:[/cyan] {cfg.default_video_template}\n"
    f"[cyan]Preview buffer ms:[/cyan] {cfg.preview_buffer_ms}\n"
    f"[cyan]Preview volume:[/cyan] {cfg.preview_volume}\n"
    f"[dim]──────────────────────[/dim]\n"
    f"[cyan]Working dir:[/cyan] {cfg.working_dir}\n"
    f"[cyan]Backup dir:[/cyan] {cfg.songsets_backup_dir}",
    title="Configuration",
    border_style="green",
)
```

### 6. `src/stream_of_worship/app/services/songset_io.py`

**Changes:**
- Rename `export_songset()` → `backup_songset()`
- Rename `export_all()` → `backup_all()`
- Rename `import_songset()` → `restore_songset()`
- Update docstrings

```python
class SongsetIOService:
    """Service for backing up and restoring songsets."""

    def backup_songset(self, songset_id: str, output_path: Path) -> Path:
        """Backup a songset to a JSON file."""
        # ...

    def backup_all(self, output_dir: Path) -> list[Path]:
        """Backup all songsets to JSON files in a directory."""
        # ...

    def restore_songset(
        self, input_path: Path, on_conflict: str = "rename"
    ) -> ImportResult:
        """Restore a songset from a JSON file."""
        # ...
```

### 7. `src/stream_of_worship/app/screens/settings.py`

**Changes:**
- Replace "Output Directory" input with "Working Directory"
- Update to read/write `working_dir` instead of `output_dir`
- Remove cache dir input (not configurable)

```python
def compose(self) -> ComposeResult:
    yield Header()

    with Vertical():
        yield Label("[bold]Settings[/bold]", id="title")

        with Horizontal(id="working_dir_row"):
            yield Label("Working Directory:")
            yield Input(id="working_dir_input", value=str(self.config.working_dir))

        with Horizontal(id="gap_row"):
            yield Label("Default Gap (beats):")
            yield Input(id="gap_input", value=str(self.config.default_gap_beats))

        # ... rest of settings

def action_save(self) -> None:
    """Save settings."""
    try:
        self.config.working_dir = __import__("pathlib").Path(
            self.query_one("#working_dir_input", Input).value
        )
        # ... rest of save logic
```

### 8. `src/stream_of_worship/app/app.py`

**Changes:**
- No changes needed - uses `config.cache_dir` and `config.output_dir` which are still accessible as properties

### 9. Tests

**`tests/app/test_config.py`:**
- Update `get_app_config_dir()` assertions: `"sow"` → `"stream-of-worship"`
- Update `cache_dir` test: check it returns `~/.cache/stream-of-worship`
- Update `output_dir` test: check it returns `working_dir / "output"`
- Update `log_dir` test: check it returns `working_dir / "logs"`
- Add `working_dir` default test
- Add `songsets_backup_dir` test
- Update TOML load/save tests for new field names
- Remove tests for old `songsets_export_dir` field (or keep as backward compat alias test)

**`tests/admin/test_config.py`:**
- Update `get_config_dir()` assertions: `"sow-admin"` → `"stream-of-worship-admin"`
- Update `get_cache_dir()` assertions: `"sow-admin"` → `"stream-of-worship-admin"`
- Remove `cache_dir` field tests (no longer on config object)
- Add test that `get_cache_dir()` returns correct path

**`tests/unit/test_paths.py`:**
- Update macOS `get_cache_dir()` expectation: `~/Library/Caches/sow` → `~/.cache/stream-of-worship`
- Update Linux `get_cache_dir()` expectation: `~/.cache/sow` → `~/.cache/stream-of-worship`

## Files Modified

| File | Change |
|------|--------|
| `src/stream_of_worship/core/paths.py` | macOS cache: `~/Library/Caches/sow` → `~/.cache/stream-of-worship`; Linux cache: `~/.cache/sow` → `~/.cache/stream-of-worship` |
| `src/stream_of_worship/admin/config.py` | Config dir: `sow-admin` → `stream-of-worship-admin`; cache dir: `sow-admin` → `stream-of-worship-admin`; remove `cache_dir` field; remove `[paths]` from TOML |
| `src/stream_of_worship/admin/main.py` | Update `config show` panel to use `get_cache_dir()` function |
| `src/stream_of_worship/admin/commands/audio.py` | Replace `config.cache_dir` with `get_cache_dir()` (3 call sites: lines 1724, 2863, 3151) |
| `src/stream_of_worship/app/config.py` | Config dir: `sow` → `stream-of-worship`; add `working_dir` field; remove `cache_dir`/`log_dir`/`output_dir`/`songsets_export_dir` fields; add property methods; remove `get_default_export_dir()` |
| `src/stream_of_worship/app/main.py` | Rename `export` → `backup`, `export-all` → `backup-all`, `import` → `restore`; update config display |
| `src/stream_of_worship/app/services/songset_io.py` | Rename `export_songset` → `backup_songset`, `export_all` → `backup_all`, `import_songset` → `restore_songset` |
| `src/stream_of_worship/app/screens/settings.py` | Replace "Output Directory" with "Working Directory"; remove cache dir input |
| `tests/app/test_config.py` | Update path assertions, field names, add `working_dir` tests |
| `tests/admin/test_config.py` | Update path assertions, remove `cache_dir` field tests |
| `tests/unit/test_paths.py` | Update macOS/Linux cache path expectations |
| `README.md` | Update config paths, examples, add configurable paths table |
| `DEVELOPER.md` | Update config paths in Advanced Configuration section |
| `src/stream_of_worship/admin/README.md` | Update config path, example, troubleshooting paths |
| `src/stream_of_worship/app/README.md` | Update config path, example, explain `working_dir` |
| `examples/sow-admin-config.toml` | Update bucket default, remove `[paths]` section |
| `examples/sow-app-config.toml` | Update bucket default, replace path fields with `working_dir` |

## Documentation Updates

Update the following files to reflect new paths and configurable options:

### 1. `README.md`

**Changes:**
- Update Admin CLI config path: `~/.config/sow-admin/config.toml` → `~/.config/stream-of-worship-admin/config.toml`
- Update User App config path: `~/.config/sow/config.toml` → `~/.config/stream-of-worship/config.toml`
- Update Admin CLI config example: remove `[paths]` section, update `bucket` default to `"stream-of-worship"`
- Update User App config example: replace `cache_dir`/`output_dir` with `working_dir`, update `bucket` default
- Add note that cache directory is not configurable (always at platform standard location)
- Update CLI command examples: `songsets export` → `songsets backup`, `songsets import` → `songsets restore`

**New Admin CLI config example:**
```toml
[service]
analysis_url = "http://localhost:8000"

[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.neon.tech/sow"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"
```

**New User App config example:**
```toml
[database]
url = "postgresql://sow_app@ep-xxx-pooler.neon.tech/sow"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"

[app]
working_dir = "~/stream-of-worship"
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
```

**Add configuration notes:**
```markdown
### Configurable Paths

| Component | Configurable Path | Default | Config Key |
|-----------|------------------|---------|------------|
| **Admin CLI** | None (cache at `~/.cache/stream-of-worship-admin`) | — | — |
| **User App** | Working directory | `~/stream-of-worship` | `working_dir` in `[app]` |

**Derived paths (User App only):**
- Logs: `<working_dir>/logs/`
- Output: `<working_dir>/output/`
- Backup: `<working_dir>/backup/`

**Cache locations (not configurable):**
- Admin CLI: `~/.cache/stream-of-worship-admin/`
- User App: `~/.cache/stream-of-worship/`
```

### 2. `DEVELOPER.md`

**Changes:**
- Update Admin CLI config path in "Advanced Configuration" section
- Remove `[paths]` section from config example
- Add note about cache directory being non-configurable
- Update R2 bucket default in examples

### 3. `src/stream_of_worship/admin/README.md`

**Changes:**
- Update config file location: `~/.config/sow-admin/config.toml` → `~/.config/stream-of-worship-admin/config.toml`
- Update example config: remove `[paths]` section, change `bucket = "sow-audio"` to `bucket = "stream-of-worship"`
- Add note: "Cache directory is always at `~/.cache/stream-of-worship-admin/` and is not configurable."
- Update troubleshooting `lsof` path: `~/.config/sow-admin/db/sow.db` → `~/.config/stream-of-worship-admin/db/sow.db`

### 4. `src/stream_of_worship/app/README.md`

**Changes:**
- Update config file location references
- Update config example: replace `cache_dir`/`output_dir`/`log_dir` with `working_dir`
- Add note: "Cache directory is always at `~/.cache/stream-of-worship/` and is not configurable."
- Update "Configuration" section to explain `working_dir` and derived paths

### 5. `examples/sow-admin-config.toml`

**Changes:**
- Update `bucket` default: `"sow-audio"` → `"stream-of-worship"`
- Remove `[paths]` section if present
- Add comment explaining cache is at standard location

### 6. `examples/sow-app-config.toml`

**Changes:**
- Update `bucket` default: `"sow-audio"` → `"stream-of-worship"`
- Replace `cache_dir`/`output_dir`/`log_dir` with `working_dir`
- Add comments explaining derived paths

### 7. Other specs/runbooks referencing old paths

Files to review and update path references:
- `specs/sqlite_turso_to_neon_migration_runbook_v4.md`
- `specs/sqlite_turso_to_neon_migration_runbook_v2.md`
- `specs/sqlite_turso_to_neon_migration_runbook.md`
- Any other specs under `specs/` or `reports/` that reference `~/.config/sow-admin`, `~/.config/sow`, `~/.cache/sow`, etc.

## Backward Compatibility

- Old TOML keys (`log_dir`, `output_dir`, `export_dir`, `cache_dir`) are silently ignored on load
- `songsets_export_dir` property exists as backward compat alias for `songsets_backup_dir`
- Users with custom paths need to set `working_dir` in their config after upgrade

## Verification

1. Test suite:
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
     --ignore=tests/services/analysis \
     --ignore=services/qwen3/tests \
     --ignore=services/analysis/tests -v
   ```

2. Admin CLI config:
   ```bash
   uv run --extra admin sow-admin config show
   # Should show cache dir as ~/.cache/stream-of-worship-admin
   ```

3. User App config:
   ```bash
   uv run --extra app sow-app config show
   # Should show working_dir and backup_dir, not cache_dir
   ```

4. Backup/restore commands:
   ```bash
   uv run --extra app sow-app songsets backup <id>
   uv run --extra app sow-app songsets backup-all
   uv run --extra app sow-app songsets restore <file.json>
   ```

5. Verify cache locations:
   - Admin: `~/.cache/stream-of-worship-admin/`
   - App: `~/.cache/stream-of-worship/`

6. Verify working dir structure:
   ```
   ~/stream-of-worship/
   ├── logs/
   ├── output/
   └── backup/
   ```

## Manual Migration Instructions

Users upgrading from previous versions must manually migrate their data. Run these commands before using the new version:

### 1. Admin CLI Migration

```bash
# Move config file
mv ~/.config/sow-admin ~/.config/stream-of-worship-admin

# Move cache directory (macOS)
mv ~/Library/Caches/sow-admin ~/.cache/stream-of-worship-admin

# Move cache directory (Linux)
mv ~/.cache/sow-admin ~/.cache/stream-of-worship-admin
```

### 2. User App Migration

```bash
# Move config file
mv ~/.config/sow ~/.config/stream-of-worship

# Move cache directory (macOS)
mkdir -p ~/.cache
mv ~/Library/Caches/sow ~/.cache/stream-of-worship

# Move cache directory (Linux)
mv ~/.cache/sow ~/.cache/stream-of-worship

# Create new working directory structure
mkdir -p ~/stream-of-worship/{logs,output,backup}

# Move existing output files (if any)
mv ~/sow/output/* ~/stream-of-worship/output/ 2>/dev/null || true

# Move existing songset exports (if any)
mv ~/Documents/sow-songsets/* ~/stream-of-worship/backup/ 2>/dev/null || true
```

### 3. Update Config TOML

Edit `~/.config/stream-of-worship/config.toml` and add:

```toml
[app]
working_dir = "~/stream-of-worship"
```

Remove old path keys if present:
- `cache_dir` (under `[app]`)
- `output_dir` (under `[app]`)
- `log_dir` (under `[app]`)
- `export_dir` (under `[songsets]`)

### 4. CLI Command Changes

Update any scripts using old command names:
- `sow-app songsets export` → `sow-app songsets backup`
- `sow-app songsets export-all` → `sow-app songsets backup-all`
- `sow-app songsets import` → `sow-app songsets restore`

### 5. Verify Migration

```bash
# Check config is found
uv run --extra admin sow-admin config show
uv run --extra app sow-app config show

# Verify cache contents moved
ls ~/.cache/stream-of-worship-admin/
ls ~/.cache/stream-of-worship/
```

## Out of Scope

- Renaming the Python package `src/stream_of_worship/`
- Renaming CLI entry points (`sow-admin`, `sow-app`)
- Auto-migration of old config files
- `core/paths.py` `get_user_data_dir()` (used by legacy components only)
- POC scripts
- Service caches under `services/analysis/`, `services/qwen3/`
