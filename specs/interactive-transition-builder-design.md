# Interactive Worship Transition Builder - Design Specification

**Version**: 1.0
**Date**: 2026-01-09
**Status**: Design Review

## Executive Summary

This document specifies the design for an interactive text-based transition builder that allows users to create custom worship song transitions based on principles from the "Worship Transitions" handbook (Session 23). The tool provides real-time parameter adjustment, audio preview, and export capabilities through a modern terminal user interface.

## Background

### Current State
The existing system (`generate_section_transitions.py`) generates transitions in batch mode with fixed parameters:
- 4 pre-defined variants (medium-crossfade, medium-silence, vocal-fade, drum-fade)
- Fixed transition durations and fade curves
- No interactive parameter adjustment
- Batch processing of all compatible song pairs

### User Requirements
Based on the PDF concepts and user feedback, the new interactive builder should:
1. Implement the 3 core PDF transition types (Overlap, Short Gap, No Break)
2. Allow real-time parameter experimentation
3. Provide 2-column layout for simultaneous Song A and Song B viewing
4. Generate transitions on-demand with immediate preview
5. Save transitions with full parameter metadata

## System Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interface (Rich TUI)                │
│  ┌──────────────────────┬──────────────────────────────┐   │
│  │      Song A Panel    │      Song B Panel            │   │
│  │  - Metadata          │  - Metadata                  │   │
│  │  - Section List      │  - Section List              │   │
│  │  - Playback Controls │  - Playback Controls         │   │
│  └──────────────────────┴──────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────┐    │
│  │        Transition Configuration Panel              │    │
│  │  - Type Selection (Overlap/Short Gap/No Break)     │    │
│  │  - Parameter Controls (sliders, toggles)           │    │
│  │  - Compatibility Display                           │    │
│  └────────────────────────────────────────────────────┘    │
│  ┌────────────────────────────────────────────────────┐    │
│  │             Action Panel                           │    │
│  │  [Preview] [Save] [Quit] [Help]                    │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                   Application Logic Layer                   │
│  ┌──────────────┬──────────────────┬────────────────────┐  │
│  │ Metadata     │ Transition       │ Audio              │  │
│  │ Loader       │ Generator        │ Processor          │  │
│  │ - Songs      │ - Overlap        │ - Stem Loader      │  │
│  │ - Sections   │ - Short Gap      │ - Fade Engine      │  │
│  │ - Features   │ - No Break       │ - Mixer            │  │
│  └──────────────┴──────────────────┴────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      Data Layer                             │
│  ┌─────────────────────┬────────────────────────────────┐  │
│  │ Metadata (JSON)     │ Audio Files                    │  │
│  │ - section_features  │ - Stems (WAV)                  │  │
│  │ - poc_full_results  │ - Original (MP3)               │  │
│  └─────────────────────┴────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Module Structure

```
interactive_transition_builder/
├── __init__.py
├── main.py                          # Application entry point
│   - State machine for UI workflow
│   - Keyboard input handling
│   - Main event loop
│
├── ui/                              # User Interface Components
│   ├── __init__.py
│   ├── layout.py                    # Rich-based layout manager
│   │   - 2-column song panels
│   │   - Configuration panel
│   │   - Action panel
│   ├── song_selector.py             # Song/section browsing
│   │   - Song list display
│   │   - Section table rendering
│   │   - Selection state management
│   ├── parameter_controls.py        # Interactive parameter widgets
│   │   - Numeric input controls
│   │   - Multi-select toggles
│   │   - Real-time validation
│   └── status_display.py            # Status and progress
│       - Status messages
│       - Progress bars
│       - Error display
│
├── audio/                           # Audio Processing
│   ├── __init__.py
│   ├── stem_loader.py               # Stem loading and caching
│   │   - Load 4-stem audio files
│   │   - Section extraction
│   │   - LRU cache implementation
│   ├── transition_generator.py      # Core transition algorithms
│   │   - generate_overlap()
│   │   - generate_short_gap()
│   │   - generate_no_break()
│   │   - Fade curve generation
│   │   - Audio mixing
│   └── playback.py                  # Audio playback
│       - sounddevice integration
│       - Seek controls (±5s)
│       - Playback state management
│
├── models/                          # Data Models
│   ├── __init__.py
│   ├── song.py                      # Song and Section classes
│   │   - Song metadata
│   │   - Section metadata
│   │   - Compatibility calculation
│   ├── transition_config.py         # TransitionConfig dataclass
│   │   - Parameter validation
│   │   - Serialization/deserialization
│   └── transition_types.py          # TransitionType enum
│       - OVERLAP
│       - SHORT_GAP
│       - NO_BREAK
│
└── utils/                           # Utilities
    ├── __init__.py
    ├── metadata_loader.py           # JSON metadata loading
    │   - Load section_features.json
    │   - Load poc_full_results.json
    │   - Parse and structure data
    └── export.py                    # Export functionality
        - FLAC audio export
        - JSON metadata export
        - Default naming convention
```

