# Song Transition Preview App – Complete Design Specification

---

## 1. Overview & Goals

The Song Transition Preview App is a **keyboard-first, text-based Python terminal application** designed to help users experiment with, evaluate, and save audio transitions between two songs.

Primary goals:
- Fast experimentation with song sections and transition parameters
- Non-destructive iteration and comparison
- Clear separation between creation, evaluation, and discovery
- Scalability from small song lists to large catalogs
- Session-based workflow optimized for creative flow

The application is designed to be implemented using a TUI framework such as **Textual**, but this document is framework-agnostic.

---

## 2. High-Level Architecture

### Core Components

- **App Controller**
  - Owns global state
  - Manages screen switching
  - Owns shared services (playback, transition generation)

- **Screens**
  - GenerationScreen
  - HistoryScreen
  - SongSearchScreen
  - HelpOverlayScreen (modal)

- **Services**
  - PlaybackService (PyAudio backend)
  - TransitionGenerationService (blocking with progress updates)
  - SessionHistoryStore (in-memory, capped at 50 transitions)
  - SongCatalogLoader (JSON-based, eager loading)
  - SessionLogger (writes to ./session_TIMESTAMP.log)
  - ErrorLogger (writes to ./transitions_errors.log)

- **Data Sources**
  - Song metadata and section data loaded from JSON files (output of poc_analysis_allinone.py and analyze_sections.py)
  - Generated audio stored in temporary directory (ephemeral) or user-specified output folder (saved)
  - Configuration loaded from ./config.json

---

## 3. Screens Overview

### Screen List

| Screen | Purpose |
|--------|---------|
| GenerationScreen | Select songs/sections, configure parameters, generate transitions |
| HistoryScreen | Review, compare, modify, and save generated transitions |
| SongSearchScreen | Search and filter songs when catalog grows large (modal) |
| HelpOverlayScreen | Display keyboard shortcuts (modal, triggered by ? or F1) |

Screens are mutually exclusive except for modal overlays. SongSearchScreen and HelpOverlayScreen behave as **modal screens** that always return to the previous screen.

---

## 4. Generation Screen

### Responsibilities

- Select **Song A** and **Song B** (cannot select same song for both)
- Select **exactly one section** per song
- Display song-level and section-level metadata
- Configure transition parameters (base + type-specific)
- Generate transitions (standard or ephemeral)
- Preview individual song sections

### Conceptual Layout

```
┌──────────────────────────────────────────────────────────────┐
│ GENERATION SCREEN                                            │
│ [MODIFY MODE: Based on Transition #3]  ← if in modify mode  │
│                                                              │
│ ┌───────────────┬───────────────┐                            │
│ │ SONG A        │ SONG B        │                            │
│ │ Song List     │ Song List     │  Songs sorted by           │
│ │ (with inline  │ (sorted by    │  compatibility when        │
│ │  BPM + Key)   │  compat %)    │  Song A selected           │
│ │               │               │                            │
│ │ Section List  │ Section List  │  Format: "Chorus           │
│ │               │               │  (1:23-2:10, 47s)"         │
│ │               │               │                            │
│ │ Metadata      │ Metadata      │  BPM, Key (C major),       │
│ │ Panel         │ Panel         │  Duration, Compat %        │
│ └───────────────┴───────────────┘                            │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ TRANSITION PARAMETERS                                    │ │
│ │ Type: [Crossfade ▼]                                      │ │
│ │ Overlap: [4.0 beats] (negative = gap)                    │ │
│ │ Fade Window: [8 beats] (symmetric)                       │ │
│ │ Fade Speed: [2 beats]                                    │ │
│ │ Stems to Fade: [All ▼]  (bass, drums, other, vocals)    │ │
│ │ [Type-specific params in extension dict...]              │ │
│ │                                                          │ │
│ │ ⚠ Warning: Overlap exceeds Song A section  ← if present │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ PLAYBACK & GENERATION                                    │ │
│ │ [Play A]  [Play B]  [Generate]  [Shift+G: Quick Test]   │ │
│ │                                                          │ │
│ │ Progress: [Spinner] 12.3s elapsed  ← during generation   │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ FOOTER: H=History  /=Search  Tab=Next Panel  ?=Help         │
│         ←=Seek -3s  →=Seek +4s  Space=Play/Pause            │
└──────────────────────────────────────────────────────────────┘
```

### Song & Section Selection Rules

- Songs are loaded from pre-analyzed JSON files (eager loading at startup)
- Song JSON schema: output from `poc_analysis_allinone.py`
  - Required fields: filename, filepath, duration, tempo, key, mode, key_confidence, full_key, loudness_db, spectral_centroid, sections[]
  - Sections include: label, start, end, duration
  - Compatibility scores are song-level (from previous analysis output)
- Each song exposes a list of sections in chronological order
- User may select **only one section per song**
- Cannot select the same song for both Song A and Song B positions
- Song B list is **sorted by compatibility score** (descending) after Song A is selected
  - Format: "Song Title • 128 BPM • C major (87%)"
  - Ties broken alphabetically by song title
