#!/usr/bin/env python3
"""
Section-Level Transition Generator: Worship Music Transition System

Version: 2.1.0
Date: 2026-01-06
Purpose: Generate multi-variant section transitions with comprehensive metadata

This script generates two types of transitions for each viable section pair:
- Medium-Crossfade: Full sections with equal-power crossfade (8s)
- Medium-Silence: Full sections with tempo-based silence gap (configurable beats)

Outputs comprehensive metadata in v2.0 schema with review support.
"""

import warnings
warnings.filterwarnings('ignore')

# Audio processing
import librosa
import soundfile as sf

# Data and math
import numpy as np
import pandas as pd

# Utilities
from pathlib import Path
import json
from datetime import datetime
import argparse
import sys
import uuid

# Import from section analysis
from analyze_sections import (
    analyze_all_sections, DEFAULT_WEIGHTS, validate_weights, log,
    AUDIO_DIR, OUTPUT_DIR, CACHE_DIR
)

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG = {
    'output_dir': OUTPUT_DIR / 'section_transitions',
    'min_score': 60,           # Minimum section compatibility score
    'max_pairs': None,         # Limit number of pairs (None = all viable pairs)

    # Audio processing options
    'sample_rate': 44100,          # Audio sample rate
    'output_format': 'flac',       # Output format: 'flac' or 'wav'

    # Variant options (v2.0)
    'medium_crossfade_duration': 8.0,   # Fixed duration for crossfade variant
    'silence_beats': 4,                  # Number of beats for silence variant
    'silence_fade_duration': 1.0,        # Fade out/in duration for silence variant

    # Stem-based fade transition options (v2.1)
    'stem_fade_transition_beats': 8,     # Total beats per song for transition zone
    'stem_fade_duration_beats': 4,       # Beats for fade in/out within transition zone

    # Optional features
    'generate_waveforms': False,   # Create visualization plots
    'verbose': True                # Print detailed progress
}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_stems_for_section(song_path, section, stems_base_dir, sr=44100):
    """
    Load all 4 stems (bass, drums, other, vocals) for a specific section.

    Args:
        song_path: Path to original audio file (e.g., "do_it_again.mp3")
        section: Section dict with 'start', 'end' keys (in seconds)
        stems_base_dir: Base stems directory (e.g., poc_output_allinone/stems)
        sr: Target sample rate

    Returns:
        dict: {
            'bass': stereo_array,
            'drums': stereo_array,
            'other': stereo_array,
            'vocals': stereo_array
        }

    Raises:
        FileNotFoundError: If stems don't exist for this song
    """
    # Map song filename to stem directory
    song_stem = Path(song_path).stem  # e.g., "do_it_again.mp3" -> "do_it_again"
    stem_dir = stems_base_dir / song_stem

    # Check if stems exist
    required_stems = ['bass', 'drums', 'other', 'vocals']
    for stem_name in required_stems:
        stem_path = stem_dir / f"{stem_name}.wav"
        if not stem_path.exists():
            raise FileNotFoundError(
                f"Stem not found: {stem_path}\n"
                f"Run stem generation first: python poc/poc_analysis_allinone.py --generate-stems"
            )

    # Load each stem and extract section
    section_start = int(section['start'] * sr)
    section_end = int(section['end'] * sr)

    stems = {}
    for stem_name in required_stems:
        stem_path = stem_dir / f"{stem_name}.wav"

        # Load stem audio
        y, sr_loaded = librosa.load(str(stem_path), sr=sr, mono=False)

        # Ensure stereo
        if y.ndim == 1:
            y = np.stack([y, y])

        # Extract section
        stem_section = y[:, section_start:section_end]
        stems[stem_name] = stem_section

    return stems


def generate_transition_filename(song_a, song_b, label_a, label_b, duration):
    """
    Create descriptive filename for section transition audio.

    Format: transition_section_{song_a_base}_{label_a}_{song_b_base}_{label_b}_{duration}s.flac
    Example: transition_section_joy_chorus_heaven_chorus_8s.flac
    """
    # Remove extensions
    base_a = song_a.replace('.mp3', '').replace('.flac', '')
    base_b = song_b.replace('.mp3', '').replace('.flac', '')

    # Sanitize (remove special chars)
    base_a = base_a.replace(' ', '_')
    base_b = base_b.replace(' ', '_')

    return f"transition_section_{base_a}_{label_a}_{base_b}_{label_b}_{duration}s.{CONFIG['output_format']}"






# =============================================================================
# SECTION PAIR SELECTION
# =============================================================================

def select_transition_candidates(compatibility_df, min_score=None, max_pairs=None):
    """
    Filter compatible section pairs worthy of transition generation.

    Args:
        compatibility_df: DataFrame from analyze_sections
        min_score: Minimum overall_score (0-100) to consider
        max_pairs: Optional limit on number of pairs (None = all viable pairs)

    Returns:
        List of candidate pairs sorted by score (best first)
    """
    if min_score is None:
        min_score = CONFIG['min_score']
    if max_pairs is None:
        max_pairs = CONFIG['max_pairs']

    # Filter by threshold
    viable = compatibility_df[compatibility_df['overall_score'] >= min_score].copy()

    # Sort by overall score (descending)
    viable = viable.sort_values('overall_score', ascending=False)

    # Optionally limit
    if max_pairs:
        viable = viable.head(max_pairs)

    log(f"\nFiltered {len(viable)} viable section pairs (min_score >= {min_score}):")
    for idx, row in viable.head(10).iterrows():
        log(f"  {row['song_a']} [{row['section_a_label']}] → {row['song_b']} [{row['section_b_label']}]: "
            f"{row['overall_score']:.1f}/100 "
            f"(tempo: {row['tempo_score']:.1f}, key: {row['key_score']:.1f}, "
            f"embed: {row['embeddings_score']:.1f})")

    if len(viable) > 10:
        log(f"  ... and {len(viable) - 10} more pairs")

    return viable.to_dict('records')


# =============================================================================
# SECTION TRANSITION GENERATION
# =============================================================================