## Transition Types (PDF-Based)

### 1. Overlap (Intro Overlap)

**Concept**: From PDF page 1 - "as the last note of the first song is sung, the introduction to the second song begins."

**Visual Representation**:
```
Song A: [================|~~~~fade~~~~]
Song B:           [~~~~fade~~~~|================]
                        ↑
                   overlap region
```

**Parameters**:
- `transition_window`: Total duration of transition zone (e.g., 6 beats)
- `overlap_window`: Duration of actual overlap (e.g., 2 beats)
- `stems_to_fade`: Which stems fade out in Song A (e.g., ['vocals', 'drums'])
- `fade_window_pct`: Fade duration as % of transition_window (e.g., 80%)

**Algorithm**:
1. Extract last `transition_window` seconds from Song A section
2. Extract first `transition_window` seconds from Song B section
3. Load 4-stem audio for both excerpts
4. Apply fade-out to selected stems in Song A over last `fade_window_pct` of transition_window
5. Apply equal-power crossfade to the `overlap_window` region (last part of A overlaps with first part of B)
   - Song A: Fade OUT all selected stems using `sqrt(1 - t)` curve
   - Song B: Fade IN all selected stems using `sqrt(t)` curve
6. Concatenate: `[A_before_fade] + [overlap_mixed] + [B_after_overlap]`

**Example** (transition_window=4 beats, overlap_window=2 beats, fade_window_pct=80%):
- Song A: Last 4 beats, vocals fade out over last 3.2 beats (80% of 4 beats)
- Song B: First 4 beats, vocals fade in over first 3.2 beats (80% of 4 beats)
- Overlap: Last 2 beats of A mixed with first 2 beats of B
- Crossfade: Equal-power mixing over full 2 beats
- Total duration: ~6 beats (4 beats - 2 beats + 4 beats)

### 2. Short Gap

**Concept**: From PDF page 2 - "the first song ends and the second song begins after a very brief moment, just enough to 'clear the air.'"

**Visual Representation**:
```
Song A: [================]
        [silence gap]
Song B:                    [================]
```

**Parameters**:
- `transition_window`: Fade zone per song (e.g., 8 beats)
- `gap_window`: Duration of silence (e.g., 1 beat)
- `stems_to_fade`: Which stems fade in/out (e.g., ['vocals', 'drums'])
- `fade_window_pct`: Fade duration as % of transition_window (e.g., 80%)

**Algorithm**:
1. Extract last `transition_window` from Song A section
2. Extract first `transition_window` from Song B section
3. Load 4-stem audio for both excerpts
4. Apply fade-out to selected stems in Song A over last `fade_window_pct`
5. Create stereo silence of duration `gap_window`
6. Apply fade-in to selected stems in Song B over first `fade_window_pct`
7. Concatenate: `[A_before_fade] + [A_fade_out] + [silence] + [B_fade_in] + [B_after_fade]`

**Example** (transition_window=8 beats, gap_window=1 beat, fade_window_pct=80%):
- Song A: Last 4 beats, fade out over last 3.2 beats
- Silence: 1 beat
- Song B: First 4 beats, fade in over first 3.2 beats
- Total duration: 4 beats + 1 beat + 4 beats = 9 beats

### 3. No Break

**Concept**: From PDF page 2 - "The beat continues constantly without break. The two songs join together with one song beginning as the other ends."

**Visual Representation**:
```
Song A: [================]
Song B:                  [================]
                                  ↑
                          continuous beat
```

