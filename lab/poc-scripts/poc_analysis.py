#!/usr/bin/env python3
"""
POC Analysis: Worship Music Transition System

Version: 0.1.0-poc
Date: 2024-12-30
Goal: Validate audio analysis pipeline with 3-5 Stream of Praise worship songs

Validation Goals:
1. Tempo detection accuracy (within ±5 BPM of manual count)
2. Key detection accuracy (matches sheet music)
3. Structure segmentation quality (meaningful boundaries)
4. Transition rendering quality (natural sounding crossfades)
"""

import warnings
warnings.filterwarnings('ignore')

# Set matplotlib backend before importing pyplot
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for Docker

# Audio processing
import librosa
import librosa.display
import soundfile as sf

# Data and math
import numpy as np
import pandas as pd
from scipy import signal

# Visualization
import matplotlib.pyplot as plt
import seaborn as sns
sns.set_style("whitegrid")

# Utilities
from pathlib import Path
import json
from datetime import datetime

# Configuration
AUDIO_DIR = Path("poc_audio")
OUTPUT_DIR = Path("poc_output")


def analyze_song(filepath):
    """
    Run complete feature extraction on a single song.

    Returns dictionary with:
    - Basic metadata (filename, duration)
    - Tempo analysis (BPM, beats)
    - Key detection (key, mode, confidence)
    - Energy metrics (RMS, loudness dB)
    - Structure (sections, boundaries)
    - Raw data for visualization
    """
    print(f"\n{'='*70}")
    print(f"Analyzing: {filepath.name}")
    print(f"{'='*70}")

    # === LOAD AUDIO ===
    y, sr = librosa.load(filepath, sr=22050, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"✓ Loaded: {duration:.1f}s @ {sr} Hz")

    # === TEMPO DETECTION ===
    tempo_librosa, beats_frames = librosa.beat.beat_track(
        y=y, sr=sr, start_bpm=80, units='frames'
    )
    tempo_librosa = float(tempo_librosa)  # Convert to scalar to avoid format errors
    beats_time = librosa.frames_to_time(beats_frames, sr=sr)
    print(f"✓ Tempo: {tempo_librosa:.1f} BPM ({len(beats_time)} beats detected)")

    # === KEY DETECTION ===
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    chroma_avg = np.mean(chroma, axis=1)

    # Krumhansl-Schmuckler key profiles
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
    mode, key, confidence = best_key
    print(f"✓ Key: {key} {mode} (confidence: {confidence:.3f})")

    # === ENERGY ANALYSIS ===
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    loudness_mean = float(np.mean(rms_db))
    loudness_std = float(np.std(rms_db))

    # Spectral centroid (brightness)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    centroid_mean = float(np.mean(centroid))

    print(f"✓ Energy: {loudness_mean:.1f} dB (±{loudness_std:.1f})")
    print(f"  Brightness: {centroid_mean:.0f} Hz")

    # === STRUCTURE SEGMENTATION ===
    # Multi-feature approach: Combine harmonic, timbral, and rhythmic features
    # to detect major structural boundaries (verse, chorus, bridge transitions)

    hop_length_struct = 512

    # 1. Harmonic features (chord/key changes)
    chroma_cqt = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length_struct)

    # 2. Timbral features (instrument/vocal changes)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop_length_struct)
    mfcc = librosa.util.normalize(mfcc, axis=1)

    # 3. Spectral contrast (texture changes)
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop_length_struct)
    contrast = librosa.util.normalize(contrast, axis=1)

    # Combine features into single matrix
    features = np.vstack([chroma_cqt, mfcc, contrast])

    # Compute self-similarity matrix on combined features
    R = librosa.segment.recurrence_matrix(
        features,
        mode='affinity',
        metric='cosine',
        width=9,  # Compare across ±9 frames (~4 seconds at hop=512)
        sym=True
    )

    # Extract novelty (boundary strength) from recurrence
    # Use checkerboard kernel to detect transitions
    novelty = librosa.segment.recurrence_to_lag(R, pad=False, axis=1)

    # Compute the degree (row sum) of recurrence matrix
    # Low degree = different from surroundings = likely boundary
    deg = np.sum(R, axis=0)
    deg_smooth = signal.medfilt(deg, kernel_size=9)  # Median filter to remove noise

    # Invert so peaks = boundaries
    novelty_combined = 1.0 - (deg_smooth / np.max(deg_smooth))

    # Smooth the novelty curve with Gaussian filter
    from scipy.ndimage import gaussian_filter1d
    novelty_smooth = gaussian_filter1d(novelty_combined, sigma=20)

    # Ensure float64 dtype and handle NaN/inf values
    novelty_smooth = np.asarray(novelty_smooth, dtype=np.float64)
    novelty_smooth = np.nan_to_num(novelty_smooth, nan=0.0, posinf=0.0, neginf=0.0)

    # Adaptive threshold: find prominent peaks only
    threshold = float(np.mean(novelty_smooth) + 0.5 * np.std(novelty_smooth))

    # Find peaks above threshold with minimum spacing
    peaks = librosa.util.peak_pick(
        novelty_smooth,
        pre_max=30,   # ~3 seconds
        post_max=30,
        pre_avg=30,
        post_avg=30,
        delta=float(threshold * 0.3),
        wait=60       # Minimum ~6 seconds between boundaries
    )

    # Convert to time
    boundary_times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop_length_struct)
    boundaries = [0.0] + boundary_times.tolist() + [duration]

    # Post-process: merge segments that are too short (< 10 seconds)
    min_segment_duration = 10.0
    filtered_boundaries = [boundaries[0]]

    for i in range(1, len(boundaries) - 1):
        if boundaries[i] - filtered_boundaries[-1] >= min_segment_duration:
            filtered_boundaries.append(boundaries[i])

    # Always include the final boundary
    filtered_boundaries.append(boundaries[-1])
    boundaries = filtered_boundaries

    # Safety check: if we have too few sections, try lower threshold
    if len(boundaries) <= 3:  # Only intro/outro or single section
        print("  ⚠️  Initial segmentation found too few sections, adjusting...")
        threshold = float(np.mean(novelty_smooth) + 0.2 * np.std(novelty_smooth))
        peaks = librosa.util.peak_pick(
            novelty_smooth,
            pre_max=20,
            post_max=20,
            pre_avg=20,
            post_avg=20,
            delta=float(threshold * 0.2),
            wait=40
        )
        boundary_times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop_length_struct)
        boundaries = [0.0] + boundary_times.tolist() + [duration]

        # Re-filter for minimum duration
        filtered_boundaries = [boundaries[0]]
        for i in range(1, len(boundaries) - 1):
            if boundaries[i] - filtered_boundaries[-1] >= min_segment_duration:
                filtered_boundaries.append(boundaries[i])
        filtered_boundaries.append(boundaries[-1])
        boundaries = filtered_boundaries

    # Label sections (simplified heuristic)
    sections = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        sec_duration = end - start

        if i == 0 and sec_duration < 15:
            label = 'intro'
        elif i == len(boundaries) - 2 and sec_duration < 20:
            label = 'outro'
        elif sec_duration > 30:
            label = 'verse'
        else:
            label = 'chorus'

        sections.append({
            'label': label,
            'start': start,
            'end': end,
            'duration': sec_duration
        })

    print(f"✓ Structure: {len(sections)} sections detected")
    for sec in sections:
        print(f"  {sec['start']:.1f}s - {sec['end']:.1f}s: {sec['label']} ({sec['duration']:.1f}s)")

    # === RETURN RESULTS ===
    return {
        # Metadata
        'filename': filepath.name,
        'filepath': str(filepath),
        'duration': duration,

        # Rhythm
        'tempo': tempo_librosa,
        'num_beats': len(beats_time),
        'beats': beats_time.tolist()[:100],  # Store first 100 beats

        # Harmony
        'key': key,
        'mode': mode,
        'key_confidence': float(confidence),
        'full_key': f"{key} {mode}",

        # Energy
        'loudness_db': loudness_mean,
        'loudness_std': loudness_std,
        'spectral_centroid': centroid_mean,

        # Structure
        'num_sections': len(sections),
        'sections': sections,
        'boundaries': boundaries,

        # Raw data for visualization (prefixed with _)
        '_y': y,
        '_sr': sr,
        '_chroma': chroma,
        '_rms': rms,
        '_beats': beats_time
    }


