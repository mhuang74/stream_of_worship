#!/usr/bin/env python3
"""
Section-Level Analysis: Worship Music Transition System

Version: 1.0.0
Date: 2026-01-03
Purpose: Analyze and compare song sections (chorus, verse, bridge) for compatibility

This script extends the song-level analysis to section-level, enabling
more precise transitions between musically similar sections (e.g., chorus-to-chorus).

Features:
- Extract section-specific features (tempo, key, energy, embeddings)
- Select "best" chorus per song using energy heuristics
- Calculate section compatibility with configurable weights
- Support for traditional metrics (tempo/key/energy) and ML embeddings
- Configurable stem selection for embeddings (all, vocals, bass, etc.)
"""

import warnings
warnings.filterwarnings('ignore')

# Audio processing
import librosa
import numpy as np

# Data and visualization
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
sns.set_style("whitegrid")

# Utilities
from pathlib import Path
import json
import argparse
from datetime import datetime
import sys

# Import from existing analysis script
from poc_analysis_allinone import (
    analyze_song_allinone, compute_file_hash, load_from_cache,
    AUDIO_DIR, CACHE_DIR, OUTPUT_DIR as ALLINONE_OUTPUT_DIR
)

# Base output directory for this script
OUTPUT_DIR = ALLINONE_OUTPUT_DIR


# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_WEIGHTS = {
    'tempo': 0.25,
    'key': 0.25,
    'energy': 0.15,
    'embeddings': 0.35
}

STEM_MAP = {'bass': 0, 'drums': 1, 'other': 2, 'vocals': 3}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def log(message, verbose=True):
    """Print message if verbose mode is enabled."""
    if verbose:
        print(message)


def validate_weights(weights):
    """Validate that weights sum to 1.0."""
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Weights must sum to 1.0, got {total:.6f}")
    return weights


def parse_embedding_stems(embedding_stems_str):
    """
    Parse embedding stems string into list of indices.

    Args:
        embedding_stems_str: 'all', 'vocals', 'bass+drums', etc.

    Returns:
        List of stem indices (0-3)
    """
    if embedding_stems_str == 'all':
        return [0, 1, 2, 3]
    elif embedding_stems_str in STEM_MAP:
        return [STEM_MAP[embedding_stems_str]]
    else:
        # Parse combinations like 'bass+drums' -> [0, 1]
        stem_names = embedding_stems_str.split('+')
        return [STEM_MAP[name.strip()] for name in stem_names if name.strip() in STEM_MAP]


# =============================================================================
# SECTION FEATURE EXTRACTION
# =============================================================================

def compute_energy_score(loudness_db, spectral_centroid, duration):
    """
    Compute 0-100 energy score for chorus selection.

    Strategy:
    - Louder is better (but normalize to avoid clipping bias)
    - Brighter is better (higher spectral centroid)
    - Typical chorus duration is better (20-60s range)

    Args:
        loudness_db: Mean RMS loudness in dB
        spectral_centroid: Mean spectral centroid in Hz
        duration: Section duration in seconds

    Returns:
        Energy score 0-100
    """
    # Normalize loudness (-60 to 0 dB → 0 to 100)
    loudness_norm = (loudness_db + 60) / 60 * 100
    loudness_norm = np.clip(loudness_norm, 0, 100)

    # Normalize brightness (1000-5000 Hz → 0 to 100)
    brightness_norm = (spectral_centroid - 1000) / 4000 * 100
    brightness_norm = np.clip(brightness_norm, 0, 100)

    # Duration penalty (penalize very short <10s or very long >90s sections)
    if 20 <= duration <= 60:
        duration_score = 100
    elif duration < 20:
        duration_score = duration / 20 * 100
    else:  # duration > 60
        duration_score = max(50, 100 - (duration - 60) * 2)

    # Weighted average
    energy_score = (loudness_norm * 0.70 +
                    brightness_norm * 0.20 +
                    duration_score * 0.10)

    return energy_score