**Parameters**:
- `transition_window`: Duration of crossfade (e.g., 8 beats)
- `stems_to_fade`: Which stems to crossfade (typically 'all')
- `fade_window_pct`: Crossfade duration as % of transition_window (100% = full overlap)

**Algorithm**:
1. Extract last `transition_window` from Song A section
2. Extract first `transition_window` from Song B section
3. Load 4-stem audio for both excerpts
4. Calculate fade duration: `transition_window * (fade_window_pct / 100)`
5. Concatenate: `[A_with_fade] + [B_with_fade]`

**Example** (transition_window=8 beats, fade_window_pct=100%):
- Song A: Last 4 beats, selected stems fade out over 4 beats
- Song B: First 4 beats, selected stems fade in over 4 beats

- Total duration: 8 beats (no gap)

## Configurable Parameters

### Parameter Specifications

| Parameter | Type | Range | Default | Unit | Transition Types |
|-----------|------|-------|---------|------|------------------|
| `transition_window` | float | 2.0 - 16.0 | 8.0 | beats  | All |
| `overlap_window` | float | 0.5 - 8.0 | 4.0 | beats | Overlap only |
| `gap_window` | float | 0.5 - 8.0 | 2.0 | beats | Short Gap only |
| `stems_to_fade` | list | ['vocals', 'drums', 'bass', 'other'] | ['vocals', 'drums'] | N/A | All |
| `fade_window_pct` | int | 0 - 100 | 80 | percent | All |

### Parameter Constraints

1. **Overlap Window**: Must be ≤ transition_window
2. **Fade Window %**: 100% means symmetric fade (equal fade-in and fade-out duration)
3. **Stems to Fade**: At least one stem must be selected (or 'all')
4. **Transition Window**: Must be shorter than both Song A and Song B sections

### Default Presets by Transition Type

**Overlap**:
- transition_window: 6 beats
- overlap_window: 2 beats
- stems_to_fade: ['vocals', 'drums']
- fade_window_pct: 80%

**Short Gap**:
- transition_window: 9 beats
- gap_window: 1 beat
- stems_to_fade: ['others', 'drums', 'bass']
- fade_window_pct: 80%

**No Break**:
- transition_window: 8 beats
- stems_to_fade: ['others', 'drums', 'bass']
- fade_window_pct: 100%

## User Interface Design

### Layout Structure

