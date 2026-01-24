# Refactoring Summary: Output Directory Configuration

## Overview

Refactored the output directory configuration to properly use the config system instead of hardcoding paths in the generation service.

## Changes Made

### 1. Configuration System (`app/utils/config.py`)

**Added new configuration field:**
- `output_songs_folder: Path` - Directory for full song outputs (separate from transitions)

**Default values:**
- `output_folder` default: `"./output_transitions"` (for transitions)
- `output_songs_folder` default: `"./output_songs"` (for full songs)

### 2. Configuration File (`config.json`)

**Added new field:**
```json
{
  "output_folder": "../output_transitions",
  "output_songs_folder": "../output_songs",
  ...
}
```

### 3. Generation Service (`app/services/generation.py`)

**Updated constructor:**
```python
def __init__(self, output_dir: Path, output_songs_dir: Path, stems_folder: Path | None = None):
    """Initialize the generation service.

    Args:
        output_dir: Directory to save generated transitions
        output_songs_dir: Directory to save full song outputs
        stems_folder: Directory containing stem files (optional)
    """
    self.output_dir = output_dir
    self.output_dir.mkdir(parents=True, exist_ok=True)
    self.output_songs_dir = output_songs_dir
    self.output_songs_dir.mkdir(parents=True, exist_ok=True)
    self.stems_folder = stems_folder
```

**Updated `generate_full_song_output` method:**
- Removed hardcoded `Path("output_songs")`
- Now uses `self.output_songs_dir` from config

### 4. Main Application (`app/main.py`)

**Updated service initialization:**
```python
self.generation = TransitionGenerationService(
    output_dir=self.config.output_folder,
    output_songs_dir=self.config.output_songs_folder,
    stems_folder=self.config.stems_folder
)
```

### 5. Directory Naming Convention

**Standardized directory names:**
- Old: `transitions_output` → New: `output_transitions`
- Old: `song_sets_output` → New: `output_songs`

All naming now follows the pattern: `output_{type}`

## Benefits

1. **Configuration-driven**: All output paths are now configurable via `config.json`
2. **No hardcoded paths**: The generation service is completely decoupled from path decisions
3. **Flexibility**: Users can easily change output directories without modifying code
4. **Consistency**: Both transition and song output directories are configured the same way
5. **Testability**: Tests automatically use the configured paths from `config.json`

## File Structure

After these changes, the expected directory structure is:

```
stream_of_worship/
├── output_transitions/          # Transition files (gap transitions, etc.)
│   ├── transition_gap_*.flac
│   └── preview_*.flac
├── output_songs/                # Full song output files
│   └── songset_*.flac
├── transition_builder_v2/
│   ├── config.json             # Configuration including output paths
│   ├── app/
│   │   ├── services/
│   │   │   └── generation.py   # Uses config for output paths
│   │   └── utils/
│   │       └── config.py       # Defines output_songs_folder field
│   └── tests/
│       └── ...                 # Tests use config automatically
└── ...
```

## Migration Notes

**For existing users:**

1. Update `config.json` to include the new `output_songs_folder` field
2. If not specified, defaults to `"./output_songs"` relative to config location
3. Existing transition files remain in `output_folder` path
4. New full song outputs will use `output_songs_folder` path

**Example config.json:**
```json
{
  "audio_folder": "../poc_audio",
  "output_folder": "../output_transitions",
  "output_songs_folder": "../output_songs",
  "analysis_json": "../poc_output_allinone/poc_full_results.json",
  "stems_folder": "../poc_output_allinone/stems",
  "default_transition_type": "gap",
  "max_history_size": 50,
  "auto_play_on_generate": true,
  "session_logging": true,
  "error_logging": true
}
```

## Verification

The refactoring was verified by:

1. ✅ Config loads successfully with new field
2. ✅ Paths resolve correctly (absolute paths created)
3. ✅ Directories are created automatically on service init
4. ✅ Tests use config paths automatically via fixtures
5. ✅ No hardcoded paths remain in generation service

## Related Files Updated

- `app/utils/config.py` - Added `output_songs_folder` field
- `config.json` - Added `output_songs_folder` configuration
- `app/services/generation.py` - Uses config paths, not hardcoded
- `app/main.py` - Passes `output_songs_folder` to service
- `tests/test_screens.py` - Updated directory name checks
- `tests/run_workflow_test.py` - Updated directory name checks
- `tests/README.md` - Updated documentation
- `IMPLEMENTATION_STATUS.md` - Updated directory references