def extract_section_features(song_result, section_idx, audio_path, verbose=True):
    """
    Extract all necessary features for a single section.

    Args:
        song_result: Full analysis result from analyze_song_allinone()
        section_idx: Index of section in song_result['sections']
        audio_path: Path to original audio file
        verbose: Enable verbose output

    Returns:
        Dictionary with section features
    """
    section = song_result['sections'][section_idx]

    log(f"\n  Extracting features for section {section_idx}: {section['label']} "
        f"({section['start']:.1f}s - {section['end']:.1f}s)", verbose)

    # === AUDIO EXTRACTION ===
    y, sr = librosa.load(audio_path, sr=44100, mono=False)

    # Convert to mono for analysis
    if y.ndim > 1:
        y_mono = librosa.to_mono(y)
    else:
        y_mono = y
        y = np.stack([y, y])  # Ensure stereo for output

    # Extract section audio
    start_samples = int(section['start'] * sr)
    end_samples = int(section['end'] * sr)
    section_audio = y[:, start_samples:end_samples]
    section_mono = y_mono[start_samples:end_samples]

    # === TEMPO ESTIMATION (Beat Density) ===
    beats_in_section = [b for b in song_result['_beats']
                        if section['start'] <= b <= section['end']]
    if len(beats_in_section) > 1:
        beat_density = len(beats_in_section) / section['duration']
        section_tempo = beat_density * 60  # Convert to BPM
    else:
        # Fallback to song-level tempo if no beats in section
        section_tempo = song_result['tempo']

    # === KEY DETECTION (Chroma-based) ===
    chroma = librosa.feature.chroma_cqt(y=section_mono, sr=sr, hop_length=512)
    chroma_avg = np.mean(chroma, axis=1)

    # Krumhansl-Schmuckler key profiles (same as song-level)
    keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    correlations = []
    for shift in range(12):
        # Major key correlation
        major_corr = np.corrcoef(
            chroma_avg,
            np.roll(major_profile, shift)
        )[0, 1]
        correlations.append(('major', keys[shift], major_corr))

        # Minor key correlation
        minor_corr = np.corrcoef(
            chroma_avg,
            np.roll(minor_profile, shift)
        )[0, 1]
        correlations.append(('minor', keys[shift], minor_corr))

    best_key = max(correlations, key=lambda x: x[2])
    mode, key, key_confidence = best_key

    # === ENERGY METRICS ===
    rms = librosa.feature.rms(y=section_mono, frame_length=2048, hop_length=512)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    loudness_db = float(np.mean(rms_db))
    loudness_std = float(np.std(rms_db))

    # Spectral centroid (brightness)
    centroid = librosa.feature.spectral_centroid(y=section_mono, sr=sr)[0]
    spectral_centroid = float(np.mean(centroid))

    # Compute energy score for chorus selection
    energy_score = compute_energy_score(loudness_db, spectral_centroid, section['duration'])

    # === EMBEDDINGS EXTRACTION ===
    hop_length = song_result.get('embeddings_hop_length', 512)
    embed_sr = song_result.get('embeddings_sr', 22050)

    timestep_start = int(section['start'] * embed_sr / hop_length)
    timestep_end = int(section['end'] * embed_sr / hop_length)

    # Clamp indices to valid range
    embeddings = song_result['_embeddings']

    # Handle different embedding shapes from allin1
    # Expected: (4 stems, timesteps, 24 dims) or (4, timesteps, 24, 8)
    if embeddings.ndim == 4 and embeddings.shape[0] == 4:
        # Format from allin1: (4, timesteps, 24, 8)
        # Average over the last dimension to get (4, timesteps, 24)
        embeddings = embeddings.mean(axis=-1)  # Now (4, timesteps, 24)

    if embeddings.ndim == 3 and embeddings.shape[0] == 4 and embeddings.shape[2] == 24:
        # Standard format: (4, timesteps, 24)
        timestep_end = min(timestep_end, embeddings.shape[1])
        timestep_start = max(0, timestep_start)
        section_embeddings = embeddings[:, timestep_start:timestep_end, :]
        # Compute mean/std across time for each stem (axis=1 is time)
        embeddings_mean = section_embeddings.mean(axis=1)  # (4, 24)
        embeddings_std = section_embeddings.std(axis=1)    # (4, 24)
    elif embeddings.ndim == 3 and embeddings.shape[0] == 4 and embeddings.shape[1] == 24:
        # Alternative format: (4, 24, timesteps) - need to transpose
        timestep_end = min(timestep_end, embeddings.shape[2])
        timestep_start = max(0, timestep_start)
        section_embeddings = embeddings[:, :, timestep_start:timestep_end]
        # Compute mean/std across time for each stem (axis=2 is time)
        embeddings_mean = section_embeddings.mean(axis=2)  # (4, 24)
        embeddings_std = section_embeddings.std(axis=2)    # (4, 24)
    elif embeddings.ndim == 3:
        # Format: (timesteps, 4, 24) or other
        timestep_end = min(timestep_end, embeddings.shape[0])
        timestep_start = max(0, timestep_start)
        section_embeddings = embeddings[timestep_start:timestep_end, :, :]
        # Compute mean/std across time for each stem (axis=0 is time)
        embeddings_mean = section_embeddings.mean(axis=0)  # (4, 24)
        embeddings_std = section_embeddings.std(axis=0)    # (4, 24)
    else:
        raise ValueError(f"Unexpected embeddings shape: {embeddings.shape}")

    log(f"    Tempo: {section_tempo:.1f} BPM (from {len(beats_in_section)} beats)", verbose)
    log(f"    Key: {key} {mode} (confidence: {key_confidence:.3f})", verbose)
    log(f"    Energy: {loudness_db:.1f} dB, Brightness: {spectral_centroid:.0f} Hz", verbose)
    log(f"    Energy Score: {energy_score:.1f}/100", verbose)
    log(f"    Embeddings shape: {section_embeddings.shape}", verbose)

    return {
        # Metadata
        'song_filename': song_result['filename'],
        'section_index': section_idx,
        'label': section['label'],
        'start': section['start'],
        'end': section['end'],
        'duration': section['duration'],

        # Traditional Metrics
        'tempo': float(section_tempo),
        'key': key,
        'mode': mode,
        'key_confidence': float(key_confidence),
        'full_key': f"{key} {mode}",
        'loudness_db': loudness_db,
        'loudness_std': loudness_std,
        'spectral_centroid': spectral_centroid,
        'energy_score': energy_score,

        # Embedding Features
        'embeddings_shape': section_embeddings.shape,
        'embeddings_mean': embeddings_mean,  # (4, 24) ndarray
        'embeddings_std': embeddings_std,    # (4, 24) ndarray

        # Raw Data (not serialized to CSV)
        '_section_audio': section_audio,
        '_embeddings': section_embeddings,
    }


