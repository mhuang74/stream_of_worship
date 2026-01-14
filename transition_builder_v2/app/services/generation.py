"""Transition generation service."""
from pathlib import Path
import numpy as np
import soundfile as sf

from app.models.song import Song
from app.models.transition import TransitionRecord


class TransitionGenerationService:
    """Service for generating audio transitions between song sections."""

    def __init__(self, output_dir: Path):
        """Initialize the generation service.

        Args:
            output_dir: Directory to save generated transitions
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_gap_transition(
        self,
        song_a: Song,
        song_b: Song,
        section_a_index: int,
        section_b_index: int,
        gap_beats: float = 1.0,
        section_a_start_adjust: int = 0,
        section_a_end_adjust: int = 0,
        section_b_start_adjust: int = 0,
        section_b_end_adjust: int = 0,
        sample_rate: int = 44100
    ) -> tuple[Path, dict]:
        """Generate a gap transition between two song sections.

        Gap transition: section A + silence (N beats) + section B.
        No fading, all stems preserved.

        Args:
            song_a: First song
            song_b: Second song
            section_a_index: Index of section in song A
            section_b_index: Index of section in song B
            gap_beats: Number of beats of silence (default: 1.0)
            section_a_start_adjust: Beats to adjust section A start (-4 to +4)
            section_a_end_adjust: Beats to adjust section A end (-4 to +4)
            section_b_start_adjust: Beats to adjust section B start (-4 to +4)
            section_b_end_adjust: Beats to adjust section B end (-4 to +4)
            sample_rate: Sample rate for output (default: 44100)

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        # Get sections
        section_a = song_a.sections[section_a_index]
        section_b = song_b.sections[section_b_index]

        # Load audio files
        audio_a, sr_a = sf.read(str(song_a.filepath), dtype='float32')
        audio_b, sr_b = sf.read(str(song_b.filepath), dtype='float32')

        # Ensure stereo
        if audio_a.ndim == 1:
            audio_a = np.stack([audio_a, audio_a], axis=-1)
        if audio_b.ndim == 1:
            audio_b = np.stack([audio_b, audio_b], axis=-1)

        # Apply section adjustments
        # Positive = extend (start earlier/end later), Negative = clip (start later/end earlier)
        # For start: +N starts N beats later (clip), -N starts N beats earlier (extend)
        # For end: +N ends N beats later (extend), -N ends N beats earlier (clip)
        beat_duration_a = 60.0 / song_a.tempo  # Duration of one beat in seconds
        beat_duration_b = 60.0 / song_b.tempo

        # Adjust section A boundaries
        section_a_start = section_a.start + (section_a_start_adjust * beat_duration_a)
        section_a_end = section_a.end + (section_a_end_adjust * beat_duration_a)

        # Adjust section B boundaries
        section_b_start = section_b.start + (section_b_start_adjust * beat_duration_b)
        section_b_end = section_b.end + (section_b_end_adjust * beat_duration_b)

        # Clamp to file boundaries
        section_a_start = max(0, section_a_start)
        section_a_end = min(len(audio_a) / sr_a, section_a_end)
        section_b_start = max(0, section_b_start)
        section_b_end = min(len(audio_b) / sr_b, section_b_end)

        # Extract section A
        start_sample_a = int(section_a_start * sr_a)
        end_sample_a = int(section_a_end * sr_a)
        section_a_audio = audio_a[start_sample_a:end_sample_a]

        # Extract section B
        start_sample_b = int(section_b_start * sr_b)
        end_sample_b = int(section_b_end * sr_b)
        section_b_audio = audio_b[start_sample_b:end_sample_b]

        # Resample if necessary (assume both songs at same sample rate for now)
        # TODO: Add resampling if sr_a != sr_b

        # Calculate gap duration in seconds using song A's tempo
        # 1 beat = 60 / BPM seconds
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
            section_a_start_adjust = kwargs.get("section_a_start_adjust", 0)
            section_a_end_adjust = kwargs.get("section_a_end_adjust", 0)
            section_b_start_adjust = kwargs.get("section_b_start_adjust", 0)
            section_b_end_adjust = kwargs.get("section_b_end_adjust", 0)
            return self.generate_gap_transition(
                song_a, song_b, section_a_index, section_b_index, gap_beats,
                section_a_start_adjust, section_a_end_adjust,
                section_b_start_adjust, section_b_end_adjust
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
        sample_rate: int = 44100
    ) -> tuple[Path, dict]:
        """Generate a focused preview of transition point.

        Preview: last N beats of section A + gap + first N beats of section B.
        Useful for quick auditioning of the transition point.

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

        Returns:
            Tuple of (output_path, metadata_dict)
        """
        # Get sections
        section_a = song_a.sections[section_a_index]
        section_b = song_b.sections[section_b_index]

        # Load audio files
        audio_a, sr_a = sf.read(str(song_a.filepath), dtype='float32')
        audio_b, sr_b = sf.read(str(song_b.filepath), dtype='float32')

        # Ensure stereo
        if audio_a.ndim == 1:
            audio_a = np.stack([audio_a, audio_a], axis=-1)
        if audio_b.ndim == 1:
            audio_b = np.stack([audio_b, audio_b], axis=-1)

        # Apply section adjustments
        # Positive = extend (start earlier/end later), Negative = clip (start later/end earlier)
        # For start: +N starts N beats later (clip), -N starts N beats earlier (extend)
        # For end: +N ends N beats later (extend), -N ends N beats earlier (clip)
        beat_duration_a = 60.0 / song_a.tempo  # Duration of one beat in seconds
        beat_duration_b = 60.0 / song_b.tempo

        # Adjust section A boundaries
        section_a_start_adjusted = section_a.start + (section_a_start_adjust * beat_duration_a)
        section_a_end_adjusted = section_a.end + (section_a_end_adjust * beat_duration_a)

        # Adjust section B boundaries
        section_b_start_adjusted = section_b.start + (section_b_start_adjust * beat_duration_b)
        section_b_end_adjusted = section_b.end + (section_b_end_adjust * beat_duration_b)

        # Clamp to file boundaries
        section_a_start_adjusted = max(0, section_a_start_adjusted)
        section_a_end_adjusted = min(len(audio_a) / sr_a, section_a_end_adjusted)
        section_b_start_adjusted = max(0, section_b_start_adjusted)
        section_b_end_adjusted = min(len(audio_b) / sr_b, section_b_end_adjusted)

        # Calculate preview duration in seconds using each song's tempo
        preview_duration_a = (preview_beats * 60.0) / song_a.tempo
        preview_duration_b = (preview_beats * 60.0) / song_b.tempo

        # Extract LAST N beats of section A (from adjusted boundaries)
        section_a_start = int(section_a_start_adjusted * sr_a)
        section_a_end = int(section_a_end_adjusted * sr_a)
        preview_samples_a = int(preview_duration_a * sr_a)

        # Take from end of section A
        section_a_preview_start = max(section_a_start, section_a_end - preview_samples_a)
        section_a_preview = audio_a[section_a_preview_start:section_a_end]

        # Extract FIRST N beats of section B (from adjusted boundaries)
        section_b_start = int(section_b_start_adjusted * sr_b)
        section_b_end = int(section_b_end_adjusted * sr_b)
        preview_samples_b = int(preview_duration_b * sr_b)

        # Take from beginning of section B
        section_b_preview_end = min(section_b_end, section_b_start + preview_samples_b)
        section_b_preview = audio_b[section_b_start:section_b_preview_end]

        # Add gap if specified
        if gap_beats > 0:
            gap_duration_seconds = (gap_beats * 60.0) / song_a.tempo
            gap_samples = int(gap_duration_seconds * sr_a)
            silence = np.zeros((gap_samples, 2), dtype='float32')
            preview_audio = np.vstack([section_a_preview, silence, section_b_preview])
        else:
            preview_audio = np.vstack([section_a_preview, section_b_preview])

        # Generate output filename for preview
        output_filename = f"preview_{song_a.filename}_{section_a.label}_{song_b.filename}_{section_b.label}_{preview_beats}beats.flac"
        output_path = self.output_dir / output_filename

        # Save audio
        sf.write(str(output_path), preview_audio, sr_a, format='FLAC')

        # Create metadata
        metadata = {
            "type": "focused_preview",
            "song_a": song_a.filename,
            "song_b": song_b.filename,
            "section_a": {
                "label": section_a.label,
                "index": section_a_index,
            },
            "section_b": {
                "label": section_b.label,
                "index": section_b_index,
            },
            "preview_beats": preview_beats,
            "gap_beats": gap_beats,
            "total_duration_seconds": preview_audio.shape[0] / sr_a,
            "sample_rate": sr_a,
            "output_file": str(output_path)
        }

        return output_path, metadata