```
┌──────────────────────────────────────────────────────────────────────┐
│                Interactive Transition Builder v1.0                   │
│              Press H for help | Press Q to quit                      │
├─────────────────────────────────┬────────────────────────────────────┤
│          SONG A                 │          SONG B                    │
├─────────────────────────────────┼────────────────────────────────────┤
│ Selected: do_it_again.mp3       │ Selected: heaven_open.mp3          │
│ Key: D major | BPM: 136.0       │ Key: G major | BPM: 128.0          │
│ Duration: 265.2s (4:25)         │ Duration: 252.3s (4:12)            │
│                                 │                                    │
│ Available Sections:             │ Available Sections:                │
│ ┌───┬─────────┬─────────────┐  │ ┌───┬─────────┬─────────────┐     │
│ │ # │ Label   │ Time Range  │  │ │ # │ Label   │ Time Range  │     │
│ ├───┼─────────┼─────────────┤  │ ├───┼─────────┼─────────────┤     │
│ │ 1 │ Intro   │ 0.0 - 12.5s │  │ │ 1 │ Intro   │ 0.0 - 15.2s │     │
│ │ 2 │ Verse   │ 12.5 - 35.8s│  │ │ 2 │ Verse   │ 15.2 - 42.1s│     │
│ │ 3 │ Chorus★ │ 35.8 - 58.2s│  │ │ 3 │ Chorus★ │ 42.1 - 65.8s│     │
│ │ 4 │ Bridge  │ 58.2 - 78.9s│  │ │ 4 │ Bridge  │ 65.8 - 88.4s│     │
│ │ 5 │ Outro   │ 78.9 - 95.3s│  │ │ 5 │ Outro   │ 88.4 - 102.7s│    │
│ └───┴─────────┴─────────────┘  │ └───┴─────────┴─────────────┘     │
│                                 │                                    │
│ Selected: [3] Chorus            │ Selected: [3] Chorus               │
│ Duration: 22.4s | Energy: 84/100│ Duration: 23.7s | Energy: 78/100  │
│                                 │                                    │
│ Commands:                       │ Commands:                          │
│  s1-s5: Select section          │  t1-t5: Select section             │
│  play: Play selected section    │  play: Play selected section       │
│  next: Move to Song B selection │                                    │
├─────────────────────────────────┴────────────────────────────────────┤
│                    TRANSITION CONFIGURATION                          │
├──────────────────────────────────────────────────────────────────────┤
│ Type: [1] Overlap  [2] Short Gap  [3] No Break                      │
│ Current: [2] Short Gap                                               │
│                                                                      │
│ Parameters:                                                          │
│  transition_window:  8.0s    [↑↓ to adjust, step: 0.5s]            │
│                              ████████░░░░░░░░ (50%)                  │
│  gap_window:         2.0s    [↑↓ to adjust, step: 0.5s]            │
│                              ████░░░░░░░░░░░░ (25%)                  │
│  stems_to_fade:      ☑ vocals  ☑ drums  ☐ bass  ☐ other            │
│                              [v,d,b,o keys to toggle]                │
│  fade_window_pct:    80%     [↑↓ to adjust, step: 5%]              │
│                              ████████████████░░ (80%)                │
│                                                                      │
│ Compatibility Score: 78.5 / 100                                     │
│  ├─ Tempo:      85.0 / 100  (136.0 → 128.0 BPM, diff: 5.9%)       │
│  ├─ Key:        72.0 / 100  (D major → G major)                    │
│  ├─ Energy:     80.0 / 100  (diff: 2.3 dB)                         │
│  └─ Embeddings: 75.0 / 100  (average stem similarity)              │
│                                                                      │
│ Commands:                                                            │
│  type: Change transition type                                        │
│  param: Adjust parameters                                            │
│  reset: Reset to defaults                                            │
├──────────────────────────────────────────────────────────────────────┤
│                             ACTIONS                                  │
├──────────────────────────────────────────────────────────────────────┤
│  [P] Preview Transition  │  [S] Save Transition  │  [Q] Quit        │
│  [H] Help                │  [R] Reset Parameters │  [B] Back        │
└──────────────────────────────────────────────────────────────────────┘
│ Status: Ready to preview. Press P to generate transition.            │
└──────────────────────────────────────────────────────────────────────┘
```

### Navigation Flow

```
1. Launch Application
         ↓
2. Song A Selection
   - Browse list of 11 songs
   - View metadata (Key, BPM, Duration)
   - Command: Select song number (1-11)
         ↓
3. Song A Section Selection
   - View section list (Verse, Chorus, Bridge, etc.)
   - Play individual sections for preview
   - Command: s1-s5 to select, 'play' to preview
         ↓
4. Song B Selection
   - Browse list of 11 songs
   - View metadata
   - Command: Select song number (1-11)
         ↓
5. Song B Section Selection
   - View section list
   - Play individual sections
   - Command: t1-t5 to select, 'play' to preview
         ↓
6. Transition Type Selection
   - Choose: [1] Overlap, [2] Short Gap, [3] No Break
   - Display default parameters for chosen type
   - Command: type, then 1-3
         ↓
7. Parameter Adjustment
   - Adjust transition_window, overlap/gap, stems, fade %
   - Real-time validation and visual feedback
   - Command: param, then arrow keys/toggles
         ↓
8. Preview
   - Generate transition (2-5 seconds)
   - Play with seek controls (← → for ±5s)
   - Command: p
         ↓
9. Refine or Save
   - Return to step 7 for adjustments, OR
   - Save to disk with metadata
   - Command: s (save) or param (adjust)
         ↓
10. Save
   - Prompt for filename
   - Default: transition_{songA}_{sectionA}_to_{songB}_{sectionB}.flac
   - Export FLAC + JSON metadata
   - Command: Enter filename or press Enter for default
```

### Keyboard Controls