def calculate_compatibility(song_a, song_b):
    """
    Calculate compatibility scores between two songs.

    Scoring:
    - Tempo: 100 if <5% diff, scales down to 0 at >20% diff
    - Key: 100 if same, 80 if compatible, 40 otherwise
    - Energy: Based on loudness difference
    - Overall: Weighted average (40% tempo + 40% key + 20% energy)
    """
    # === TEMPO COMPATIBILITY ===
    tempo_diff_pct = abs(song_a['tempo'] - song_b['tempo']) / max(song_a['tempo'], song_b['tempo'])

    if tempo_diff_pct < 0.05:
        tempo_score = 100.0
    elif tempo_diff_pct < 0.10:
        tempo_score = 100 - (tempo_diff_pct - 0.05) * 400  # Linear 100->80
    elif tempo_diff_pct < 0.15:
        tempo_score = 80 - (tempo_diff_pct - 0.10) * 400   # Linear 80->60
    elif tempo_diff_pct < 0.20:
        tempo_score = 60 - (tempo_diff_pct - 0.15) * 1200  # Linear 60->0
    else:
        tempo_score = 0.0

    # === KEY COMPATIBILITY (SIMPLIFIED) ===
    # Full implementation would use Camelot wheel
    if song_a['key'] == song_b['key'] and song_a['mode'] == song_b['mode']:
        key_score = 100.0
    elif song_a['key'] == song_b['key']:  # Same root, different mode (relative)
        key_score = 80.0
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
        key_a = f"{song_a['key']}{' ' if song_a['mode'] == 'major' else 'm'}"
        key_b = f"{song_b['key']}{' ' if song_b['mode'] == 'major' else 'm'}"

        if key_b in compatible_keys.get(song_a['key'], []):
            key_score = 70.0
        else:
            key_score = 40.0

    # === ENERGY COMPATIBILITY ===
    energy_diff = abs(song_a['loudness_db'] - song_b['loudness_db'])
    energy_score = max(0, 100 - energy_diff * 5)  # 5dB diff = 75 score

    # === OVERALL SCORE ===
    overall_score = (tempo_score * 0.40 +
                     key_score * 0.40 +
                     energy_score * 0.20)

    return {
        'song_a': song_a['filename'],
        'song_b': song_b['filename'],
        'tempo_a': song_a['tempo'],
        'tempo_b': song_b['tempo'],
        'tempo_diff_pct': tempo_diff_pct * 100,
        'tempo_score': round(tempo_score, 1),
        'key_a': song_a['full_key'],
        'key_b': song_b['full_key'],
        'key_score': round(key_score, 1),
        'energy_diff_db': round(energy_diff, 1),
        'energy_score': round(energy_score, 1),
        'overall_score': round(overall_score, 1)
    }


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
    print(f"\nCreating {crossfade_duration}s crossfade...")

    # Load stereo audio for higher quality transition
    y_a, sr = librosa.load(song_a_path, sr=44100, mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=44100, mono=False)

    # Ensure stereo (2 channels)
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    crossfade_samples = int(crossfade_duration * sr)

    # Extract segments
    outro = y_a[:, -crossfade_samples:]  # Last N seconds of A
    intro = y_b[:, :crossfade_samples]   # First N seconds of B

    print(f"  Outro shape: {outro.shape}")
    print(f"  Intro shape: {intro.shape}")

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


