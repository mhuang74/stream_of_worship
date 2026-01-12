"""
Transition configuration data model.

Holds all configurable parameters for generating transitions.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime

from .transition_types import TransitionType
from .song import Song, Section


@dataclass
class TransitionConfig:
    """
    Configuration for generating a transition between two sections.

    All parameters are configurable in real-time by the user.

    Attributes:
        transition_type: Type of transition (OVERLAP, SHORT_GAP, NO_BREAK)
        transition_window: Total transition zone duration in seconds
        overlap_window: Overlap duration in seconds (Overlap type only)
        gap_window: Silence gap duration in seconds (Short Gap type only)
        stems_to_fade: List of stems to fade (vocals, drums, bass, other)
        fade_window_pct: Fade duration as % of transition_window (0-100)
        song_a: Source Song A
        section_a: Source Section from Song A
        song_b: Source Song B
        section_b: Source Section from Song B
        compatibility_score: Overall compatibility score (0-100)
        tempo_score: Tempo compatibility score
        key_score: Key compatibility score
        energy_score: Energy compatibility score
        embeddings_score: Embeddings similarity score
    """

    # Core transition parameters
    transition_type: TransitionType = TransitionType.SHORT_GAP
    transition_window: float = 8.0
    overlap_window: float = 4.0
    gap_window: float = 2.0
    stems_to_fade: List[str] = field(default_factory=lambda: ['vocals', 'drums'])
    fade_window_pct: int = 80

    # Source selections (optional until user selects)
    song_a: Optional[Song] = None
    section_a: Optional[Section] = None
    song_b: Optional[Song] = None
    section_b: Optional[Section] = None

    # Compatibility scores (calculated when both sections selected)
    compatibility_score: float = 0.0
    tempo_score: float = 0.0
    key_score: float = 0.0
    energy_score: float = 0.0
    embeddings_score: float = 0.0

    # Parameter definitions for validation and UI
    PARAMETER_SPECS = {
        'transition_window': {
            'min': 2.0,
            'max': 16.0,
            'step': 0.5,
            'unit': 's',
            'label': 'Transition Window',
            'description': 'Total duration of transition zone'
        },
        'overlap_window': {
            'min': 0.5,
            'max': 8.0,
            'step': 0.5,
            'unit': 's',
            'label': 'Overlap Window',
            'description': 'Duration of overlap (Overlap type only)',
            'enabled_for': [TransitionType.OVERLAP]
        },
        'gap_window': {
            'min': 0.5,
            'max': 8.0,
            'step': 0.5,
            'unit': 's',
            'label': 'Gap Window',
            'description': 'Duration of silence gap (Short Gap type only)',
            'enabled_for': [TransitionType.SHORT_GAP]
        },
        'fade_window_pct': {
            'min': 0,
            'max': 100,
            'step': 5,
            'unit': '%',
            'label': 'Fade Window %',
            'description': 'Fade duration as % of transition_window'
        }
    }

    AVAILABLE_STEMS = ['vocals', 'drums', 'bass', 'other']

    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate all parameters are within acceptable ranges.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate transition_window
        spec = self.PARAMETER_SPECS['transition_window']
        if not (spec['min'] <= self.transition_window <= spec['max']):
            return False, f"transition_window must be between {spec['min']}-{spec['max']}s"

        # Validate overlap_window (if Overlap type)
        if self.transition_type == TransitionType.OVERLAP:
            spec = self.PARAMETER_SPECS['overlap_window']
            if not (spec['min'] <= self.overlap_window <= spec['max']):
                return False, f"overlap_window must be between {spec['min']}-{spec['max']}s"
            if self.overlap_window > self.transition_window:
                return False, "overlap_window must be <= transition_window"

        # Validate gap_window (if Short Gap type)
        if self.transition_type == TransitionType.SHORT_GAP:
            spec = self.PARAMETER_SPECS['gap_window']
            if not (spec['min'] <= self.gap_window <= spec['max']):
                return False, f"gap_window must be between {spec['min']}-{spec['max']}s"

        # Validate fade_window_pct
        spec = self.PARAMETER_SPECS['fade_window_pct']
        if not (spec['min'] <= self.fade_window_pct <= spec['max']):
            return False, f"fade_window_pct must be between {spec['min']}-{spec['max']}%"

        # Validate stems_to_fade
        if not self.stems_to_fade:
            return False, "At least one stem must be selected"
        for stem in self.stems_to_fade:
            if stem not in self.AVAILABLE_STEMS:
                return False, f"Invalid stem: {stem}"

        # Validate source selections
        if self.song_a is None or self.section_a is None:
            return False, "Song A and Section A must be selected"
        if self.song_b is None or self.section_b is None:
            return False, "Song B and Section B must be selected"

        # Validate section durations
        if self.section_a.duration < self.transition_window:
            return False, f"Section A duration ({self.section_a.duration:.1f}s) must be >= transition_window ({self.transition_window}s)"
        if self.section_b.duration < self.transition_window:
            return False, f"Section B duration ({self.section_b.duration:.1f}s) must be >= transition_window ({self.transition_window}s)"

        return True, None

    def update_compatibility_scores(self):
        """Calculate and update compatibility scores between selected sections."""
        if self.section_a and self.section_b:
            scores = self.section_a.calculate_compatibility(self.section_b)
            self.compatibility_score = scores['overall_score']
            self.tempo_score = scores['tempo_score']
            self.key_score = scores['key_score']
            self.energy_score = scores['energy_score']
            self.embeddings_score = scores['embeddings_score']

    def get_active_parameters(self) -> List[str]:
        """
        Get list of active parameters based on transition type.

        Returns:
            List of parameter names that apply to current transition type
        """
        active = ['transition_window', 'stems_to_fade', 'fade_window_pct']

        if self.transition_type == TransitionType.OVERLAP:
            active.insert(1, 'overlap_window')
        elif self.transition_type == TransitionType.SHORT_GAP:
            active.insert(1, 'gap_window')

        return active

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize configuration to dictionary for JSON export.

        Returns:
            Dictionary representation of configuration
        """
        return {
            'version': '1.0',
            'generated_at': datetime.now().isoformat(),
            'transition_type': self.transition_type.value,
            'parameters': {
                'transition_window': self.transition_window,
                'overlap_window': self.overlap_window,
                'gap_window': self.gap_window,
                'stems_to_fade': self.stems_to_fade,
                'fade_window_pct': self.fade_window_pct
            },
            'song_a': {
                'filename': self.song_a.filename if self.song_a else None,
                'key': self.song_a.key if self.song_a else None,
                'tempo': self.song_a.tempo if self.song_a else None,
                'section': {
                    'index': self.section_a.index if self.section_a else None,
                    'label': self.section_a.label if self.section_a else None,
                    'start': self.section_a.start if self.section_a else None,
                    'end': self.section_a.end if self.section_a else None,
                    'duration': self.section_a.duration if self.section_a else None
                } if self.section_a else None
            },
            'song_b': {
                'filename': self.song_b.filename if self.song_b else None,
                'key': self.song_b.key if self.song_b else None,
                'tempo': self.song_b.tempo if self.song_b else None,
                'section': {
                    'index': self.section_b.index if self.section_b else None,
                    'label': self.section_b.label if self.section_b else None,
                    'start': self.section_b.start if self.section_b else None,
                    'end': self.section_b.end if self.section_b else None,
                    'duration': self.section_b.duration if self.section_b else None
                } if self.section_b else None
            },
            'compatibility': {
                'overall_score': self.compatibility_score,
                'tempo_score': self.tempo_score,
                'key_score': self.key_score,
                'energy_score': self.energy_score,
                'embeddings_score': self.embeddings_score
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TransitionConfig':
        """
        Deserialize configuration from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            TransitionConfig instance

        Note: This creates a config without Song/Section objects,
              only storing metadata. Full reconstruction would require
              access to the metadata loader.
        """
        transition_type = TransitionType.from_string(data['transition_type'])
        params = data['parameters']

        config = cls(
            transition_type=transition_type,
            transition_window=params['transition_window'],
            overlap_window=params.get('overlap_window', 4.0),
            gap_window=params.get('gap_window', 2.0),
            stems_to_fade=params['stems_to_fade'],
            fade_window_pct=params['fade_window_pct']
        )

        # Set compatibility scores
        compat = data.get('compatibility', {})
        config.compatibility_score = compat.get('overall_score', 0.0)
        config.tempo_score = compat.get('tempo_score', 0.0)
        config.key_score = compat.get('key_score', 0.0)
        config.energy_score = compat.get('energy_score', 0.0)
        config.embeddings_score = compat.get('embeddings_score', 0.0)

        return config

    def reset_to_defaults(self):
        """Reset parameters to default values based on transition type."""
        if self.transition_type == TransitionType.OVERLAP:
            self.transition_window = 8.0
            self.overlap_window = 4.0
            self.stems_to_fade = ['vocals', 'drums']
            self.fade_window_pct = 80
        elif self.transition_type == TransitionType.SHORT_GAP:
            self.transition_window = 8.0
            self.gap_window = 2.0
            self.stems_to_fade = ['vocals', 'drums']
            self.fade_window_pct = 80
        elif self.transition_type == TransitionType.NO_BREAK:
            self.transition_window = 8.0
            self.stems_to_fade = ['vocals', 'drums', 'bass', 'other']
            self.fade_window_pct = 100