| Key | Action | Context |
|-----|--------|---------|
| `1-11` | Select song | Song selection mode |
| `s1-s5` | Select section for Song A | Section selection |
| `t1-t5` | Select section for Song B | Section selection |
| `play` | Play selected section | Section selected |
| `type` | Change transition type | Configuration mode |
| `1-3` | Select transition type | After 'type' command |
| `param` | Enter parameter adjustment | Configuration mode |
| `↑↓` | Adjust numeric parameter | Parameter mode |
| `v,d,b,o` | Toggle stems (vocals, drums, bass, other) | Parameter mode |
| `p` | Preview transition | All selections complete |
| `s` | Save transition | After preview |
| `r` | Reset parameters to defaults | Configuration mode |
| `b` | Back to previous step | Any mode |
| `h` | Show help | Any mode |
| `q` | Quit application | Any mode |
| `←→` | Seek ±5s during playback | Playback mode |
| `Ctrl+C` | Stop playback | Playback mode |

## Data Models

### Song

```python
@dataclass
class Song:
    filename: str                    # e.g., "do_it_again.mp3"
    filepath: Path                   # Full path to original audio
    duration: float                  # Total duration in seconds
    tempo: float                     # BPM
    key: str                        # Musical key (e.g., "D major")
    loudness_db: float              # Average loudness
    spectral_centroid: float        # Brightness measure
    sections: List[Section]         # List of sections

    def get_section(self, index: int) -> Section:
        """Get section by index."""

    def get_section_by_label(self, label: str) -> List[Section]:
        """Get all sections with matching label."""
```

### Section

```python
@dataclass
class Section:
    song_filename: str              # Parent song
    index: int                      # Section index in song
    label: str                      # e.g., "chorus", "verse", "bridge"
    start: float                    # Start time in seconds
    end: float                      # End time in seconds
    duration: float                 # Duration in seconds
    tempo: float                    # Section-specific BPM
    key: str                        # Section-specific key
    energy_score: float             # 0-100 energy rating
    loudness_db: float              # Average loudness
    spectral_centroid: float        # Brightness

    def get_compatibility(self, other: 'Section') -> float:
        """Calculate compatibility score with another section."""
```

### TransitionConfig

```python
@dataclass
class TransitionConfig:
    transition_type: TransitionType              # OVERLAP, SHORT_GAP, NO_BREAK
    transition_window: float = 8.0               # seconds
    overlap_window: float = 4.0                  # seconds (Overlap only)
    gap_window: float = 2.0                      # seconds (Short Gap only)
    stems_to_fade: List[str] = field(default_factory=lambda: ['vocals', 'drums'])
    fade_window_pct: int = 80                    # 0-100%

    # Source selections
    song_a: Song
    section_a: Section
    song_b: Song
    section_b: Section

    # Compatibility info
    compatibility_score: float = 0.0
    tempo_score: float = 0.0
    key_score: float = 0.0
    energy_score: float = 0.0
    embeddings_score: float = 0.0

    def validate(self) -> bool:
        """Validate all parameters are within acceptable ranges."""

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""

    @classmethod
    def from_dict(cls, data: dict) -> 'TransitionConfig':
        """Deserialize from dictionary."""
```

### TransitionType

```python
class TransitionType(Enum):
    OVERLAP = "overlap"              # Intro Overlap from PDF
    SHORT_GAP = "short_gap"          # Short Gap from PDF
    NO_BREAK = "no_break"            # No Break from PDF
```

## Audio Processing

### Stem Loading

**Source**: `/Users/mhuang/Projects/Development/stream_of_worship/poc_output_allinone/stems/{song_stem}/`

**Files per Song**:
- `bass.wav` - Bass track
- `drums.wav` - Drums/percussion track
- `other.wav` - Other instruments (keys, strings, etc.)
- `vocals.wav` - Vocal track

**Loading Process**:
1. Map song filename to stem directory (e.g., `do_it_again.mp3` → `do_it_again/`)
2. Load each stem using `librosa.load(sr=44100, mono=False)`
3. Ensure stereo format (2 channels)
4. Extract section range: `stem[:, start_sample:end_sample]`
5. Cache loaded stems in memory for reuse

**Sample Rate**: 44100 Hz (standard CD quality)
**Format**: Stereo (2 channels)

### Fade Curves

**Equal-Power Crossfade**:
- Preserves energy during fade
- Fade-out curve: `sqrt(1 - t)` where t ∈ [0, 1]
- Fade-in curve: `sqrt(t)` where t ∈ [0, 1]
- Ensures total energy remains constant