def main():
    """Main execution function."""
    # === CELL 1: SETUP AND IMPORTS ===
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"POC Analysis Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Audio directory: {AUDIO_DIR.absolute()}")
    print(f"Output directory: {OUTPUT_DIR.absolute()}")
    print()

    # List available songs
    audio_files = sorted(list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.flac")))
    print(f"Found {len(audio_files)} audio files:")
    for i, f in enumerate(audio_files, 1):
        print(f"  {i}. {f.name}")

    if len(audio_files) < 3:
        print("\n⚠️  WARNING: Need at least 3 songs for meaningful POC validation")
        print("   Please add more audio files to poc_audio/ directory")
    elif len(audio_files) > 5:
        print("\n⚠️  NOTE: More than 5 songs found. POC will analyze all.")
    else:
        print(f"\n✓ Good! {len(audio_files)} songs ready for analysis")

    # === CELL 3: ANALYZE ALL SONGS ===
    results = []
    errors = []

    for audio_file in audio_files:
        try:
            result = analyze_song(audio_file)
            results.append(result)
        except Exception as e:
            print(f"\n❌ ERROR processing {audio_file.name}: {str(e)}")
            errors.append({'file': audio_file.name, 'error': str(e)})

    # Create summary DataFrame
    df_summary = pd.DataFrame([
        {k: v for k, v in r.items() if not k.startswith('_')}
        for r in results
    ])

    # Display summary
    print("\n" + "="*70)
    print("ANALYSIS SUMMARY")
    print("="*70)
    if not df_summary.empty:
        print(df_summary[['filename', 'duration', 'tempo', 'full_key', 'loudness_db', 'num_sections']].to_string(index=False))

    if errors:
        print(f"\n⚠️  {len(errors)} files failed to process:")
        for err in errors:
            print(f"  - {err['file']}: {err['error']}")

    # Save summary to CSV
    if not df_summary.empty:
        csv_path = OUTPUT_DIR / "poc_summary.csv"
        df_summary.to_csv(csv_path, index=False)
        print(f"\n✓ Summary saved to: {csv_path}")

        # Save full results to JSON (including raw data references)
        json_path = OUTPUT_DIR / "poc_full_results.json"
        results_serializable = [
            {k: v for k, v in r.items() if not k.startswith('_')}
            for r in results
        ]
        with open(json_path, 'w') as f:
            json.dump(results_serializable, f, indent=2)
        print(f"✓ Full results saved to: {json_path}")
    else:
        print("\n⚠️  No songs were successfully analyzed.")

    # === CELL 4: VISUALIZATIONS ===
    if not results:
        print("⚠️  No results to visualize. Please ensure audio files are in poc_audio/ directory.")
    else:
        # Create comprehensive visualization
        n_songs = len(results)
        fig, axes = plt.subplots(n_songs, 3, figsize=(18, 5*n_songs))

        # Handle single song case
        if n_songs == 1:
            axes = axes.reshape(1, -1)

        for idx, result in enumerate(results):
            y = result['_y']
            sr = result['_sr']
            chroma = result['_chroma']
            rms = result['_rms']
            beats = result['_beats']

            # === Panel 1: Waveform with beats ===
            ax = axes[idx, 0]
            times = np.arange(len(y)) / sr
            ax.plot(times, y, alpha=0.7, linewidth=0.5)

            # Mark beats
            for beat in beats[:50]:  # First 50 beats for clarity
                ax.axvline(beat, color='red', alpha=0.3, linewidth=1)

            ax.set_title(f"{result['filename']}\nWaveform + Beats", fontsize=12, fontweight='bold')
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude")
            ax.grid(True, alpha=0.3)

            # === Panel 2: Chromagram ===
            ax = axes[idx, 1]
            img = librosa.display.specshow(
                chroma,
                sr=sr,
                x_axis='time',
                y_axis='chroma',
                hop_length=512,
                ax=ax,
                cmap='coolwarm'
            )
            ax.set_title(f"Chromagram\nDetected Key: {result['full_key']} (conf: {result['key_confidence']:.2f})",
                         fontsize=12, fontweight='bold')
            plt.colorbar(img, ax=ax, format='%.2f')

            # === Panel 3: Energy Profile ===
            ax = axes[idx, 2]
            rms_times = librosa.times_like(rms, sr=sr, hop_length=512)
            ax.plot(rms_times, rms, color='purple', linewidth=2)
            ax.fill_between(rms_times, 0, rms, alpha=0.3, color='purple')

            # Mark sections
            for section in result['sections']:
                color = {'intro': 'green', 'verse': 'blue', 'chorus': 'orange', 'outro': 'red'}.get(section['label'], 'gray')
                ax.axvspan(section['start'], section['end'], alpha=0.2, color=color, label=section['label'])

            ax.set_title(f"Energy Profile\n{result['tempo']:.1f} BPM, {result['loudness_db']:.1f} dB",
                         fontsize=12, fontweight='bold')
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("RMS Energy")
            ax.grid(True, alpha=0.3)

            # Legend (only unique labels)
            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys(), loc='upper right', fontsize=8)

        plt.tight_layout()
        viz_path = OUTPUT_DIR / "poc_analysis_visualizations.png"
        plt.savefig(viz_path, dpi=150, bbox_inches='tight')
        print(f"✓ Visualizations saved to: {viz_path}")

    # === CELL 5: COMPATIBILITY ANALYSIS ===
    if len(results) >= 2:
        # Calculate all pairwise compatibilities
        compatibilities = []
        for i, song_a in enumerate(results):
            for j, song_b in enumerate(results):
                if i < j:  # Avoid self-comparison and duplicates
                    compat = calculate_compatibility(song_a, song_b)
                    compatibilities.append(compat)

        # Create compatibility DataFrame
        df_compat = pd.DataFrame(compatibilities)
        df_compat_sorted = df_compat.sort_values('overall_score', ascending=False)

        print("\n" + "="*70)
        print("COMPATIBILITY MATRIX")
        print("="*70)
        print(df_compat_sorted.to_string(index=False))

        # Save to CSV
        compat_csv_path = OUTPUT_DIR / "poc_compatibility_scores.csv"
        df_compat_sorted.to_csv(compat_csv_path, index=False)
        print(f"\n✓ Compatibility matrix saved to: {compat_csv_path}")

        # Visualize compatibility heatmap
        if len(results) >= 2:
            plt.figure(figsize=(10, 8))

            # Create pivot table for heatmap
            song_names = [r['filename'] for r in results]
            matrix = np.zeros((len(song_names), len(song_names)))

            for compat in compatibilities:
                i = next(idx for idx, r in enumerate(results) if r['filename'] == compat['song_a'])
                j = next(idx for idx, r in enumerate(results) if r['filename'] == compat['song_b'])
                matrix[i, j] = compat['overall_score']
                matrix[j, i] = compat['overall_score']

            sns.heatmap(matrix, annot=True, fmt='.1f', cmap='RdYlGn', vmin=0, vmax=100,
                        xticklabels=song_names, yticklabels=song_names)
            plt.title("Song Compatibility Matrix\n(Overall Score 0-100)", fontsize=14, fontweight='bold')
            plt.tight_layout()
            heatmap_path = OUTPUT_DIR / "poc_compatibility_heatmap.png"
            plt.savefig(heatmap_path, dpi=150, bbox_inches='tight')
            print(f"✓ Heatmap saved to: {heatmap_path}")
    else:
        print("\n⚠️  Need at least 2 songs to analyze compatibility")
        compatibilities = []

    # === CELL 6: SIMPLE TRANSITION PROTOTYPE ===
    if len(compatibilities) > 0:
        best_pair = df_compat_sorted.iloc[0]

        print("\n" + "="*70)
        print("TRANSITION PROTOTYPE")
        print("="*70)
        print(f"Creating transition between most compatible pair:")
        print(f"  Song A: {best_pair['song_a']}")
        print(f"  Song B: {best_pair['song_b']}")
        print(f"  Overall compatibility: {best_pair['overall_score']:.1f}/100")
        print(f"  Tempo match: {best_pair['tempo_score']:.1f}/100 ({best_pair['tempo_a']:.1f} -> {best_pair['tempo_b']:.1f} BPM)")
        print(f"  Key match: {best_pair['key_score']:.1f}/100 ({best_pair['key_a']} -> {best_pair['key_b']})")

        # Find file paths
        song_a_path = AUDIO_DIR / best_pair['song_a']
        song_b_path = AUDIO_DIR / best_pair['song_b']

        # Create transition
        transition, sr = create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=10.0)

        # Save transition audio
        safe_name_a = best_pair['song_a'].replace('.mp3', '').replace('.flac', '')
        safe_name_b = best_pair['song_b'].replace('.mp3', '').replace('.flac', '')
        transition_filename = f"transition_{safe_name_a}_to_{safe_name_b}.flac"
        transition_path = OUTPUT_DIR / transition_filename

        sf.write(transition_path, transition.T, sr)
        print(f"\n✓ Transition audio saved to: {transition_path}")
        print(f"  Duration: {transition.shape[1] / sr:.1f}s")
        print(f"  Channels: {transition.shape[0]}")
        print(f"  Sample rate: {sr} Hz")

        # Visualize transition waveform
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))

        # Left channel
        ax = axes[0]
        times = np.arange(transition.shape[1]) / sr
        ax.plot(times, transition[0, :], linewidth=0.5, color='blue')
        ax.fill_between(times, 0, transition[0, :], alpha=0.3, color='blue')
        ax.set_title("Transition Waveform - Left Channel", fontsize=12, fontweight='bold')
        ax.set_ylabel("Amplitude")
        ax.grid(True, alpha=0.3)
        ax.axvline(5.0, color='red', linestyle='--', label='Crossfade midpoint')
        ax.legend()

        # Right channel
        ax = axes[1]
        ax.plot(times, transition[1, :], linewidth=0.5, color='green')
        ax.fill_between(times, 0, transition[1, :], alpha=0.3, color='green')
        ax.set_title("Transition Waveform - Right Channel", fontsize=12, fontweight='bold')
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude")
        ax.grid(True, alpha=0.3)
        ax.axvline(5.0, color='red', linestyle='--', label='Crossfade midpoint')
        ax.legend()

        plt.tight_layout()
        waveform_path = OUTPUT_DIR / "transition_waveform.png"
        plt.savefig(waveform_path, dpi=150, bbox_inches='tight')
        print(f"✓ Waveform visualization saved to: {waveform_path}")

    else:
        print("\n⚠️  No song pairs to analyze (need at least 2 songs)")

    # === CELL 7: POC SUMMARY AND NEXT STEPS ===
    print("\n" + "="*70)
    print("POC SUMMARY REPORT")
    print("="*70)

    if results:
        # Statistics
        print(f"\n📊 Analysis Statistics:")
        print(f"  Total songs analyzed: {len(results)}")
        print(f"  Total errors: {len(errors)}")
        print(f"  Tempo range: {df_summary['tempo'].min():.1f} - {df_summary['tempo'].max():.1f} BPM")
        print(f"  Keys detected: {', '.join(sorted(df_summary['full_key'].unique()))}")
        print(f"  Average duration: {df_summary['duration'].mean():.1f}s ({df_summary['duration'].mean()/60:.1f} min)")
        print(f"  Average sections per song: {df_summary['num_sections'].mean():.1f}")

        if len(compatibilities) > 0:
            print(f"\n🔗 Compatibility Analysis:")
            print(f"  Total pairings analyzed: {len(compatibilities)}")
            print(f"  Best compatibility score: {df_compat['overall_score'].max():.1f}/100")
            print(f"  Worst compatibility score: {df_compat['overall_score'].min():.1f}/100")
            print(f"  Average compatibility: {df_compat['overall_score'].mean():.1f}/100")
            print(f"  High-quality pairs (>70): {len(df_compat[df_compat['overall_score'] > 70])}")

        # Outputs generated
        print(f"\n📁 Outputs Generated:")
        outputs = [
            ("poc_summary.csv", "Summary table (CSV)"),
            ("poc_full_results.json", "Full analysis results (JSON)"),
            ("poc_analysis_visualizations.png", "Song visualizations (waveform, chroma, energy)"),
        ]

        if len(compatibilities) > 0:
            outputs.extend([
                ("poc_compatibility_scores.csv", "Compatibility matrix (CSV)"),
                ("poc_compatibility_heatmap.png", "Compatibility heatmap"),
            ])
            if 'transition_filename' in locals():
                outputs.extend([
                    (transition_filename, "Sample transition audio (FLAC)"),
                    ("transition_waveform.png", "Transition waveform visualization")
                ])

        for idx, (filename, description) in enumerate(outputs, 1):
            filepath = OUTPUT_DIR / filename
            if filepath.exists():
                size_kb = filepath.stat().st_size / 1024
                print(f"  {idx}. {filename}")
                print(f"     {description} ({size_kb:.1f} KB)")

        # Validation questions
        print(f"\n✅ VALIDATION CHECKLIST:")
        print(f"Please manually verify the following:")
        print(f"\n1. Tempo Accuracy:")
        print(f"   - Listen to each song and tap along to count BPM")
        print(f"   - Compare to detected tempo (should be within ±5 BPM)")
        print(f"   - Detected tempos: {dict(zip(df_summary['filename'], df_summary['tempo'].round(1)))}")

        print(f"\n2. Key Detection:")
        print(f"   - Compare detected keys to sheet music (if available)")
        print(f"   - Or use external key detection tools (e.g., Mixed In Key)")
        print(f"   - Detected keys: {dict(zip(df_summary['filename'], df_summary['full_key']))}")

        print(f"\n3. Transition Quality:")
        if len(compatibilities) > 0 and 'transition_path' in locals():
            print(f"   - Listen to: {transition_path}")
            print(f"   - Does the crossfade sound natural?")
            print(f"   - Are there any jarring discontinuities?")
            print(f"   - Does the tempo/key mismatch create dissonance?")
        else:
            print(f"   - (Need at least 2 songs to test transitions)")

        print(f"\n4. Section Boundaries:")
        print(f"   - Review visualizations: poc_analysis_visualizations.png")
        print(f"   - Do colored regions align with actual song structure?")
        print(f"   - Are intro/outro/verse/chorus labels reasonable?")

        # Next steps
        print(f"\n🚀 NEXT STEPS:")
        print(f"\n✓ POC Complete - Ready for Phase 2 if validation passes!")
        print(f"\nIf validation is successful:")
        print(f"  1. Document any accuracy issues or edge cases")
        print(f"  2. Proceed to Phase 2: Core Infrastructure")
        print(f"     - Implement PostgreSQL database schema")
        print(f"     - Build modular preprocessing pipeline")
        print(f"     - Add madmom beat tracking for improved accuracy")
        print(f"     - Implement Camelot wheel for key compatibility")
        print(f"  3. Reference: specs/worship-music-transition-system-design.md")

        print(f"\nIf validation fails:")
        print(f"  1. Document specific failure cases")
        print(f"  2. Adjust analysis parameters (see analyze_song function)")
        print(f"  3. Consider alternative algorithms:")
        print(f"     - madmom for tempo (already in pyproject.toml)")
        print(f"     - Essentia for key detection")
        print(f"     - Manual boundary annotation")
    else:
        print(f"\n⚠️  No audio files were analyzed.")
        print(f"\nPlease:")
        print(f"  1. Place 3-5 audio files (MP3/FLAC) in: {AUDIO_DIR.absolute()}")
        print(f"  2. Re-run this script")

    print(f"\n" + "="*70)
    print(f"POC Analysis Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"="*70)


if __name__ == "__main__":
    main()
