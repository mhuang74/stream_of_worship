"""History screen for reviewing and managing generated transitions."""
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, Label, ListView, ListItem, Header, Footer, Input
from textual.binding import Binding

from app.state import AppState, ActiveScreen, GenerationMode
from app.models.transition import TransitionRecord
from app.utils.logger import get_error_logger


class TransitionListPanel(ListView):
    """Panel displaying list of generated transitions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.border_title = "TRANSITION LIST (Newest First)"
        self.transitions: list[TransitionRecord] = []

    def set_transitions(self, transitions: list[TransitionRecord], selected_index: int | None = None):
        """Set the list of transitions to display.

        Args:
            transitions: List of transitions to display (newest first)
            selected_index: Index of currently selected transition
        """
        self.transitions = transitions
        self.clear()

        if not transitions:
            # Show empty state message
            item = ListItem(Label("  No transitions generated yet"))
            self.append(item)
            return

        for idx, transition in enumerate(transitions):
            # Check output type for different display
            if transition.output_type == "full_song":
                type_icon = "♫"  # Musical note for full songs
                type_display = "Full Song"
            else:
                type_icon = "⇄"  # Arrow for transitions
                type_display = transition.transition_type.capitalize()

            # Format: #5 [Icon] Type: Song A -> Song B (87%)
            compat_pct = int(transition.compatibility_score)
            text = f"#{transition.id} {type_icon} {type_display}: {transition.song_a_filename} → {transition.song_b_filename} ({compat_pct}%)"

            # Add selection indicator
            if selected_index is not None and idx == selected_index:
                text = f"-> {text}"
            else:
                text = f"   {text}"

            # Add saved indicator
            if transition.is_saved:
                text += " [Saved]"

            item = ListItem(Label(text))

            if selected_index is not None and idx == selected_index:
                item.add_class("--selected")

            self.append(item)



class TransitionDetailsPanel(Static):
    """Panel displaying details of the selected transition."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.border_title = "TRANSITION DETAILS"

    def set_transition(self, transition: TransitionRecord | None):
        """Display details for a transition.

        Args:
            transition: The transition to display details for, or None to clear
        """
        if not transition:
            self.update("No transition selected")
            return

        # Build details text
        if transition.output_type == "full_song":
            type_display = "Full Song Output"
        else:
            type_display = transition.transition_type.capitalize()

        details = f"""Type: {type_display}
Songs: {transition.song_a_filename} [{transition.section_a_label}] -> {transition.song_b_filename} [{transition.section_b_label}]
Compatibility: {int(transition.compatibility_score)}%
Generated: {transition.format_time()}
Status: {transition.status_display}"""

        # Add full song specific details
        if transition.output_type == "full_song" and transition.parameters:
            num_before = transition.parameters.get("num_song_a_sections_before", 0)
            num_after = transition.parameters.get("num_song_b_sections_after", 0)
            total_duration = transition.parameters.get("total_duration", 0)
            details += f"\nStructure: {num_before} sections + transition + {num_after} sections"
            details += f"\nTotal Duration: {int(total_duration)}s"

        if transition.is_saved and transition.saved_path:
            # Show just the filename, not the full path
            details += f"\nSaved: {transition.saved_path.name}"

        if transition.save_note:
            details += f"\n[bold]Note:[/bold] {transition.save_note}"

        self.update(details)


