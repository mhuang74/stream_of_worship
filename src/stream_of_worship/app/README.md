# Stream of Worship - User App (TUI)

An interactive Textual TUI application for worship leaders to browse the song catalog, assemble multi-song songsets with smooth transitions, preview audio, and export final audio + lyrics video files.

## Features

### 🎵 Songset Management
- **Create and manage songsets** - Organize multiple songs into sets for worship sessions
- **Reorder songs** - Drag-and-drop or keyboard-based reordering in the songset editor
- **Persistent storage** - Songsets saved to local SQLite database

### 📚 Catalog Browsing
- **Browse master catalog** - View all songs from the admin-managed catalog
- **Search and filter** - Find songs by title or artist
- **View song details** - See available recordings, keys, and lyrics

### 🎛️ Transition Controls
- **Gap-based transitions** - Configure silence duration between songs (in beats)
- **Crossfade support** - Optional crossfade between consecutive songs
- **Key shifting** - Adjust key (semitones) for each song to match your set
- **Fine-tuning UI** - Edit transitions with visual feedback

### 🎧 Audio Preview
- **In-app playback** - Preview individual songs or full songsets
- **Miniaudio backend** - Low-latency audio playback
- **Playback controls** - Play, pause, stop with keyboard shortcuts

### 🎬 Export & Video Generation
- **Audio export** - Export combined audio file with all transitions
- **Lyrics video generation** - Generate synced lyrics videos with:
  - Multiple templates (dark, gradient_warm, gradient_blue)
  - LRC-based timestamp synchronization
  - Smooth transitions between songs
- **Progress tracking** - Visual progress bar during export with cancel option
- **Background processing** - Export runs in background thread

### ⚙️ Configuration
- **Cache directory** - Local cache for downloaded audio assets
- **Output directory** - Where exported files are saved
- **Default video template** - Choose your preferred visual style
- **Default gap beats** - Set your preferred default transition gap
- **TOML-based config** - Easy configuration file editing

## Launching the App

### Prerequisites
1. Install dependencies with app extra:
   ```bash
   uv pip install -e ".[admin,app]"
   ```

2. Set up R2 credentials (for downloading audio assets):
   ```bash
   export SOW_R2_ACCESS_KEY_ID="your-key"
   export SOW_R2_SECRET_ACCESS_KEY="your-secret"
   ```

3. Ensure you have a config file with database path:
   ```toml
   database_url = "/path/to/sow.db"

   [app]
   cache_dir = "/path/to/cache"
   output_dir = "/path/to/output"
   default_gap_beats = 2.0
   default_video_template = "dark"
   ```

### Launch

```bash
# With config file
sow-app --config /path/to/config.toml

# With database URL directly
sow-app --database-url "sqlite:////path/to/sow.db"

# Show help
sow-app --help
```

## Navigation

| Key | Action |
|-----|--------|
| `↑/↓` or `k/j` | Navigate lists |
| `Enter` | Select / Confirm |
| `Escape` or `q` | Go back |
| `Tab` | Next field |
| `Shift+Tab` | Previous field |

## Screen Overview

1. **Songset List** - View and select existing songsets, create new ones
2. **Browse** - Browse catalog and add songs to current songset
3. **Songset Editor** - Reorder songs, remove songs, edit transitions
4. **Transition Detail** - Fine-tune gap, crossfade, and key shift for each transition
5. **Export Progress** - Monitor export progress with cancel option
6. **Settings** - Configure app preferences

## Export Output

Exported files are saved to your configured output directory:

```
output/
├── {songset_name}_{timestamp}.mp3    # Combined audio file
└── {songset_name}_{timestamp}.mp4    # Lyrics video
```

## Architecture

The User App follows a service-oriented architecture:

- **AppState**: Central reactive state management with observer pattern
- **Services**: Modular services for catalog, playback, audio/video processing
- **Screens**: Textual-based UI screens composing the interface
- **Database**: SQLite with separate clients for read-only (admin tables) and read-write (app tables) access

## Troubleshooting

**App won't start**: Check that database file exists and is accessible

**No audio playback**: Verify miniaudio is installed and audio files are cached

**Export fails**: Check R2 credentials and ensure FFmpeg is installed

**Missing songs in catalog**: Run `sow-admin catalog scrape-songs` to populate database
