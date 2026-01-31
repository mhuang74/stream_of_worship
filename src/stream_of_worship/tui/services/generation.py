"""Transition generation service for TUI.

Handles generation of audio transitions between songs using stem separation
for smooth, gapless playback.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from stream_of_worship.tui.models.transition import TransitionParams, TransitionRecord
from stream_of_worship.tui.utils.logger import (
    get_error_logger,
    get_session_logger,
)


class TransitionGenerationService:
    """Service for generating audio transitions between songs."""

    def __init__(
        self,
        output_dir: Path,
        output_songs_dir: Path,
        stems_folder: Path,
    ):
        """Initialize transition generation service.

        Args:
            output_dir: Directory for generated transition clips
            output_songs_dir: Directory for full song outputs
            stems_folder: Directory containing separated stems
        """
        self.output_dir = Path(output_dir)
        self.output_songs_dir = Path(output_songs_dir)
        self.stems_folder = Path(stems_folder)

        # Ensure directories exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_songs_dir.mkdir(parents=True, exist_ok=True)

        # Track next ID for transitions
        self._next_id = 1

        self._error_logger = get_error_logger()
        self._session_logger = get_session_logger()

    def generate_transition(
        self,
        song_a_filename: str,
        song_b_filename: str,
        section_a: "Section",
        section_b: "Section",
        parameters: TransitionParams,
    ) -> Optional[TransitionRecord]:
        """Generate a transition between two songs.

        Args:
            song_a_filename: Filename of first song
            song_b_filename: Filename of second song
            section_a: Section from song A to use
            section_b: Section from song B to use
            parameters: Transition parameters

        Returns:
            TransitionRecord with metadata, or None if failed
        """
        logger = self._session_logger
        if not logger or not logger.enabled:
            logger = None
        else:
            logger = self._session_logger

        # Log generation start
        params_dict = parameters.to_dict()
        if logger:
            logger.log_generation_start(
                song_a_filename,
                song_b_filename,
                section_a.label,
                section_b.label,
                parameters.transition_type,
                params_dict,
            )

        # Generate unique filename for output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"transition_{timestamp}_{song_a_filename}_to_{song_b_filename}.flac"
        output_path = self.output_dir / output_filename

        try:
            # Determine output type based on transition type
            output_type = "transition"

            # Placeholder implementation - would use actual audio processing
            # In production, this would:
            # 1. Load stems for both songs
            # 2. Apply fade curves based on parameters
            # 3. Mix together with gap/overlap
            # 4. Export to FLAC

            import time
            time.sleep(0.1)  # Simulate processing

            # Calculate estimated duration
            from_duration = section_a.end - section_a.start
            to_duration = section_b.end - section_b.start

            if parameters.is_gap:
                total_duration = from_duration + to_duration + parameters.gap_beats * 0.5
            else:
                total_duration = from_duration + to_duration - parameters.overlap * 0.5

            # Create a simple dummy file for now
            output_path.touch()

            # Log completion
            if logger:
                logger.log_generation_complete(
                    str(output_path),
                    total_duration,
                    used_stems=True,
                )

            # Create transition record
            record = TransitionRecord(
                id=self._next_id,
                transition_type=parameters.transition_type,
                song_a_filename=song_a_filename,
                song_b_filename=song_b_filename,
                section_a_label=section_a.label,
                section_b_label=section_b.label,
                compatibility_score=85,  # Placeholder
                generated_at=datetime.now(),
                audio_path=output_path,
                is_saved=False,
                parameters=params_dict,
                output_type=output_type,
            )

            self._next_id += 1
            return record

        except Exception as e:
            if logger:
                self._error_logger.log_generation_error(
                    song_a_filename,
                    song_b_filename,
                    parameters.transition_type,
                    e,
                    params_dict,
                )
            return None

    def save_transition(
        self,
        transition: TransitionRecord,
        save_path: Optional[Path] = None,
        save_note: Optional[str] = None,
    ) -> bool:
        """Save a generated transition to output_songs directory.

        Args:
            transition: Transition record to save
            save_path: Optional path for saved file (auto-generated if None)
            save_note: Optional note to attach

        Returns:
            True if successful, False otherwise
        """
        if save_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = self.output_songs_dir / f"saved_transition_{timestamp}_{transition.song_a_filename}_to_{transition.song_b_filename}.flac"

        try:
            # Copy or move the file
            if transition.audio_path.exists():
                import shutil
                shutil.copy2(transition.audio_path, save_path)

            transition.is_saved = True
            transition.saved_path = save_path
            transition.save_note = save_note
            return True

        except Exception as e:
            if self._error_logger and self._error_logger.enabled:
                self._error_logger.log_file_error(str(save_path), e, operation="write")
            return False

    def generate_full_song(
        self,
        song_filename: str,
        section: "Section",
        parameters: TransitionParams,
    ) -> Optional[TransitionRecord]:
        """Generate a full song with transition parameters applied.

        This is for the case where user wants to export just one song
        with section selection and adjustments.

        Args:
            song_filename: Filename of song
            section: Section to use (may be full song)
            parameters: Section adjustment parameters

        Returns:
            TransitionRecord with metadata, or None if failed
        """
        # Similar to generate_transition but for single song
        # This would apply section boundaries and generate the
        # requested portion of the song

        # Placeholder implementation
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"full_song_{timestamp}_{song_filename}.flac"
        output_path = self.output_songs_dir / output_filename

        try:
            output_path.touch()

            record = TransitionRecord(
                id=self._next_id,
                transition_type="full",
                song_a_filename=song_filename,
                song_b_filename="",
                section_a_label=section.label,
                section_b_label="",
                compatibility_score=100,
                generated_at=datetime.now(),
                audio_path=output_path,
                is_saved=False,
                parameters={"output_type": "full_song"},
                output_type="full_song",
                full_song_path=output_path,
            )

            self._next_id += 1
            return record

        except Exception as e:
            if self._error_logger and self._error_logger.enabled:
                self._error_logger.log_generation_error(
                    song_filename,
                    "",
                    "full",
                    e,
                    parameters.to_dict(),
                )
            return None
