"""Transition generation service."""
from pathlib import Path
import numpy as np
import soundfile as sf

from app.models.song import Song
from app.models.transition import TransitionRecord
from app.utils.logger import get_session_logger


# Available stem types
STEM_TYPES = ["vocals", "bass", "drums", "other"]


def create_logarithmic_fade_out(num_samples: int, fade_bottom: float = 0.0) -> np.ndarray:
    """Create a logarithmic fade-out curve.

    Goes from 1.0 (full volume) to fade_bottom following dB curve.

    Args:
        num_samples: Number of samples for the fade
        fade_bottom: Minimum volume at end of fade (0.0 to 1.0, default 0.0)

    Returns:
        1D numpy array of gain values from 1.0 to fade_bottom
    """
    if num_samples <= 0:
        return np.array([])

    # Clamp fade_bottom to valid range
    fade_bottom = max(0.0, min(1.0, fade_bottom))

    if fade_bottom >= 1.0:
        return np.ones(num_samples, dtype=np.float32)

    if fade_bottom <= 0.001:
        min_db = -60.0
    else:
        min_db = 20.0 * np.log10(fade_bottom)

    # Linear dB ramp from 0 to min_db
    db_curve = np.linspace(0, min_db, num_samples)
    gain_curve = 10 ** (db_curve / 20.0)
    return gain_curve.astype(np.float32)


def create_logarithmic_fade_in(num_samples: int, fade_bottom: float = 0.0) -> np.ndarray:
    """Create a logarithmic fade-in curve.

    Goes from fade_bottom to 1.0 (full volume) following dB curve.

    Args:
        num_samples: Number of samples for the fade
        fade_bottom: Starting volume (0.0 to 1.0, default 0.0)

    Returns:
        1D numpy array of gain values from fade_bottom to 1.0
    """
    if num_samples <= 0:
        return np.array([])

    # Clamp fade_bottom to valid range
    fade_bottom = max(0.0, min(1.0, fade_bottom))

    if fade_bottom >= 1.0:
        return np.ones(num_samples, dtype=np.float32)

    if fade_bottom <= 0.001:
        min_db = -60.0
    else:
        min_db = 20.0 * np.log10(fade_bottom)

    # Linear dB ramp from min_db to 0
    db_curve = np.linspace(min_db, 0, num_samples)
    gain_curve = 10 ** (db_curve / 20.0)
    return gain_curve.astype(np.float32)


def get_stem_folder_name(song_filename: str) -> str:
    """Convert song filename to stem folder name.

    Examples:
        'do_it_again.flac' -> 'do_it_again'
        'joy_to_heaven.mp3' -> 'joy_to_heaven'

    Args:
        song_filename: The song filename with extension

    Returns:
        Folder name (filename without extension)
    """
    return Path(song_filename).stem


