"""
Transition generator implementing PDF-based algorithms.

Implements 3 transition types from the Worship Transitions handbook:
1. Overlap (Intro Overlap)
2. Short Gap
3. No Break
"""

import numpy as np
from typing import Tuple, Dict

from ..models import TransitionConfig, TransitionType
from .stem_loader import StemLoader


class TransitionGenerator:
    """
    Generates worship song transitions using stem manipulation and mixing.

    Implements the 3 PDF-based transition types with configurable parameters.
    """

    def __init__(self, stem_loader: StemLoader, sample_rate: int = 44100):
        """
        Initialize transition generator.

        Args:
            stem_loader: StemLoader instance for loading audio stems
            sample_rate: Audio sample rate (default: 44100 Hz)
        """
        self.stem_loader = stem_loader
        self.sample_rate = sample_rate

    def generate(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate transition audio based on configuration.

        Args:
            config: TransitionConfig with all parameters

        Returns:
            Tuple of (audio_array, metadata):
                - audio_array: Stereo audio (2, num_samples)
                - metadata: Dictionary with generation info

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate configuration
        is_valid, error_msg = config.validate()
        if not is_valid:
            raise ValueError(f"Invalid configuration: {error_msg}")

        # Route to appropriate generator
        if config.transition_type == TransitionType.OVERLAP:
            return self._generate_overlap(config)
        elif config.transition_type == TransitionType.SHORT_GAP:
            return self._generate_short_gap(config)
        elif config.transition_type == TransitionType.NO_BREAK:
            return self._generate_no_break(config)
        else:
            raise ValueError(f"Unknown transition type: {config.transition_type}")

    def _generate_overlap(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate Overlap (Intro Overlap) transition.

        Algorithm (updated to include full sections):
        1. Load FULL sections for Song A and Song B
        2. Apply fade-out to selected stems in Song A over last fade_window_pct of transition_window
        3. Apply equal-power crossfade during overlap_window region:
           - Song A: Fade OUT selected stems using sqrt(1-t) curve
           - Song B: Fade IN selected stems using sqrt(t) curve
        4. Concatenate: [Full A with transitions] + [overlap_mixed] + [Full B with transitions]
        """
        # Convert beats to seconds
        tw = config.get_transition_window_seconds()
        ow = config.get_overlap_window_seconds()
        fade_pct = config.fade_window_pct / 100.0

        # Load FULL section stems for Song A
        stems_a = self.stem_loader.load_section_stems(
            config.song_a.filename,
            config.section_a
        )

        # Load FULL section stems for Song B
        stems_b = self.stem_loader.load_section_stems(
            config.song_b.filename,
            config.section_b
        )

        # Calculate samples for transition window and overlap
        transition_samples = int(tw * self.sample_rate)
        fade_duration = tw * fade_pct
        fade_samples = int(fade_duration * self.sample_rate)
        overlap_samples = int(ow * self.sample_rate)

        # Get section lengths
        section_a_length = next(iter(stems_a.values())).shape[1]
        section_b_length = next(iter(stems_b.values())).shape[1]

        # Calculate transition start point in Song A (last transition_window of section)
        transition_start_a = max(0, section_a_length - transition_samples)

        # Apply fade-out to Song A (before overlap region, within transition window)
        fade_start_a = max(0, section_a_length - fade_samples - overlap_samples)
        fade_end_a = section_a_length - overlap_samples

        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                # Get actual slice length and create matching fade curve
                actual_slice = stems_a[stem_name][:, fade_start_a:fade_end_a]
                fade_out_curve = self._create_fade_out_curve(actual_slice.shape[1])
                stems_a[stem_name][:, fade_start_a:fade_end_a] = actual_slice * fade_out_curve

        # Apply equal-power crossfade during overlap region
        a_overlap_start = max(0, section_a_length - overlap_samples)
        b_overlap_end = min(overlap_samples, section_b_length)

        # Apply crossfade to stems in overlap region
        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                # Fade out Song A stems in overlap region
                actual_slice = stems_a[stem_name][:, a_overlap_start:]
                equal_power_out = self._create_equal_power_fade_out(actual_slice.shape[1])
                stems_a[stem_name][:, a_overlap_start:] = actual_slice * equal_power_out

            if stem_name in stems_b:
                # Fade in Song B stems in overlap region
                actual_slice = stems_b[stem_name][:, :b_overlap_end]
                equal_power_in = self._create_equal_power_fade_in(actual_slice.shape[1])
                stems_b[stem_name][:, :b_overlap_end] = actual_slice * equal_power_in

        # Mix stems to full audio
        audio_a = self._mix_stems(stems_a)
        audio_b = self._mix_stems(stems_b)

        # Calculate overlap region indices for final audio
        a_overlap_start_final = max(0, audio_a.shape[1] - overlap_samples)
        b_overlap_end_final = min(overlap_samples, audio_b.shape[1])

        # Mix overlap region
        overlap_audio = (
            audio_a[:, a_overlap_start_final:] +
            audio_b[:, :b_overlap_end_final]
        )
        overlap_audio = np.clip(overlap_audio, -1.0, 1.0)

        # Concatenate: [A_before_overlap] + [overlap_mixed] + [B_after_overlap]
        result = np.concatenate([
            audio_a[:, :a_overlap_start_final],
            overlap_audio,
            audio_b[:, b_overlap_end_final:]
        ], axis=1)

        metadata = {
            'transition_type': 'overlap',
            'transition_window_beats': config.transition_window,
            'transition_window_seconds': tw,
            'overlap_window_beats': config.overlap_window,
            'overlap_window_seconds': ow,
            'fade_window_pct': config.fade_window_pct,
            'stems_faded': config.stems_to_fade,
            'duration': result.shape[1] / self.sample_rate,
            'sample_rate': self.sample_rate
        }

        return result, metadata

    def _generate_short_gap(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate Short Gap transition.

        Algorithm (updated to include full sections):
        1. Load FULL sections for Song A and Song B
        2. Fade OUT selected stems in Song A over last fade_window_pct of transition_window
        3. Create gap_window (in beats/seconds) of silence
        4. Fade IN selected stems in Song B over first fade_window_pct of transition_window
        5. Concatenate: [Full A with fade_out] + [silence] + [Full B with fade_in]
        """
        # Convert beats to seconds
        tw = config.get_transition_window_seconds()
        gw = config.get_gap_window_seconds()
        fade_pct = config.fade_window_pct / 100.0

        # Load FULL section stems
        stems_a = self.stem_loader.load_section_stems(
            config.song_a.filename,
            config.section_a
        )
        stems_b = self.stem_loader.load_section_stems(
            config.song_b.filename,
            config.section_b
        )

        # Get section lengths
        section_a_length = next(iter(stems_a.values())).shape[1]
        section_b_length = next(iter(stems_b.values())).shape[1]

        # Calculate fade duration and samples
        fade_duration = tw * fade_pct
        fade_samples = int(fade_duration * self.sample_rate)

        # Apply fade-out to Song A (last fade_duration seconds)
        fade_start_a = max(0, section_a_length - fade_samples)

        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                actual_slice = stems_a[stem_name][:, fade_start_a:]
                fade_out_curve = self._create_fade_out_curve(actual_slice.shape[1])
                stems_a[stem_name][:, fade_start_a:] = actual_slice * fade_out_curve

        # Apply fade-in to Song B (first fade_duration seconds)
        fade_end_b = min(fade_samples, section_b_length)

        for stem_name in config.stems_to_fade:
            if stem_name in stems_b:
                actual_slice = stems_b[stem_name][:, :fade_end_b]
                fade_in_curve = self._create_fade_in_curve(actual_slice.shape[1])
                stems_b[stem_name][:, :fade_end_b] = actual_slice * fade_in_curve

        # Mix stems
        audio_a = self._mix_stems(stems_a)
        audio_b = self._mix_stems(stems_b)

        # Create silence gap
        gap_samples = int(gw * self.sample_rate)
        silence = np.zeros((2, gap_samples))

        # Concatenate all parts
        result = np.concatenate([audio_a, silence, audio_b], axis=1)

        metadata = {
            'transition_type': 'short_gap',
            'transition_window_beats': config.transition_window,
            'transition_window_seconds': tw,
            'gap_window_beats': config.gap_window,
            'gap_window_seconds': gw,
            'fade_window_pct': config.fade_window_pct,
            'stems_faded': config.stems_to_fade,
            'duration': result.shape[1] / self.sample_rate,
            'sample_rate': self.sample_rate
        }

        return result, metadata

    def _generate_no_break(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate No Break transition.

        Algorithm (updated to include full sections):
        1. Load FULL sections for Song A and Song B
        2. Calculate fade duration based on fade_window_pct of transition_window
        3. Apply equal-power crossfade at junction: end of A and start of B
        4. Concatenate: [Full A with fade] + [crossfade] + [Full B with fade]
        """
        # Convert beats to seconds
        tw = config.get_transition_window_seconds()
        fade_pct = config.fade_window_pct / 100.0

        # Load FULL section stems
        stems_a = self.stem_loader.load_section_stems(
            config.song_a.filename,
            config.section_a
        )
        stems_b = self.stem_loader.load_section_stems(
            config.song_b.filename,
            config.section_b
        )

        # Get section lengths
        section_a_length = next(iter(stems_a.values())).shape[1]
        section_b_length = next(iter(stems_b.values())).shape[1]

        # Calculate crossfade duration and samples
        crossfade_duration = tw * fade_pct
        crossfade_samples = int(crossfade_duration * self.sample_rate)

        # Apply crossfade to selected stems at end of A and start of B
        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                fade_start = max(0, section_a_length - crossfade_samples)
                actual_slice = stems_a[stem_name][:, fade_start:]
                fade_out_curve = self._create_equal_power_fade_out(actual_slice.shape[1])
                stems_a[stem_name][:, fade_start:] = actual_slice * fade_out_curve

            if stem_name in stems_b:
                fade_end = min(crossfade_samples, section_b_length)
                actual_slice = stems_b[stem_name][:, :fade_end]
                fade_in_curve = self._create_equal_power_fade_in(actual_slice.shape[1])
                stems_b[stem_name][:, :fade_end] = actual_slice * fade_in_curve

        # Mix stems
        audio_a = self._mix_stems(stems_a)
        audio_b = self._mix_stems(stems_b)

        # Mix crossfade region at junction
        crossfade_start_a = max(0, section_a_length - crossfade_samples)
        crossfade_end_b = min(crossfade_samples, section_b_length)

        crossfade_audio = (
            audio_a[:, crossfade_start_a:] +
            audio_b[:, :crossfade_end_b]
        )
        crossfade_audio = np.clip(crossfade_audio, -1.0, 1.0)

        # Concatenate: [A_before_crossfade] + [crossfade] + [B_after_crossfade]
        result = np.concatenate([
            audio_a[:, :crossfade_start_a],
            crossfade_audio,
            audio_b[:, crossfade_end_b:]
        ], axis=1)

        metadata = {
            'transition_type': 'no_break',
            'transition_window_beats': config.transition_window,
            'transition_window_seconds': tw,
            'crossfade_duration_seconds': crossfade_duration,
            'fade_window_pct': config.fade_window_pct,
            'stems_faded': config.stems_to_fade,
            'duration': result.shape[1] / self.sample_rate,
            'sample_rate': self.sample_rate
        }

        return result, metadata

    def _mix_stems(self, stems: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Mix all stems into a single stereo audio array.

        Args:
            stems: Dictionary of stem arrays

        Returns:
            Mixed stereo audio array (2, num_samples)
        """
        # Find maximum length
        max_length = max(stem.shape[1] for stem in stems.values())

        # Initialize mixed audio
        mixed = np.zeros((2, max_length))

        # Add all stems
        for stem_audio in stems.values():
            mixed[:, :stem_audio.shape[1]] += stem_audio

        # Clip to prevent distortion
        mixed = np.clip(mixed, -1.0, 1.0)

        return mixed

    def _create_fade_out_curve(self, num_samples: int) -> np.ndarray:
        """
        Create linear fade-out curve.

        Args:
            num_samples: Number of samples

        Returns:
            Fade curve array (1, num_samples)
        """
        t = np.linspace(1.0, 0.0, num_samples)
        return t.reshape(1, -1)

    def _create_fade_in_curve(self, num_samples: int) -> np.ndarray:
        """
        Create linear fade-in curve.

        Args:
            num_samples: Number of samples

        Returns:
            Fade curve array (1, num_samples)
        """
        t = np.linspace(0.0, 1.0, num_samples)
        return t.reshape(1, -1)

    def _create_equal_power_fade_out(self, num_samples: int) -> np.ndarray:
        """
        Create equal-power fade-out curve.

        Uses sqrt(1 - t) for energy preservation.

        Args:
            num_samples: Number of samples

        Returns:
            Fade curve array (1, num_samples)
        """
        t = np.linspace(0.0, 1.0, num_samples)
        curve = np.sqrt(1.0 - t)
        return curve.reshape(1, -1)

    def _create_equal_power_fade_in(self, num_samples: int) -> np.ndarray:
        """
        Create equal-power fade-in curve.

        Uses sqrt(t) for energy preservation.

        Args:
            num_samples: Number of samples

        Returns:
            Fade curve array (1, num_samples)
        """
        t = np.linspace(0.0, 1.0, num_samples)
        curve = np.sqrt(t)
        return curve.reshape(1, -1)