- Highlighting a song displays:
  - BPM
  - Key (traditional notation: "C major", "A minor")
  - Duration (MM:SS format)
  - Compatibility score (if Song A selected)
- Highlighting a section displays:
  - Section label and time range: "Chorus (1:23-2:10, 47s)"
  - Section-level tempo, key, energy metrics
- Selected items use background color change only (no icons/checkboxes)

### Startup Behavior

- App validates all audio files at startup
- Shows warnings for missing files but allows continuation with available songs
- If critical files missing, displays clear error messages

---

## 5. Generation Modes

### Modes

| Mode | Description |
|------|-------------|
| Fresh | Default mode for new transitions |
| Modify | Parameters and selections pre-filled from a historical transition |

### Modify Mode Rules

- Entered from HistoryScreen via 'M' key
- Shows banner at top: "MODIFY MODE: Based on Transition #3"
- Pre-selects everything: Song A, Song B, sections, and all parameters
- Original transition remains unchanged in history
- Generating creates a **new transition record**
- `Esc` exits Modify Mode, returns to Fresh Mode, and resets all parameters to defaults

---

## 6. Transition Parameters

### Base Parameters (Common to All Transition Types)

All transition types must support these base parameters:

- **overlap** (float, in beats)
  - Amount of audio overlap between Song A and Song B
  - Can be negative to indicate a gap instead of overlap

- **fade_window** (float, in beats)
  - Total duration of fade window
  - Applied symmetrically to both Song A and Song B

- **fade_speed** (float, in beats)
  - How quickly the fade occurs within the fade window

- **stems_to_fade** (list of strings)
  - Which stems to apply fading: ["all"] or combinations like ["bass", "drums", "vocals"]
  - Options: "bass", "drums", "other", "vocals"

### Type-Specific Parameters

- Stored in an **extension dictionary** keyed by parameter name
- Schema is intentionally flexible to support different transition algorithms
- Examples might include:
  - Beat alignment offset
  - EQ adjustments
  - Reverb/delay effects
  - Time-stretching ratios

### Parameter Behavior