# =============================================================================
# CHORUS SELECTION
# =============================================================================

def select_best_chorus(song_result, audio_path, fallback_to_verse=True, verbose=True):
    """
    Identify the "best" chorus in a song using energy-based heuristics.

    Args:
        song_result: Full analysis result from analyze_song_allinone()
        audio_path: Path to original audio file
        fallback_to_verse: If True, use verse if no chorus found
        verbose: Enable verbose output

    Returns:
        Dictionary from extract_section_features() for best chorus, or None
    """
    log(f"\nSelecting best chorus from {song_result['filename']}...", verbose)

    # Filter sections by label
    choruses = [i for i, sec in enumerate(song_result['sections'])
                if sec['label'].lower() == 'chorus']

    if not choruses and fallback_to_verse:
        log("  No chorus found, falling back to verse...", verbose)
        choruses = [i for i, sec in enumerate(song_result['sections'])
                    if sec['label'].lower() == 'verse']

    if not choruses:
        log("  No chorus or verse sections found, skipping song.", verbose)
        return None

    log(f"  Found {len(choruses)} candidate sections", verbose)

    # Extract features for all candidates
    candidates = []
    for idx in choruses:
        features = extract_section_features(song_result, idx, audio_path, verbose=False)
        candidates.append((idx, features, features['energy_score']))

    # Select section with highest energy score
    best_idx, best_features, best_score = max(candidates, key=lambda x: x[2])

    log(f"  Selected section {best_idx}: {best_features['label']} "
        f"({best_features['start']:.1f}s-{best_features['end']:.1f}s) "
        f"with energy score {best_score:.1f}/100", verbose)

    return best_features


# =============================================================================
# COMPATIBILITY SCORING
# =============================================================================

def calculate_tempo_score(tempo_a, tempo_b):
    """Calculate tempo compatibility score (0-100)."""
    tempo_diff_pct = abs(tempo_a - tempo_b) / max(tempo_a, tempo_b)

    if tempo_diff_pct < 0.05:
        return 100.0
    elif tempo_diff_pct < 0.10:
        return 100 - (tempo_diff_pct - 0.05) * 400  # Linear 100->80
    elif tempo_diff_pct < 0.15:
        return 80 - (tempo_diff_pct - 0.10) * 400   # Linear 80->60
    elif tempo_diff_pct < 0.20:
        return 60 - (tempo_diff_pct - 0.15) * 1200  # Linear 60->0
    else:
        return 0.0