class TransitionGenerationService:
    """Service for generating audio transitions between song sections."""

    def __init__(self, output_dir: Path, stems_folder: Path | None = None):
        """Initialize the generation service.

        Args:
            output_dir: Directory to save generated transitions
            stems_folder: Directory containing stem files (optional)
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stems_folder = stems_folder

    def _get_stems_path(self, song_filename: str) -> Path | None:
        """Get path to stems folder for a song.

        Args:
            song_filename: The song filename

        Returns:
            Path to stems folder, or None if not available
        """
        if self.stems_folder is None:
            return None

        folder_name = get_stem_folder_name(song_filename)
        stems_path = self.stems_folder / folder_name

        if stems_path.exists():
            return stems_path
        return None

    def _load_stems(
        self,
        stems_path: Path,
        sample_rate: int | None = None
    ) -> tuple[dict[str, np.ndarray], int]:
        """Load all stem files from a folder.

        Args:
            stems_path: Path to folder containing stem wav files
            sample_rate: Expected sample rate (for validation)

        Returns:
            Tuple of (dict of stem_name -> audio array, sample_rate)
        """
        stems = {}
        sr = None

        for stem_name in STEM_TYPES:
            stem_file = stems_path / f"{stem_name}.wav"
            if stem_file.exists():
                audio, stem_sr = sf.read(str(stem_file), dtype='float32')
                if sr is None:
                    sr = stem_sr
                # Ensure stereo
                if audio.ndim == 1:
                    audio = np.stack([audio, audio], axis=-1)
                stems[stem_name] = audio

        return stems, sr or 44100

    def _apply_fade_to_stems(
        self,
        stems: dict[str, np.ndarray],
        stems_to_fade: list[str],
        fade_type: str,  # "out" or "in"
        fade_samples: int,
        at_start: bool = False,  # If True, fade at start; if False, fade at end
        fade_bottom: float = 0.0,
    ) -> dict[str, np.ndarray]:
        """Apply fade to specified stems.

        Args:
            stems: Dict of stem_name -> audio array
            stems_to_fade: List of stem names to apply fade to
            fade_type: "out" for fade-out, "in" for fade-in
            fade_samples: Number of samples for fade duration
            at_start: If True, apply fade at start of audio; if False, at end
            fade_bottom: Minimum volume during fade (0.0 to 1.0, default 0.0)

        Returns:
            Dict of stem_name -> audio array with fades applied
        """
        if fade_samples <= 0:
            return stems

        # Create fade curve
        if fade_type == "out":
            fade_curve = create_logarithmic_fade_out(fade_samples, fade_bottom=fade_bottom)
        else:
            fade_curve = create_logarithmic_fade_in(fade_samples, fade_bottom=fade_bottom)

        # Expand to stereo for broadcasting
        fade_curve_stereo = fade_curve[:, np.newaxis]

        result = {}
        for stem_name, audio in stems.items():
            if stem_name in stems_to_fade or "all" in stems_to_fade:
                # Apply fade
                audio = audio.copy()
                if at_start:
                    # Fade at beginning
                    actual_samples = min(fade_samples, len(audio))
                    audio[:actual_samples] *= fade_curve_stereo[:actual_samples]
                else:
                    # Fade at end
                    actual_samples = min(fade_samples, len(audio))
                    audio[-actual_samples:] *= fade_curve_stereo[:actual_samples]
                result[stem_name] = audio
            else:
                # Keep at full volume
                result[stem_name] = audio

        return result

    def _mix_stems(self, stems: dict[str, np.ndarray]) -> np.ndarray:
        """Mix multiple stems together.

        Args:
            stems: Dict of stem_name -> audio array

        Returns:
            Mixed audio array
        """
        if not stems:
            return np.array([])

        # Find the longest stem
        max_len = max(len(audio) for audio in stems.values())

        # Mix by adding all stems together
        mixed = np.zeros((max_len, 2), dtype=np.float32)
        for audio in stems.values():
            mixed[:len(audio)] += audio

        return mixed

    def generate_gap_transition(
        self,
        song_a: Song,
        song_b: Song,
        section_a_index: int,
        section_b_index: int,
        gap_beats: float = 1.0,
        fade_window_beats: float = 8.0,
        fade_bottom: float = 0.33,
        stems_to_fade: list[str] | None = None,
        section_a_start_adjust: int = 0,
        section_a_end_adjust: int = 0,
        section_b_start_adjust: int = 0,
        section_b_end_adjust: int = 0,
        sample_rate: int = 44100
    ) -> tuple[Path, dict]:
        """Generate a gap transition between two song sections with optional fading.

        Gap transition: section A (with fade-out) + silence + section B (with fade-in).
        Fading is applied to selected stems using logarithmic (dB) curve.
        fade_window is split equally: half for fade-out, half for fade-in.

        Args:
            song_a: First song
            song_b: Second song
            section_a_index: Index of section in song A
            section_b_index: Index of section in song B
            gap_beats: Number of beats of silence (default: 1.0)
            fade_window_beats: Total fade duration in beats (half for each direction)
            fade_bottom: Minimum volume during fade as percentage (0.0 to 1.0). Default 0.33.
            stems_to_fade: List of stems to fade ["bass", "drums", "other", "vocals"]
                           Empty list = no fade. Default: ["bass", "drums", "other"]
            section_a_start_adjust: Beats to adjust section A start (-4 to +4)
            section_a_end_adjust: Beats to adjust section A end (-4 to +4)
            section_b_start_adjust: Beats to adjust section B start (-4 to +4)
            section_b_end_adjust: Beats to adjust section B end (-4 to +4)
            sample_rate: Sample rate for output (default: 44100)

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        if stems_to_fade is None:
            stems_to_fade = ["bass", "drums", "other"]

        # Get sections
        section_a = song_a.sections[section_a_index]
        section_b = song_b.sections[section_b_index]

        # Check if stems are available for both songs
        stems_path_a = self._get_stems_path(song_a.filename)
        stems_path_b = self._get_stems_path(song_b.filename)
        use_stems = stems_path_a is not None and stems_path_b is not None and len(stems_to_fade) > 0

        # Log generation start
        session_logger = get_session_logger()
        if session_logger:
            session_logger.log_generation_start(
                song_a=song_a.filename,
                song_b=song_b.filename,
                section_a_label=section_a.label,
                section_b_label=section_b.label,
                transition_type="gap",
                parameters={
                    "gap_beats": gap_beats,
                    "fade_window_beats": fade_window_beats,
                    "fade_bottom": fade_bottom,
                    "stems_to_fade": stems_to_fade,
                }
            )

        # Calculate beat durations
        beat_duration_a = 60.0 / song_a.tempo
        beat_duration_b = 60.0 / song_b.tempo

        # Adjust section boundaries
        section_a_start = section_a.start + (section_a_start_adjust * beat_duration_a)
        section_a_end = section_a.end + (section_a_end_adjust * beat_duration_a)
        section_b_start = section_b.start + (section_b_start_adjust * beat_duration_b)
        section_b_end = section_b.end + (section_b_end_adjust * beat_duration_b)

        if use_stems:
            # Load stems for both songs
            stems_a, sr_a = self._load_stems(stems_path_a)
            stems_b, sr_b = self._load_stems(stems_path_b)

            # Clamp to file boundaries (use first stem's length)
            first_stem_a = next(iter(stems_a.values()))
            first_stem_b = next(iter(stems_b.values()))
            section_a_start = max(0, section_a_start)
            section_a_end = min(len(first_stem_a) / sr_a, section_a_end)
            section_b_start = max(0, section_b_start)
            section_b_end = min(len(first_stem_b) / sr_b, section_b_end)

            # Extract sections from stems
            start_sample_a = int(section_a_start * sr_a)
            end_sample_a = int(section_a_end * sr_a)
            start_sample_b = int(section_b_start * sr_b)
            end_sample_b = int(section_b_end * sr_b)

            section_stems_a = {name: audio[start_sample_a:end_sample_a] for name, audio in stems_a.items()}
            section_stems_b = {name: audio[start_sample_b:end_sample_b] for name, audio in stems_b.items()}

            # Calculate fade samples (half of fade_window for each direction)
            fade_out_beats = fade_window_beats / 2.0
            fade_in_beats = fade_window_beats / 2.0
            fade_out_samples = int((fade_out_beats * 60.0 / song_a.tempo) * sr_a)
            fade_in_samples = int((fade_in_beats * 60.0 / song_b.tempo) * sr_b)

            # Apply fade-out to section A stems (at end)
            section_stems_a = self._apply_fade_to_stems(
                section_stems_a,
                stems_to_fade,
                "out",
                fade_out_samples,
                at_start=False,
                fade_bottom=fade_bottom,
            )

            # Log fade-out operation for Song A
            if session_logger:
                stems_kept = [s for s in STEM_TYPES if s not in stems_to_fade]
                session_logger.log_stems_operation(
                    song_name=song_a.filename,
                    stems_to_fade=stems_to_fade,
                    stems_kept=stems_kept,
                    fade_type="out",
                    fade_bottom=fade_bottom
                )

            # Apply fade-in to section B stems (at start)
            section_stems_b = self._apply_fade_to_stems(
                section_stems_b,
                stems_to_fade,
                "in",
                fade_in_samples,
                at_start=True,
                fade_bottom=fade_bottom,
            )

            # Log fade-in operation for Song B
            if session_logger:
                stems_kept = [s for s in STEM_TYPES if s not in stems_to_fade]
                session_logger.log_stems_operation(
                    song_name=song_b.filename,
                    stems_to_fade=stems_to_fade,
                    stems_kept=stems_kept,
                    fade_type="in",
                    fade_bottom=fade_bottom
                )

            # Mix stems
            section_a_audio = self._mix_stems(section_stems_a)
            section_b_audio = self._mix_stems(section_stems_b)
        else:
            # Fallback: load full audio and apply fade to entire mix
            # Log fallback reason
            if session_logger:
                if stems_path_a is None and stems_path_b is None:
                    reason = "Stems not available for either song"
                elif stems_path_a is None:
                    reason = f"Stems not available for {song_a.filename}"
                elif stems_path_b is None:
                    reason = f"Stems not available for {song_b.filename}"
                else:
                    reason = "No stems selected for fading"
                session_logger.log_fallback(reason)

            audio_a, sr_a = sf.read(str(song_a.filepath), dtype='float32')
            audio_b, sr_b = sf.read(str(song_b.filepath), dtype='float32')

            # Ensure stereo
            if audio_a.ndim == 1:
                audio_a = np.stack([audio_a, audio_a], axis=-1)
            if audio_b.ndim == 1:
                audio_b = np.stack([audio_b, audio_b], axis=-1)

            # Clamp to file boundaries
            section_a_start = max(0, section_a_start)
            section_a_end = min(len(audio_a) / sr_a, section_a_end)
            section_b_start = max(0, section_b_start)
            section_b_end = min(len(audio_b) / sr_b, section_b_end)

            # Extract sections
            start_sample_a = int(section_a_start * sr_a)
            end_sample_a = int(section_a_end * sr_a)
            start_sample_b = int(section_b_start * sr_b)
            end_sample_b = int(section_b_end * sr_b)

            section_a_audio = audio_a[start_sample_a:end_sample_a].copy()
            section_b_audio = audio_b[start_sample_b:end_sample_b].copy()

            # Apply fades to full mix if stems_to_fade is not empty
            if len(stems_to_fade) > 0:
                fade_out_beats = fade_window_beats / 2.0
                fade_in_beats = fade_window_beats / 2.0
                fade_out_samples = int((fade_out_beats * 60.0 / song_a.tempo) * sr_a)
                fade_in_samples = int((fade_in_beats * 60.0 / song_b.tempo) * sr_b)

                # Apply fade-out at end of section A
                if fade_out_samples > 0 and len(section_a_audio) > 0:
                    fade_out_curve = create_logarithmic_fade_out(
                        fade_out_samples, fade_bottom=fade_bottom
                    )
                    actual_samples = min(fade_out_samples, len(section_a_audio))
                    section_a_audio[-actual_samples:] *= fade_out_curve[
                        :actual_samples, np.newaxis
                    ]

                # Apply fade-in at start of section B
                if fade_in_samples > 0 and len(section_b_audio) > 0:
                    fade_in_curve = create_logarithmic_fade_in(
                        fade_in_samples, fade_bottom=fade_bottom
                    )
                    actual_samples = min(fade_in_samples, len(section_b_audio))
                    section_b_audio[:actual_samples] *= fade_in_curve[
                        :actual_samples, np.newaxis
                    ]

        # Calculate gap duration in seconds using song A's tempo
        gap_duration_seconds = (gap_beats * 60.0) / song_a.tempo
        gap_samples = int(gap_duration_seconds * sr_a)

        # Create silence (stereo)
        silence = np.zeros((gap_samples, 2), dtype='float32')

        # Concatenate: section A + silence + section B
        transition_audio = np.vstack([section_a_audio, silence, section_b_audio])

        # Generate output filename
        output_filename = f"transition_gap_{song_a.filename}_{section_a.label}_{song_b.filename}_{section_b.label}_{gap_beats}beats.flac"
        output_path = self.output_dir / output_filename

        # Save audio
        sf.write(str(output_path), transition_audio, sr_a, format='FLAC')

        # Log generation complete
        if session_logger:
            session_logger.log_generation_complete(
                output_path=str(output_path),
                duration_seconds=transition_audio.shape[0] / sr_a,
                used_stems=use_stems
            )

        # Create metadata
        metadata = {
            "type": "gap",
            "song_a": song_a.filename,
            "song_b": song_b.filename,
            "section_a": {
                "label": section_a.label,
                "index": section_a_index,
                "start": section_a.start,
                "end": section_a.end,
                "duration": section_a.duration
            },
            "section_b": {
                "label": section_b.label,
                "index": section_b_index,
                "start": section_b.start,
                "end": section_b.end,
                "duration": section_b.duration
            },
            "gap_beats": gap_beats,
            "gap_duration_seconds": gap_duration_seconds,

            "fade_window_beats": fade_window_beats,
            "fade_bottom": fade_bottom,
            "stems_to_fade": stems_to_fade,
            "used_stems": use_stems,
            "total_duration_seconds": transition_audio.shape[0] / sr_a,
            "sample_rate": sr_a,
            "output_file": str(output_path)
        }

        return output_path, metadata

    def generate_transition(
        self,
        song_a: Song,
        song_b: Song,
        section_a_index: int,
        section_b_index: int,
        transition_type: str,
        **kwargs
    ) -> tuple[Path, dict]:
        """Generate a transition of the specified type.

        Args:
            song_a: First song
            song_b: Second song
            section_a_index: Index of section in song A
            section_b_index: Index of section in song B
            transition_type: Type of transition ("gap", "crossfade", etc.)
            **kwargs: Additional parameters specific to transition type

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        if transition_type.lower() == "gap":
            gap_beats = kwargs.get("gap_beats", 1.0)
            fade_window_beats = kwargs.get("fade_window_beats", 8.0)
            fade_bottom = kwargs.get("fade_bottom", 0.33)
            stems_to_fade = kwargs.get("stems_to_fade", None)
            section_a_start_adjust = kwargs.get("section_a_start_adjust", 0)
            section_a_end_adjust = kwargs.get("section_a_end_adjust", 0)
            section_b_start_adjust = kwargs.get("section_b_start_adjust", 0)
            section_b_end_adjust = kwargs.get("section_b_end_adjust", 0)
            
            return self.generate_gap_transition(
                song_a=song_a,
                song_b=song_b,
                section_a_index=section_a_index,
                section_b_index=section_b_index,
                gap_beats=gap_beats,
                fade_window_beats=fade_window_beats,
                fade_bottom=fade_bottom,
                stems_to_fade=stems_to_fade,
                section_a_start_adjust=section_a_start_adjust,
                section_a_end_adjust=section_a_end_adjust,
                section_b_start_adjust=section_b_start_adjust,
                section_b_end_adjust=section_b_end_adjust
            )
        else:
            raise NotImplementedError(f"Transition type '{transition_type}' not yet implemented")

    def generate_focused_preview(
        self,
        song_a: Song,
        song_b: Song,
        section_a_index: int,
        section_b_index: int,
        preview_beats: float = 4.0,
        gap_beats: float = 0.0,
        section_a_start_adjust: int = 0,
        section_a_end_adjust: int = 0,
        section_b_start_adjust: int = 0,
        section_b_end_adjust: int = 0,
        sample_rate: int = 44100,
        **kwargs
    ) -> tuple[Path, dict]:
        """Generate a focused preview of transition point using the production path.

        Preview: last N beats of section A + gap + first N beats of section B.
        Delegates to generate_gap_transition to ensure WYSIWYG results (including fades/stems).

        Args:
            song_a: First song
            song_b: Second song
            section_a_index: Index of section in song A
            section_b_index: Index of section in song B
            preview_beats: Number of beats to preview from each section (default: 4.0)
            gap_beats: Number of beats of silence gap (default: 0.0)
            section_a_start_adjust: Beats to adjust section A start (-4 to +4)
            section_a_end_adjust: Beats to adjust section A end (-4 to +4)
            section_b_start_adjust: Beats to adjust section B start (-4 to +4)
            section_b_end_adjust: Beats to adjust section B end (-4 to +4)
            sample_rate: Sample rate for output (default: 44100)
            **kwargs: Additional arguments passed to generate_gap_transition (e.g., stems_to_fade)

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        # Get sections to calculate durations
        section_a = song_a.sections[section_a_index]
        section_b = song_b.sections[section_b_index]

        # Calculate beat durations
        beat_duration_a = 60.0 / song_a.tempo
        beat_duration_b = 60.0 / song_b.tempo

        # Calculate section durations in beats
        # Note: We use the actual time duration from the section object
        section_duration_beats_a = (section_a.end - section_a.start) / beat_duration_a
        section_duration_beats_b = (section_b.end - section_b.start) / beat_duration_b

        # Calculate adjustments for the preview window
        # Section A: We want the LAST `preview_beats`.
        # So we adjust the start time forward to: End - `preview_beats`
        # New_Start_Adjust = (Original_Duration_Beats + Original_End_Adjust) - Preview_Beats
        # We take max with original start adjust to ensure we don't start before the user intended (if they clipped it heavily)
        preview_start_adjust_a = max(
            section_a_start_adjust,
            section_duration_beats_a + section_a_end_adjust - preview_beats
        )

        # Section B: We want the FIRST `preview_beats`.
        # So we adjust the end time backward to: Start + `preview_beats`
        # New_End_Adjust = (Original_Start_Adjust + Preview_Beats) - Original_Duration_Beats
        # We take min with original end adjust
        preview_end_adjust_b = min(
            section_b_end_adjust,
            -section_duration_beats_b + section_b_start_adjust + preview_beats
        )

        # Generate using the production path
        # This gives us fades, stems, and consistent audio processing
        output_path, metadata = self.generate_gap_transition(
            song_a=song_a,
            song_b=song_b,
            section_a_index=section_a_index,
            section_b_index=section_b_index,
            gap_beats=gap_beats,
            section_a_start_adjust=preview_start_adjust_a,
            section_a_end_adjust=section_a_end_adjust,
            section_b_start_adjust=section_b_start_adjust,
            section_b_end_adjust=preview_end_adjust_b,
            sample_rate=sample_rate,
            **kwargs
        )

        # Rename the output file to match preview naming convention
        # Original: transition_gap_...
        # New: preview_...
        new_filename = f"preview_{song_a.filename}_{section_a.label}_{song_b.filename}_{section_b.label}_{preview_beats}beats.flac"
        new_output_path = self.output_dir / new_filename
        
        # Move/Rename file
        if output_path.exists():
            output_path.replace(new_output_path)
            output_path = new_output_path

        # Update metadata to reflect this is a preview
        metadata["type"] = "focused_preview"
        metadata["preview_beats"] = preview_beats
        metadata["output_file"] = str(output_path)
        
        # Add original adjustments for reference since we modified them in the call
        metadata["original_adjustments"] = {
            "section_a_start": section_a_start_adjust,
            "section_a_end": section_a_end_adjust,
            "section_b_start": section_b_start_adjust,
            "section_b_end": section_b_end_adjust
        }

        return output_path, metadata

    def generate_full_song_output(
        self,
        song_a: Song,
        song_b: Song,
        section_a_index: int,
        section_b_index: int,
        transition_audio_path: Path,
        sr: int = 44100  # Sample rate (should match transition)
    ) -> tuple[Path, dict]:
        """Generate a complete song set:
        - Song A sections before selected section
        - Previously generated transition
        - Song B sections after selected section

        Args:
            song_a: Song A object
            song_b: Song B object
            section_a_index: Index of selected Song A section
            section_b_index: Index of selected Song B section
            transition_audio_path: Path to previously generated transition audio
            sr: Sample rate

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        # 1. Load transition audio
        transition_audio, sr_transition = sf.read(str(transition_audio_path))
        if sr != sr_transition:
            raise ValueError(f"Sample rate mismatch: expected {sr}, got {sr_transition}")

        # Ensure stereo
        if transition_audio.ndim == 1:
            transition_audio = np.stack([transition_audio, transition_audio], axis=-1)

        # 2. Load Song A sections BEFORE selected section
        song_a_audio_parts = []
        if section_a_index > 0:
            song_a_full, sr_a = sf.read(str(song_a.filepath), dtype='float32')
            if sr != sr_a:
                import librosa
                song_a_full = librosa.resample(song_a_full.T, orig_sr=sr_a, target_sr=sr).T

            # Ensure stereo
            if song_a_full.ndim == 1:
                song_a_full = np.stack([song_a_full, song_a_full], axis=-1)

            for i in range(section_a_index):
                section = song_a.sections[i]
                start_sample = int(section.start * sr)
                end_sample = int(section.end * sr)
                section_audio = song_a_full[start_sample:end_sample]
                song_a_audio_parts.append(section_audio)

        # 3. Load Song B sections AFTER selected section
        song_b_audio_parts = []
        if section_b_index < len(song_b.sections) - 1:
            song_b_full, sr_b = sf.read(str(song_b.filepath), dtype='float32')
            if sr != sr_b:
                import librosa
                song_b_full = librosa.resample(song_b_full.T, orig_sr=sr_b, target_sr=sr).T

            # Ensure stereo
            if song_b_full.ndim == 1:
                song_b_full = np.stack([song_b_full, song_b_full], axis=-1)

            for i in range(section_b_index + 1, len(song_b.sections)):
                section = song_b.sections[i]
                start_sample = int(section.start * sr)
                end_sample = int(section.end * sr)
                section_audio = song_b_full[start_sample:end_sample]
                song_b_audio_parts.append(section_audio)

        # 4. Concatenate all parts
        all_parts = []

        # Add Song A prefix sections
        all_parts.extend(song_a_audio_parts)

        # Add transition (contains Song A selected + gap + Song B selected)
        all_parts.append(transition_audio)

        # Add Song B suffix sections
        all_parts.extend(song_b_audio_parts)

        # Combine
        if all_parts:
            output_audio = np.vstack(all_parts)
        else:
            # Edge case: only transition (no before/after sections)
            output_audio = transition_audio

        # 5. Create output directory
        output_dir = Path("song_sets_output")
        output_dir.mkdir(exist_ok=True)

        # 6. Generate filename
        section_a_label = song_a.sections[section_a_index].label
        section_b_label = song_b.sections[section_b_index].label
        filename = (
            f"songset_{song_a.filename}_{section_a_label}_to_"
            f"{song_b.filename}_{section_b_label}.flac"
        )
        output_path = output_dir / filename

        # 7. Write to file
        sf.write(str(output_path), output_audio, sr, format='FLAC')

        # 8. Create metadata
        metadata = {
            "output_type": "full_song",
            "song_a": song_a.filename,
            "song_b": song_b.filename,
            "section_a_index": section_a_index,
            "section_b_index": section_b_index,
            "section_a_label": section_a_label,
            "section_b_label": section_b_label,
            "num_song_a_sections_before": section_a_index,
            "num_song_b_sections_after": len(song_b.sections) - section_b_index - 1,
            "total_duration": len(output_audio) / sr,
            "sample_rate": sr,
        }

        # 9. Log
        session_logger = get_session_logger()
        if session_logger:
            session_logger.log_generation_complete(
                song_a=song_a.filename,
                song_b=song_b.filename,
                output_path=str(output_path),
                duration=metadata["total_duration"],
                extra_info=f"Full song output: {metadata['num_song_a_sections_before']} + transition + {metadata['num_song_b_sections_after']} sections"
            )

        return output_path, metadata
