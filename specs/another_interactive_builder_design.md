# Continuous Interactive Transition Builder - Design Specification

**Version**: 2.0
**Date**: 2026-01-09
**Status**: Design Review
**Supersedes**: v1.0 one-shot workflow

## Executive Summary

This document specifies a redesign of the Interactive Worship Transition Builder to transform it from a one-shot workflow tool into a continuous application with a persistent UI. Users should be able to explore multiple song combinations, transition types, and parameters in a single session, only saving transitions when they find ones they like.

## Problem Statement

### Current Behavior (v1.0)
The current implementation follows a linear workflow:
1. Select Song A → Section A
2. Select Song B → Section B
3. Choose Transition Type
4. Adjust Parameters (optional)
5. Preview
6. Save (optional)
7. **EXIT**

**Pain Points**:
- Tool exits after one iteration, requiring restart to try different combinations
- Cannot compare different transition types quickly
- No way to go back and change selections without restarting
- Parameters and selections disappear from view as you progress
- Unclear what choices were made once you're in the parameter adjustment phase

### Desired Behavior (v2.0)
A continuous application that:
- Remains running until user explicitly quits
- Displays persistent context (selected songs, sections, parameters)
- Allows quick iteration on selections and parameters
- Shows previews without committing to save
- Saves only when user explicitly chooses to
- Provides clear navigation between different configuration aspects

## Design Philosophy

### Application vs. Wizard
- **v1.0**: Wizard-style (step 1 → step 2 → ... → done)
- **v2.0**: Application-style (persistent UI, free navigation, multiple operations)

### Fixed Layout Principle
All major UI elements remain in fixed screen positions:
- **Top banner**: Always shows application title and help
- **Left panel**: Always shows Song A info
- **Right panel**: Always shows Song B info
- **Center panel**: Always shows transition configuration
- **Bottom panel**: Always shows available actions and status

### Context Visibility
Users should always see:
- Currently selected Song A and Section A
- Currently selected Song B and Section B
- Current transition type and all parameters
- Compatibility score
- What actions are available

## UI Layout

### Screen Layout (80x40 minimum terminal size)

