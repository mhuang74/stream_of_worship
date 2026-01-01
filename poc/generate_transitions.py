#!/usr/bin/env python3
"""
Multi-Transition Generator: Worship Music Transition System

Version: 1.0.0
Date: 2026-01-02
Purpose: Generate multiple transition audio files from pre-computed compatibility analysis

This script reads existing analysis results and generates transitions for all viable
song pairs with multiple crossfade durations for human evaluation.
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

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG = {
    # Directories
    'input_dir': Path('poc_output_allinone'),
    'audio_dir': Path('poc_audio'),
    'output_dir': Path('poc_output_allinone/transitions'),

    # Filtering
    'min_score': 60,           # Minimum compatibility score (0-100)
    'max_pairs': None,         # Limit number of pairs (None = all viable pairs)

    # Crossfade options
    'durations': [6, 8, 10, 12],  # All possible durations
    'adaptive_duration': True,     # Use smart duration selection based on scores
    'sample_rate': 44100,          # Audio sample rate
    'output_format': 'flac',       # Output format: 'flac' or 'wav'

    # Optional features
    'generate_waveforms': False,   # Create visualization plots
    'verbose': True                # Print detailed progress
}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def log(message, verbose=True):
    """Print message if verbose mode is enabled."""
    if verbose and CONFIG['verbose']:
        print(message)


def generate_transition_filename(song_a, song_b, duration):
    """
    Create descriptive filename for transition audio.

    Format: transition_{song_a_base}_{song_b_base}_{duration}s.flac
    Example: transition_joy_to_heaven_praise_8s.flac
    """
    # Remove extensions
    base_a = song_a.replace('.mp3', '').replace('.flac', '')
    base_b = song_b.replace('.mp3', '').replace('.flac', '')

    # Sanitize (remove special chars)
    base_a = base_a.replace(' ', '_')
    base_b = base_b.replace(' ', '_')

    return f"transition_{base_a}_{base_b}_{duration}s.{CONFIG['output_format']}"


# =============================================================================
# DATA LOADING
# =============================================================================

def load_analysis_results():
    """
    Load pre-computed analysis results from output directory.

    Returns:
        tuple: (compatibility_df, song_data_dict)
    """
    log("\nLoading analysis results...")

    # Load compatibility scores
    compat_path = CONFIG['input_dir'] / 'poc_compatibility_scores.csv'
    if not compat_path.exists():
        raise FileNotFoundError(f"Compatibility scores not found: {compat_path}")
    compatibility_df = pd.read_csv(compat_path)
    log(f"  Loaded {len(compatibility_df)} song pairs from {compat_path.name}")

    # Load full song analysis data (optional - for future section-aware features)
    json_path = CONFIG['input_dir'] / 'poc_full_results.json'
    song_data = {}
    if json_path.exists():
        with open(json_path, 'r') as f:
            song_list = json.load(f)
        song_data = {item['filename']: item for item in song_list}
        log(f"  Loaded analysis data for {len(song_data)} songs from {json_path.name}")

    return compatibility_df, song_data


# =============================================================================
# PAIR SELECTION
# =============================================================================

def select_transition_candidates(compatibility_df, min_score=None, max_pairs=None):
    """
    Filter compatible pairs worthy of transition generation.

    Args:
        compatibility_df: DataFrame from poc_compatibility_scores.csv
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

    log(f"\nFiltered {len(viable)} viable pairs (min_score >= {min_score}):")
    for idx, row in viable.iterrows():
        log(f"  {row['song_a']} → {row['song_b']}: {row['overall_score']:.1f}/100 "
            f"(tempo: {row['tempo_score']:.1f}, key: {row['key_score']:.1f})")

    return viable.to_dict('records')


# =============================================================================
# DURATION SELECTION
# =============================================================================

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
# CROSSFADE GENERATION
# =============================================================================

def create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=8.0):
    """
    Create equal-power crossfade between two songs.

    Algorithm:
    1. Load stereo audio at 44100 Hz
    2. Extract outro of song A (last N seconds)
    3. Extract intro of song B (first N seconds)
    4. Apply equal-power fade curves (sqrt for energy preservation)
    5. Mix faded segments

    Returns: (transition_audio, sample_rate)
    """
    # Load stereo audio for higher quality transition
    y_a, sr = librosa.load(song_a_path, sr=CONFIG['sample_rate'], mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=CONFIG['sample_rate'], mono=False)

    # Ensure stereo (2 channels)
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    crossfade_samples = int(crossfade_duration * sr)

    # Extract segments
    outro = y_a[:, -crossfade_samples:]  # Last N seconds of A
    intro = y_b[:, :crossfade_samples]   # First N seconds of B

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


