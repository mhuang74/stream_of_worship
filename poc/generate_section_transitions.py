#!/usr/bin/env python3
"""
Section-Level Transition Generator: Worship Music Transition System

Version: 1.0.0
Date: 2026-01-03
Purpose: Generate chorus-to-chorus transition audio files from section compatibility analysis

This script reads section compatibility results and generates transitions for all viable
section pairs with multiple crossfade durations for human evaluation.
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


# =============================================================================
# MAIN TRANSITION GENERATION
# =============================================================================

def generate_all_transitions(candidates, section_features_map, audio_dir):
    """
    Generate all section transition audio files for candidate pairs.

    Args:
        candidates: List of viable pairs from select_transition_candidates()
        section_features_map: Dict mapping (song_filename, section_index) -> section features
        audio_dir: Directory containing audio files

    Returns:
        List of metadata dicts for each generated transition
    """
    log(f"\n{'='*70}")
    log("GENERATING SECTION TRANSITIONS")
    log(f"{'='*70}")

    # Create output directory
    CONFIG['output_dir'].mkdir(parents=True, exist_ok=True)

    transitions = []
    transition_id = 1

    for pair in candidates:
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

        # Determine durations for this pair
        durations = determine_crossfade_durations(pair)

        log(f"\nPair {len(transitions)//len(durations) + 1}: "
            f"{song_a} [{section_a['label']}] → {song_b} [{section_b['label']}]")
        log(f"  Score: {pair['overall_score']:.1f}/100 "
            f"(tempo: {pair['tempo_score']:.1f}, key: {pair['key_score']:.1f}, "
            f"embed: {pair['embeddings_score']:.1f})")
        log(f"  Generating {len(durations)} transitions: {durations}s")

        # Find audio file paths
        song_a_path = audio_dir / song_a
        song_b_path = audio_dir / song_b

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
                transition, sr = generate_section_transition(
                    song_a_path,
                    song_b_path,
                    section_a,
                    section_b,
                    crossfade_duration=duration
                )

                # Save audio file
                filename = generate_transition_filename(
                    song_a, song_b,
                    section_a['label'], section_b['label'],
                    duration
                )
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

                if pair['embeddings_score'] >= 80:
                    notes.append("High embeddings similarity")
                elif pair['embeddings_score'] >= 60:
                    notes.append("Moderate embeddings similarity")

                # Store metadata
                transition_meta = {
                    'id': transition_id,
                    'song_a': song_a,
                    'song_b': song_b,
                    'section_a': {
                        'label': section_a['label'],
                        'index': section_a_idx,
                        'start': section_a['start'],
                        'end': section_a['end'],
                        'duration': section_a['duration']
                    },
                    'section_b': {
                        'label': section_b['label'],
                        'index': section_b_idx,
                        'start': section_b['start'],
                        'end': section_b['end'],
                        'duration': section_b['duration']
                    },
                    'compatibility': {
                        'overall_score': float(pair['overall_score']),
                        'tempo_score': float(pair['tempo_score']),
                        'key_score': float(pair['key_score']),
                        'energy_score': float(pair['energy_score']),
                        'embeddings_score': float(pair['embeddings_score']),
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

def save_metadata(transitions, weights, embedding_stems):
    """Save detailed metadata JSON."""
    metadata = {
        'generated_at': datetime.now().isoformat(),
        'total_transitions': len(transitions),
        'configuration': {
            'min_score_threshold': CONFIG['min_score'],
            'crossfade_durations': CONFIG['durations'],
            'adaptive_duration': CONFIG['adaptive_duration'],
            'output_format': CONFIG['output_format'].upper(),
            'sample_rate': CONFIG['sample_rate'],
            'compatibility_weights': weights,
            'embedding_stems_used': embedding_stems
        },
        'transitions': transitions
    }

    metadata_path = CONFIG['output_dir'] / 'section_transitions_metadata.json'
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
            'section_a_label': t['section_a']['label'],
            'section_b_label': t['section_b']['label'],
            'overall_score': t['compatibility']['overall_score'],
            'tempo_score': t['compatibility']['tempo_score'],
            'key_score': t['compatibility']['key_score'],
            'energy_score': t['compatibility']['energy_score'],
            'embeddings_score': t['compatibility']['embeddings_score'],
            'duration_s': t['crossfade_duration'],
            'file_size_mb': t['file_size_mb'],
            'filename': t['filename'],
            'notes': t['notes']
        })

    df = pd.DataFrame(summary_data)
    summary_path = CONFIG['output_dir'] / 'section_transitions_summary.csv'
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
        key = f"{t['song_a']} [{t['section_a']['label']}] → {t['song_b']} [{t['section_b']['label']}]"
        if key not in pairs:
            pairs[key] = []
        pairs[key].append(t)

    log(f"\n  Transitions by section pair:")
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
    log(f"  2. Review metadata: section_transitions_metadata.json")
    log(f"  3. Compare scores: section_transitions_summary.csv")
    log(f"  4. Evaluate which durations sound best for each section pair")
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

        # Phase 3: Generate transitions
        transitions = generate_all_transitions(candidates, section_features_map, args.audio_dir)

        # Phase 4: Save outputs
        if transitions:
            save_metadata(transitions, weights, args.embedding_stems)
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