def calculate_key_score(key_a, mode_a, key_b, mode_b):
    """Calculate key compatibility score (0-100)."""
    if key_a == key_b and mode_a == mode_b:
        return 100.0
    elif key_a == key_b:  # Same root, different mode (relative)
        return 80.0
    else:
        # Simplified compatible key mapping
        compatible_keys = {
            'C': ['G', 'F', 'Am'],
            'G': ['D', 'C', 'Em'],
            'D': ['A', 'G', 'Bm'],
            'A': ['E', 'D', 'F#m'],
            'E': ['B', 'A', 'C#m'],
            'F': ['C', 'Bb', 'Dm'],
        }
        key_b_str = f"{key_b}{' ' if mode_b == 'major' else 'm'}"

        if key_b_str in compatible_keys.get(key_a, []):
            return 70.0
        else:
            return 40.0


def calculate_section_compatibility(section_a, section_b, weights=None, embedding_stems='all'):
    """
    Compute hybrid compatibility score between two sections.

    Args:
        section_a, section_b: Section feature dicts from extract_section_features()
        weights: Dict with keys 'tempo', 'key', 'energy', 'embeddings' (0.0-1.0)
        embedding_stems: Which stems to use ('all', 'vocals', 'bass+drums', etc.)

    Returns:
        Dictionary with detailed compatibility scores
    """
    # Use provided weights or defaults
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    # Validate weights sum to 1.0
    validate_weights(weights)

    # === TEMPO SCORE ===
    tempo_score = calculate_tempo_score(section_a['tempo'], section_b['tempo'])
    tempo_diff_pct = abs(section_a['tempo'] - section_b['tempo']) / max(section_a['tempo'], section_b['tempo']) * 100

    # === KEY SCORE ===
    key_score = calculate_key_score(section_a['key'], section_a['mode'],
                                     section_b['key'], section_b['mode'])

    # === ENERGY SCORE ===
    energy_diff = abs(section_a['loudness_db'] - section_b['loudness_db'])
    energy_score = max(0, 100 - energy_diff * 5)

    # === EMBEDDINGS SCORE ===
    embeddings_score = 0
    stem_similarities = {}

    if weights['embeddings'] > 0:
        # Parse embedding stems
        stem_indices = parse_embedding_stems(embedding_stems)

        # Compute cosine similarity for selected stems
        stem_scores = []
        for stem_idx in stem_indices:
            stem_name = list(STEM_MAP.keys())[list(STEM_MAP.values()).index(stem_idx)]

            emb_a = section_a['embeddings_mean'][stem_idx]  # (24,)
            emb_b = section_b['embeddings_mean'][stem_idx]  # (24,)

            # Cosine similarity
            cosine_sim = np.dot(emb_a, emb_b) / (np.linalg.norm(emb_a) * np.linalg.norm(emb_b))
            # Convert [-1, 1] to [0, 100]
            stem_score = (cosine_sim + 1) / 2 * 100
            stem_scores.append(stem_score)
            stem_similarities[f'embeddings_{stem_name}_similarity'] = float(stem_score)

        # Average across selected stems
        embeddings_score = float(np.mean(stem_scores))
    else:
        # Fill in N/A for all stems when embeddings disabled
        for stem_name in STEM_MAP.keys():
            stem_similarities[f'embeddings_{stem_name}_similarity'] = 0.0

    # === OVERALL SCORE ===
    if weights['embeddings'] == 0:
        # Renormalize other weights to sum to 1.0
        total_weight = weights['tempo'] + weights['key'] + weights['energy']
        overall_score = (tempo_score * weights['tempo'] +
                         key_score * weights['key'] +
                         energy_score * weights['energy']) / total_weight
    else:
        overall_score = (tempo_score * weights['tempo'] +
                         key_score * weights['key'] +
                         energy_score * weights['energy'] +
                         embeddings_score * weights['embeddings'])

    return {
        'song_a': section_a['song_filename'],
        'song_b': section_b['song_filename'],
        'section_a_label': section_a['label'],
        'section_b_label': section_b['label'],
        'section_a_index': section_a['section_index'],
        'section_b_index': section_b['section_index'],
        'section_a_time': f"{section_a['start']:.1f}s-{section_a['end']:.1f}s",
        'section_b_time': f"{section_b['start']:.1f}s-{section_b['end']:.1f}s",

        # Scores
        'overall_score': round(overall_score, 1),
        'tempo_score': round(tempo_score, 1),
        'key_score': round(key_score, 1),
        'energy_score': round(energy_score, 1),
        'embeddings_score': round(embeddings_score, 1),

        # Individual metrics
        'tempo_a': round(section_a['tempo'], 1),
        'tempo_b': round(section_b['tempo'], 1),
        'tempo_diff_pct': round(tempo_diff_pct, 2),
        'key_a': section_a['full_key'],
        'key_b': section_b['full_key'],
        'energy_diff_db': round(energy_diff, 1),

        # Embeddings details
        **stem_similarities
    }


