#!/usr/bin/env python3
"""
Section-Level Transition Generator: Worship Music Transition System

Version: 2.0.0
Date: 2026-01-05
Purpose: Generate multi-variant section transitions with comprehensive metadata

This script generates three types of transitions for each viable section pair:
- Short: Crossfade only (6-12s)
- Medium: Full sections with crossfade
- Long: Extended context (2 sections each)

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

    # Crossfade options
    'durations': [6, 8, 10, 12],  # All possible durations
    'adaptive_duration': True,     # Use smart duration selection based on scores
    'sample_rate': 44100,          # Audio sample rate
    'output_format': 'flac',       # Output format: 'flac' or 'wav'

    # Variant options (v2.0)
    'short_crossfade_adaptive': True,  # Use adaptive duration for short
    'medium_crossfade_duration': 8.0,   # Fixed duration for medium
    'long_crossfade_duration': 10.0,    # Fixed duration for long

    # Optional features
    'generate_waveforms': False,   # Create visualization plots
    'verbose': True                # Print detailed progress
}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

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


def determine_crossfade_durations(pair_info, adaptive=None):
    """
    Select appropriate crossfade durations based on compatibility scores.

    Strategy:
    - High tempo match (95+): Shorter fades work (6-8s)
    - Good tempo match (80-95): Medium fades (8-10s)
    - Poor tempo match (<80): Longer fades needed (10-12s)

    Args:
        pair_info: Dict with compatibility scores
        adaptive: Use adaptive selection (if None, uses CONFIG setting)

    Returns:
        List of durations to generate (e.g., [6, 8])
    """
    if adaptive is None:
        adaptive = CONFIG['adaptive_duration']

    if not adaptive:
        # Generate all durations
        return CONFIG['durations']

    # Adaptive selection based on tempo score
    tempo_score = pair_info['tempo_score']

    if tempo_score >= 95:
        return [6, 8]  # Near-perfect tempo match
    elif tempo_score >= 80:
        return [8, 10]  # Good tempo match
    else:
        return [10, 12]  # Need longer blend time


# =============================================================================
# SECTION SELECTION FOR VARIANTS
# =============================================================================

def select_sections_for_long_transition(sections, target_section_index):
    """
    Select sections for long-form transition (v2.0).

    Algorithm:
    - Filter out intro/outro sections
    - For Song A: Get target section and the one before it
    - For Song B: Get target section and the one after it

    Args:
        sections: List of all sections from allin1 analysis (ordered chronologically)
        target_section_index: Index of the primary section used in compatibility analysis

    Returns:
        Tuple of (pre_section, main_section, post_section)
        Any element can be None if not available
    """
    # Filter out intro/outro
    content_sections = [s for s in sections if s['label'] not in ['intro', 'outro']]

    if not content_sections:
        return None, None, None

    # Find target section in content_sections
    try:
        target_idx_in_content = next(
            i for i, s in enumerate(content_sections)
            if sections.index(s) == target_section_index
        )
    except StopIteration:
        # Target section is intro/outro, fallback to first content section
        return None, content_sections[0] if content_sections else None, \
               content_sections[1] if len(content_sections) > 1 else None

    # For Song A: Get pre-context section
    if target_idx_in_content > 0:
        pre_section = content_sections[target_idx_in_content - 1]
    else:
        pre_section = None

    main_section = content_sections[target_idx_in_content]

    # For Song B: Get post-context section
    if target_idx_in_content < len(content_sections) - 1:
        post_section = content_sections[target_idx_in_content + 1]
    else:
        post_section = None

    return pre_section, main_section, post_section


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

def generate_section_transition(song_a_path, song_b_path, section_a, section_b,
                                 crossfade_duration=8.0):
    """
    Create crossfade transition between two song sections.

    Algorithm:
    1. Load stereo audio at 44100 Hz
    2. Extract section audio segments using start/end times
    3. Take last N seconds of section A and first N seconds of section B
    4. Apply equal-power fade curves (sqrt for energy preservation)
    5. Mix faded segments

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section info dicts with 'start', 'end' keys
        crossfade_duration: Crossfade duration in seconds

    Returns:
        (transition_audio, sample_rate)
    """
    # Load stereo audio for higher quality transition
    y_a, sr = librosa.load(song_a_path, sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=CONFIG['sample_rate'], mono=False)

    # Ensure stereo (2 channels)
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Extract sections
    section_a_start = int(section_a['start'] * sr)
    section_a_end = int(section_a['end'] * sr)
    section_b_start = int(section_b['start'] * sr)
    section_b_end = int(section_b['end'] * sr)

    section_a_audio = y_a[:, section_a_start:section_a_end]
    section_b_audio = y_b[:, section_b_start:section_b_end]

    # Determine crossfade region
    crossfade_samples = int(crossfade_duration * sr)

    # Handle short sections (adaptive crossfade)
    if section_a_audio.shape[1] < crossfade_samples:
        log(f"    WARNING: Section A shorter than crossfade, reducing duration from {crossfade_duration}s "
            f"to {section_a_audio.shape[1] / sr:.1f}s")
        crossfade_samples = section_a_audio.shape[1]

    if section_b_audio.shape[1] < crossfade_samples:
        log(f"    WARNING: Section B shorter than crossfade, reducing duration from {crossfade_duration}s "
            f"to {section_b_audio.shape[1] / sr:.1f}s")
        crossfade_samples = section_b_audio.shape[1]

    # Take last N seconds of section A
    outro = section_a_audio[:, -crossfade_samples:]

    # Take first N seconds of section B
    intro = section_b_audio[:, :crossfade_samples]

    # Equal-power crossfade curves
    fade_curve = np.linspace(0, 1, crossfade_samples)
    fade_out = np.sqrt(1 - fade_curve)  # Starts at 1, ends at 0
    fade_in = np.sqrt(fade_curve)       # Starts at 0, ends at 1

    # Apply fades to both channels
    outro_faded = outro * fade_out
    intro_faded = intro * fade_in

    # Mix
    transition = outro_faded + intro_faded

    return transition, sr


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
    y_a, sr = librosa.load(song_a_path, sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=CONFIG['sample_rate'], mono=False)

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


def generate_long_transition(song_a_path, song_b_path, sections_a, sections_b,
                             section_a_idx, section_b_idx, crossfade_duration=10.0):
    """
    Create long transition: 2 sections from A + crossfade + 2 sections from B.

    Args:
        song_a_path, song_b_path: Paths to audio files
        sections_a, sections_b: Complete list of sections for each song
        section_a_idx, section_b_idx: Indices of primary sections
        crossfade_duration: Crossfade duration in seconds

    Returns:
        (transition_audio, sample_rate, actual_duration, sections_used_a, sections_used_b)
    """
    # Load stereo audio
    y_a, sr = librosa.load(song_a_path, sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=CONFIG['sample_rate'], mono=False)

    # Ensure stereo
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Select sections for long transition
    pre_a, main_a, _ = select_sections_for_long_transition(sections_a, section_a_idx)
    _, main_b, post_b = select_sections_for_long_transition(sections_b, section_b_idx)

    # Build section list for song A
    sections_to_use_a = []
    if pre_a is not None:
        sections_to_use_a.append(pre_a)
    if main_a is not None:
        sections_to_use_a.append(main_a)

    # Build section list for song B
    sections_to_use_b = []
    if main_b is not None:
        sections_to_use_b.append(main_b)
    if post_b is not None:
        sections_to_use_b.append(post_b)

    if not sections_to_use_a or not sections_to_use_b:
        raise ValueError("Not enough sections available for long transition")

    # Extract audio for all sections
    start_a = sections_to_use_a[0]['start']
    end_a = sections_to_use_a[-1]['end']
    start_b = sections_to_use_b[0]['start']
    end_b = sections_to_use_b[-1]['end']

    audio_a = y_a[:, int(start_a * sr):int(end_a * sr)]
    audio_b = y_b[:, int(start_b * sr):int(end_b * sr)]

    # Create crossfade region
    crossfade_samples = int(crossfade_duration * sr)

    # Handle short audio
    if audio_a.shape[1] < crossfade_samples:
        crossfade_samples = audio_a.shape[1]
    if audio_b.shape[1] < crossfade_samples:
        crossfade_samples = audio_b.shape[1]

    # Split audio
    audio_a_pre = audio_a[:, :-crossfade_samples]
    audio_a_fade = audio_a[:, -crossfade_samples:]
    audio_b_fade = audio_b[:, :crossfade_samples]
    audio_b_post = audio_b[:, crossfade_samples:]

    # Create fade curves
    fade_curve = np.linspace(0, 1, crossfade_samples)
    fade_out = np.sqrt(1 - fade_curve)
    fade_in = np.sqrt(fade_curve)

    # Apply fades
    audio_a_faded = audio_a_fade * fade_out
    audio_b_faded = audio_b_fade * fade_in
    crossfade = audio_a_faded + audio_b_faded

    # Concatenate
    transition = np.concatenate([audio_a_pre, crossfade, audio_b_post], axis=1)

    actual_duration = transition.shape[1] / sr

    # Return section labels used
    sections_used_a = [s['label'] for s in sections_to_use_a]
    sections_used_b = [s['label'] for s in sections_to_use_b]

    return transition, sr, actual_duration, sections_used_a, sections_used_b


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
    Generate all three variants (short, medium, long) for a section pair.

    Args:
        pair: Compatibility info dict
        section_a, section_b: Section feature dicts
        song_a_path, song_b_path: Path objects to audio files
        sections_a, sections_b: Full section lists for each song
        section_a_idx, section_b_idx: Section indices
        audio_dir: Output audio directory

    Returns:
        List of variant metadata dicts
    """
    variants = []

    # === SHORT VARIANT (Crossfade Only) ===
    log(f"    Generating SHORT variant (crossfade only)...")

    # Determine duration (adaptive or fixed)
    if CONFIG['short_crossfade_adaptive']:
        durations = determine_crossfade_durations(pair)
        short_duration = durations[0]  # Use first duration from adaptive selection
    else:
        short_duration = 8.0

    try:
        transition, sr = generate_section_transition(
            song_a_path, song_b_path, section_a, section_b,
            crossfade_duration=short_duration
        )

        # Generate filename
        base_a = song_a_path.stem
        base_b = song_b_path.stem
        filename = f"transition_short_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{int(short_duration)}s.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'short' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'short',
            'crossfade_duration': short_duration,
            'total_duration': transition.shape[1] / sr,
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ SHORT: {filename} ({file_size_mb:.2f} MB)")

    except Exception as e:
        log(f"      ✗ Failed to generate SHORT variant: {e}")

    # === MEDIUM VARIANT (Full Sections) ===
    log(f"    Generating MEDIUM variant (full sections)...")

    try:
        transition, sr, duration = generate_medium_transition(
            song_a_path, song_b_path, section_a, section_b,
            crossfade_duration=CONFIG['medium_crossfade_duration']
        )

        # Generate filename
        filename = f"transition_medium_{base_a}_{section_a['label']}_{base_b}_{section_b['label']}_{int(CONFIG['medium_crossfade_duration'])}s.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'medium' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'medium',
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

        log(f"      ✓ MEDIUM: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except Exception as e:
        log(f"      ✗ Failed to generate MEDIUM variant: {e}")

    # === LONG VARIANT (Extended Context) ===
    log(f"    Generating LONG variant (extended context)...")

    try:
        transition, sr, duration, sections_used_a, sections_used_b = generate_long_transition(
            song_a_path, song_b_path, sections_a, sections_b,
            section_a_idx, section_b_idx,
            crossfade_duration=CONFIG['long_crossfade_duration']
        )

        # Generate filename
        sections_str_a = '-'.join(sections_used_a)
        sections_str_b = '-'.join(sections_used_b)
        filename = f"transition_long_{base_a}_{sections_str_a}_{base_b}_{sections_str_b}_{int(CONFIG['long_crossfade_duration'])}s.{CONFIG['output_format']}"

        # Save audio
        filepath = audio_dir / 'long' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        variants.append({
            'variant_type': 'long',
            'crossfade_duration': CONFIG['long_crossfade_duration'],
            'total_duration': duration,
            'sections_included': {
                'song_a': sections_used_a,
                'song_b': sections_used_b
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ LONG: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except Exception as e:
        log(f"      ✗ Failed to generate LONG variant: {e}")

    return variants


def generate_all_transitions(candidates, section_features_map, audio_dir, cache_dir):
    """
    Generate all section transition audio files for candidate pairs (v2.0).

    This function generates all three variants (short, medium, long) for each pair
    and creates comprehensive v2.0 metadata.

    Args:
        candidates: List of viable pairs from select_transition_candidates()
        section_features_map: Dict mapping (song_filename, section_index) -> section features
        audio_dir: Directory containing audio files
        cache_dir: Cache directory for loading section data

    Returns:
        List of transition metadata dicts (v2.0 schema)
    """
    log(f"\n{'='*70}")
    log("GENERATING SECTION TRANSITIONS (v2.0)")
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

            # Build sections_used metadata
            sections_used_a = []
            sections_used_b = []

            # For long variant, get the actual sections used
            long_variant = next((v for v in variants if v['variant_type'] == 'long'), None)
            if long_variant:
                sections_used_a = long_variant.get('sections_included', {}).get('song_a', [section_a['label']])
                sections_used_b = long_variant.get('sections_included', {}).get('song_b', [section_b['label']])
            else:
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
                    'adaptive_duration_used': CONFIG['short_crossfade_adaptive'],
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
            'short_crossfade_adaptive': CONFIG['short_crossfade_adaptive'],
            'medium_crossfade_duration': CONFIG['medium_crossfade_duration'],
            'long_crossfade_duration': CONFIG['long_crossfade_duration'],
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
    """Print final summary report (v2.0)."""
    log(f"\n{'='*70}")
    log("GENERATION COMPLETE (v2.0)")
    log(f"{'='*70}")

    if not transitions:
        log("\n  No transitions were generated.")
        log("  Try lowering min_score threshold or adding more songs to poc_audio/")
        return

    log(f"\n  Total transition pairs: {len(transitions)}")
    log(f"  Output directory: {CONFIG['output_dir'].absolute()}")

    # Count variants
    total_variants = sum(len(t['variants']) for t in transitions)
    short_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'short')
    medium_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'medium')
    long_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'long')

    log(f"\n  Total variants generated: {total_variants}")
    log(f"    Short (crossfade only): {short_count}")
    log(f"    Medium (full sections): {medium_count}")
    log(f"    Long (extended context): {long_count}")

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
    log(f"  2. Audio files organized in: {CONFIG['output_dir']}/audio/{{short,medium,long}}/")
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
    print(f"  Compatibility weights: {weights}")
    print(f"  Embedding stems: {args.embedding_stems}")

    # Update CONFIG
    CONFIG['min_score'] = args.min_score
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