```
┌────────────────────────────────────────────────────────────────────────────┐
│        Interactive Transition Builder v2.0           [H]elp | [Q]uit       │
├──────────────────────────┬─────────────────────────┬────────────────────────┤
│    SONG A                │   TRANSITION CONFIG     │    SONG B              │
├──────────────────────────┼─────────────────────────┼────────────────────────┤
│ ► do_it_again.mp3        │ Type: Short Gap         │ ► heaven_open.mp3      │
│   D major | 136 BPM      │                         │   G major | 128 BPM    │
│   Duration: 4:25         │ Parameters:             │   Duration: 4:12       │
│                          │   transition_window:    │                        │
│ Section: ► Chorus        │   8.0 beats (3.5s)      │ Section: ► Chorus      │
│   Time: 35.8s - 58.2s    │                         │   Time: 42.1s - 65.8s  │
│   Duration: 22.4s        │   gap_window:           │   Duration: 23.7s      │
│   Energy: 84/100         │   1.0 beats (0.4s)      │   Energy: 78/100       │
│                          │                         │                        │
│ [C]hange Song            │   stems_to_fade:        │ [T]hange Song          │
│ [S]ection List           │   ☑ vocals ☑ drums      │ Se[c]tion List         │
│                          │   ☐ bass   ☑ other      │                        │
│                          │                         │                        │
│                          │   fade_window_pct: 80%  │                        │
│                          │                         │                        │
│                          │ Compatibility: 78.5/100 │                        │
│                          │   Tempo:  85.0          │                        │
│                          │   Key:    72.0          │                        │
│                          │   Energy: 80.0          │                        │
│                          │                         │                        │
│                          │ [Y]pe | [P]arameters    │                        │
├──────────────────────────┴─────────────────────────┴────────────────────────┤
│ ACTIONS:                                                                    │
│  [R]eview Transition | [A]ve Transition | [N]ew Transition | [Q]uit        │
├─────────────────────────────────────────────────────────────────────────────┤
│ Status: ● Ready to preview transition                                      │
│ Last: Generated transition (12.4s) at 10:42:35                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Panel Responsibilities

#### Left Panel: Song A Context
- **Always visible**:
  - Selected song name (or "Not Selected")
  - Song metadata (key, BPM, duration)
  - Selected section (or "Not Selected")
  - Section metadata (time range, duration, energy)
- **Actions**:
  - `[C]hange Song` - Open song selection overlay
  - `[S]ection List` - Open section selection overlay

#### Center Panel: Transition Configuration
- **Always visible**:
  - Current transition type (Overlap, Short Gap, No Break)
  - All relevant parameters for current type
  - Compatibility score breakdown
- **Actions**:
  - `[Y]pe` - Change transition type (opens type selector)
  - `[P]arameters` - Adjust parameters (opens parameter editor)

#### Right Panel: Song B Context
- **Always visible**:
  - Selected song name (or "Not Selected")
  - Song metadata (key, BPM, duration)
  - Selected section (or "Not Selected")
  - Section metadata (time range, duration, energy)
- **Actions**:
  - `[T]hange Song` - Open song selection overlay
  - Se`[c]tion List` - Open section selection overlay

#### Bottom Panel: Actions & Status
- **Action Bar**:
  - `[R]eview Transition` - Generate and play preview (only enabled when both songs/sections selected)
  - `[A]ve Transition` - Save current configuration to FLAC+JSON
  - `[N]ew Transition` - Clear all selections and start fresh
  - `[Q]uit` - Exit application
- **Status Bar**:
  - Current state indicator (● Ready, ⏵ Playing, ⚙ Generating, ✓ Saved)
  - Last action performed with timestamp
  - Error messages if any

## Navigation Model

### Modal Overlays
Instead of changing the entire screen, use temporary overlays that preserve context:

#### Song Selection Overlay
```
┌────────────────────────────────────────────┐
│ Select Song A                         [ESC]│
├────┬─────────────────────┬─────┬─────┬─────┤
│ #  │ Filename            │ Key │ BPM │ Dur │
├────┼─────────────────────┼─────┼─────┼─────┤
│ 1  │ do_it_again.mp3     │ D   │ 136 │4:25 │
│ 2  │ heaven_open.mp3     │ G   │ 128 │4:12 │
│ 3  │ way_maker.mp3       │ Bb  │ 132 │5:18 │
│ ...│ ...                 │ ... │ ... │ ... │
│ 11 │ build_my_life.mp3   │ G   │ 140 │4:03 │
└────┴─────────────────────┴─────┴─────┴─────┘
 Enter number (1-11) or ESC to cancel
```

#### Section Selection Overlay
```
┌─────────────────────────────────────────────────────┐
│ Select Section from: do_it_again.mp3           [ESC]│
├───┬─────────┬─────────────────┬──────────┬──────────┤
│ # │ Label   │ Time Range      │ Duration │ Energy   │
├───┼─────────┼─────────────────┼──────────┼──────────┤
│ 1 │ Intro   │ 0.0s - 12.5s    │ 12.5s    │ 75.2/100 │
│ 2 │ Verse   │ 12.5s - 35.8s   │ 23.3s    │ 68.4/100 │
│ 3 │ Chorus  │ 35.8s - 58.2s   │ 22.4s    │ 84.1/100 │
│ 4 │ Bridge  │ 58.2s - 78.9s   │ 20.7s    │ 72.8/100 │
│ 5 │ Outro   │ 78.9s - 95.3s   │ 16.4s    │ 65.3/100 │
└───┴─────────┴─────────────────┴──────────┴──────────┘
 Enter number (1-5) or ESC to cancel
```

#### Transition Type Selector
```
┌──────────────────────────────────────────────────┐
│ Select Transition Type                      [ESC]│
├──────────────────────────────────────────────────┤
│ [1] Overlap (Intro Overlap)                      │
│     Last note of Song A overlaps with intro of   │
│     Song B. Creates smooth handoff.              │
│     Default: 6 beats window, 2 beats overlap     │
│                                                  │
│ [2] Short Gap ●                                  │
│     Brief silence between songs to "clear the    │
│     air". Creates intentional pause.             │
│     Default: 9 beats window, 1 beat gap          │
│                                                  │
│ [3] No Break                                     │
│     Continuous beat, seamless flow. No pause     │
│     between songs.                               │
│     Default: 8 beats window, 100% fade           │
└──────────────────────────────────────────────────┘
 Enter number (1-3) or ESC to cancel
