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

        Algorithm:
        1. Extract last transition_window seconds from Song A section
        2. Extract first transition_window seconds from Song B section
        3. Fade OUT selected stems in Song A over last fade_window_pct
        4. Keep all Song B stems at full volume
        5. Mix overlap_window region
        6. Concatenate: [A_pre] + [overlap_mixed] + [B_post]
        """
        tw = config.transition_window
        ow = config.overlap_window
        fade_pct = config.fade_window_pct / 100.0

        # Load stems for last tw seconds of Song A
        stems_a = self.stem_loader.load_partial_section_stems(
            config.song_a.filename,
            config.section_a,
            start_offset=max(0, config.section_a.duration - tw),
            duration=tw
        )

        # Load stems for first tw seconds of Song B
        stems_b = self.stem_loader.load_partial_section_stems(
            config.song_b.filename,
            config.section_b,
            start_offset=0,
            duration=tw
        )

        # Mix stems to full audio
        audio_a = self._mix_stems(stems_a)
        audio_b = self._mix_stems(stems_b)

        # Calculate fade duration and samples
        fade_duration = tw * fade_pct
        fade_samples = int(fade_duration * self.sample_rate)
        overlap_samples = int(ow * self.sample_rate)

        # Apply fade to selected stems in Song A (last fade_samples)
        audio_a_faded = audio_a.copy()
        fade_start_sample = max(0, audio_a.shape[1] - fade_samples)
        fade_curve = self._create_fade_out_curve(fade_samples)

        # Apply fade to selected stems
        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                stem_fade_start = max(0, stems_a[stem_name].shape[1] - fade_samples)
                stems_a[stem_name][:, stem_fade_start:] *= fade_curve

        # Remix with faded stems
        audio_a_faded = self._mix_stems(stems_a)

        # Calculate overlap region indices
        a_overlap_start = max(0, audio_a_faded.shape[1] - overlap_samples)
        b_overlap_end = min(overlap_samples, audio_b.shape[1])

        # Mix overlap region
        overlap_audio = (
            audio_a_faded[:, a_overlap_start:] +
            audio_b[:, :b_overlap_end]
        )
        overlap_audio = np.clip(overlap_audio, -1.0, 1.0)

        # Concatenate: [A_before_overlap] + [overlap] + [B_after_overlap]
        result = np.concatenate([
            audio_a_faded[:, :a_overlap_start],
            overlap_audio,
            audio_b[:, b_overlap_end:]
        ], axis=1)

        metadata = {
            'transition_type': 'overlap',
            'transition_window': tw,
            'overlap_window': ow,
            'fade_window_pct': config.fade_window_pct,
            'stems_faded': config.stems_to_fade,
            'duration': result.shape[1] / self.sample_rate,
            'sample_rate': self.sample_rate
        }

        return result, metadata

    def _generate_short_gap(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate Short Gap transition.

        Algorithm:
        1. Extract last transition_window from Song A section
        2. Extract first transition_window from Song B section
        3. Fade OUT selected stems in Song A over last fade_window_pct
        4. Create gap_window seconds of silence
        5. Fade IN selected stems in Song B over first fade_window_pct
        6. Concatenate: [A_pre] + [A_fade_out] + [silence] + [B_fade_in] + [B_post]
        """
        tw = config.transition_window
        gw = config.gap_window
        fade_pct = config.fade_window_pct / 100.0

        # Load stems
        stems_a = self.stem_loader.load_partial_section_stems(
            config.song_a.filename,
            config.section_a,
            start_offset=max(0, config.section_a.duration - tw),
            duration=tw
        )
        stems_b = self.stem_loader.load_partial_section_stems(
            config.song_b.filename,
            config.section_b,
            start_offset=0,
            duration=tw
        )

        # Calculate fade samples
        fade_duration = tw * fade_pct
        fade_samples = int(fade_duration * self.sample_rate)

        # Apply fade-out to Song A
        fade_out_curve = self._create_fade_out_curve(fade_samples)
        fade_start_a = max(0, next(iter(stems_a.values())).shape[1] - fade_samples)

        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                stems_a[stem_name][:, fade_start_a:] *= fade_out_curve

        # Apply fade-in to Song B
        fade_in_curve = self._create_fade_in_curve(fade_samples)
        fade_end_b = min(fade_samples, next(iter(stems_b.values())).shape[1])

        for stem_name in config.stems_to_fade:
            if stem_name in stems_b:
                stems_b[stem_name][:, :fade_end_b] *= fade_in_curve[:fade_end_b]

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
            'transition_window': tw,
            'gap_window': gw,
            'fade_window_pct': config.fade_window_pct,
            'stems_faded': config.stems_to_fade,
            'duration': result.shape[1] / self.sample_rate,
            'sample_rate': self.sample_rate
        }

        return result, metadata

    def _generate_no_break(self, config: TransitionConfig) -> Tuple[np.ndarray, dict]:
        """
        Generate No Break transition.

        Algorithm:
        1. Extract last transition_window from Song A section
        2. Extract first transition_window from Song B section
        3. Apply equal-power crossfade to selected stems
        4. Mix crossfade region
        5. Concatenate: [A_pre] + [crossfade_mixed] + [B_post]
        """
        tw = config.transition_window
        fade_pct = config.fade_window_pct / 100.0

        # Load stems
        stems_a = self.stem_loader.load_partial_section_stems(
            config.song_a.filename,
            config.section_a,
            start_offset=max(0, config.section_a.duration - tw),
            duration=tw
        )
        stems_b = self.stem_loader.load_partial_section_stems(
            config.song_b.filename,
            config.section_b,
            start_offset=0,
            duration=tw
        )

        # Calculate crossfade samples
        crossfade_duration = tw * fade_pct
        crossfade_samples = int(crossfade_duration * self.sample_rate)

        # Create equal-power crossfade curves
        fade_out_curve = self._create_equal_power_fade_out(crossfade_samples)
        fade_in_curve = self._create_equal_power_fade_in(crossfade_samples)

        # Apply crossfade to selected stems
        for stem_name in config.stems_to_fade:
            if stem_name in stems_a:
                fade_start = max(0, stems_a[stem_name].shape[1] - crossfade_samples)
                actual_fade_samples = stems_a[stem_name].shape[1] - fade_start
                stems_a[stem_name][:, fade_start:] *= fade_out_curve[:actual_fade_samples]

            if stem_name in stems_b:
                fade_end = min(crossfade_samples, stems_b[stem_name].shape[1])
                stems_b[stem_name][:, :fade_end] *= fade_in_curve[:fade_end]

        # Mix stems
        audio_a = self._mix_stems(stems_a)
        audio_b = self._mix_stems(stems_b)

        # Mix crossfade region
        crossfade_start_a = max(0, audio_a.shape[1] - crossfade_samples)
        crossfade_end_b = min(crossfade_samples, audio_b.shape[1])

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
            'transition_window': tw,
            'crossfade_duration': crossfade_duration,
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
