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

- **Services**
  - PlaybackService
  - TransitionGenerationService
  - SessionHistoryStore
  - SongCatalogLoader (JSON-based)

- **Data Sources**
  - Song metadata and section data loaded from JSON files
  - Generated audio stored in temporary or user-specified locations

---

## 3. Screens Overview

### Screen List

| Screen | Purpose |
|------|--------|
| GenerationScreen | Select songs/sections, configure parameters, generate transitions |
| HistoryScreen | Review, compare, modify, and save generated transitions |
| SongSearchScreen | Search and filter songs when catalog grows large |

Screens are mutually exclusive. SongSearchScreen behaves as a **modal screen** that always returns to GenerationScreen.

---

## 4. Generation Screen

### Responsibilities

- Select **Song A** and **Song B**
- Select **exactly one section** per song
- Display song-level and section-level metadata
- Configure transition parameters (abstracted)
- Generate transitions
- Preview individual song sections

### Conceptual Layout

```
┌──────────────────────────────────────────────────────────────┐
│ GENERATION SCREEN                                            │
│                                                              │
│ ┌───────────────┬───────────────┐                            │
│ │ SONG A        │ SONG B        │                            │
│ │ Song List     │ Song List     │                            │
│ │ Section List  │ Section List  │                            │
│ │ Metadata      │ Metadata      │                            │
│ └───────────────┴───────────────┘                            │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ TRANSITION PARAMETERS                                    │ │
│ │ Transition Type + other parameters (abstracted)          │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ PLAYBACK & GENERATION                                    │ │
│ │ Play A | Play B | Generate Transition                    │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ FOOTER: H=History  /=Search  ←-3s  →+4s  Space=Play/Pause    │
└──────────────────────────────────────────────────────────────┘
```

### Song & Section Selection Rules

- Songs are loaded from pre-analyzed JSON files
- Each song exposes a list of sections
- User may select **only one section per song**
- Highlighting a song displays:
  - BPM
  - Key / scale
  - Duration
  - Other metadata
- Highlighting a section displays:
  - Section duration

---

## 5. Generation Modes

### Modes

| Mode | Description |
|----|------------|
| Fresh | Default mode for new transitions |
| Modify | Parameters pre-filled from a historical transition |

### Modify Mode Rules

- Entered from HistoryScreen
- Parameters are copied from selected transition
- Original transition remains unchanged
- Generating creates a **new transition record**
- Visual indicator shows Modify Mode
- `Esc` exits Modify Mode and returns to Fresh Mode

---

## 6. Transition Parameters

- Dedicated section on GenerationScreen
- Always visible when in GenerationScreen
- Includes:
  - Transition Type
  - Additional sub-parameters (intentionally unspecified)
- Parameters are:
  - Editable in GenerationScreen
  - Read-only snapshots in HistoryScreen

---

## 7. Playback System

### Capabilities

- Play:
  - Song A section
  - Song B section
  - Generated transition
  - Historical transition
- Shared across all screens

### Controls

| Key | Action |
|---|---|
| Space | Play / Pause |
| ← | Seek backward 3 seconds |
| → | Seek forward 4 seconds |

Playback is clamped to the active audio segment boundaries.

Playback stops automatically when switching screens.

---

## 8. History / Comparison Screen

### Responsibilities

- Display all transitions generated in current session
- Allow playback and comparison
- Display parameter snapshots
- Save transitions to disk
- Enter Modify Mode

### Conceptual Layout

```
┌──────────────────────────────────────────────┐
│ HISTORY SCREEN                               │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ TRANSITION LIST                          │ │
│ │ → Transition #1 [Type X]                 │ │
│ │   Transition #2 [Type Y]                 │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ TRANSITION DETAILS                       │ │
│ │ Source Songs & Sections                  │ │
│ │ Parameter Snapshot (read-only)           │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ PLAYBACK & ACTIONS                       │ │
│ │ Play | Save | Modify                     │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ FOOTER: G=Generate  M=Modify  S=Save  ← →   │
└──────────────────────────────────────────────┘
```

### History Rules

- History is **session-scoped**
- Transitions are immutable
- Selecting a transition:
  - Updates playback target
  - Displays its parameters
- Modify loads parameters into GenerationScreen

---

## 9. Song Search Screen

### Purpose

Provides scalable song discovery when catalog grows large (100+ songs).

### Invocation

- Opened from GenerationScreen
- Context-aware: selecting for Song A or Song B

### Conceptual Layout

```
┌──────────────────────────────────────────────┐
│ SONG SEARCH                                  │
│ Selecting for: Song A                        │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ SEARCH & FILTERS                         │ │
│ │ Keyword                                 │ │
│ │ BPM Range                               │ │
│ │ Key / Scale                             │ │
│ │ Theme / Tags                            │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ RESULTS LIST                             │ │
│ │ → Song 1 · 124 BPM · 8A                  │ │
│ │   Song 2 · 128 BPM · 9A                  │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ SONG METADATA                            │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ FOOTER: Space=Preview/Stop Enter=Select  Esc=Cancel│
└──────────────────────────────────────────────┘
```

### Rules

- Selecting a song returns to GenerationScreen
- Cancelling leaves previous selection unchanged
- Preview is playback limited to 10 seconds; toggle Space again to stop

---

## 10. State Model

### App-Level State

```python
class ActiveScreen(Enum):
    GENERATION = "generation"
    HISTORY = "history"

class GenerationMode(Enum):
    FRESH = "fresh"
    MODIFY = "modify"

class AppState:
    active_screen: ActiveScreen
    generation_mode: GenerationMode
    base_transition_id: str | None

    left_song_id: str | None
    left_section_id: str | None
    right_song_id: str | None
    right_section_id: str | None

    transition_type: str | None
    transition_parameters: dict

    transition_history: list

    playback_target: str | None
    playback_position: float
    playback_state: str
```

---

## 11. Screen Transition Diagram

```
┌──────────────┐
│ Generation   │◄──────────────┐
└─────┬────────┘               │
      │ H                      │ G / Esc
      ▼                        │
┌──────────────┐               │
│ History      │───────────────┘
└──────────────┘
      ▲
      │ /
      │
┌──────────────┐
│ Song Search  │ (modal)
└──────────────┘
```

---

## 12. Generation Mode State Diagram

```
FRESH MODE
   │
   │ Modify (from history)
   ▼
MODIFY MODE
   │
   │ Generate or Esc
   ▼
FRESH MODE
```

---

## 13. Transition Lifecycle

```
Select Songs & Sections
        ↓
Configure Parameters
        ↓
Generate Transition
        ↓
Store in Session History
        ↓
Playback / Compare / Modify / Save
```

---

## 14. Primary Use Cases

1. Generate first transition between two songs
2. Generate multiple variations by tweaking parameters
3. Compare transitions side-by-side via History
4. Modify an existing transition without starting over
5. Save selected transitions to disk
6. Search and select songs from large catalogs

---

## 15. UX Principles

- Keyboard-first interaction
- Non-destructive experimentation
- Clear separation of concerns by screen
- Fast iteration loop
- Scales from simple to advanced workflows
- Creative flow preserved across screens

---

## 16. Future Extensions (Out of Scope)

- Persistent transition library across sessions
- Waveform visualization
- Multi-song chaining
- MIDI or beat-grid overlays
- Collaborative tagging

---

End of Design Specification
