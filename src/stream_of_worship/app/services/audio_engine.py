"""Audio engine service for sow-app.

Generates gap transitions between songs for multi-song exports.
Uses pydub for audio manipulation.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pydub import AudioSegment

from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.services.asset_cache import AssetCache


@dataclass
class AudioSegmentInfo:
    """Information about an audio segment in the export.

    Attributes:
        item: SongsetItem this segment represents
        audio_path: Path to the source audio file
        start_time_seconds: Start time in the final mix
        duration_seconds: Duration in the final mix
        gap_before_seconds: Gap duration before this song
    """

    item: SongsetItem
    audio_path: Path
    start_time_seconds: float
    duration_seconds: float
    gap_before_seconds: float


@dataclass
class ExportResult:
    """Result of audio export operation.

    Attributes:
        output_path: Path to the generated audio file
        total_duration_seconds: Total duration of the exported audio
        segments: List of segment information
        sample_rate: Sample rate of the output
        channels: Number of channels in the output
    """

    output_path: Path
    total_duration_seconds: float
    segments: list[AudioSegmentInfo]
    sample_rate: int = 44100
    channels: int = 2


class AudioEngine:
    """Audio engine for generating gap transitions.

    Combines multiple songs with configurable gaps between them.
    Supports volume normalization and basic audio processing.

    Attributes:
        asset_cache: Asset cache for accessing audio files
        target_lufs: Target loudness for normalization
    """

    def __init__(
        self,
        asset_cache: AssetCache,
        target_lufs: float = -14.0,
    ):
        """Initialize the audio engine.

        Args:
            asset_cache: Asset cache for accessing audio files
            target_lufs: Target loudness level (default -14 LUFS)
        """
        self.asset_cache = asset_cache
        self.target_lufs = target_lufs

    def _load_audio(self, file_path: Path) -> AudioSegment:
        """Load an audio file with pydub.

        Args:
            file_path: Path to audio file

        Returns:
            AudioSegment
        """
        return AudioSegment.from_file(str(file_path))

    def _calculate_gap_ms(self, item: SongsetItem, tempo_bpm: Optional[float] = None) -> int:
        """Calculate gap duration in milliseconds.

        Args:
            item: Songset item with gap configuration
            tempo_bpm: Tempo for beat-based gap calculation

        Returns:
            Gap duration in milliseconds
        """
        if item.crossfade_enabled and item.crossfade_duration_seconds:
            # Crossfade - no gap, just the crossfade duration
            return 0

        # Calculate gap from beats
        gap_beats = item.gap_beats if item.gap_beats is not None else 2.0

        if tempo_bpm and tempo_bpm > 0:
            # Convert beats to milliseconds
            beat_duration_ms = 60000.0 / tempo_bpm
            return int(gap_beats * beat_duration_ms)
        else:
            # Default: 2 seconds per beat estimate
            return int(gap_beats * 1000)

    def _normalize_loudness(
        self, audio: AudioSegment, target_lufs: Optional[float] = None
    ) -> AudioSegment:
        """Normalize audio to target loudness.

        Args:
            audio: Audio segment to normalize
            target_lufs: Target loudness (defaults to self.target_lufs)

        Returns:
            Normalized audio segment
        """
        target = target_lufs if target_lufs is not None else self.target_lufs

        # Simple RMS-based normalization (approximation)
        # For true LUFS, would need pyloudnorm
        current_db = audio.dBFS
        if current_db != float('-inf'):
            adjustment = target - current_db
            return audio.apply_gain(adjustment)

        return audio

    def generate_songset_audio(
        self,
        items: list[SongsetItem],
        output_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        normalize: bool = True,
    ) -> ExportResult:
        """Generate combined audio for a songset with gap transitions.

        Args:
            items: List of songset items
            output_path: Path for the output audio file
            progress_callback: Called with (current_step, total_steps)
            normalize: Whether to normalize loudness

        Returns:
            ExportResult with output information
        """
        if not items:
            raise ValueError("Cannot generate audio for empty songset")

        segments: list[AudioSegmentInfo] = []
        combined_audio: Optional[AudioSegment] = None
        current_time_ms = 0

        total_steps = len(items) * 2  # Load + process for each item
        current_step = 0

        for i, item in enumerate(items):
            # Update progress
            if progress_callback:
                progress_callback(current_step, total_steps)
            current_step += 1

            # Download/get cached audio
            if not item.recording_hash_prefix:
                raise ValueError(f"Item {item.id} has no recording")

            audio_path = self.asset_cache.download_audio(item.recording_hash_prefix)
            if not audio_path:
                raise FileNotFoundError(
                    f"Could not get audio for recording {item.recording_hash_prefix}"
                )

            # Load audio
            song_audio = self._load_audio(audio_path)

            # Calculate gap before this song
            gap_ms = 0
            if i > 0:  # No gap before first song
                gap_ms = self._calculate_gap_ms(item, item.tempo_bpm)

            # Record segment info
            segment_info = AudioSegmentInfo(
                item=item,
                audio_path=audio_path,
                start_time_seconds=current_time_ms / 1000.0 + (gap_ms / 1000.0),
                duration_seconds=len(song_audio) / 1000.0,
                gap_before_seconds=gap_ms / 1000.0,
            )
            segments.append(segment_info)

            # Add gap silence if needed
            if gap_ms > 0 and combined_audio is not None:
                silence = AudioSegment.silent(duration=gap_ms)
                combined_audio += silence
                current_time_ms += gap_ms

            # Normalize if requested
            if normalize:
                song_audio = self._normalize_loudness(song_audio)

            # Append to combined audio
            if combined_audio is None:
                combined_audio = song_audio
            else:
                combined_audio += song_audio

            current_time_ms += len(song_audio)

            # Update progress
            if progress_callback:
                progress_callback(current_step, total_steps)
            current_step += 1

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Export combined audio
        if combined_audio:
            combined_audio.export(
                str(output_path),
                format="mp3",
                bitrate="320k",
            )

        # Final progress update
        if progress_callback:
            progress_callback(total_steps, total_steps)

        return ExportResult(
            output_path=output_path,
            total_duration_seconds=current_time_ms / 1000.0,
            segments=segments,
            sample_rate=44100,
            channels=2,
        )

    def preview_transition(
        self,
        from_item: SongsetItem,
        to_item: SongsetItem,
        preview_duration_seconds: float = 15.0,
    ) -> Optional[Path]:
        """Generate a preview of a transition between two songs.

        Args:
            from_item: First song item
            to_item: Second song item
            preview_duration_seconds: Duration of the preview clip

        Returns:
            Path to preview audio file or None if generation failed
        """
        if not from_item.recording_hash_prefix or not to_item.recording_hash_prefix:
            return None

        try:
            # Get audio files
            from_path = self.asset_cache.download_audio(from_item.recording_hash_prefix)
            to_path = self.asset_cache.download_audio(to_item.recording_hash_prefix)

            if not from_path or not to_path:
                return None

            # Load audio
            from_audio = self._load_audio(from_path)
            to_audio = self._load_audio(to_path)

            # Extract end of first song
            from_duration_ms = int(preview_duration_seconds * 1000 / 2)
            from_clip = from_audio[-from_duration_ms:] if len(from_audio) > from_duration_ms else from_audio

            # Extract start of second song
            to_duration_ms = int(preview_duration_seconds * 1000 / 2)
            to_clip = to_audio[:to_duration_ms]

            # Calculate and add gap
            gap_ms = self._calculate_gap_ms(to_item, to_item.tempo_bpm)
            if gap_ms > 0:
                silence = AudioSegment.silent(duration=gap_ms)
                combined = from_clip + silence + to_clip
            else:
                combined = from_clip + to_clip

            # Create temp file
            temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            temp_path = Path(temp_file.name)
            temp_file.close()

            # Export
            combined.export(str(temp_path), format="mp3", bitrate="192k")

            return temp_path

        except Exception:
            return None

    def get_audio_info(self, hash_prefix: str) -> Optional[dict]:
        """Get information about an audio file.

        Args:
            hash_prefix: Recording hash prefix

        Returns:
            Dictionary with audio info or None
        """
        audio_path = self.asset_cache.download_audio(hash_prefix)
        if not audio_path:
            return None

        try:
            audio = self._load_audio(audio_path)
            return {
                "duration_seconds": len(audio) / 1000.0,
                "duration_ms": len(audio),
                "channels": audio.channels,
                "sample_rate": audio.frame_rate,
                "bitrate": audio.frame_width * 8 * audio.frame_rate // 1000,  # Approximate
                "file_size_bytes": audio_path.stat().st_size,
            }
        except Exception:
            return None