def generate_medium_transition(song_a_path, song_b_path, section_a, section_b, crossfade_duration=8.0):
    """
    Create medium transition: Full section A + crossfade + Full section B.

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section info dicts with 'start', 'end' keys
        crossfade_duration: Crossfade duration in seconds

    Returns:
        (transition_audio, sample_rate, actual_duration)
    """
    # Load stereo audio
    y_a, sr = librosa.load(str(song_a_path), sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(str(song_b_path), sr=CONFIG['sample_rate'], mono=False)

    # Ensure stereo
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Extract full sections
    section_a_start = int(section_a['start'] * sr)
    section_a_end = int(section_a['end'] * sr)
    section_b_start = int(section_b['start'] * sr)
    section_b_end = int(section_b['end'] * sr)

    section_a_audio = y_a[:, section_a_start:section_a_end]
    section_b_audio = y_b[:, section_b_start:section_b_end]

    # Create crossfade region
    crossfade_samples = int(crossfade_duration * sr)

    # Handle short sections
    if section_a_audio.shape[1] < crossfade_samples:
        crossfade_samples = section_a_audio.shape[1]
    if section_b_audio.shape[1] < crossfade_samples:
        crossfade_samples = section_b_audio.shape[1]

    # Split sections
    section_a_pre = section_a_audio[:, :-crossfade_samples]
    section_a_fade = section_a_audio[:, -crossfade_samples:]
    section_b_fade = section_b_audio[:, :crossfade_samples]
    section_b_post = section_b_audio[:, crossfade_samples:]

    # Create fade curves
    fade_curve = np.linspace(0, 1, crossfade_samples)
    fade_out = np.sqrt(1 - fade_curve)
    fade_in = np.sqrt(fade_curve)

    # Apply fades
    section_a_faded = section_a_fade * fade_out
    section_b_faded = section_b_fade * fade_in
    crossfade = section_a_faded + section_b_faded

    # Concatenate: pre + crossfade + post
    transition = np.concatenate([section_a_pre, crossfade, section_b_post], axis=1)

    actual_duration = transition.shape[1] / sr

    return transition, sr, actual_duration


def generate_medium_silence_transition(song_a_path, song_b_path, section_a, section_b,
                                       tempo_a, silence_beats=4, fade_duration=1.0):
    """
    Create medium transition with silence gap between sections.

    Algorithm:
    1. Load full sections A and B
    2. Calculate silence duration from tempo: (60.0 / tempo_a) * silence_beats
    3. Create fade-out at end of section A (fade_duration seconds)
    4. Create silence array
    5. Create fade-in at start of section B (fade_duration seconds)
    6. Concatenate: [section_a_pre] + [fade_out] + [silence] + [fade_in] + [section_b_post]

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section info dicts with 'start', 'end' keys
        tempo_a: Tempo of song A in BPM (for calculating silence duration)
        silence_beats: Number of beats for silence (default: 4)
        fade_duration: Duration of fade out/in in seconds (default: 1.0)

    Returns:
        (transition_audio, sample_rate, actual_duration, silence_duration)
    """
    # Load stereo audio
    y_a, sr = librosa.load(str(song_a_path), sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(str(song_b_path), sr=CONFIG['sample_rate'], mono=False)

    # Ensure stereo
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Extract full sections
    section_a_start = int(section_a['start'] * sr)
    section_a_end = int(section_a['end'] * sr)
    section_b_start = int(section_b['start'] * sr)
    section_b_end = int(section_b['end'] * sr)

    section_a_audio = y_a[:, section_a_start:section_a_end]
    section_b_audio = y_b[:, section_b_start:section_b_end]

    # Calculate silence duration from tempo
    silence_duration = (60.0 / tempo_a) * silence_beats
    silence_samples = int(silence_duration * sr)

    # Create silence array (stereo)
    silence = np.zeros((2, silence_samples), dtype=section_a_audio.dtype)

    # Prepare fade regions
    fade_samples = int(fade_duration * sr)

    # Split section A: pre-fade and fade-out region
    if section_a_audio.shape[1] < fade_samples:
        fade_samples_a = section_a_audio.shape[1]
    else:
        fade_samples_a = fade_samples

    section_a_pre = section_a_audio[:, :-fade_samples_a]
    section_a_fade = section_a_audio[:, -fade_samples_a:]

    # Split section B: fade-in region and post-fade
    if section_b_audio.shape[1] < fade_samples:
        fade_samples_b = section_b_audio.shape[1]
    else:
        fade_samples_b = fade_samples

    section_b_fade = section_b_audio[:, :fade_samples_b]
    section_b_post = section_b_audio[:, fade_samples_b:]

    # Create equal-power fade curves (using sqrt for energy preservation)
    fade_curve_out = np.linspace(1, 0, fade_samples_a)
    fade_curve_in = np.linspace(0, 1, fade_samples_b)

    fade_out_curve = np.sqrt(fade_curve_out)  # Equal-power fade out
    fade_in_curve = np.sqrt(fade_curve_in)     # Equal-power fade in

    # Apply fades
    section_a_faded = section_a_fade * fade_out_curve
    section_b_faded = section_b_fade * fade_in_curve

    # Concatenate all parts
    transition = np.concatenate([
        section_a_pre,
        section_a_faded,
        silence,
        section_b_faded,
        section_b_post
    ], axis=1)

    actual_duration = transition.shape[1] / sr

    return transition, sr, actual_duration, silence_duration


def generate_vocal_fade_transition(song_a_path, song_b_path, section_a, section_b,
                                   tempo_a, tempo_b, stems_base_dir):
    """
    Create vocal-fade transition: sections connected by vocal-only bridge with silence gap.

    Algorithm:
    1. Load all 4 stems for both sections
    2. Song A transition zone (last 8 beats):
       - Beats -8 to -4: Fade out bass, drums, other (vocals at full)
       - Beats -4 to 0: Vocals only
    3. 1 beat of silence (using Song A's tempo)
    4. Song B transition zone (first 8 beats):
       - Beats 0 to 4: Vocals only
       - Beats 4 to 8: Fade in bass, drums, other (vocals at full)
    5. Concatenate: [A_pre] + [A_fade] + [A_solo] + [silence] + [B_solo] + [B_fade] + [B_post]

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section dicts with 'start', 'end' keys
        tempo_a, tempo_b: Tempos in BPM
        stems_base_dir: Base directory for stems (e.g., OUTPUT_DIR / 'stems')

    Returns:
        (transition_audio, sample_rate, actual_duration, metadata_dict)
    """
    sr = CONFIG['sample_rate']

    # Calculate beat durations
    beat_duration_a = 60.0 / tempo_a  # seconds per beat
    beat_duration_b = 60.0 / tempo_b

    # Calculate transition zone durations
    transition_beats = CONFIG['stem_fade_transition_beats']  # 8 beats
    fade_beats = CONFIG['stem_fade_duration_beats']          # 4 beats

    transition_duration_a = beat_duration_a * transition_beats  # 8 beats in seconds
    fade_duration_a = beat_duration_a * fade_beats              # 4 beats in seconds

    transition_duration_b = beat_duration_b * transition_beats
    fade_duration_b = beat_duration_b * fade_beats

    # Convert to samples
    transition_samples_a = int(transition_duration_a * sr)
    fade_samples_a = int(fade_duration_a * sr)

    transition_samples_b = int(transition_duration_b * sr)
    fade_samples_b = int(fade_duration_b * sr)

    # === LOAD STEMS ===
    try:
        stems_a = load_stems_for_section(song_a_path, section_a, stems_base_dir, sr)
        stems_b = load_stems_for_section(song_b_path, section_b, stems_base_dir, sr)
    except FileNotFoundError as e:
        raise RuntimeError(f"Cannot generate vocal-fade transition: {e}")

    # === PROCESS SONG A ===
    # Split section A into: [pre-transition] + [transition zone]
    section_a_length = stems_a['vocals'].shape[1]

    if section_a_length < transition_samples_a:
        raise ValueError(
            f"Section A too short for transition ({section_a_length/sr:.1f}s < "
            f"{transition_duration_a:.1f}s). Need at least {transition_beats} beats."
        )

    # Split all stems at transition boundary
    split_point_a = section_a_length - transition_samples_a

    stems_a_pre = {name: stem[:, :split_point_a] for name, stem in stems_a.items()}
    stems_a_trans = {name: stem[:, split_point_a:] for name, stem in stems_a.items()}

    # Split transition zone into: [fade region] + [solo region]
    stems_a_fade = {name: stem[:, :fade_samples_a] for name, stem in stems_a_trans.items()}
    stems_a_solo = {name: stem[:, fade_samples_a:] for name, stem in stems_a_trans.items()}

    # Create fade-out curve (equal-power)
    fade_curve_out = np.linspace(1, 0, fade_samples_a)
    fade_out = np.sqrt(fade_curve_out)  # Equal-power fade

    # Apply fade to NON-VOCAL stems (bass, drums, other)
    stems_a_fade_processed = {
        'vocals': stems_a_fade['vocals'],  # Keep vocals at full volume
        'bass': stems_a_fade['bass'] * fade_out,
        'drums': stems_a_fade['drums'] * fade_out,
        'other': stems_a_fade['other'] * fade_out
    }

    # In solo region, only vocals (silence for others)
    stems_a_solo_processed = {
        'vocals': stems_a_solo['vocals'],
        'bass': np.zeros_like(stems_a_solo['bass']),
        'drums': np.zeros_like(stems_a_solo['drums']),
        'other': np.zeros_like(stems_a_solo['other'])
    }

    # === PROCESS SONG B ===
    section_b_length = stems_b['vocals'].shape[1]

    if section_b_length < transition_samples_b:
        raise ValueError(
            f"Section B too short for transition ({section_b_length/sr:.1f}s < "
            f"{transition_duration_b:.1f}s). Need at least {transition_beats} beats."
        )

    # Split section B into: [transition zone] + [post-transition]
    stems_b_trans = {name: stem[:, :transition_samples_b] for name, stem in stems_b.items()}
    stems_b_post = {name: stem[:, transition_samples_b:] for name, stem in stems_b.items()}

    # Split transition zone into: [solo region] + [fade region]
    stems_b_solo = {name: stem[:, :fade_samples_b] for name, stem in stems_b_trans.items()}
    stems_b_fade = {name: stem[:, fade_samples_b:] for name, stem in stems_b_trans.items()}

    # In solo region, only vocals
    stems_b_solo_processed = {
        'vocals': stems_b_solo['vocals'],
        'bass': np.zeros_like(stems_b_solo['bass']),
        'drums': np.zeros_like(stems_b_solo['drums']),
        'other': np.zeros_like(stems_b_solo['other'])
    }

    # Create fade-in curve (equal-power)
    fade_curve_in = np.linspace(0, 1, fade_samples_b)
    fade_in = np.sqrt(fade_curve_in)

    # Apply fade to NON-VOCAL stems
    stems_b_fade_processed = {
        'vocals': stems_b_fade['vocals'],  # Keep vocals at full volume
        'bass': stems_b_fade['bass'] * fade_in,
        'drums': stems_b_fade['drums'] * fade_in,
        'other': stems_b_fade['other'] * fade_in
    }

    # === CREATE SILENCE GAP (1 beat between songs) ===
    silence_beats = 1
    silence_duration = beat_duration_a * silence_beats  # Use Song A's tempo
    silence_samples = int(silence_duration * sr)
    silence_gap = np.zeros((2, silence_samples), dtype=stems_a['vocals'].dtype)

    # === MIX STEMS BACK TO STEREO ===
    def mix_stems(stem_dict):
        """Sum all stems to create stereo mix."""
        return (stem_dict['bass'] + stem_dict['drums'] +
                stem_dict['other'] + stem_dict['vocals'])

    # Mix each region
    audio_a_pre = mix_stems(stems_a_pre)
    audio_a_fade = mix_stems(stems_a_fade_processed)
    audio_a_solo = mix_stems(stems_a_solo_processed)

    audio_b_solo = mix_stems(stems_b_solo_processed)
    audio_b_fade = mix_stems(stems_b_fade_processed)
    audio_b_post = mix_stems(stems_b_post)

    # === CONCATENATE ALL PARTS ===
    transition = np.concatenate([
        audio_a_pre,      # Song A: pre-transition (full mix)
        audio_a_fade,     # Song A: fade out non-vocals (4 beats)
        audio_a_solo,     # Song A: vocals only (4 beats)
        silence_gap,      # 1 beat of silence (using Song A's tempo)
        audio_b_solo,     # Song B: vocals only (4 beats)
        audio_b_fade,     # Song B: fade in non-vocals (4 beats)
        audio_b_post      # Song B: post-transition (full mix)
    ], axis=1)

    actual_duration = transition.shape[1] / sr

    # Additional metadata
    metadata = {
        'transition_beats_a': transition_beats,
        'transition_beats_b': transition_beats,
        'fade_beats_a': fade_beats,
        'fade_beats_b': fade_beats,
        'silence_beats': silence_beats,
        'silence_duration': silence_duration,
        'transition_duration_a': transition_duration_a,
        'transition_duration_b': transition_duration_b,
        'featured_stem': 'vocals',
        'faded_stems': ['bass', 'drums', 'other']
    }

    return transition, sr, actual_duration, metadata


def generate_drum_fade_transition(song_a_path, song_b_path, section_a, section_b,
                                  tempo_a, tempo_b, stems_base_dir):
    """
    Create drum-fade transition: sections connected by drum-only bridge.

    Algorithm identical to vocal-fade, but keep drums instead of vocals.

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section dicts with 'start', 'end' keys
        tempo_a, tempo_b: Tempos in BPM
        stems_base_dir: Base directory for stems

    Returns:
        (transition_audio, sample_rate, actual_duration, metadata_dict)
    """
    sr = CONFIG['sample_rate']

    # Calculate beat durations
    beat_duration_a = 60.0 / tempo_a
    beat_duration_b = 60.0 / tempo_b

    transition_beats = CONFIG['stem_fade_transition_beats']
    fade_beats = CONFIG['stem_fade_duration_beats']

    transition_duration_a = beat_duration_a * transition_beats
    fade_duration_a = beat_duration_a * fade_beats

    transition_duration_b = beat_duration_b * transition_beats
    fade_duration_b = beat_duration_b * fade_beats

    transition_samples_a = int(transition_duration_a * sr)
    fade_samples_a = int(fade_duration_a * sr)

    transition_samples_b = int(transition_duration_b * sr)
    fade_samples_b = int(fade_duration_b * sr)

    # === LOAD STEMS ===
    try:
        stems_a = load_stems_for_section(song_a_path, section_a, stems_base_dir, sr)
        stems_b = load_stems_for_section(song_b_path, section_b, stems_base_dir, sr)
    except FileNotFoundError as e:
        raise RuntimeError(f"Cannot generate drum-fade transition: {e}")

    # === PROCESS SONG A ===
    section_a_length = stems_a['drums'].shape[1]

    if section_a_length < transition_samples_a:
        raise ValueError(
            f"Section A too short for transition ({section_a_length/sr:.1f}s < "
            f"{transition_duration_a:.1f}s). Need at least {transition_beats} beats."
        )

    split_point_a = section_a_length - transition_samples_a

    stems_a_pre = {name: stem[:, :split_point_a] for name, stem in stems_a.items()}
    stems_a_trans = {name: stem[:, split_point_a:] for name, stem in stems_a.items()}

    stems_a_fade = {name: stem[:, :fade_samples_a] for name, stem in stems_a_trans.items()}
    stems_a_solo = {name: stem[:, fade_samples_a:] for name, stem in stems_a_trans.items()}

    fade_curve_out = np.linspace(1, 0, fade_samples_a)
    fade_out = np.sqrt(fade_curve_out)

    # Apply fade to NON-DRUM stems (bass, vocals, other)
    stems_a_fade_processed = {
        'drums': stems_a_fade['drums'],  # Keep drums at full volume
        'bass': stems_a_fade['bass'] * fade_out,
        'vocals': stems_a_fade['vocals'] * fade_out,
        'other': stems_a_fade['other'] * fade_out
    }

    # In solo region, only drums
    stems_a_solo_processed = {
        'drums': stems_a_solo['drums'],
        'bass': np.zeros_like(stems_a_solo['bass']),
        'vocals': np.zeros_like(stems_a_solo['vocals']),
        'other': np.zeros_like(stems_a_solo['other'])
    }

    # === PROCESS SONG B ===
    section_b_length = stems_b['drums'].shape[1]

    if section_b_length < transition_samples_b:
        raise ValueError(
            f"Section B too short for transition ({section_b_length/sr:.1f}s < "
            f"{transition_duration_b:.1f}s). Need at least {transition_beats} beats."
        )

    stems_b_trans = {name: stem[:, :transition_samples_b] for name, stem in stems_b.items()}
    stems_b_post = {name: stem[:, transition_samples_b:] for name, stem in stems_b.items()}

    stems_b_solo = {name: stem[:, :fade_samples_b] for name, stem in stems_b_trans.items()}
    stems_b_fade = {name: stem[:, fade_samples_b:] for name, stem in stems_b_trans.items()}

    # Solo region: only drums
    stems_b_solo_processed = {
        'drums': stems_b_solo['drums'],
        'bass': np.zeros_like(stems_b_solo['bass']),
        'vocals': np.zeros_like(stems_b_solo['vocals']),
        'other': np.zeros_like(stems_b_solo['other'])
    }

    fade_curve_in = np.linspace(0, 1, fade_samples_b)
    fade_in = np.sqrt(fade_curve_in)

    # Apply fade to NON-DRUM stems
    stems_b_fade_processed = {
        'drums': stems_b_fade['drums'],  # Keep drums at full volume
        'bass': stems_b_fade['bass'] * fade_in,
        'vocals': stems_b_fade['vocals'] * fade_in,
        'other': stems_b_fade['other'] * fade_in
    }

    # === MIX STEMS ===
    def mix_stems(stem_dict):
        return (stem_dict['bass'] + stem_dict['drums'] +
                stem_dict['other'] + stem_dict['vocals'])

    audio_a_pre = mix_stems(stems_a_pre)
    audio_a_fade = mix_stems(stems_a_fade_processed)
    audio_a_solo = mix_stems(stems_a_solo_processed)

    audio_b_solo = mix_stems(stems_b_solo_processed)
    audio_b_fade = mix_stems(stems_b_fade_processed)
    audio_b_post = mix_stems(stems_b_post)

    # === CONCATENATE ===
    transition = np.concatenate([
        audio_a_pre,
        audio_a_fade,
        audio_a_solo,
        audio_b_solo,
        audio_b_fade,
        audio_b_post
    ], axis=1)

    actual_duration = transition.shape[1] / sr

    metadata = {
        'transition_beats_a': transition_beats,
        'transition_beats_b': transition_beats,
        'fade_beats_a': fade_beats,
        'fade_beats_b': fade_beats,
        'transition_duration_a': transition_duration_a,
        'transition_duration_b': transition_duration_b,
        'featured_stem': 'drums',
        'faded_stems': ['bass', 'vocals', 'other']
    }

    return transition, sr, actual_duration, metadata


# =============================================================================
# MAIN TRANSITION GENERATION
# =============================================================================

def load_all_song_sections(audio_dir, cache_dir):
    """
    Load all song sections from allin1 analysis.

    Optimization: First checks for existing poc_full_results.json output,
    then falls back to individual cache files or re-analysis.

    Returns:
        Dict mapping filename -> sections list
    """
    from poc_analysis_allinone import analyze_song_allinone, OUTPUT_DIR as ALLINONE_OUTPUT_DIR

    sections_map = {}

    # First, try loading from main output JSON if it exists
    json_path = ALLINONE_OUTPUT_DIR / 'poc_full_results.json'
    if json_path.exists():
        log(f"  Found existing allinone analysis: {json_path}")
        try:
            with open(json_path, 'r') as f:
                results = json.load(f)
            for result in results:
                sections_map[result['filename']] = result['sections']
            log(f"  ✓ Loaded sections for {len(sections_map)} songs from JSON output")
            return sections_map
        except Exception as e:
            log(f"  ⚠️  Could not load from JSON: {e}")
            log(f"  Falling back to individual cache/analysis...")

    # Fallback: load from individual cache files or re-analyze
    log(f"  No existing JSON found, loading from cache or running analysis...")
    audio_files = list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.flac"))

    for audio_file in audio_files:
        try:
            result = analyze_song_allinone(audio_file, cache_dir=cache_dir, use_cache=True)
            sections_map[audio_file.name] = result['sections']
        except Exception as e:
            log(f"WARNING: Could not load sections for {audio_file.name}: {e}")

    return sections_map


def generate_all_variants(pair, section_a, section_b, song_a_path, song_b_path,
                         sections_a, sections_b, section_a_idx, section_b_idx, audio_dir):
    """
    Generate all variants (medium-crossfade, medium-silence, vocal-fade, drum-fade) for a section pair.

    Args:
        pair: Compatibility info dict (must include 'tempo_a' for silence calculation)
        section_a, section_b: Section feature dicts
        song_a_path, song_b_path: Path objects to audio files
        sections_a, sections_b: Full section lists for each song
        section_a_idx, section_b_idx: Section indices
        audio_dir: Output audio directory

    Returns:
        List of variant metadata dicts
    """
    variants = []

    # Get base filenames for use across variants
    base_a = song_a_path.stem
    base_b = song_b_path.stem

    # === MEDIUM-CROSSFADE VARIANT (Full Sections with Crossfade) ===
    log(f"    Generating MEDIUM-CROSSFADE variant (full sections with crossfade)...")

    try:
        transition, sr, duration = generate_medium_transition(
            song_a_path, song_b_path, section_a, section_b,
            crossfade_duration=CONFIG['medium_crossfade_duration']
        )

        # Generate filename
        filename = f"transition_medium_crossfade_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{int(CONFIG['medium_crossfade_duration'])}s.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'medium-crossfade' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'medium-crossfade',
            'crossfade_duration': CONFIG['medium_crossfade_duration'],
            'total_duration': duration,
            'sections_included': {
                'song_a': [section_a['label']],
                'song_b': [section_b['label']]
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ MEDIUM-CROSSFADE: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except Exception as e:
        log(f"      ✗ Failed to generate MEDIUM-CROSSFADE variant: {e}")

    # === MEDIUM-SILENCE VARIANT (Full Sections with Silence Gap) ===
    log(f"    Generating MEDIUM-SILENCE variant ({CONFIG['silence_beats']}-beat silence)...")

    try:
        # Get tempo from pair info
        tempo_a = pair['tempo_a']

        transition, sr, duration, silence_duration = generate_medium_silence_transition(
            song_a_path, song_b_path, section_a, section_b,
            tempo_a=tempo_a,
            silence_beats=CONFIG['silence_beats'],
            fade_duration=CONFIG['silence_fade_duration']
        )

        # Generate filename (use actual beats value)
        filename = f"transition_medium_silence_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{CONFIG['silence_beats']}beats.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'medium-silence' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'medium-silence',
            'silence_beats': CONFIG['silence_beats'],
            'silence_duration': silence_duration,
            'fade_duration': CONFIG['silence_fade_duration'],
            'total_duration': duration,
            'tempo_used': tempo_a,
            'sections_included': {
                'song_a': [section_a['label']],
                'song_b': [section_b['label']]
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ MEDIUM-SILENCE: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s, silence: {silence_duration:.2f}s)")

    except Exception as e:
        log(f"      ✗ Failed to generate MEDIUM-SILENCE variant: {e}")

    # === VOCAL-FADE VARIANT (Full Sections with Vocal Bridge) ===
    log(f"    Generating VOCAL-FADE variant ({CONFIG['stem_fade_transition_beats']}-beat transition)...")

    try:
        # Get stems base directory
        stems_base_dir = OUTPUT_DIR / 'stems'

        # Get tempos
        tempo_a = pair['tempo_a']
        tempo_b = pair['tempo_b']

        transition, sr, duration, fade_metadata = generate_vocal_fade_transition(
            song_a_path, song_b_path, section_a, section_b,
            tempo_a, tempo_b, stems_base_dir
        )

        # Generate filename
        filename = f"transition_vocal_fade_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{CONFIG['stem_fade_transition_beats']}beats.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'vocal-fade' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'vocal-fade',
            'transition_beats': CONFIG['stem_fade_transition_beats'],
            'fade_beats': CONFIG['stem_fade_duration_beats'],
            'silence_beats': fade_metadata['silence_beats'],
            'silence_duration': fade_metadata['silence_duration'],
            'transition_duration_a': fade_metadata['transition_duration_a'],
            'transition_duration_b': fade_metadata['transition_duration_b'],
            'total_duration': duration,
            'featured_stem': 'vocals',
            'faded_stems': ['bass', 'drums', 'other'],
            'tempo_a_used': tempo_a,
            'tempo_b_used': tempo_b,
            'sections_included': {
                'song_a': [section_a['label']],
                'song_b': [section_b['label']]
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ VOCAL-FADE: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except FileNotFoundError as e:
        log(f"      ⚠️  Skipped VOCAL-FADE: Stems not found. Run with --generate-stems first.")
    except Exception as e:
        log(f"      ✗ Failed to generate VOCAL-FADE variant: {e}")

    # === DRUM-FADE VARIANT (Full Sections with Drum Bridge) ===
    log(f"    Generating DRUM-FADE variant ({CONFIG['stem_fade_transition_beats']}-beat transition)...")

    try:
        stems_base_dir = OUTPUT_DIR / 'stems'
        tempo_a = pair['tempo_a']
        tempo_b = pair['tempo_b']

        transition, sr, duration, fade_metadata = generate_drum_fade_transition(
            song_a_path, song_b_path, section_a, section_b,
            tempo_a, tempo_b, stems_base_dir
        )

        # Generate filename
        filename = f"transition_drum_fade_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{CONFIG['stem_fade_transition_beats']}beats.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'drum-fade' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'drum-fade',
            'transition_beats': CONFIG['stem_fade_transition_beats'],
            'fade_beats': CONFIG['stem_fade_duration_beats'],
            'transition_duration_a': fade_metadata['transition_duration_a'],
            'transition_duration_b': fade_metadata['transition_duration_b'],
            'total_duration': duration,
            'featured_stem': 'drums',
            'faded_stems': ['bass', 'vocals', 'other'],
            'tempo_a_used': tempo_a,
            'tempo_b_used': tempo_b,
            'sections_included': {
                'song_a': [section_a['label']],
                'song_b': [section_b['label']]
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ DRUM-FADE: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except FileNotFoundError as e:
        log(f"      ⚠️  Skipped DRUM-FADE: Stems not found. Run with --generate-stems first.")
    except Exception as e:
        log(f"      ✗ Failed to generate DRUM-FADE variant: {e}")

    return variants


def generate_all_transitions(candidates, section_features_map, audio_dir, cache_dir):
    """
    Generate all section transition audio files for candidate pairs (v2.1).

    This function generates all variants (medium-crossfade, medium-silence, vocal-fade, drum-fade)
    for each pair and creates comprehensive v2.0 metadata.

    Args:
        candidates: List of viable pairs from select_transition_candidates()
        section_features_map: Dict mapping (song_filename, section_index) -> section features
        audio_dir: Directory containing audio files
        cache_dir: Cache directory for loading section data

    Returns:
        List of transition metadata dicts (v2.0 schema)
    """
    log(f"\n{'='*70}")
    log("GENERATING SECTION TRANSITIONS (v2.1)")
    log(f"{'='*70}")

    # Create output directory structure
    output_audio_dir = CONFIG['output_dir'] / 'audio'
    output_metadata_dir = CONFIG['output_dir'] / 'metadata'
    output_metadata_dir.mkdir(parents=True, exist_ok=True)

    # Load all song sections from cache
    log(f"\nLoading song sections from allin1 cache...")
    all_sections = load_all_song_sections(audio_dir, cache_dir)
    log(f"  Loaded sections for {len(all_sections)} songs")

    transitions = []

    for pair_idx, pair in enumerate(candidates, 1):
        song_a = pair['song_a']
        song_b = pair['song_b']
        section_a_idx = pair['section_a_index']
        section_b_idx = pair['section_b_index']

        # Get section features
        section_a = section_features_map.get((song_a, section_a_idx))
        section_b = section_features_map.get((song_b, section_b_idx))

        if not section_a or not section_b:
            log(f"\n  WARNING: Could not find section features for {song_a} / {song_b}, skipping...")
            continue

        # Get full section lists
        sections_a = all_sections.get(song_a, [])
        sections_b = all_sections.get(song_b, [])

        if not sections_a or not sections_b:
            log(f"\n  WARNING: Could not load sections for {song_a} / {song_b}, skipping...")
            continue

        log(f"\nPair {pair_idx}/{len(candidates)}: "
            f"{song_a} [{section_a['label']}] → {song_b} [{section_b['label']}]")
        log(f"  Score: {pair['overall_score']:.1f}/100 "
            f"(tempo: {pair['tempo_score']:.1f}, key: {pair['key_score']:.1f}, "
            f"embed: {pair['embeddings_score']:.1f})")

        # Find audio file paths
        song_a_path = audio_dir / song_a
        song_b_path = audio_dir / song_b

        if not song_a_path.exists():
            log(f"  WARNING: Audio file not found: {song_a_path}")
            continue
        if not song_b_path.exists():
            log(f"  WARNING: Audio file not found: {song_b_path}")
            continue

        # Generate all three variants
        try:
            variants = generate_all_variants(
                pair, section_a, section_b,
                song_a_path, song_b_path,
                sections_a, sections_b,
                section_a_idx, section_b_idx,
                output_audio_dir
            )

            if not variants:
                log(f"  WARNING: No variants generated for this pair")
                continue

            # Build sections_used metadata (both variants use single sections)
            sections_used_a = [section_a['label']]
            sections_used_b = [section_b['label']]

            # Create v2.0 metadata structure
            transition_meta = {
                'transition_id': str(uuid.uuid4()),
                'generated_at': datetime.now().isoformat(),
                'version': '2.0',

                'pair': {
                    'song_a': {
                        'filename': song_a,
                        'sections_used': [
                            {
                                'index': section_a_idx,
                                'label': section_a['label'],
                                'start': section_a['start'],
                                'end': section_a['end'],
                                'duration': section_a['duration'],
                                'role': 'primary_exit'
                            }
                        ]
                    },
                    'song_b': {
                        'filename': song_b,
                        'sections_used': [
                            {
                                'index': section_b_idx,
                                'label': section_b['label'],
                                'start': section_b['start'],
                                'end': section_b['end'],
                                'duration': section_b['duration'],
                                'role': 'primary_entry'
                            }
                        ]
                    }
                },

                'compatibility': {
                    'overall_score': float(pair['overall_score']),
                    'components': {
                        'tempo': {
                            'score': float(pair['tempo_score']),
                            'weight': 0.25,
                            'weighted_contribution': float(pair['tempo_score']) * 0.25,
                            'details': {
                                'tempo_a': float(pair['tempo_a']),
                                'tempo_b': float(pair['tempo_b']),
                                'diff_bpm': abs(float(pair['tempo_a']) - float(pair['tempo_b'])),
                                'diff_pct': float(pair['tempo_diff_pct'])
                            }
                        },
                        'key': {
                            'score': float(pair['key_score']),
                            'weight': 0.25,
                            'weighted_contribution': float(pair['key_score']) * 0.25,
                            'details': {
                                'key_a': pair['key_a'],
                                'key_b': pair['key_b'],
                                'relationship': 'identical' if pair['key_a'] == pair['key_b'] else 'different'
                            }
                        },
                        'energy': {
                            'score': float(pair['energy_score']),
                            'weight': 0.15,
                            'weighted_contribution': float(pair['energy_score']) * 0.15,
                            'details': {
                                'energy_diff_db': float(pair['energy_diff_db'])
                            }
                        },
                        'embeddings': {
                            'score': float(pair['embeddings_score']),
                            'weight': 0.35,
                            'weighted_contribution': float(pair['embeddings_score']) * 0.35,
                            'details': {
                                'stems_used': 'all',
                                'similarity': float(pair['embeddings_score']) / 100.0
                            }
                        }
                    }
                },

                'variants': variants,

                'review': {
                    'status': 'pending',
                    'reviewed_at': None,
                    'reviewer_notes': '',
                    'ratings': {
                        'overall': None,
                        'theme_fit': None,
                        'musical_fit': None,
                        'energy_flow': None,
                        'lyrical_coherence': None,
                        'transition_smoothness': None
                    },
                    'preferred_variant': None,
                    'recommended_action': None,
                    'tags': []
                },

                'technical_notes': {
                    'adaptive_duration_used': False,  # No longer using adaptive durations
                    'section_fallbacks_applied': False,
                    'warnings': []
                }
            }

            transitions.append(transition_meta)
            log(f"  ✓ Generated {len(variants)} variants for this pair")

        except Exception as e:
            log(f"  ✗ ERROR generating transitions: {str(e)}")
            import traceback
            traceback.print_exc()

    return transitions


# =============================================================================
# OUTPUT GENERATION
# =============================================================================

def save_transitions_index(transitions, weights, embedding_stems):
    """
    Save master transitions index (v2.0) - single source of truth.

    Args:
        transitions: List of transition metadata dicts (v2.0 schema)
        weights: Compatibility weights dict
        embedding_stems: Embedding stems used

    Returns:
        Path to saved index file
    """
    # Calculate statistics
    total_transitions = len(transitions)
    total_pairs = len(transitions)  # Each entry is one pair with multiple variants
    reviewed_count = sum(1 for t in transitions if t['review']['status'] == 'reviewed')
    approved_count = sum(1 for t in transitions if t['review']['status'] == 'approved')

    # Calculate total storage
    total_storage_mb = sum(
        v['file_size_mb']
        for t in transitions
        for v in t['variants']
    )

    # Build master index
    master_index = {
        'schema_version': '2.0',
        'generated_at': datetime.now().isoformat(),
        'configuration': {
            'min_score_threshold': CONFIG['min_score'],
            'weights': weights,
            'embedding_stems': embedding_stems,
            'medium_crossfade_duration': CONFIG['medium_crossfade_duration'],
            'silence_beats': CONFIG['silence_beats'],
            'silence_fade_duration': CONFIG['silence_fade_duration'],
            'output_format': CONFIG['output_format'].upper(),
            'sample_rate': CONFIG['sample_rate']
        },
        'statistics': {
            'total_transitions': total_transitions,
            'total_pairs': total_pairs,
            'reviewed_count': reviewed_count,
            'approved_count': approved_count,
            'total_storage_mb': round(total_storage_mb, 2)
        },
        'transitions': transitions
    }

    # Save to metadata directory
    metadata_dir = CONFIG['output_dir'] / 'metadata'
    metadata_dir.mkdir(parents=True, exist_ok=True)
    index_path = metadata_dir / 'transitions_index.json'

    with open(index_path, 'w') as f:
        json.dump(master_index, f, indent=2)

    log(f"\n  ✓ Master transitions index saved: {index_path}")
    log(f"    Total transitions: {total_transitions}")
    log(f"    Total storage: {total_storage_mb:.2f} MB")

    return index_path


def save_summary_csv(transitions):
    """
    Save summary CSV for quick reference (v2.0).

    Exports flattened view of transitions for spreadsheet viewing.
    """
    if not transitions:
        return None

    # Flatten data for CSV (one row per transition pair)
    summary_data = []
    for t in transitions:
        # Count variants
        num_variants = len(t['variants'])
        variant_types = ', '.join([v['variant_type'] for v in t['variants']])
        total_size = sum(v['file_size_mb'] for v in t['variants'])

        summary_data.append({
            'transition_id': t['transition_id'],
            'song_a': t['pair']['song_a']['filename'],
            'song_b': t['pair']['song_b']['filename'],
            'section_a_label': t['pair']['song_a']['sections_used'][0]['label'],
            'section_b_label': t['pair']['song_b']['sections_used'][0]['label'],
            'overall_score': t['compatibility']['overall_score'],
            'tempo_score': t['compatibility']['components']['tempo']['score'],
            'key_score': t['compatibility']['components']['key']['score'],
            'energy_score': t['compatibility']['components']['energy']['score'],
            'embeddings_score': t['compatibility']['components']['embeddings']['score'],
            'num_variants': num_variants,
            'variant_types': variant_types,
            'total_size_mb': round(total_size, 2),
            'review_status': t['review']['status'],
            'generated_at': t['generated_at']
        })

    df = pd.DataFrame(summary_data)
    metadata_dir = CONFIG['output_dir'] / 'metadata'
    metadata_dir.mkdir(parents=True, exist_ok=True)
    summary_path = metadata_dir / 'transitions_summary.csv'
    df.to_csv(summary_path, index=False)

    log(f"  ✓ Summary CSV saved: {summary_path}")
    return summary_path


def print_summary_report(transitions):
    """Print final summary report (v2.1)."""
    log(f"\n{'='*70}")
    log("GENERATION COMPLETE (v2.1)")
    log(f"{'='*70}")

    if not transitions:
        log("\n  No transitions were generated.")
        log("  Try lowering min_score threshold or adding more songs to poc_audio/")
        return

    log(f"\n  Total transition pairs: {len(transitions)}")
    log(f"  Output directory: {CONFIG['output_dir'].absolute()}")

    # Count variants
    total_variants = sum(len(t['variants']) for t in transitions)
    medium_crossfade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'medium-crossfade')
    medium_silence_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'medium-silence')
    vocal_fade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'vocal-fade')
    drum_fade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'drum-fade')

    log(f"\n  Total variants generated: {total_variants}")
    log(f"    Medium-Crossfade (full sections with crossfade): {medium_crossfade_count}")
    log(f"    Medium-Silence ({CONFIG['silence_beats']}-beat silence gap): {medium_silence_count}")
    log(f"    Vocal-Fade ({CONFIG['stem_fade_transition_beats']}-beat vocal transition): {vocal_fade_count}")
    log(f"    Drum-Fade ({CONFIG['stem_fade_transition_beats']}-beat drum transition): {drum_fade_count}")

    # List transition pairs
    log(f"\n  Transition pairs:")
    for idx, t in enumerate(transitions, 1):
        pair_name = (f"{t['pair']['song_a']['filename']} [{t['pair']['song_a']['sections_used'][0]['label']}] → "
                     f"{t['pair']['song_b']['filename']} [{t['pair']['song_b']['sections_used'][0]['label']}]")
        score = t['compatibility']['overall_score']
        variant_types = ', '.join([v['variant_type'] for v in t['variants']])
        log(f"    {idx}. {pair_name}")
        log(f"       Score: {score:.1f}/100 | Variants: {variant_types}")

    # Total file size
    total_size_mb = sum(v['file_size_mb'] for t in transitions for v in t['variants'])
    log(f"\n  Total storage: {total_size_mb:.2f} MB")

    log(f"\n{'='*70}")
    log("NEXT STEPS")
    log(f"{'='*70}")
    log(f"  1. Review transitions using: python poc/review_transitions.py")
    log(f"  2. Audio files organized in: {CONFIG['output_dir']}/audio/{{medium-crossfade,medium-silence,vocal-fade,drum-fade}}/")
    log(f"  3. Master index (single source of truth): {CONFIG['output_dir']}/metadata/transitions_index.json")
    log(f"  4. Quick reference CSV: {CONFIG['output_dir']}/metadata/transitions_summary.csv")
    log(f"{'='*70}\n")


# =============================================================================
# CLI INTERFACE
# =============================================================================

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Generate section-to-section transition audio files',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Scoring weights (same as analyze_sections.py)
    parser.add_argument('--tempo-weight', type=float, default=0.25,
                        help='Weight for tempo score (0.0-1.0, default: 0.25)')
    parser.add_argument('--key-weight', type=float, default=0.25,
                        help='Weight for key score (0.0-1.0, default: 0.25)')
    parser.add_argument('--energy-weight', type=float, default=0.15,
                        help='Weight for energy score (0.0-1.0, default: 0.15)')
    parser.add_argument('--embeddings-weight', type=float, default=0.35,
                        help='Weight for embeddings score (0.0-1.0, default: 0.35)')

    # Embeddings stem selection
    parser.add_argument('--embedding-stems', type=str, default='all',
                        choices=['all', 'bass', 'drums', 'other', 'vocals',
                                 'bass+drums', 'bass+vocals', 'drums+vocals',
                                 'other+vocals', 'bass+drums+vocals'],
                        help='Which stems to use for embeddings scoring (default: all)')

    # Directories
    parser.add_argument('--audio-dir', type=Path, default=AUDIO_DIR,
                        help='Directory containing audio files')
    parser.add_argument('--cache-dir', type=Path, default=CACHE_DIR,
                        help='Directory for cached analysis results')
    parser.add_argument('--output-dir', type=Path, default=OUTPUT_DIR,
                        help='Output directory for analysis results')

    # Transition options
    parser.add_argument('--min-score', type=int, default=60,
                        help='Minimum compatibility score to generate transition (default: 60)')
    parser.add_argument('--max-pairs', type=int, default=None,
                        help='Maximum number of pairs to generate (default: all)')

    # Silence transition options
    parser.add_argument('--silence-beats', type=int, default=4,
                        help='Number of beats for silence transition (default: 4)')

    # Other options
    parser.add_argument('--section-type', type=str, default='chorus',
                        choices=['chorus', 'verse', 'bridge'],
                        help='Section type to analyze (default: chorus)')
    parser.add_argument('--fallback-to-verse', action='store_true', default=True,
                        help='Fallback to verse if chorus not found')
    parser.add_argument('--verbose', action='store_true', default=True,
                        help='Enable verbose output')

    return parser.parse_args()


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    """Main execution function."""
    args = parse_args()

    print(f"\n{'='*70}")
    print("Section-Level Transition Generator")
    print(f"{'='*70}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Build weights dictionary
    weights = {
        'tempo': args.tempo_weight,
        'key': args.key_weight,
        'energy': args.energy_weight,
        'embeddings': args.embeddings_weight
    }

    try:
        # Validate weights
        validate_weights(weights)
    except ValueError as e:
        print(f"❌ ERROR: {e}")
        print(f"   Provided weights: {weights}")
        return 1

    print(f"\nConfiguration:")
    print(f"  Audio directory: {args.audio_dir.absolute()}")
    print(f"  Output directory: {CONFIG['output_dir'].absolute()}")
    print(f"  Min score threshold: {args.min_score}")
    print(f"  Silence duration: {args.silence_beats} beats")
    print(f"  Compatibility weights: {weights}")
    print(f"  Embedding stems: {args.embedding_stems}")

    # Update CONFIG
    CONFIG['min_score'] = args.min_score
    CONFIG['silence_beats'] = args.silence_beats
    if args.max_pairs:
        CONFIG['max_pairs'] = args.max_pairs

    try:
        # Phase 1: Analyze sections (or load from cache)
        log("\n" + "="*70)
        log("PHASE 1: SECTION ANALYSIS")
        log("="*70)

        section_features, compatibility_df = analyze_all_sections(
            audio_dir=args.audio_dir,
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
            weights=weights,
            embedding_stems=args.embedding_stems,
            section_type=args.section_type,
            fallback_to_verse=args.fallback_to_verse,
            verbose=args.verbose
        )

        if not section_features or compatibility_df.empty:
            log("\n  No viable sections found for transition generation.")
            log(f"  Try lowering min_score (current: {args.min_score})")
            return 1

        # Create section features map for quick lookup
        section_features_map = {
            (feat['song_filename'], feat['section_index']): feat
            for feat in section_features
        }

        # Phase 2: Select candidates
        log("\n" + "="*70)
        log("PHASE 2: CANDIDATE SELECTION")
        log("="*70)

        candidates = select_transition_candidates(compatibility_df,
                                                   min_score=args.min_score,
                                                   max_pairs=args.max_pairs)

        if not candidates:
            log(f"\n  No viable pairs found above threshold (min_score: {args.min_score})")
            log("  Try lowering the threshold or adjusting weights.")
            return 0

        # Phase 3: Generate transitions (v2.0)
        transitions = generate_all_transitions(candidates, section_features_map, args.audio_dir, args.cache_dir)

        # Phase 4: Save outputs (v2.0)
        if transitions:
            save_transitions_index(transitions, weights, args.embedding_stems)
            save_summary_csv(transitions)
            print_summary_report(transitions)
        else:
            log("\n  No transitions were successfully generated.")
            log("  Check error messages above for details.")
            return 1

    except Exception as e:
        log(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