- Parameters are editable in GenerationScreen
- Read-only snapshots displayed in HistoryScreen
- Changing transition type **auto-resets all parameters** to defaults
- Parameter validation:
  - **Validate with warnings, not blocks**
  - Show inline warning (e.g., "⚠ Overlap exceeds Song A section")
  - Warning auto-dismisses after user edits the problematic parameter
  - Generate button still enabled (warnings don't block generation)

---

## 7. Playback System

### Capabilities

- Play:
  - Song A section (from beginning)
  - Song B section (from beginning)
  - Generated transition
  - Historical transition
- Shared across all screens
- Uses **PyAudio** backend for cross-platform support

### Controls

| Key | Action |
|-----|--------|
| Space | Play / Pause (silently ignored if no audio loaded) |
| ← | Seek backward 3 seconds |
| → | Seek forward 4 seconds (wraps to beginning if past end) |

### Playback Behavior

- Playback is clamped to the active audio segment boundaries
- Seeking past the end of audio wraps around to the beginning
- Playback stops automatically when:
  - Switching screens
  - Starting a new generation (blocks and stops playback)
- **Auto-play**: Newly generated transitions play immediately upon successful generation

---

## 8. Generation Process

### Standard Generation (G key)

1. User presses 'G' with valid selections
2. **Validation**:
   - All required selections made (songs + sections)
   - Parameters checked for warnings (shown but don't block)
3. **Generation starts**:
   - Playback stops immediately
   - UI shows progress: spinner with elapsed time (e.g., "⏳ 8.3s elapsed")
   - Main UI thread is **blocked** with progress updates
   - No cancel option (must complete)
4. **On success**:
   - Transition added to history with sequential ID (#1, #2, #3...)
   - Audio auto-plays immediately
   - User can review in History or continue generating
5. **On failure**:
   - Toast/banner notification: "Generation failed. See log for details."
   - Error logged to `./transitions_errors.log`
   - User remains in Generation screen, can adjust parameters and retry

### Ephemeral Generation (Shift+G)

- Quick test mode: generates transition without adding to history
- Audio saved to temporary directory
- Plays immediately after generation
- Temp files auto-deleted on app exit
- Use case: rapid experimentation without cluttering history

### Generation Service

- TransitionGenerationService interface (abstracted)
- Runs on main thread with progress callbacks
- Returns:
  - Success: path to generated audio file + metadata
  - Failure: error message + exception details

---

## 9. History / Comparison Screen

### Responsibilities

- Display all transitions generated in current session (max 50)
- Allow playback and comparison
- Display parameter snapshots (read-only)
- Save transitions to disk with optional notes
- Enter Modify Mode

### Conceptual Layout

```
┌──────────────────────────────────────────────┐
│ HISTORY SCREEN                               │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ TRANSITION LIST (Newest First)           │ │
│ │ → #5 Crossfade: Song A → Song B (87%)   │ │
│ │   #4 Beatmatch: Song C → Song D (92%)   │ │
│ │   #3 Crossfade: Song A → Song E (75%)   │ │
│ │   ... (max 50 transitions)               │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ TRANSITION DETAILS                       │ │
│ │ Type: Crossfade                          │ │
│ │ Songs: Song A [Chorus] → Song B [Verse]  │ │
│ │ Compatibility: 87%                       │ │
│ │ Generated: 14:32:15                      │ │
│ │ Status: ○ Temporary  (or ● Saved)        │ │
│ │ Saved Path: /path/to/file.flac ← if saved│ │
│ │                                          │ │
│ │ PARAMETERS (Read-Only):                  │ │
│ │ • Overlap: 4.0 beats                     │ │
│ │ • Fade Window: 8 beats                   │ │
│ │ • Fade Speed: 2 beats                    │ │
│ │ • Stems: All                             │ │
│ │ • [Type-specific params...]              │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ PLAYBACK & ACTIONS                       │ │
│ │ [Space: Play]  [S: Save]  [M: Modify]    │ │
│ │ [D: Delete]                              │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ FOOTER: G=Generate  M=Modify  S=Save  D=Delete│
│         Space=Play/Pause  ← →  ?=Help        │
└──────────────────────────────────────────────┘
```

### History Rules

- History is **session-scoped** (in-memory)
- Capped at **50 transitions** (oldest auto-removed when exceeded)
- Transitions are displayed in **reverse chronological order** (newest first)
- Transitions are **immutable** (cannot edit in-place)
- Transition IDs: sequential numbers (#1, #2, #3...)
- Selecting a transition:
  - Updates playback target
  - Displays its parameters (read-only)
  - Shows compatibility score with metadata
- **Modify** (M key):
  - Loads parameters into GenerationScreen
  - Pre-selects songs, sections, and all parameters
  - Switches to Generation screen in Modify Mode
- **Save** (S key):
  - Prompts for optional note/description
  - Saves as FLAC to configured output folder
  - Filename auto-generated from song names
  - Updates transition status to "Saved"
  - Path displayed in details panel after save
- **Delete** (D key):
  - Cannot delete if transition is currently playing (shows error: "Cannot delete playing transition")
  - Otherwise: prompts for confirmation, then removes from history

### Exit Warning

- When user exits app (Ctrl+C or Quit), check for unsaved transitions
- If any exist, prompt: "You have N unsaved transitions. Exit anyway? [Y/n]"
- If user confirms, exit normally
- If user cancels, return to app

---

## 10. Full Song Output Generation

### Overview

Transform the transition builder from generating isolated transitions to creating complete song sets suitable for worship services. The output consists of Song A (up to transition point) + Transition + Song B (from transition point onward).

### Output Structure

The full song output combines three parts:
1. **Song A Prefix**: All sections BEFORE the selected section (excluding the selected section itself)
2. **Transition**: Previously generated transition audio (includes selected sections from both songs + gap)
3. **Song B Suffix**: All sections AFTER the selected section (excluding the selected section itself)

Example:
- Song A has sections: [Intro, Verse 1, Chorus 1, Verse 2, Chorus 2, Bridge, Chorus 3, Outro]
- User selects Chorus 2 as transition point
- Song A prefix: [Intro, Verse 1, Chorus 1, Verse 2]
- Transition: [Chorus 2 from Song A + gap + Verse from Song B]
- Song B suffix: [Chorus, Bridge, Outro]
- Final output: All above concatenated into single audio file

### Key Bindings

| Key | Action |
|-----|--------|
| o | Create full song output from last generated transition |

### Workflow

1. User selects Song A and section (e.g., Chorus)
2. User selects Song B and section (e.g., Verse)
3. User generates transition (Shift-T key)
4. User presses 'o' to create full song output
5. System validates transition exists
6. System extracts Song A sections before selected section
7. System loads previously generated transition audio
8. System extracts Song B sections after selected section
9. System concatenates all parts with proper sample rate handling
10. Output saved to `song_sets_output/` directory
11. History record created with special "full_song" type
12. Auto-plays if configured

### Output Directory

- **Location**: `song_sets_output/` (separate from `transitions_output/`)
- **Format**: FLAC
- **Naming Convention**: `songset_{songA}_{sectionA}_to_{songB}_{sectionB}.flac`
- **Example**: `songset_do_it_again_chorus_to_joy_to_heaven_verse.flac`

### History Integration

Full song outputs are tracked in history with special characteristics:

- **Icon**: `♫` (musical note) vs `⇄` (arrow for transitions)
- **Type Display**: "Full Song" instead of transition type
- **Metadata Includes**:
  - Number of Song A prefix sections
  - Number of Song B suffix sections
  - Total duration
  - Transition details

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| First section of Song A selected | Output = Transition + Song B suffix |
| Last section of Song B selected | Output = Song A prefix + Transition |
| Both first and last sections | Output = Just the transition |
| Sample rate mismatch | Automatic resampling using librosa |
| No transition generated yet | Warning: "Generate a transition first (Shift-T)" |
| Selections changed | Warning: "Generate transition first" |

### Parameters

Full song output records include:
- `output_type`: "full_song"
- `num_song_a_sections_before`: Count of prefix sections
- `num_song_b_sections_after`: Count of suffix sections
- `total_duration`: Complete output duration in seconds
- `sample_rate`: Audio sample rate

### Use Case

**Worship Service Preparation**:
1. Sound engineer wants complete song transitions for Sunday service
2. Generates transition between "Do It Again" and "Joy to Heaven"
3. Previews transition point (t key)
4. Satisfied with transition, generates full version (Shift-T)
5. Creates complete worship set with 'o' key
6. Output includes full intro/verses from Song A, transition, and continuation through Song B
7. File ready to load into playback system for live service

---

## 11. Song Search Screen

### Purpose

Provides scalable song discovery when catalog grows large (100+ songs).

### Invocation

- Opened from GenerationScreen via '/' key
- Context-aware: knows whether selecting for Song A or Song B
- Modal behavior: always returns to GenerationScreen

### Conceptual Layout

```
┌──────────────────────────────────────────────┐
│ SONG SEARCH                                  │
│ Selecting for: Song A                        │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ SEARCH & FILTERS                         │ │
│ │ Keyword: [____________] (debounced)      │ │
│ │ BPM Range: [100] - [140] (32 matches)    │ │
│ │ Key: [Any ▼]             (32 matches)    │ │
│ │ Tags: [Worship ▼]        (18 matches)    │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ RESULTS LIST                             │ │
│ │ → Song 1 • 124 BPM • C major             │ │
│ │   Song 2 • 128 BPM • C major             │ │
│ │   Song 3 • 132 BPM • D major             │ │
│ │   ... (18 matches)                       │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ SONG METADATA                            │ │
│ │ Duration: 4:23                           │ │
│ │ Sections: 8 (2 choruses, 3 verses...)   │ │
│ │ Energy: -12.3 dB                         │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ FOOTER: Space=Preview  Enter=Select  Esc=Cancel│
└──────────────────────────────────────────────┘
```

### Search Behavior

- **Keyword filtering**: Debounced (300ms delay after last keystroke)
- **Multi-criteria filtering**: AND logic across all active filters
- **Result counts**: Each filter shows current match count (e.g., "BPM Range (32 matches)")
- **Filter persistence**: Filters remain set until app exit (across multiple search invocations)
- **Preview playback**:
  - Space key plays first 10 seconds of highlighted song
  - Plays once and stops (no looping)
  - Space again stops preview

### Rules

- Selecting a song (Enter) returns to GenerationScreen with song selected
- Cancelling (Esc) leaves previous selection unchanged
- Preview playback uses full song (not section-specific)

---

## 12. Help Overlay Screen

### Purpose

Display all keyboard shortcuts for current context.

### Invocation

- Triggered by '?' or F1 key from any screen
- Modal overlay (semi-transparent background)
- Esc dismisses overlay

### Content

- Lists all keybindings organized by screen/context
- Format:
  ```
  GENERATION SCREEN
    Tab         Cycle through panels
    G           Generate transition
    Shift+G     Quick test (ephemeral)
    H           View history
    /           Search songs
    Space       Play/Pause
    ←/→         Seek backward/forward
    Esc         Exit Modify Mode (if active)
    ?/F1        This help

  HISTORY SCREEN
    G           New transition
    M           Modify selected
    S           Save selected
    D           Delete selected
    ...
  ```

---

## 13. State Model

### App-Level State

```python
class ActiveScreen(Enum):
    GENERATION = "generation"
    HISTORY = "history"
    SONG_SEARCH = "song_search"
    HELP_OVERLAY = "help_overlay"

class GenerationMode(Enum):
    FRESH = "fresh"
    MODIFY = "modify"

class AppState:
    # Screen management
    active_screen: ActiveScreen
    previous_screen: ActiveScreen  # For modal overlays

    # Generation state
    generation_mode: GenerationMode
    base_transition_id: str | None  # ID of transition being modified

    # Song/section selection
    left_song_id: str | None
    left_section_id: str | None
    right_song_id: str | None
    right_section_id: str | None

    # Parameters (base + extension)
    transition_type: str | None
    overlap: float | None  # in beats, can be negative
    fade_window: float | None  # in beats
    fade_speed: float | None  # in beats
    stems_to_fade: list[str]  # e.g., ["bass", "vocals"]
    extension_parameters: dict  # Type-specific params

    # History
    transition_history: list[TransitionRecord]  # Max 50 items
    selected_history_index: int | None

    # Playback
    playback_target: str | None  # Path to audio file
    playback_position: float  # seconds
    playback_state: PlaybackState  # PLAYING, PAUSED, STOPPED

    # UI State
    active_validation_warnings: list[str]
    generation_in_progress: bool
    generation_start_time: float | None

class TransitionRecord:
    id: int  # Sequential: 1, 2, 3...
    transition_type: str
    song_a_filename: str
    song_b_filename: str
    section_a_label: str
    section_b_label: str
    compatibility_score: float
    generated_at: datetime
    audio_path: Path
    is_saved: bool
    saved_path: Path | None
    save_note: str | None
    # Snapshot of all parameters
    parameters: dict  # Base + extension params
```

---

## 14. Screen Transition Diagram

```
┌──────────────┐
│ Generation   │◄──────────────┐
└──┬───────┬───┘               │
   │       │                   │
   │ H     │ /                 │
   ▼       │                   │
┌──────────────┐               │
│ History      │───────────────┘
└──────────────┘     G / M


┌──────────────┐
│ Song Search  │ (modal from Generation)
└──────────────┘
   Enter/Esc returns to Generation

┌──────────────┐
│ Help Overlay │ (modal from any screen)
└──────────────┘
   Esc returns to previous screen
```

---

## 15. Generation Mode State Diagram

```
FRESH MODE (default)
   │
   │ Modify (M key from History)
   ▼
MODIFY MODE
   │ Banner: "MODIFY MODE: Based on Transition #3"
   │ Pre-selected: songs + sections + params
   │
   │ Generate (G) or Esc
   ▼
FRESH MODE (reset params)
```

---

## 16. Transition Lifecycle

```
Select Songs & Sections
        ↓
Configure Parameters
        ↓
Generate Transition (G or Shift+G)
        ↓
    ┌───┴────┐
    │        │
    ▼        ▼
Standard    Ephemeral (Quick Test)
    │        │
    ├────────┤
    ↓        ↓
Store in    Temp Storage
Session     (auto-delete)
History
    ↓
Auto-Play
    ↓
Playback / Compare / Modify / Save
```

---

## 17. Configuration File (config.json)

### Location
- **./config.json** (project root)

### Schema

```json
{
  "audio_folder": "./poc_audio",
  "output_folder": "./transitions_output",
  "default_transition_type": "crossfade",
  "max_history_size": 50,
  "auto_play_on_generate": true,
  "session_logging": true,
  "error_logging": true
}
```

### Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| audio_folder | string | Path to folder containing audio files and JSON metadata | "./poc_audio" |
| output_folder | string | Path for saving finalized transitions | "./transitions_output" |
| default_transition_type | string | Default transition type on startup | "crossfade" |
| max_history_size | int | Maximum transitions in session history | 50 |
| auto_play_on_generate | bool | Auto-play after successful generation | true |
| session_logging | bool | Enable session event logging | true |
| error_logging | bool | Enable error logging | true |

### Notes
- No compatibility threshold (compatibility used for sorting only, not filtering)
- Paths can be relative or absolute
- Config validated at startup; errors shown if malformed

---

## 18. Data Schema

### Song JSON Schema (from poc_analysis_allinone.py)

```json
{
  "filename": "song_name.mp3",
  "filepath": "/full/path/to/song_name.mp3",
  "duration": 245.67,
  "tempo": 128.5,
  "tempo_source": "allinone",
  "num_beats": 523,
  "beats": [0.0, 0.468, 0.936, ...],
  "num_downbeats": 131,
  "downbeats": [0.0, 1.872, 3.744, ...],
  "key": "C",
  "mode": "major",
  "key_confidence": 0.876,
  "full_key": "C major",
  "key_source": "librosa",
  "loudness_db": -12.3,
  "loudness_std": 3.2,
  "spectral_centroid": 2456.7,
  "num_sections": 8,
  "sections": [
    {
      "label": "intro",
      "start": 0.0,
      "end": 12.5,
      "duration": 12.5
    },
    {
      "label": "verse",
      "start": 12.5,
      "end": 45.3,
      "duration": 32.8
    },
    {
      "label": "chorus",
      "start": 45.3,
      "end": 92.1,
      "duration": 46.8
    }
  ],
  "section_label_source": "allinone_ml",
  "embeddings_shape": [4, 1234, 24],
  "embeddings_mean": 0.023,
  "embeddings_std": 0.456,
  "embeddings_hop_length": 512,
  "embeddings_sr": 22050
}
```

### Section Features JSON Schema (from analyze_sections.py)

Used for section-level compatibility analysis (optional, for advanced features):

```json
{
  "song_filename": "song_name.mp3",
  "section_index": 2,
  "label": "chorus",
  "start": 45.3,
  "end": 92.1,
  "duration": 46.8,
  "tempo": 129.2,
  "key": "C",
  "mode": "major",
  "key_confidence": 0.891,
  "full_key": "C major",
  "loudness_db": -10.5,
  "loudness_std": 2.8,
  "spectral_centroid": 2678.3,
  "energy_score": 87.3,
  "embeddings_shape": [4, 234, 24],
  "embeddings_mean": [[...], [...], [...], [...]],
  "embeddings_std": [[...], [...], [...], [...]]
}
```

### Compatibility Score Schema (Song-Level)

Compatibility scores are computed during the analysis phase (poc_analysis_allinone.py) and stored as part of the song metadata or in a separate compatibility matrix file:

```json
{
  "song_a": "song1.mp3",
  "song_b": "song2.mp3",
  "overall_score": 87.3,
  "tempo_score": 92.1,
  "key_score": 85.0,
  "energy_score": 81.5,
  "tempo_a": 128.0,
  "tempo_b": 130.5,
  "tempo_diff_pct": 1.9,
  "key_a": "C major",
  "key_b": "C major"
}
```

---

## 19. Logging

### Session Log (./session_TIMESTAMP.log)

- Enabled if `session_logging: true` in config.json
- Filename format: `session_2026-01-13_14-32-15.log`
- Logs all user actions and state changes:
  - Screen transitions
  - Song/section selections
  - Parameter changes
  - Generation events (start, success, failure)
  - Playback events
  - Save operations

Example log entries:
```
2026-01-13 14:32:15 [INFO] App started
2026-01-13 14:32:16 [INFO] Loaded 24 songs from ./poc_audio
2026-01-13 14:32:18 [INFO] Song A selected: song1.mp3
2026-01-13 14:32:20 [INFO] Section selected: chorus (1:23-2:10)
2026-01-13 14:32:25 [INFO] Generation started (type: crossfade)
2026-01-13 14:32:37 [INFO] Generation completed (12.3s)
2026-01-13 14:32:37 [INFO] Auto-play started
```

### Error Log (./transitions_errors.log)

- Enabled if `error_logging: true` in config.json
- Appends all error events with full stack traces
- Includes:
  - Generation failures
  - File I/O errors
  - Playback errors
  - Validation errors (critical only)

Example log entries:
```
2026-01-13 14:45:23 [ERROR] Generation failed for transition song1→song2
  Error: Insufficient audio overlap for fade window
  Stack trace:
    File "transition_service.py", line 123, in generate
      raise InsufficientOverlapError(...)
```

---

## 20. Panel Focus and Keyboard Navigation

### Generation Screen Panel Focus

- **Three main panels**: Song A, Song B, Parameters
- **Tab key** cycles focus through panels in order: Song A → Song B → Parameters → Song A
- **Arrow keys** navigate within the focused panel:
  - ↑/↓ navigate lists (songs, sections, parameter fields)
  - ←/→ seek during playback (global, not panel-specific)
- Focus indicated by visual highlight (border or background color)

### Global Keybindings (Available Everywhere)

| Key | Action | Notes |
|-----|--------|-------|
| Space | Play/Pause | Silently ignored if no audio loaded |
| ← | Seek backward 3s | Wraps to beginning if before start |
| → | Seek forward 4s | Wraps to beginning if past end |
| ? or F1 | Show help overlay | Modal, Esc to dismiss |
| Ctrl+C | Exit app | Warns if unsaved transitions exist |

### Generation Screen Specific

| Key | Action | Notes |
|-----|--------|-------|
| Tab | Cycle panel focus | Song A → Song B → Parameters |
| H | Switch to History screen | Preserves all Generation state |
| / | Open Song Search (modal) | Context-aware (Song A or B) |
| Shift+T | Generate transition | Creates transition audio file |
| t | Preview transition | Generates focused preview (last 4 beats + gap + first 4 beats) |
| o | Create output | Creates full song set (Song A prefix + transition + Song B suffix) |
| Esc | Exit Modify Mode | Only if in Modify Mode; resets params |

### History Screen Specific

| Key | Action | Notes |
|-----|--------|-------|
| G | Switch to Generation screen | Enter Fresh Mode |
| M | Modify selected transition | Switch to Generation in Modify Mode |
| S | Save selected transition | Prompts for optional note |
| D | Delete selected transition | Blocked if currently playing |
| ↑/↓ | Navigate transition list | Select different transition |

### Song Search Screen Specific (Modal)

| Key | Action | Notes |
|-----|--------|-------|
| Enter | Select highlighted song | Returns to Generation screen |
| Esc | Cancel search | Returns without changing selection |
| Space | Preview song (10s) | Plays once, press again to stop |
| ↑/↓ | Navigate results | Move through filtered song list |
| Tab | Move between filter fields | Keyword → BPM → Key → Tags |

---

## 21. UI Visual Specifications

### Color Coding

- **Compatibility scores**:
  - ≥80%: Green
  - 60-79%: Yellow
  - 40-59%: Orange
  - <40%: Red

- **Status indicators**:
  - Playing: Green
  - Paused: Yellow
  - Stopped: Gray
  - Generating: Blue (spinner)

### Footer Hints (Context-Aware)

Footer dynamically shows only currently valid actions:

**Generation Screen (Fresh Mode, incomplete selections)**:
```
FOOTER: H=History  /=Search  Tab=Next Panel  ?=Help
```

**Generation Screen (Fresh Mode, ready to generate)**:
```
FOOTER: G=Generate  Shift+G=Quick Test  H=History  Tab=Next  ?=Help
```

**Generation Screen (Modify Mode)**:
```
FOOTER: [MODIFY MODE] G=Generate  Esc=Exit Modify  H=History  ?=Help
```

**History Screen (transition selected)**:
```
FOOTER: M=Modify  S=Save  D=Delete  G=New  Space=Play  ?=Help
```

**History Screen (transition playing)**:
```
FOOTER: Space=Pause  ←/→=Seek  M=Modify  S=Save  ?=Help
```

### Layout Responsiveness

- **Fixed layout**: Panels maintain fixed proportions
- **Scrollbars**: Appear if content exceeds panel height/width
- **Minimum terminal size**: Not enforced, but recommended 120x40 for optimal experience
- **Resize handling**: Scrollbars adjust dynamically

---

## 22. Primary Use Cases

1. **Generate first transition between two songs**
   - User selects Song A, chooses chorus section
   - Song B list sorts by compatibility
   - User selects compatible Song B, chooses verse section
   - Adjusts parameters (overlap, fade window)
   - Presses 'G' to generate
   - Transition auto-plays
   - Added to History as #1

2. **Generate multiple variations by tweaking parameters**
   - User stays in Generation screen after first generation
   - Adjusts fade_speed parameter
   - Presses 'G' again
   - New transition (#2) added to History
   - Auto-plays immediately
   - User presses 'H' to compare #1 and #2

3. **Compare transitions side-by-side via History**
   - User in History screen
   - Navigates between #1 and #2 with ↑/↓
   - Presses Space to play each
   - Views parameter differences in detail panel
   - Decides #2 is better, presses 'S' to save

4. **Modify an existing transition without starting over**
   - User selects transition #2 in History
   - Presses 'M' (Modify)
   - Switches to Generation screen in Modify Mode
   - Banner shows "MODIFY MODE: Based on Transition #2"
   - Songs, sections, and parameters pre-selected
   - User adjusts overlap from 4.0 to 6.0 beats
   - Presses 'G' to generate
   - New transition #3 created (original #2 unchanged)
   - Auto-plays #3

5. **Save selected transitions to disk**
   - User in History screen, selects transition #3
   - Presses 'S' (Save)
   - Prompted: "Save as: transition_songA_to_songB.flac"
   - Prompted: "Note (optional): Final version for Sunday service"
   - Saves to configured output_folder
   - Transition status updates to "● Saved"
   - Path displayed: "/path/to/transitions_output/transition_songA_to_songB.flac"

6. **Search and select songs from large catalogs**
   - User in Generation screen, presses '/' to search
   - Types "praise" in keyword field (debounced)
   - Filters BPM range: 120-130
   - Results update: "18 matches"
   - Previews first song with Space (10s playback)
   - Presses Enter to select
   - Returns to Generation screen with song selected

7. **Quick experimentation with ephemeral generation**
   - User wants to test a wild parameter combination
   - Adjusts fade_speed to extreme value
   - Presses 'Shift+G' (Quick Test)
   - Generates without adding to history
   - Audio saved to temp dir and auto-plays
   - If it sounds bad, user can immediately adjust params and try again
   - If it sounds good, user presses 'G' to generate normally and add to history

---

## 23. UX Principles

- **Keyboard-first interaction**: All actions accessible via keyboard
- **Non-destructive experimentation**: History is immutable, Modify creates new records
- **Clear separation of concerns by screen**: Generation for creation, History for evaluation
- **Fast iteration loop**: Auto-play, preserved state, ephemeral mode
- **Scales from simple to advanced workflows**: Basic (generate + save) to advanced (modify, compare, search)
- **Creative flow preserved across screens**: State persists, no data loss on navigation
- **Immediate feedback**: Auto-play, context-aware hints, inline warnings
- **Fail-safe behaviors**: Prevent destructive actions (delete playing transition, exit with unsaved work)

---

## 24. Edge Cases and Error Handling

### Missing Audio Files
- **At startup**: Scan and show warnings, allow continuation with available songs
- **During selection**: Skip missing songs in lists
- **During generation**: Show error toast + log to error log

### Invalid Metadata
- **Missing required fields**: Skip song, log warning
- **Malformed JSON**: Skip file, log error
- **Incompatible schema version**: Show error, fail gracefully

### Generation Failures
- **Insufficient overlap**: Show error toast + inline warning
- **Missing stems**: Show error, log to error log
- **Timeout (>5 minutes)**: Show error, log as failure
- **Unexpected exception**: Show error toast, log full stack trace

### Playback Errors
- **Audio file not found**: Show error toast, stop playback
- **Unsupported format**: Show error, log details
- **Audio device unavailable**: Show error, suggest checking system audio

### Low Compatibility Scores
- **Song pair <40%**: Show warning when generating: "⚠ Low compatibility (35%). Proceed anyway?"
- **Allow generation**: User can experiment freely
- **Not a blocking error**: Just informational

### History Overflow
- **Cap at 50**: Oldest transition auto-removed
- **User notification**: Toast: "History full. Removed oldest transition (#1)."

### Unsaved Work on Exit
- **Check on quit**: If unsaved transitions exist, prompt: "You have N unsaved transitions. Exit anyway? [Y/n]"
- **Confirmation required**: User must explicitly confirm

---

## 25. Future Extensions (Out of Scope for V1)

- Persistent transition library across sessions
- Waveform visualization in playback panel
- Multi-song chaining (A → B → C sequences)
- MIDI or beat-grid overlays
- Collaborative tagging and sharing
- Advanced section compatibility (chorus-to-chorus, verse-to-bridge)
- Real-time parameter adjustment during playback
- Batch generation (generate all compatible pairs)
- Undo/redo for generation actions
- Export session history as JSON or CSV
- A/B testing mode with blind playback
- Integration with DJ software (export cue points)

---

## 26. Implementation Notes

### Technology Stack (Recommended)

- **TUI Framework**: Textual (Python)
- **Audio Backend**: PyAudio (cross-platform)
- **Audio Processing**: librosa, soundfile
- **Data Format**: JSON (song metadata), FLAC (audio output)
- **Configuration**: JSON (./config.json)

### Project Structure

```
stream_of_worship/
├── config.json
├── poc_audio/              # Input: audio files + JSON metadata
├── transitions_output/     # Output: saved transitions
├── session_*.log          # Session logs
├── transitions_errors.log # Error logs
├── app/
│   ├── main.py            # Entry point
│   ├── state.py           # AppState model
│   ├── screens/
│   │   ├── generation.py
│   │   ├── history.py
│   │   ├── search.py
│   │   └── help_overlay.py
│   ├── services/
│   │   ├── playback.py    # PlaybackService (PyAudio)
│   │   ├── generation.py  # TransitionGenerationService
│   │   ├── catalog.py     # SongCatalogLoader
│   │   └── history.py     # SessionHistoryStore
│   ├── models/
│   │   ├── song.py
│   │   ├── section.py
│   │   └── transition.py
│   └── utils/
│       ├── logger.py
│       ├── config.py
│       └── validators.py
└── poc/
    ├── poc_analysis_allinone.py  # Source of song JSON
    └── analyze_sections.py       # Source of section features
```

### Testing Considerations

- **Unit tests**: Services (playback, generation, history)
- **Integration tests**: Screen transitions, state management
- **Manual tests**: Full user workflows (use cases 1-7)
- **Performance tests**: Catalog loading (100+ songs), history cap enforcement

### Accessibility

- **Keyboard-only operation**: No mouse required
- **High-contrast themes**: Configurable in Textual
- **Screen reader support**: Use semantic widgets from Textual
- **Terminal compatibility**: Test on macOS Terminal, iTerm2, Windows Terminal, Linux terminals

---

## 27. Glossary

| Term | Definition |
|------|------------|
| **Section** | A labeled segment of a song (intro, verse, chorus, bridge, outro) with start/end timestamps |
| **Transition** | Generated audio that blends the end of Song A with the beginning of Song B |
| **Compatibility Score** | 0-100 metric indicating how well two songs match musically (tempo, key, energy) |
| **Base Parameters** | Core transition parameters shared by all transition types (overlap, fade_window, etc.) |
| **Extension Parameters** | Type-specific parameters unique to each transition algorithm |
| **Ephemeral Generation** | Quick test mode (Shift+G) that generates audio without adding to history |
| **Session History** | In-memory list of all transitions generated during the current app session (max 50) |
| **Modify Mode** | Generation screen state where a historical transition's parameters are pre-loaded for editing |
| **Modal Screen** | Overlay screen (Song Search, Help) that blocks interaction with underlying screen |

---

## 28. Decision Log

### Key Design Decisions and Rationale

1. **Session-scoped history (not persistent)**
   - **Rationale**: Focuses on creative flow within a session. Saves are intentional, not automatic. Reduces complexity.

2. **Exactly one section per song**
   - **Rationale**: Simplifies UX. Most transitions work best with single, focused sections. Advanced multi-section support is future work.

3. **Compatibility for sorting, not filtering**
   - **Rationale**: Users should experiment freely. Sorting guides toward good matches, but doesn't restrict choices.

4. **Blocking generation with progress updates**
   - **Rationale**: Simpler architecture than async. Acceptable for 10-30s generation times. User focuses on one task.

5. **Auto-play after generation**
   - **Rationale**: Immediate feedback is critical for creative iteration. User can always pause if needed.

6. **Cap history at 50 transitions**
   - **Rationale**: Prevents unbounded memory growth. 50 is enough for extensive experimentation within a session.

7. **Ephemeral generation (Shift+G)**
   - **Rationale**: Enables rapid "what if?" testing without cluttering history. Lowered friction for experimentation.

8. **Wrap-around seeking**
   - **Rationale**: Common in audio players. Allows continuous listening of short sections during evaluation.

9. **PyAudio backend**
   - **Rationale**: Widely supported, good cross-platform compatibility, adequate latency for this use case.

10. **Traditional key notation (not Camelot)**
    - **Rationale**: Accessible to all users (not just DJs). Can add Camelot as optional display in future.

---

End of Design Specification