```

#### Parameter Editor
```
┌──────────────────────────────────────────────────┐
│ Adjust Parameters - Short Gap              [ESC]│
├──────────────────────────────────────────────────┤
│ [W/S] Navigate | [A/D] or [←/→] Adjust | [SPACE] Toggle
│                                                  │
│ ► transition_window:  8.0 beats (3.5s @ 136 BPM)│
│   ████████░░░░░░░░ [2.0 - 16.0 beats]            │
│                                                  │
│   gap_window:       1.0 beats (0.4s @ 136 BPM)  │
│   ██░░░░░░░░░░░░░░ [0.5 - 8.0 beats]             │
│                                                  │
│   stems_to_fade:                                 │
│   ☑ vocals   ☑ drums   ☐ bass   ☑ other         │
│                                                  │
│   fade_window_pct:  80%                          │
│   ████████████████░░ [0 - 100%]                  │
│                                                  │
│ [R]eset to Defaults | [Enter] Apply | [ESC] Cancel
└──────────────────────────────────────────────────┘
```

#### Save Dialog
```
┌──────────────────────────────────────────────────┐
│ Save Transition                             [ESC]│
├──────────────────────────────────────────────────┤
│ Output Directory:                                │
│ section_transitions/                             │
│                                                  │
│ Filename:                                        │
│ [transition_do_it_again_chorus_to_heaven__]      │
│ │                                                │
│ Default: transition_do_it_again_chorus_to_       │
│          heaven_open_chorus                      │
│                                                  │
│ Files to be created:                             │
│  • transition_....flac (audio, ~2.1 MB)          │
│  • transition_....json (metadata, ~3 KB)         │
│                                                  │
│ [Enter] Save | [ESC] Cancel                      │
└──────────────────────────────────────────────────┘
```

### Playback Overlay
When user presses `[R]eview Transition`:
```
┌────────────────────────────────────────────────────┐
│ ⏵ Playing Transition                               │
├────────────────────────────────────────────────────┤
│                                                    │
│  ████████████████░░░░░░░░░░░░░░░░░░░░░  67%       │
│  0:08.3 / 0:12.4                                   │
│                                                    │
│  ← → Seek ±5s | SPACE Pause | ESC Stop            │
│                                                    │
│  Song A (Chorus): [########====                    │
│  Gap:             [            ===                 │
│  Song B (Chorus):                  ==========####] │
│                                                    │
└────────────────────────────────────────────────────┘
```

## User Workflows

### Workflow 1: First-Time User
```
1. Launch application
   → See empty panels with "Not Selected" placeholders

2. Press [C] to select Song A
   → Overlay appears with song list
   → User enters "1" to select do_it_again.mp3
   → Overlay closes, left panel updates with song info

3. Press [S] to select Section A
   → Overlay appears with section list
   → User enters "3" to select Chorus
   → Overlay closes, left panel updates with section info

4. Press [T] to select Song B
   → Overlay appears with song list
   → User enters "2" to select heaven_open.mp3
   → Overlay closes, right panel updates

5. Press [C] to select Section B
   → Overlay appears with section list
   → User enters "3" to select Chorus
   → Overlay closes, right panel updates

6. Notice center panel shows default "Overlap" transition type
   → Press [Y] to change type
   → Overlay appears with 3 options
   → User enters "2" for Short Gap
   → Overlay closes, center panel updates with new parameters

7. Press [P] to adjust parameters
   → Parameter editor overlay appears
   → User navigates with W/S, adjusts with A/D
   → Press Enter to apply
   → Center panel updates with new values

8. Press [R] to review transition
   → Status shows "⚙ Generating transition..."
   → Playback overlay appears
   → Audio plays with progress bar
   → Press ESC to stop

9. Not satisfied, press [Y] to try "No Break" type
   → Quick type change
   → Press [R] to review again

10. Satisfied! Press [A] to save
    → Save dialog appears
    → User accepts default filename or customizes
    → Press Enter to save
    → Status shows "✓ Saved: transition_do_it_again_chorus_to_heaven_open_chorus.flac"

11. Want to try another combination
    → Press [C] to change Song A to "way_maker.mp3"
    → Press [S] to select different section
    → Press [R] to review
    → Continue experimenting...

12. Done for now, press [Q] to quit
    → Application exits cleanly
```

### Workflow 2: Quick Experimentation
User wants to try one song with multiple target songs:

```
1. Select Song A (do_it_again) and Section (Chorus)
2. Select Song B (heaven_open) and Section (Chorus)
3. Press [R] to review → Listen
4. Press [T] to change Song B to "way_maker"
5. Press [R] to review → Listen
6. Press [T] to change Song B to "goodness_of_god"
7. Press [R] to review → Listen
8. Like option #2, press [T] to go back to "way_maker"
9. Press [A] to save
10. Press [T] to try next song...
```

### Workflow 3: Parameter Optimization
User wants to fine-tune a transition:

```
1. Select both songs and sections
2. Select transition type (Short Gap)
3. Press [R] to review → Gap feels too long
4. Press [P] to edit parameters
5. Adjust gap_window from 1.0 to 0.5 beats
6. Press Enter to apply
7. Press [R] to review → Better!
8. Press [P] to edit again
9. Change fade_window_pct from 80% to 100%
10. Press Enter to apply
11. Press [R] to review → Perfect!
12. Press [A] to save
```

## State Management

### Application State
```python
@dataclass
class AppState:
    # Selections
    song_a: Optional[Song] = None
    section_a: Optional[Section] = None
    song_b: Optional[Song] = None
    section_b: Optional[Section] = None

    # Configuration
    transition_type: TransitionType = TransitionType.OVERLAP
    config: TransitionConfig = field(default_factory=TransitionConfig)

    # Runtime state
    current_modal: Optional[str] = None  # None, "song_a", "section_a", "type", "params", "save", "playing"
    last_generated_audio: Optional[np.ndarray] = None
    last_generated_metadata: Optional[dict] = None
    last_action: str = ""
    last_action_time: datetime = None

    # UI state
    status_message: str = "Ready"
    status_icon: str = "●"  # ●=ready, ⏵=playing, ⚙=generating, ✓=saved, ✗=error
    error_message: Optional[str] = None

    def is_ready_to_preview(self) -> bool:
        """Check if we have enough selections to generate a preview."""
        return all([
            self.song_a is not None,
            self.section_a is not None,
            self.song_b is not None,
            self.section_b is not None
        ])

    def is_ready_to_save(self) -> bool:
        """Check if we have generated audio to save."""
        return self.last_generated_audio is not None
```

### State Transitions
```
Initial State:
  → All selections = None
  → Default transition type = OVERLAP
  → Status = "Ready to select songs"

After Song A selected:
  → song_a = <Song>
  → Status = "Select section for Song A"

After Section A selected:
  → section_a = <Section>
  → Status = "Select Song B"

After Song B selected:
  → song_b = <Song>
  → Status = "Select section for Song B"

After Section B selected:
  → section_b = <Section>
  → Status = "Ready to preview transition"
  → [R]eview action enabled

After [R]eview pressed:
  → Status = "⚙ Generating transition..."
  → Generate audio
  → last_generated_audio = <audio>
  → current_modal = "playing"
  → Play audio
  → After playback: Status = "Ready to review again or save"
  → [A]ve action enabled

After [A]ve pressed:
  → current_modal = "save"
  → Show save dialog
  → After save: Status = "✓ Saved: <filename>"
  → last_action = "Saved transition at HH:MM:SS"

After any selection change:
  → last_generated_audio = None  # Invalidate preview
  → [A]ve action disabled
  → Status = "Configuration changed - review to preview"
```

## Key Navigation Principles

### Always Available Keys
These work from the main screen (no modal open):
- `H` - Show help overlay
- `Q` - Quit application (with confirmation if unsaved changes)
- `C` - Change Song A
- `S` - Select Section A (only if Song A selected)
- `T` - Change Song B
- `c` (lowercase) - Select Section B (only if Song B selected)
- `Y` - Change transition type
- `P` - Edit parameters
- `R` - Review/preview transition (only if ready)
- `A` - Save transition (only if audio generated)
- `N` - New transition (clear all selections, start fresh)

### Modal Keys
When a modal is open:
- `ESC` - Always closes modal without changes
- `Enter` - Confirms selection/changes (context-dependent)
- Number keys - Select item (in lists)
- Arrow keys / WASD - Navigate (in editors)
- Space - Toggle (in multi-select)

### ESC Behavior
- ESC in modal → Close modal, return to main screen
- ESC in main screen → Does nothing (user must press Q to quit)
- ESC during playback → Stop playback, close playback overlay

## Visual Design Principles

### Color Coding
- **Cyan**: Headers, selected items, primary actions
- **Green**: Success states, save confirmations
- **Yellow**: Warnings, status updates
- **Red**: Errors, destructive actions
- **Magenta**: Song B elements (to distinguish from Song A)
- **White**: Default text
- **Dim/Gray**: Disabled actions, placeholders

### Status Icons
- `●` Ready state
- `⏵` Playing audio
- `⚙` Generating/processing
- `✓` Success/saved
- `✗` Error state
- `⏸` Paused
- `►` Selected item

### Layout Consistency
- Always 3 columns: Left (Song A), Center (Config), Right (Song B)
- Always 2-line status bar at bottom
- Always single-line title bar at top
- Modals always centered with shadow effect
- Minimum terminal size: 80x40 characters

## Keyboard Shortcuts Summary

| Key | Action | Available When |
|-----|--------|----------------|
| `H` | Show help | Always |
| `Q` | Quit | Always |
| `C` | Change Song A | Always |
| `S` | Section list for Song A | Song A selected |
| `T` | Change Song B | Always |
| `c` | Section list for Song B | Song B selected |
| `Y` | Change transition type | Always |
| `P` | Edit parameters | Always |
| `R` | Review transition | Both songs/sections selected |
| `A` | Save transition | Audio generated |
| `N` | New transition (clear) | Always |
| `ESC` | Close modal / Stop playback | Modal open or playing |
| `Enter` | Confirm selection | Modal open |
| `1-9` | Select item | List modal open |
| `←→` | Seek during playback | Playing |
| `Space` | Pause/resume | Playing |
| `W/S` | Navigate up/down | Parameter editor |
| `A/D` or `←→` | Adjust value | Parameter editor |

## Implementation Considerations

### Rich Library Usage
```python
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.align import Align
from rich.text import Text

# Main layout structure
layout = Layout()
layout.split_column(
    Layout(name="header", size=3),
    Layout(name="main"),
    Layout(name="actions", size=3),
    Layout(name="status", size=2)
)

layout["main"].split_row(
    Layout(name="song_a", ratio=1),
    Layout(name="config", ratio=1),
    Layout(name="song_b", ratio=1)
)

# Use Live to update without clearing screen
with Live(layout, console=console, refresh_per_second=4) as live:
    while True:
        # Update layout panels based on state
        update_panels(layout, app_state)
        # Handle keyboard input
        key = get_key()
        # Update state based on input
        handle_input(key, app_state)
```

### Input Handling
Use non-blocking keyboard input to keep UI responsive:
```python
import sys
import tty
import termios
import select

def get_key(timeout=0.1):
    """Non-blocking keyboard input."""
    if select.select([sys.stdin], [], [], timeout)[0]:
        return sys.stdin.read(1)
    return None

def handle_input(key, state):
    """Map key presses to state changes."""
    if state.current_modal is None:
        # Main screen shortcuts
        if key == 'c':
            open_song_selector(state, 'song_a')
        elif key == 's' and state.song_a:
            open_section_selector(state, 'section_a')
        # ... etc
    else:
        # Modal-specific shortcuts
        if state.current_modal == 'song_selector':
            handle_song_selector_input(key, state)
        # ... etc
```

### Audio Playback Integration
Continue using the existing playback system with seek controls:
```python
class AudioPlayer:
    def play(self, audio_data, on_stop_callback=None):
        """Non-blocking playback with event callback."""
        # Start playback in background thread
        # Return control to main loop
        # Call callback when stopped/finished

    def seek(self, offset_seconds):
        """Seek forward/backward during playback."""

    def pause(self):
        """Pause playback."""

    def resume(self):
        """Resume playback."""

    def stop(self):
        """Stop playback."""
```

### Transition Generation
Make generation async to keep UI responsive:
```python
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=1)

def generate_transition_async(config, callback):
    """Generate transition in background thread."""
    future = executor.submit(generator.generate, config)
    future.add_done_callback(callback)
```

## Testing Considerations

### Manual Testing Checklist
- [ ] Application launches and shows empty state correctly
- [ ] Can select Song A and Section A via overlays
- [ ] Can select Song B and Section B via overlays
- [ ] Center panel updates when transition type changed
- [ ] Parameter editor shows correct controls per type
- [ ] Preview generates and plays audio
- [ ] Can change selections and preview updates accordingly
- [ ] Save dialog works and creates files
- [ ] ESC closes modals without changes
- [ ] All keyboard shortcuts work as expected
- [ ] Layout remains stable during operations
- [ ] Status messages update appropriately
- [ ] Error states display clearly
- [ ] Can complete multiple transitions in one session
- [ ] New transition command clears state properly
- [ ] Quit command exits cleanly

### Edge Cases
- What happens if user changes Song A while audio is playing?
  → Stop playback, invalidate preview, update layout

- What if user tries to save without generating preview?
  → [A]ve action should be disabled (grayed out)

- What if terminal is too small?
  → Show warning message, suggest minimum size

- What if stem files are missing?
  → Show error in status bar, prevent preview

- What if user presses Q during playback?
  → Stop playback first, then show quit confirmation

## Migration from v1.0

### Reusable Components
- `TransitionConfig` data model → No changes needed
- `StemLoader` → No changes needed
- `TransitionGenerator` → No changes needed
- `AudioPlayer` → Add pause/resume methods
- Display functions → Refactor into panel update functions

### New Components Needed
- `AppState` class - Centralized state management
- `ModalManager` - Handle overlay display and input
- `LayoutManager` - Build and update fixed-layout panels
- `InputHandler` - Map keyboard input to state changes
- `StatusManager` - Track and display status/errors

### Code Organization
```
interactive_transition_builder/
├── main.py                     # NEW: Main event loop, state machine
├── app_state.py                # NEW: AppState class
├── ui/
│   ├── layout_manager.py       # NEW: Fixed-layout panel updates
│   ├── modal_manager.py        # NEW: Overlay handling
│   ├── input_handler.py        # NEW: Keyboard input routing
│   └── status_manager.py       # NEW: Status/error display
├── audio/                      # REUSE: No changes
│   ├── stem_loader.py
│   ├── transition_generator.py
│   └── playback.py             # UPDATE: Add pause/resume
├── models/                     # REUSE: No changes
│   ├── song.py
│   ├── transition_config.py
│   └── transition_types.py
└── utils/                      # REUSE: No changes
    ├── metadata_loader.py
    └── export.py
```

## Success Criteria

The v2.0 continuous UI is successful if:

1. **Iteration Speed**: User can try 5+ different song combinations in under 2 minutes
2. **Context Clarity**: User always knows what's selected without scrolling
3. **No Exits**: User can work for entire session without restarting
4. **Quick Changes**: Changing any selection takes ≤ 3 keystrokes
5. **Responsive**: UI updates feel instant (< 100ms)
6. **Forgiving**: ESC always cancels current modal without side effects
7. **Discoverable**: New users can figure out basic workflow without docs
8. **Efficient**: Power users can navigate entirely by keyboard

## Future Enhancements (v3.0+)

### History & Undo
- Track last 10 configurations
- Press `U` to undo last change
- Press `Shift+U` to redo

### Favorites
- Press `F` to mark current configuration as favorite
- Press `Shift+F` to show favorites list
- Quick load from favorites

### Batch Operations
- Press `B` to enter batch mode
- Select multiple Song B targets
- Generate all previews
- Save all that meet criteria

### Comparison Mode
- Press `Shift+R` to generate 2+ variants side-by-side
- Compare Overlap vs Short Gap vs No Break
- A/B test different parameters

### Session Persistence
- Auto-save session on exit
- Press `L` to load last session
- Resume exactly where you left off

## Conclusion

This continuous UI redesign transforms the Interactive Transition Builder from a one-shot wizard into a true application that supports rapid experimentation and iteration. The fixed-layout approach with modal overlays maintains context while allowing free navigation between different aspects of transition configuration.

Key improvements:
- **Persistent context**: Always see what's selected
- **No restarts**: Iterate indefinitely in one session
- **Quick changes**: Minimal keystrokes to try new ideas
- **Clear actions**: Always know what's available
- **Forgiving**: ESC cancels, nothing permanent until save

The design preserves all functionality from v1.0 while dramatically improving the workflow for users who want to explore and refine transitions.

---

**Next Steps**:
1. Review and approve this design
2. Implement new state management system
3. Build fixed-layout UI with Rich
4. Add modal overlay system
5. Integrate with existing audio/transition logic
6. User testing and iteration