# =============================================================================
# MAIN ANALYSIS FUNCTION
# =============================================================================

def load_all_song_results(audio_dir, cache_dir, verbose=True):
    """
    Load all song analysis results.

    Optimization: First checks for existing poc_full_results.json output,
    then falls back to individual cache files or re-analysis.

    Returns:
        List of song result dictionaries
    """
    results = []

    # First, try loading from main output JSON if it exists
    json_path = ALLINONE_OUTPUT_DIR / 'poc_full_results.json'
    if json_path.exists():
        log(f"  Found existing allinone analysis: {json_path}", verbose)
        try:
            with open(json_path, 'r') as f:
                results_data = json.load(f)

            # We need to call load_from_cache to get _embeddings and _beats
            # but we skip the heavy librosa loading inside analyze_song_allinone
            for result in results_data:
                audio_file = audio_dir / result['filename']
                if audio_file.exists():
                    try:
                        h = compute_file_hash(audio_file)
                        full_result = load_from_cache(h, cache_dir)
                        if full_result:
                            results.append(full_result)
                        else:
                            # Fallback if cache missing
                            results.append(analyze_song_allinone(audio_file, cache_dir=cache_dir, use_cache=True))
                    except Exception as e:
                        log(f"  ⚠️  Error reloading {result['filename']}: {e}", verbose)

            log(f"  ✓ Loaded results for {len(results)} songs via JSON + cache", verbose)
            return results
        except Exception as e:
            log(f"  ⚠️  Could not load from JSON: {e}", verbose)
            log(f"  Falling back to individual cache/analysis...", verbose)

    # Fallback: search for audio files and analyze each
    audio_files = sorted(list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.flac")))
    for audio_file in audio_files:
        try:
            results.append(analyze_song_allinone(audio_file, cache_dir=cache_dir, use_cache=True))
        except Exception as e:
            log(f"  ⚠️  Error analyzing {audio_file.name}: {e}", verbose)

    return results


def analyze_all_sections(audio_dir=AUDIO_DIR, cache_dir=CACHE_DIR, output_dir=OUTPUT_DIR,
                         weights=None, embedding_stems='all', section_type='chorus',
                         fallback_to_verse=True, verbose=True):
    """
    Analyze all songs and extract best chorus sections.

    Args:
        audio_dir: Directory containing audio files
        cache_dir: Directory for cached analysis results
        output_dir: Output directory for results
        weights: Dict with keys 'tempo', 'key', 'energy', 'embeddings' (0.0-1.0)
        embedding_stems: Which stems to use for embeddings
        section_type: Section type to analyze ('chorus', 'verse', 'bridge')
        fallback_to_verse: Fallback to verse if chorus not found
        verbose: Enable verbose output

    Returns:
        (section_features_list, compatibility_df)
    """
    # Validate weights
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()
    else:
        validate_weights(weights)

    log(f"\n{'='*70}", verbose)
    log("SECTION-LEVEL ANALYSIS", verbose)
    log(f"{'='*70}", verbose)
    log(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", verbose)
    log(f"\nConfiguration:", verbose)
    log(f"  Audio directory: {audio_dir.absolute()}", verbose)
    log(f"  Output directory: {output_dir.absolute()}", verbose)
    log(f"  Section type: {section_type}", verbose)
    log(f"  Fallback to verse: {fallback_to_verse}", verbose)
    log(f"  Compatibility weights: {weights}", verbose)
    log(f"  Embedding stems: {embedding_stems}", verbose)

    # List audio files
    audio_files = sorted(list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.flac")))
    log(f"\nFound {len(audio_files)} audio files", verbose)

    if len(audio_files) < 2:
        log("\n⚠️  Need at least 2 songs for compatibility analysis", verbose)
        return [], pd.DataFrame()

    # === ANALYZE ALL SONGS ===
    log(f"\n{'='*70}", verbose)
    log("ANALYZING SONGS", verbose)
    log(f"{'='*70}", verbose)

    section_features = []
    skipped_songs = []

    song_results = load_all_song_results(audio_dir, cache_dir, verbose=verbose)

    for song_result in song_results:
        try:
            audio_file = audio_dir / song_result['filename']

            # Select best chorus (or verse fallback)
            best_section = select_best_chorus(song_result, audio_file,
                                               fallback_to_verse=fallback_to_verse,
                                               verbose=verbose)

            if best_section:
                section_features.append(best_section)
            else:
                skipped_songs.append(song_result['filename'])

        except Exception as e:
            log(f"\n❌ ERROR processing {song_result['filename']}: {str(e)}", verbose)
            import traceback
            traceback.print_exc()
            skipped_songs.append(song_result['filename'])

    if skipped_songs:
        log(f"\n⚠️  Skipped {len(skipped_songs)} songs: {', '.join(skipped_songs)}", verbose)

    if len(section_features) < 2:
        log("\n⚠️  Not enough valid sections for compatibility analysis", verbose)
        return section_features, pd.DataFrame()

    # === CALCULATE COMPATIBILITY ===
    log(f"\n{'='*70}", verbose)
    log("CALCULATING SECTION COMPATIBILITY", verbose)
    log(f"{'='*70}", verbose)

    compatibilities = []
    for i, section_a in enumerate(section_features):
        for j, section_b in enumerate(section_features):
            if i < j:  # Avoid self-comparison and duplicates
                compat = calculate_section_compatibility(
                    section_a, section_b,
                    weights=weights,
                    embedding_stems=embedding_stems
                )
                compatibilities.append(compat)

    compatibility_df = pd.DataFrame(compatibilities)
    compatibility_df = compatibility_df.sort_values('overall_score', ascending=False)

    log(f"\nCalculated {len(compatibilities)} pairwise compatibilities", verbose)
    log(f"Score range: {compatibility_df['overall_score'].min():.1f} - "
        f"{compatibility_df['overall_score'].max():.1f}", verbose)

    return section_features, compatibility_df


# =============================================================================
# OUTPUT GENERATION
# =============================================================================

def save_section_results(section_features, compatibility_df, output_dir, weights, embedding_stems):
    """Save section analysis results to disk."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save section features JSON
    features_path = output_dir / 'section_features.json'
    features_serializable = []
    for feat in section_features:
        feat_dict = {k: v for k, v in feat.items() if not k.startswith('_')}
        # Convert numpy arrays to lists
        feat_dict['embeddings_mean'] = feat['embeddings_mean'].tolist()
        feat_dict['embeddings_std'] = feat['embeddings_std'].tolist()
        feat_dict['embeddings_shape'] = list(feat['embeddings_shape'])
        features_serializable.append(feat_dict)

    with open(features_path, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'total_sections': len(section_features),
            'configuration': {
                'weights': weights,
                'embedding_stems': embedding_stems
            },
            'sections': features_serializable
        }, f, indent=2)

    print(f"\n✓ Section features saved: {features_path}")

    # Save compatibility CSV
    if not compatibility_df.empty:
        csv_path = output_dir / 'section_compatibility_scores.csv'
        compatibility_df.to_csv(csv_path, index=False)
        print(f"✓ Compatibility scores saved: {csv_path}")

        # Generate heatmap
        generate_compatibility_heatmap(section_features, compatibility_df, output_dir)


def generate_compatibility_heatmap(section_features, compatibility_df, output_dir):
    """Generate section compatibility heatmap visualization."""
    if len(section_features) < 2:
        return

    # Create labels with song name and section type
    labels = [f"{feat['song_filename']} [{feat['label']}]" for feat in section_features]

    # Create compatibility matrix
    n = len(section_features)
    matrix = np.zeros((n, n))

    for _, row in compatibility_df.iterrows():
        i = next((idx for idx, f in enumerate(section_features)
                  if f['song_filename'] == row['song_a'] and f['section_index'] == row['section_a_index']), None)
        j = next((idx for idx, f in enumerate(section_features)
                  if f['song_filename'] == row['song_b'] and f['section_index'] == row['section_b_index']), None)

        if i is not None and j is not None:
            matrix[i, j] = row['overall_score']
            matrix[j, i] = row['overall_score']

    # Plot heatmap
    plt.figure(figsize=(12, 10))
    sns.heatmap(matrix, annot=True, fmt='.1f', cmap='RdYlGn', vmin=0, vmax=100,
                xticklabels=labels, yticklabels=labels, cbar_kws={'label': 'Compatibility Score'})
    plt.title("Section-Level Compatibility Matrix\n(Chorus-to-Chorus Compatibility)",
              fontsize=14, fontweight='bold')
    plt.xticks(rotation=45, ha='right')
    plt.yticks(rotation=0)
    plt.tight_layout()

    heatmap_path = output_dir / 'section_compatibility_heatmap.png'
    plt.savefig(heatmap_path, dpi=150, bbox_inches='tight')
    print(f"✓ Heatmap saved: {heatmap_path}")
    plt.close()


# =============================================================================
# CLI INTERFACE
# =============================================================================

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Analyze song sections for compatibility (chorus, verse, bridge)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default weights (25% tempo, 25% key, 15% energy, 35% embeddings)
  python analyze_sections.py

  # Disable embeddings entirely (traditional metrics only)
  python analyze_sections.py --embeddings-weight 0 --tempo-weight 0.4 --key-weight 0.4 --energy-weight 0.2

  # Use only vocals embeddings
  python analyze_sections.py --embedding-stems vocals

  # Custom weights favoring key harmony
  python analyze_sections.py --tempo-weight 0.2 --key-weight 0.5 --energy-weight 0.1 --embeddings-weight 0.2
        """
    )

    # Scoring weights
    parser.add_argument('--tempo-weight', type=float, default=0.25,
                        help='Weight for tempo score (0.0-1.0, default: 0.25)')
    parser.add_argument('--key-weight', type=float, default=0.25,
                        help='Weight for key score (0.0-1.0, default: 0.25)')
    parser.add_argument('--energy-weight', type=float, default=0.15,
                        help='Weight for energy score (0.0-1.0, default: 0.15)')
    parser.add_argument('--embeddings-weight', type=float, default=0.35,
                        help='Weight for embeddings score (0.0-1.0, default: 0.35). Set to 0 to disable.')

    # Embeddings stem selection
    parser.add_argument('--embedding-stems', type=str, default='all',
                        choices=['all', 'bass', 'drums', 'other', 'vocals',
                                 'bass+drums', 'bass+vocals', 'drums+vocals',
                                 'other+vocals', 'bass+drums+vocals'],
                        help='Which stems to use for embeddings scoring (default: all)')

    # Other options
    parser.add_argument('--audio-dir', type=Path, default=AUDIO_DIR,
                        help='Directory containing audio files')
    parser.add_argument('--cache-dir', type=Path, default=CACHE_DIR,
                        help='Directory for cached analysis results')
    parser.add_argument('--output-dir', type=Path, default=OUTPUT_DIR,
                        help='Output directory for results')
    parser.add_argument('--section-type', type=str, default='chorus',
                        choices=['chorus', 'verse', 'bridge'],
                        help='Section type to analyze (default: chorus)')
    parser.add_argument('--fallback-to-verse', action='store_true', default=True,
                        help='Fallback to verse if chorus not found (default: True)')
    parser.add_argument('--verbose', action='store_true', default=True,
                        help='Enable verbose output (default: True)')

    return parser.parse_args()


def main():
    """Main execution function."""
    args = parse_args()

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

    # Run analysis
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

    # Save results
    if section_features:
        save_section_results(section_features, compatibility_df, args.output_dir,
                             weights, args.embedding_stems)

        print(f"\n{'='*70}")
        print("ANALYSIS COMPLETE")
        print(f"{'='*70}")
        print(f"  Sections analyzed: {len(section_features)}")
        print(f"  Compatibility pairs: {len(compatibility_df)}")
        print(f"  Output directory: {args.output_dir.absolute()}")
        print(f"{'='*70}\n")
    else:
        print("\n⚠️  No sections were successfully analyzed.")
        print("   Please check error messages above for details.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