class ParametersReadOnlyPanel(Static):
    """Panel displaying read-only parameter snapshot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.border_title = "PARAMETERS (Read-Only)"

    def set_parameters(self, params: dict | None):
        """Display parameters for a transition.

        Args:
            params: The parameters dictionary, or None to clear
        """
        if not params:
            self.update("No parameters")
            return

        # Build parameters text
        lines = []

        # Check if this is a full song output
        if params.get("output_type") == "full_song":
            lines.append(f"Output Type: Full Song")
            if "num_song_a_sections_before" in params:
                lines.append(f"Song A prefix sections: {params['num_song_a_sections_before']}")
            if "num_song_b_sections_after" in params:
                lines.append(f"Song B suffix sections: {params['num_song_b_sections_after']}")
            if "total_duration" in params:
                lines.append(f"Total duration: {int(params['total_duration'])}s")
        else:
            # Base parameters for regular transitions
            if "type" in params:
                lines.append(f"Type: {params['type']}")
            if "gap_beats" in params:
                lines.append(f"Gap: {params['gap_beats']} beats")
            if "overlap" in params:
                lines.append(f"Overlap: {params['overlap']} beats")
            if "fade_window" in params:
                lines.append(f"Fade Window: {params['fade_window']} beats")
            if "fade_speed" in params:
                lines.append(f"Fade Speed: {params['fade_speed']} beats")
            if "stems_to_fade" in params:
                stems = params['stems_to_fade']
                if isinstance(stems, list):
                    lines.append(f"Stems: {', '.join(stems)}")
                else:
                    lines.append(f"Stems: {stems}")

            # Section adjustments
            adjusts = []
            if params.get("section_a_start_adjust"):
                adjusts.append(f"A start: {params['section_a_start_adjust']:+d}")
            if params.get("section_a_end_adjust"):
                adjusts.append(f"A end: {params['section_a_end_adjust']:+d}")
            if params.get("section_b_start_adjust"):
                adjusts.append(f"B start: {params['section_b_start_adjust']:+d}")
            if params.get("section_b_end_adjust"):
                adjusts.append(f"B end: {params['section_b_end_adjust']:+d}")

            if adjusts:
                lines.append(f"Section Adjusts: {', '.join(adjusts)}")

            # Extension parameters
            if params.get("extension"):
                for key, value in params["extension"].items():
                    lines.append(f"{key}: {value}")

        self.update("\n".join(lines) if lines else "No parameters")


class SaveNoteInput(Input):
    """Input for entering save note."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, placeholder="Enter optional note for saved transition...", **kwargs)