# =============================================================================
# MAIN TRANSITION GENERATION
# =============================================================================

def generate_all_transitions(candidates, song_data):
    """
    Generate all transition audio files for candidate pairs.

    Args:
        candidates: List of viable pairs from select_transition_candidates()
        song_data: Dict of song analysis data (optional)

    Returns:
        List of metadata dicts for each generated transition
    """
    log(f"\n{'='*70}")
    log("GENERATING TRANSITIONS")
    log(f"{'='*70}")

    # Create output directory
    CONFIG['output_dir'].mkdir(parents=True, exist_ok=True)

    transitions = []
    transition_id = 1

    for pair in candidates:
        song_a = pair['song_a']
        song_b = pair['song_b']

        # Determine durations for this pair
        durations = determine_crossfade_durations(pair)

        log(f"\nPair {len(transitions)//len(durations) + 1}: {song_a} → {song_b}")
        log(f"  Score: {pair['overall_score']:.1f}/100 "
            f"(tempo: {pair['tempo_score']:.1f}, key: {pair['key_score']:.1f})")
        log(f"  Generating {len(durations)} transitions: {durations}s")

        # Find audio file paths
        song_a_path = CONFIG['audio_dir'] / song_a
        song_b_path = CONFIG['audio_dir'] / song_b

        if not song_a_path.exists():
            log(f"  WARNING: Audio file not found: {song_a_path}")
            continue
        if not song_b_path.exists():
            log(f"  WARNING: Audio file not found: {song_b_path}")
            continue

        # Generate transition for each duration
        for duration in durations:
            try:
                log(f"    Creating {duration}s crossfade...")

                # Generate crossfade
                transition, sr = create_simple_crossfade(
                    song_a_path,
                    song_b_path,
                    crossfade_duration=duration
                )

                # Save audio file
                filename = generate_transition_filename(song_a, song_b, duration)
                filepath = CONFIG['output_dir'] / filename
                sf.write(filepath, transition.T, sr)

                # Get file size
                file_size_mb = filepath.stat().st_size / (1024 * 1024)

                # Generate recommendations/notes
                notes = []
                if pair['tempo_score'] >= 95:
                    notes.append(f"Excellent tempo match ({pair['tempo_a']:.1f}→{pair['tempo_b']:.1f} BPM)")
                elif pair['tempo_score'] >= 80:
                    notes.append(f"Good tempo match ({pair['tempo_a']:.1f}→{pair['tempo_b']:.1f} BPM)")
                else:
                    notes.append(f"Tempo mismatch ({pair['tempo_a']:.1f}→{pair['tempo_b']:.1f} BPM)")

                if pair['key_score'] >= 90:
                    notes.append(f"Same key ({pair['key_a']})")
                elif pair['key_score'] >= 70:
                    notes.append(f"Compatible keys ({pair['key_a']}→{pair['key_b']})")
                else:
                    notes.append(f"Different keys ({pair['key_a']}→{pair['key_b']})")

                # Store metadata
                transition_meta = {
                    'id': transition_id,
                    'song_a': song_a,
                    'song_b': song_b,
                    'compatibility': {
                        'overall_score': float(pair['overall_score']),
                        'tempo_score': float(pair['tempo_score']),
                        'key_score': float(pair['key_score']),
                        'energy_score': float(pair['energy_score']),
                        'tempo_a': float(pair['tempo_a']),
                        'tempo_b': float(pair['tempo_b']),
                        'tempo_diff_pct': float(pair['tempo_diff_pct']),
                        'key_a': pair['key_a'],
                        'key_b': pair['key_b'],
                        'energy_diff_db': float(pair['energy_diff_db'])
                    },
                    'crossfade_duration': duration,
                    'filename': filename,
                    'file_size_mb': round(file_size_mb, 2),
                    'sample_rate': sr,
                    'channels': transition.shape[0],
                    'notes': ', '.join(notes)
                }

                transitions.append(transition_meta)
                transition_id += 1

                log(f"      Saved: {filename} ({file_size_mb:.2f} MB)")

            except Exception as e:
                log(f"    ERROR generating {duration}s transition: {str(e)}")
                import traceback
                traceback.print_exc()

    return transitions


# =============================================================================
# OUTPUT GENERATION
# =============================================================================

