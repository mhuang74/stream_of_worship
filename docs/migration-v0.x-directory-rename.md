# Migration Guide: Directory Rename (v0.x)

This guide covers manual steps needed when upgrading to the standardized cache/directory layout.

## What Changed

| Old path | New path |
|---|---|
| `~/.local/share/stream_of_worship/` | `~/.local/share/sow/` (Linux data) |
| `~/.cache/stream_of_worship/` | `~/.cache/sow/` (Linux cache) |
| `~/.cache/stream-of-worship/` | `~/.cache/sow/` (POC script cache) |
| `~/Library/Application Support/StreamOfWorship/` | `~/Library/Application Support/sow/` (macOS data) |
| `~/Library/Caches/StreamOfWorship/` | `~/Library/Caches/sow/` (macOS cache) |
| `~/.config/sow-app/` | `~/.config/sow/` (app config) |
| `~/.config/sow-app/cache/` | `~/.cache/sow/` (app cache, now XDG-compliant) |
| `<cache>/whisper_cache/` | `<cache>/whisper/` (whisper model cache subdir) |
| `<cache>/logs/` | `~/.local/share/sow/logs/` (logs decoupled from cache) |
| `~/StreamOfWorship/output` | `~/sow/output` (app output dir) |
| `<data>/output_transitions/` | `<data>/output/transitions/` |
| `<data>/output_songs/` | `<data>/output/songs/` |

## Migration Steps

### 1. Move app config

```bash
mv ~/.config/sow-app ~/.config/sow
```

### 2. Move app cache to XDG cache location

```bash
mkdir -p ~/.cache/sow
# Move cached audio assets (hash-prefixed dirs)
cp -r ~/.config/sow-app/cache/. ~/.cache/sow/
rm -rf ~/.config/sow-app/cache
```

Or set `SOW_CACHE_DIR=~/.config/sow/cache` in your environment to keep the old location.

### 3. Move legacy song/audio cache

```bash
# Linux
mv ~/.cache/stream_of_worship ~/.cache/sow 2>/dev/null || true
mv ~/.cache/stream-of-worship ~/.cache/sow 2>/dev/null || true

# macOS
mv ~/Library/Caches/StreamOfWorship ~/Library/Caches/sow 2>/dev/null || true
```

### 4. Move legacy data directory

```bash
# Linux
mv ~/.local/share/stream_of_worship ~/.local/share/sow 2>/dev/null || true

# macOS
mv ~/Library/Application\ Support/StreamOfWorship ~/Library/Application\ Support/sow 2>/dev/null || true
```

### 5. Rename whisper cache subdir

```bash
mv ~/.cache/sow/whisper_cache ~/.cache/sow/whisper 2>/dev/null || true
```

### 6. Move logs out of cache

```bash
mkdir -p ~/.local/share/sow/logs
mv ~/.cache/sow/logs/* ~/.local/share/sow/logs/ 2>/dev/null || true
```

## Environment Variables

| Old variable | New variable | Notes |
|---|---|---|
| `STREAM_OF_WORSHIP_DATA_DIR` | `SOW_DATA_DIR` | Legacy still works as fallback |
| *(none)* | `SOW_CACHE_DIR` | Override app cache dir |
| *(none)* | `SOW_ADMIN_CACHE_DIR` | Override admin cache dir |

## Databases

Admin DB (`~/.config/sow-admin/db/sow.db`) and App DB (`~/.config/sow/db/sow.db`) remain separate — do **not** merge them.

## Notes

- POC ML model-weight caches (`~/.cache/whisper`, `~/.cache/qwen3_asr`, `~/.cache/qwen3_tts`, `~/.cache/huggingface`) are intentionally **not** moved. These are managed by the respective ML libraries.
- `poc/test_whisper.py` contains a hardcoded absolute path (`/Users/mhuang/.cache/whisper`). This path remains machine-specific and must be updated manually on each machine.
- Admin cache (`~/.cache/sow-admin/`) and App cache (`~/.cache/sow/`) are kept **separate** by design — the admin CLI downloads assets for processing, while the app downloads assets for playback. Each component manages its own cache independently.
