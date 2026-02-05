# Song Transition Preview App - V2

A keyboard-first, text-based Python terminal application for experimenting with, evaluating, and saving audio transitions between songs.

## Features

- **Generation Screen**: Select songs and sections, configure transition parameters, generate transitions
- **Keyboard-first navigation**: Fast, efficient workflow optimized for creative experimentation
- **Non-destructive iteration**: Compare multiple transitions side-by-side
- **Session-based workflow**: Keep history of up to 50 transitions per session

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the app by editing `config.json`:
   - Set `audio_folder` to point to your audio files
   - Set `analysis_json` to point to your song metadata JSON
   - Set `output_folder` for saved transitions

## Running the App

From the `transition_builder_v2` directory:

```bash
python -m app.main
```

Or use the provided script:

```bash
./run.sh
```

## Configuration

The `config.json` file contains:

- `audio_folder`: Path to folder containing audio files
- `output_folder`: Path for saving finalized transitions
- `analysis_json`: Path to JSON file with song metadata (from `poc_analysis_allinone.py`)
- `default_transition_type`: Default transition type on startup
- `max_history_size`: Maximum transitions in session history (default: 50)
- `auto_play_on_generate`: Auto-play after successful generation
- `session_logging`: Enable session event logging
- `error_logging`: Enable error logging

## Keyboard Shortcuts

### Global
- `Space`: Play / Pause
- `←`: Seek backward 3 seconds
- `→`: Seek forward 4 seconds
- `?` or `F1`: Show help
- `Ctrl+C`: Exit app

### Generation Screen
- `Tab`: Cycle through panels (Song A → Song B → Parameters)
- `H`: Switch to History screen
- `/`: Open Song Search
- `G`: Generate transition
- `Shift+G`: Quick test (ephemeral generation)
- `Esc`: Exit Modify Mode (if active)
- `P`: Play Song A section
- `L`: Play Song B section

## Project Structure

```
transition_builder_v2/
├── app/
│   ├── main.py              # Application entry point
│   ├── state.py             # AppState model
│   ├── models/
│   │   ├── song.py          # Song and Section models
│   │   └── transition.py    # TransitionRecord model
│   ├── services/
│   │   ├── catalog.py       # SongCatalogLoader
│   │   └── playback.py      # PlaybackService (stub)
│   ├── screens/
│   │   ├── generation.py    # Generation screen
│   │   └── generation.tcss  # Generation screen styles
│   └── utils/
│       └── config.py        # Configuration loader
├── config.json              # Application configuration
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Current Status

### ✅ Implemented
- Data models (Song, Section, TransitionRecord, AppState)
- Song catalog loading from JSON
- Generation screen UI layout
- Keyboard navigation and bindings
- Song and section selection logic
- Basic panel focus management

### 🚧 In Progress
- Parameter configuration panel (currently placeholder)
- Parameter validation with warnings

### ⏳ TODO
- Playback service implementation (PyAudio)
- Transition generation service
- History screen
- Song search screen
- Help overlay
- Session and error logging
- Screen transitions

## Design Specification

See the full design specification in `../specs/enhanced_tui_song_transition_builder.md`.

## Development

The app uses [Textual](https://textual.textualize.io/) for the TUI framework.

To enable Textual DevTools for debugging:
```bash
textual console
# In another terminal:
python -m app.main
```

## License

See parent project LICENSE.