def save_metadata(transitions):
    """Save detailed metadata JSON."""
    metadata = {
        'generated_at': datetime.now().isoformat(),
        'total_transitions': len(transitions),
        'configuration': {
            'min_score_threshold': CONFIG['min_score'],
            'crossfade_durations': CONFIG['durations'],
            'adaptive_duration': CONFIG['adaptive_duration'],
            'output_format': CONFIG['output_format'].upper(),
            'sample_rate': CONFIG['sample_rate']
        },
        'transitions': transitions
    }

    metadata_path = CONFIG['output_dir'] / 'transitions_metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)

    log(f"\n  Metadata saved: {metadata_path}")
    return metadata_path


def save_summary_csv(transitions):
    """Save summary CSV for quick reference."""
    if not transitions:
        return None

    # Flatten data for CSV
    summary_data = []
    for t in transitions:
        summary_data.append({
            'id': t['id'],
            'song_a': t['song_a'],
            'song_b': t['song_b'],
            'overall_score': t['compatibility']['overall_score'],
            'tempo_score': t['compatibility']['tempo_score'],
            'key_score': t['compatibility']['key_score'],
            'energy_score': t['compatibility']['energy_score'],
            'duration_s': t['crossfade_duration'],
            'file_size_mb': t['file_size_mb'],
            'filename': t['filename'],
            'notes': t['notes']
        })

    df = pd.DataFrame(summary_data)
    summary_path = CONFIG['output_dir'] / 'transitions_summary.csv'
    df.to_csv(summary_path, index=False)

    log(f"  Summary CSV saved: {summary_path}")
    return summary_path


def print_summary_report(transitions):
    """Print final summary report."""
    log(f"\n{'='*70}")
    log("GENERATION COMPLETE")
    log(f"{'='*70}")

    if not transitions:
        log("\n  No transitions were generated.")
        log("  Try lowering min_score threshold or adding more songs to poc_audio/")
        return

    log(f"\n  Total transitions generated: {len(transitions)}")
    log(f"  Output directory: {CONFIG['output_dir'].absolute()}")

    # Group by pair
    pairs = {}
    for t in transitions:
        key = f"{t['song_a']} → {t['song_b']}"
        if key not in pairs:
            pairs[key] = []
        pairs[key].append(t)

    log(f"\n  Transitions by pair:")
    for pair_name, pair_transitions in pairs.items():
        durations = [str(t['crossfade_duration']) + 's' for t in pair_transitions]
        score = pair_transitions[0]['compatibility']['overall_score']
        log(f"    {pair_name}: {', '.join(durations)} (score: {score:.1f})")

    # Total file size
    total_size_mb = sum(t['file_size_mb'] for t in transitions)
    log(f"\n  Total size: {total_size_mb:.2f} MB")

    log(f"\n{'='*70}")
    log("NEXT STEPS")
    log(f"{'='*70}")
    log(f"  1. Listen to transitions in: {CONFIG['output_dir']}/")
    log(f"  2. Review metadata: transitions_metadata.json")
    log(f"  3. Compare scores: transitions_summary.csv")
    log(f"  4. Evaluate which durations sound best for each pair")
    log(f"{'='*70}\n")


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    """Main execution function."""
    print(f"\n{'='*70}")
    print("Multi-Transition Generator")
    print(f"{'='*70}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nConfiguration:")
    print(f"  Input directory: {CONFIG['input_dir'].absolute()}")
    print(f"  Audio directory: {CONFIG['audio_dir'].absolute()}")
    print(f"  Output directory: {CONFIG['output_dir'].absolute()}")
    print(f"  Min score threshold: {CONFIG['min_score']}")
    print(f"  Adaptive duration: {CONFIG['adaptive_duration']}")
    print(f"  Available durations: {CONFIG['durations']}s")

    try:
        # Phase 1: Load data
        compatibility_df, song_data = load_analysis_results()

        # Phase 2: Select candidates
        candidates = select_transition_candidates(compatibility_df)

        if not candidates:
            log("\n  No viable pairs found above threshold.")
            log(f"  Try lowering min_score (current: {CONFIG['min_score']})")
            return

        # Phase 3: Generate transitions
        transitions = generate_all_transitions(candidates, song_data)

        # Phase 4: Save outputs
        if transitions:
            save_metadata(transitions)
            save_summary_csv(transitions)
            print_summary_report(transitions)
        else:
            log("\n  No transitions were successfully generated.")
            log("  Check error messages above for details.")

    except Exception as e:
        log(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    return 0


if __name__ == "__main__":
    exit(main())