class HistoryScreen(Screen):
    """Screen for reviewing and managing generated transitions."""

    BINDINGS = [
        Binding("g", "go_to_generation", "New Transition", show=True),
        Binding("m", "modify_transition", "Modify", show=True),
        Binding("s", "save_transition", "Save", show=True),
        Binding("d", "delete_transition", "Delete", show=True),
        Binding("space", "start_playback", "Play", show=True),
        Binding("left", "seek_backward", "-3s", show=True),
        Binding("right", "seek_forward", "+4s", show=True),
        Binding("escape", "stop_playback", "Stop", show=True),
        Binding("?", "show_help", "Help", show=True),
        Binding("f1", "show_help", "Help", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self, state: AppState, catalog, playback, generation, *args, **kwargs):
        """Initialize the history screen.

        Args:
            state: Application state
            catalog: Song catalog loader
            playback: Playback service
            generation: Transition generation service
        """
        super().__init__(*args, **kwargs)
        self.state = state
        self.catalog = catalog
        self.playback = playback
        self.generation = generation
        self._saving_transition = False  # Flag for save mode
        self._pending_delete_id: int | None = None  # ID of transition pending deletion confirmation

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header()

        with Horizontal(id="history_main_content"):
            # Left column: Transition list
            with Vertical(id="history_left_panel"):
                self.transition_list = TransitionListPanel(id="transition_list")
                yield self.transition_list

            # Right column: Details and parameters
            with Vertical(id="history_right_panel"):
                self.details_panel = TransitionDetailsPanel(id="details_panel")
                yield self.details_panel

                self.parameters_panel = ParametersReadOnlyPanel(id="params_readonly_panel")
                yield self.parameters_panel

        # Save note input (hidden by default)
        with Container(id="save_container"):
            yield Label("Save Transition - Enter optional note:", id="save_label")
            self.save_input = SaveNoteInput(id="save_input")
            yield self.save_input

        yield Footer()

    def on_mount(self) -> None:
        """Handle screen mount event."""
        # Hide save input initially
        self.query_one("#save_container").display = False
        self.update_screen()

    def update_screen(self):
        """Update the screen based on current state."""
        # Update transition list
        self.transition_list.set_transitions(
            self.state.transition_history,
            self.state.selected_history_index
        )

        # Restore cursor position
        if self.state.selected_history_index is not None:
            self.transition_list.index = self.state.selected_history_index

        # Update details panel
        selected = self.state.get_selected_transition()
        self.details_panel.set_transition(selected)

        # Update parameters panel
        if selected:
            self.parameters_panel.set_parameters(selected.parameters)
        else:
            self.parameters_panel.set_parameters(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        if event.list_view.id == "transition_list":
            index = event.list_view.index
            if index is not None and 0 <= index < len(self.state.transition_history):
                self.state.selected_history_index = index
                self._update_details_panel()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Handle list item highlight (cursor movement)."""
        if event.list_view.id == "transition_list":
            index = event.list_view.index
            if index is not None and 0 <= index < len(self.state.transition_history):
                self.state.selected_history_index = index
                self._pending_delete_id = None  # Clear pending delete on selection change
                self._update_details_panel()

    def _update_details_panel(self):
        """Update only the details and parameters panels (lightweight update)."""
        selected = self.state.get_selected_transition()
        self.details_panel.set_transition(selected)
        if selected:
            self.parameters_panel.set_parameters(selected.parameters)
        else:
            self.parameters_panel.set_parameters(None)

    def action_go_to_generation(self):
        """Switch to generation screen (G key)."""
        self.state.active_screen = ActiveScreen.GENERATION
        # Exit modify mode if active
        if self.state.generation_mode == GenerationMode.MODIFY:
            self.state.generation_mode = GenerationMode.FRESH
            self.state.base_transition_id = None
        self.app.switch_screen("generation")

    def action_modify_transition(self):
        """Modify selected transition (M key)."""
        selected = self.state.get_selected_transition()
        if not selected:
            self.notify("No transition selected", severity="warning")
            return

        # Enter modify mode
        self.state.enter_modify_mode(selected)

        # Look up section indices from song catalog
        song_a = self.catalog.get_song(selected.song_a_filename)
        song_b = self.catalog.get_song(selected.song_b_filename)

        if song_a:
            for idx, section in enumerate(song_a.sections):
                if section.label == selected.section_a_label:
                    self.state.left_section_index = idx
                    break

        if song_b:
            for idx, section in enumerate(song_b.sections):
                if section.label == selected.section_b_label:
                    self.state.right_section_index = idx
                    break

        # Load section adjustments from parameters
        params = selected.parameters
        self.state.from_section_start_adjust = params.get("section_a_start_adjust", 0)
        self.state.from_section_end_adjust = params.get("section_a_end_adjust", 0)
        self.state.to_section_start_adjust = params.get("section_b_start_adjust", 0)
        self.state.to_section_end_adjust = params.get("section_b_end_adjust", 0)

        # Switch to generation screen
        self.state.active_screen = ActiveScreen.GENERATION
        self.app.switch_screen("generation")
        self.notify(f"Modifying transition #{selected.id}")

    def action_save_transition(self):
        """Save selected transition (S key)."""
        selected = self.state.get_selected_transition()
        if not selected:
            self.notify("No transition selected", severity="warning")
            return

        if selected.is_saved:
            self.notify("Transition already saved", severity="warning")
            return

        # Show save input
        self._saving_transition = True
        self.query_one("#save_container").display = True
        self.save_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle save note submission."""
        if event.input.id == "save_input" and self._saving_transition:
            self._complete_save(event.input.value)

    def _complete_save(self, note: str):
        """Complete the save operation."""
        selected = self.state.get_selected_transition()
        if not selected:
            return

        try:
            # Copy audio file to output folder
            from pathlib import Path
            source_path = Path(selected.audio_path) if isinstance(selected.audio_path, str) else selected.audio_path
            if not source_path.exists():
                self.notify("Source audio file not found", severity="error")
                return

            # Generate filename
            output_folder = self.app.config.output_folder
            output_folder.mkdir(parents=True, exist_ok=True)

            # Clean up filenames for output
            song_a_name = Path(selected.song_a_filename).stem
            song_b_name = Path(selected.song_b_filename).stem
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"saved_transition_{song_a_name}_to_{song_b_name}_{timestamp}.flac"
            output_path = output_folder / output_filename

            # Copy file
            import shutil
            shutil.copy2(source_path, output_path)

            # Write FLAC metadata
            try:
                from mutagen.flac import FLAC
                audio = FLAC(str(output_path))
                audio["TITLE"] = f"Transition: {song_a_name} -> {song_b_name}"
                audio["ARTIST"] = "Song Transition Preview"
                audio["ALBUM"] = "Generated Transitions"
                audio["GENRE"] = f"Transition ({selected.transition_type.capitalize()})"
                audio["DESCRIPTION"] = f"From: {selected.song_a_filename} [{selected.section_a_label}], To: {selected.song_b_filename} [{selected.section_b_label}]"

                # Add transition parameters as custom tags
                params = selected.parameters or {}
                if params.get("type"):
                    audio["TRANSITION_TYPE"] = str(params["type"])
                if params.get("gap_beats"):
                    audio["GAP_BEATS"] = str(params["gap_beats"])
                if params.get("overlap"):
                    audio["OVERLAP_BEATS"] = str(params["overlap"])
                if params.get("fade_window"):
                    audio["FADE_WINDOW"] = str(params["fade_window"])
                if params.get("fade_speed"):
                    audio["FADE_SPEED"] = str(params["fade_speed"])
                if params.get("stems_to_fade"):
                    stems = params["stems_to_fade"]
                    if isinstance(stems, list):
                        audio["STEMS_TO_FADE"] = ", ".join(stems)
                    else:
                        audio["STEMS_TO_FADE"] = str(stems)

                # Section adjustments
                adjusts = []
                if params.get("section_a_start_adjust"):
                    adjusts.append(f"A start: {params['section_a_start_adjust']:+d}")
                if params.get("section_a_end_adjust"):
                    adjusts.append(f"A end: {params['section_a_end_adjust']:+d}")
                if params.get("section_b_start_adjust"):
                    adjusts.append(f"B start: {params['section_b_start_adjust']:+d}")
                if params.get("section_b_end_adjust"):
                    adjusts.append(f"B end: {params['section_b_end_adjust']:+d}")
                if adjusts:
                    audio["SECTION_ADJUSTS"] = ", ".join(adjusts)

                if note:
                    audio["COMMENT"] = note
                audio.save()
            except Exception as e:
                # Don't fail the save if metadata writing fails
                self.notify(f"Warning: Could not write metadata: {e}", severity="warning")
                logger = get_error_logger()
                if logger:
                    logger.log_file_error(str(output_path), e, operation="write_metadata")

            # Update transition record
            selected.is_saved = True
            selected.saved_path = output_path
            selected.save_note = note if note else None

            self.notify(f"Saved: {output_filename}")

        except Exception as e:
            self.notify(f"Error saving: {str(e)}", severity="error")
            logger = get_error_logger()
            if logger and selected:
                logger.log_file_error(
                    str(selected.audio_path),
                    e,
                    operation="save_transition"
                )

        finally:
            # Hide save input and update screen
            self._saving_transition = False
            self.query_one("#save_container").display = False
            self.save_input.value = ""
            self.update_screen()

    def action_delete_transition(self):
        """Delete selected transition (D key, requires confirmation)."""
        selected = self.state.get_selected_transition()
        if not selected:
            self.notify("No transition selected", severity="warning")
            self._pending_delete_id = None
            return

        # Check if currently playing
        if self.playback.current_file and str(selected.audio_path) == str(self.playback.current_file):
            self.notify("Cannot delete playing transition", severity="error")
            self._pending_delete_id = None
            return

        # Check if this is a confirmation (same transition selected)
        if self._pending_delete_id == selected.id:
            # Confirmed - delete the transition
            idx = self.state.selected_history_index
            if idx is not None:
                self.state.transition_history.pop(idx)

                # Update selection
                if len(self.state.transition_history) == 0:
                    self.state.selected_history_index = None
                elif idx >= len(self.state.transition_history):
                    self.state.selected_history_index = len(self.state.transition_history) - 1

                self.notify(f"Deleted transition #{selected.id}")
                self.update_screen()

            self._pending_delete_id = None
        else:
            # First press - ask for confirmation
            self._pending_delete_id = selected.id
            self.notify(f"Press D again to delete transition #{selected.id}", severity="warning")

    def action_start_playback(self):
        """Start playback of current transition from beginning (Space key)."""
        selected = self.state.get_selected_transition()
        if not selected:
            self.notify("No transition selected", severity="warning")
            return

        # Always stop any current playback first to ensure clean state
        # (even if is_playing is False, there might be lingering threads)
        self.playback.stop()

        # Load and play from beginning
        from pathlib import Path
        audio_path = Path(selected.audio_path) if isinstance(selected.audio_path, str) else selected.audio_path
        if audio_path.exists():
            if self.playback.load(audio_path):
                self.playback.play()
                self.notify(f"Playing transition #{selected.id}")
            else:
                self.notify("Failed to load audio", severity="error")
        else:
            self.notify("Audio file not found", severity="error")

    def action_stop_playback(self):
        """Stop playback (Esc key)."""
        if self._saving_transition:
            # Cancel save mode
            self._saving_transition = False
            self.query_one("#save_container").display = False
            self.save_input.value = ""
            self.notify("Save cancelled")
        elif self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()
            self.notify("Playback stopped")

    def action_seek_backward(self):
        """Seek backward 3 seconds (Left arrow key)."""
        if self.playback.current_file:
            self.playback.seek(-3.0)
            self.notify(f"Seek to {self.playback.position:.1f}s")

    def action_seek_forward(self):
        """Seek forward 4 seconds (Right arrow key)."""
        if self.playback.current_file:
            self.playback.seek(4.0)
            self.notify(f"Seek to {self.playback.position:.1f}s")

    def action_show_help(self):
        """Show help overlay (? or F1 key)."""
        self.notify("Help (not yet implemented)")

    def action_quit(self):
        """Quit the application (Ctrl+Q or Ctrl+C)."""
        self.app.action_quit()
