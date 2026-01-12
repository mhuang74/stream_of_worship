# Interactive Worship Transition Builder

A text-based interactive tool for creating worship song transitions based on the "Worship Transitions" handbook concepts (Overlap, Short Gap, No Break) with real-time parameter adjustment.

## Features

- **3 PDF-Based Transition Types:**
  - **Overlap (Intro Overlap)**: Last note of Song A overlaps with intro of Song B
  - **Short Gap**: Brief silence between songs to "clear the air"
  - **No Break**: Continuous beat, seamless flow

- **Fully Configurable Parameters** (all durations in beats, auto-converted to seconds based on tempo):
  - transition_window: Total transition zone duration in beats
  - overlap_window / gap_window: Type-specific duration in beats
  - stems_to_fade: Select which stems to manipulate (vocals, drums, bass, other)
  - fade_window_pct: Fade duration as percentage of transition_window

- **Real-Time Generation:** Generate transitions on-demand with immediate audio preview
- **Compatibility Scoring:** Automatic tempo, key, and energy compatibility analysis
- **Export to FLAC + JSON:** Save transitions with full parameter metadata

## Installation

### Requirements
- Python 3.8+
- Dependencies: `rich`, `sounddevice`, `soundfile`, `librosa`, `numpy`

### Install Dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies with uv
uv pip install -r requirements.txt
```

Or using pip:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Workflow

```bash
# Make sure virtual environment is activated
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Run the application
python -m interactive_transition_builder.main
```

The interactive tool will guide you through:

1. **Select Song A** - Choose from available songs
2. **Select Section A** - Pick a section (verse, chorus, bridge, etc.)
3. **Select Song B** - Choose the second song
4. **Select Section B** - Pick a section from Song B
5. **Choose Transition Type** - Select Overlap, Short Gap, or No Break
6. **Adjust Parameters** - Customize transition settings (optional)
7. **Preview** - Generate and play the transition
8. **Save** - Export to FLAC + JSON

### Example Session

```
Step 1: Select Song A
┌───┬──────────────────────┬────────────┬────────┬──────────┐
│ # │ Filename             │ Key        │ BPM    │ Duration │
├───┼──────────────────────┼────────────┼────────┼──────────┤
│ 1 │ do_it_again.mp3      │ D major    │ 136.0  │ 4:25     │
│ 2 │ heaven_open.mp3      │ G major    │ 128.0  │ 4:12     │
...

Enter song number: 1
✓ Selected: do_it_again.mp3

Step 2: Select Section from Song A
┌───┬─────────┬─────────────────┬──────────┬────────┐
│ # │ Label   │ Time Range      │ Duration │ Energy │
├───┼─────────┼─────────────────┼──────────┼────────┤
│ 1 │ Intro   │ 0.0s - 12.5s    │ 12.5s    │ 75.2/100│
│ 2 │ Verse   │ 12.5s - 35.8s   │ 23.3s    │ 68.4/100│
│ 3 │ Chorus  │ 35.8s - 58.2s   │ 22.4s    │ 84.1/100│
...

Enter section number: 3
✓ Selected: chorus

...

Generating transition...
✓ Generated transition (12.4s)

Play preview? (y/n) [y]: y
▶ Playing audio
  Duration: 12.4s | ← → to seek ±5s | Ctrl+C to stop

Save transition? (y/n) [n]: y
Filename [transition_do_it_again_chorus_to_heaven_open_chorus]:

✓ Exported transition:
  Audio: section_transitions/transition_do_it_again_chorus_to_heaven_open_chorus.flac
  Metadata: section_transitions/transition_do_it_again_chorus_to_heaven_open_chorus.json
  Duration: 12.4s
```

## Output Files

### Audio File (FLAC)
- Format: FLAC (lossless compression)
- Sample rate: 44100 Hz
- Channels: Stereo (2)
- Location: `section_transitions/*.flac`

### Metadata File (JSON)
Contains full configuration and compatibility scores:
```json
{
  "version": "1.0",
  "transition_type": "short_gap",
  "parameters": {
    "transition_window": 8.0,
    "gap_window": 2.0,
    "stems_to_fade": ["vocals", "drums"],
    "fade_window_pct": 80
  },
  "song_a": { ... },
  "song_b": { ... },
  "compatibility": {
    "overall_score": 78.5,
    "tempo_score": 85.0,
    "key_score": 72.0,
    "energy_score": 80.0
  },
  "audio": { ... }
}
```

## Architecture

```
interactive_transition_builder/
├── models/                  # Data models
│   ├── song.py             # Song and Section classes
│   ├── transition_config.py # Configuration with validation
│   └── transition_types.py  # TransitionType enum
├── audio/                   # Audio processing
│   ├── stem_loader.py      # Stem loading with caching
│   ├── transition_generator.py # Core algorithms
│   └── playback.py         # Audio playback with seek
├── utils/                   # Utilities
│   ├── metadata_loader.py  # JSON metadata loading
│   └── export.py           # FLAC + JSON export
└── main.py                 # Application entry point
```

## Data Sources

- **Metadata**: `poc_output_allinone/section_features.json` and `poc_full_results.json`
- **Audio Stems**: `poc_output_allinone/stems/{song_name}/bass.wav` (+ drums, other, vocals)
- **Original Audio**: `poc_audio/*.mp3`

## Transition Algorithms

**Important**: All transitions include **FULL sections** from both Song A and Song B. The transition effects are applied at the junction points between sections.

### Overlap (Intro Overlap)
**Default**: 6 beats transition window, 2 beats overlap, stems: vocals + drums

1. Load FULL sections for Song A and Song B
2. Apply fade-out to selected stems in Song A over last `fade_window_pct` of `transition_window`
3. Apply equal-power crossfade during `overlap_window` region at the end of A / start of B:
   - Song A: sqrt(1-t) fade out
   - Song B: sqrt(t) fade in
4. Concatenate: [Full A with transitions] + [overlap_mixed] + [Full B with transitions]

**Output**: Complete section A + overlap transition + complete section B

### Short Gap
**Default**: 9 beats transition window, 1 beat gap, stems: other + drums + bass

1. Load FULL sections for Song A and Song B
2. Fade OUT selected stems in Song A over last `fade_window_pct` of `transition_window`
3. Add `gap_window` (in beats) of silence
4. Fade IN selected stems in Song B over first `fade_window_pct` of `transition_window`
5. Concatenate: [Full A with fade_out] + [silence] + [Full B with fade_in]

**Output**: Complete section A (with fade out) + silence gap + complete section B (with fade in)

### No Break
**Default**: 8 beats transition window, stems: other + drums + bass, 100% fade

1. Load FULL sections for Song A and Song B
2. Calculate fade duration based on `fade_window_pct` of `transition_window`
3. Apply equal-power crossfade at junction (end of A, start of B)
4. Concatenate: [Full A with fade] + [crossfade] + [Full B with fade]

**Output**: Complete section A + crossfade + complete section B

## References

- Design specification: `specs/interactive-transition-builder-design.md`
- PDF handbook: `docs/WorshipTransitions-Handout-SESSION-23.pdf`

## Version

1.0.0 - Initial release (2026-01-09)