**Linear Fade** (alternative):
- Simple linear interpolation
- Fade-out curve: `1 - t`
- Fade-in curve: `t`
- Used for individual stems when not crossfading

### Audio Mixing

**Overlap Mixing**:
```python
# Mix two stereo arrays
mixed = array_a + array_b

# Clip to prevent distortion
mixed = np.clip(mixed, -1.0, 1.0)
```

**Concatenation**:
```python
# Join arrays along time axis (axis=1 for stereo)
result = np.concatenate([part_a, part_b, part_c], axis=1)
```

### Export Format

**Audio**:
- Format: FLAC (Free Lossless Audio Codec)
- Sample rate: 44100 Hz
- Channels: Stereo (2)
- Bit depth: 16-bit
- Compression: Default FLAC compression

**Metadata** (JSON):
```json
{
  "version": "1.0",
  "generated_at": "2026-01-09T10:30:00",
  "transition_type": "short_gap",
  "parameters": {
    "transition_window": 8.0,
    "gap_window": 2.0,
    "stems_to_fade": ["vocals", "drums"],
    "fade_window_pct": 80
  },
  "song_a": {
    "filename": "do_it_again.mp3",
    "key": "D major",
    "tempo": 136.0,
    "section": {
      "index": 5,
      "label": "chorus",
      "start": 63.21,
      "end": 85.27,
      "duration": 22.06
    }
  },
  "song_b": {
    "filename": "heaven_open.mp3",
    "key": "G major",
    "tempo": 128.0,
    "section": {
      "index": 3,
      "label": "chorus",
      "start": 42.1,
      "end": 65.8,
      "duration": 23.7
    }
  },
  "compatibility": {
    "overall_score": 78.5,
    "tempo_score": 85.0,
    "key_score": 72.0,
    "energy_score": 80.0,
    "embeddings_score": 75.0
  },
  "audio": {
    "sample_rate": 44100,
    "channels": 2,
    "duration": 18.0,
    "format": "flac"
  }
}
```

## Integration with Existing System

### Metadata Sources

**Section Features** (`poc_output_allinone/section_features.json`):
- Section-level tempo, key, energy
- Start/end times for each section
- Section labels (verse, chorus, bridge, etc.)
- Energy scores (0-100)
- Embedding vectors (optional for advanced compatibility)

**Song Metadata** (`poc_output_allinone/poc_full_results.json`):
- Song-level tempo, key, duration
- Beat grid and downbeats
- Loudness and spectral features

### Audio Sources

**Stems** (`poc_output_allinone/stems/{song_name}/`):
- 4-stem separation (bass, drums, other, vocals)
- WAV format, 44100 Hz, stereo
- Used for transition generation

**Original Audio** (`poc_audio/{song_name}.mp3`):
- Original full-mix MP3 files
- Used for individual section playback (optional)

### Reusable Components

**From `review_transitions.py`**:
- Audio playback with sounddevice
- Arrow key seeking (±5s)
- Terminal input handling (tty.setcbreak)

**From `generate_section_transitions.py`**:
- Stem loading pattern
- Section extraction logic
- Audio concatenation and mixing

**From `analyze_sections.py`**:
- Compatibility scoring formulas
- Tempo/key/energy weights (25/25/15/35%)
- Feature extraction patterns

## Default File Paths

### Input Data
- Stems: `poc_output_allinone/stems/`
- Metadata: `poc_output_allinone/section_features.json`
- Song data: `poc_output_allinone/poc_full_results.json`
- Original audio: `poc_audio/`

### Output Location
- Default save directory: `section_transitions/`
- Filename pattern: `transition_{songA}_{sectionA}_to_{songB}_{sectionB}.flac`
- Metadata: Same name with `.json` extension

### Example
```
Input:
  - do_it_again: poc_output_allinone/stems/do_it_again/*.wav
  - heaven_open: poc_output_allinone/stems/heaven_open/*.wav

Output:
  - section_transitions/transition_do_it_again_chorus_to_heaven_open_chorus.flac
  - section_transitions/transition_do_it_again_chorus_to_heaven_open_chorus.json
```

## Implementation Priorities

### Phase 1: Core Functionality (MVP)
1. ✅ Data models (Song, Section, TransitionConfig)
2. ✅ Metadata loader (JSON parsing)
3. ✅ Basic UI layout (2-column with Rich)
4. ✅ Song and section selection
5. ✅ Transition generator (3 algorithms)
6. ✅ Audio preview
7. ✅ Export to FLAC + JSON

### Phase 2: Enhanced UX
8. Real-time parameter controls with visual feedback
9. Compatibility score display
10. Help screen and keyboard shortcuts
11. Error handling and validation
12. Progress indicators during generation

### Phase 3: Polish
13. Individual section playback
14. Parameter presets
15. Recently used songs/sections
16. Undo/redo for parameter changes
17. Batch export multiple configurations

## Testing Strategy

### Unit Tests
- Transition algorithm correctness (fade curves, mixing)
- Parameter validation
- Metadata loading
- Stem loading and caching

### Integration Tests
- End-to-end workflow (select → configure → preview → save)
- File I/O (FLAC export, JSON metadata)
- UI navigation flow

### Manual Testing Checklist
- [ ] All 11 songs load correctly with metadata
- [ ] Section lists display for all songs
- [ ] Individual section playback works
- [ ] All 3 transition types generate correctly
- [ ] Parameters adjust in real-time
- [ ] Preview plays audio smoothly
- [ ] Seek controls work during playback (←→)
- [ ] Saved FLAC files play correctly
- [ ] JSON metadata contains all required fields
- [ ] Compatibility scores calculate correctly
- [ ] UI renders properly in terminal (80x24 minimum)

## Performance Considerations

### Stem Loading
- **First load**: ~2-3 seconds per song (load 4 stems from disk)
- **Cached load**: <100ms (in-memory cache)
- **Strategy**: Lazy load stems only when needed for preview/save

### Transition Generation
- **Overlap**: ~1-2 seconds (load stems + fade + mix)
- **Short Gap**: ~1-2 seconds (load stems + fade + silence + concatenate)
- **No Break**: ~2-3 seconds (load stems + crossfade + mix)

### UI Responsiveness
- **Layout render**: <50ms (Rich library handles efficiently)
- **Parameter update**: <10ms (immediate visual feedback)
- **Input handling**: <5ms (keyboard polling)

## Dependencies

### Python Packages
- `rich` - Terminal UI (panels, tables, layout)
- `sounddevice` - Audio playback
- `soundfile` - Audio I/O (FLAC export)
- `librosa` - Audio loading and processing
- `numpy` - Array operations and mixing
- `dataclasses` - Data model definitions (built-in)
- `pathlib` - File path handling (built-in)
- `json` - Metadata serialization (built-in)

### System Requirements
- Python 3.8+
- Terminal with 80x24 minimum size
- Audio output device (for playback)
- ~2GB available RAM (for stem caching)

## Future Enhancements

### Advanced Features
1. **Beat-matching**: Automatically align beats during No Break transitions
2. **Key transposition**: Transpose one song to match the other's key
3. **Tempo adjustment**: Time-stretch to match tempos
4. **Custom fade curves**: Exponential, logarithmic, S-curve
5. **Multi-section transitions**: Chain multiple sections (A → B → C)

### UI Improvements
1. **Waveform visualization**: Show audio waveforms for sections
2. **Spectrum analyzer**: Real-time frequency display during playback
3. **Keyboard shortcuts cheat sheet**: Always visible
4. **Color themes**: Light/dark mode support
5. **Mouse support**: Click to select (optional)

### Export Options
1. **Multiple formats**: WAV, MP3, AAC export
2. **Batch export**: Save multiple parameter variations
3. **Playlist generation**: Create M3U with transitions
4. **Cloud upload**: Direct upload to storage services

## Conclusion

This design provides a comprehensive blueprint for implementing an interactive worship transition builder that:
- Faithfully implements the 3 PDF-based transition types
- Provides complete parameter control with real-time adjustment
- Offers a professional 2-column TUI for intuitive song comparison
- Generates high-quality FLAC transitions with full metadata
- Integrates seamlessly with existing stem and metadata infrastructure

The modular architecture allows for phased implementation, starting with core functionality and progressively adding enhanced features based on user feedback.

---

**Next Steps**:
1. Review and approve this design document
2. Begin Phase 1 implementation (core functionality)
3. Conduct user testing with MVP
4. Iterate based on feedback
5. Implement Phase 2 and 3 enhancements
